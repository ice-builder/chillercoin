"""
Scalper Pro — Dynamic Stop Manager

Replaces static SL/TP with adaptive stops that evolve during a trade.

4 Phases:
  1. INITIAL (0-15 min): Wide stop = emergency only, let price breathe
  2. CONFIRM (15-30 min): If favorable → tighten to breakeven+buffer
  3. TRAIL (30+ min): ATR-based trailing stop, tracks peak price
  4. PROTECT (at +1.5×SL): Aggressive trail to lock profits

ALWAYS: Emergency hard stop (5% default) — never removed, final safety net.
"""
import time
import math
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import config

logger = logging.getLogger("scalper.dynamic_stops")


@dataclass
class StopState:
    """Current state of dynamic stops for one position."""
    phase: str = "initial"       # initial / confirm / trail / protect
    phase_start_time: float = 0.0

    # Core prices
    entry_price: float = 0.0
    direction: str = "long"
    peak_price: float = 0.0      # Best price seen (for trailing)

    # Current stop levels
    current_sl: float = 0.0      # Actual stop price
    emergency_sl: float = 0.0    # Hard stop price — never moves toward entry
    current_tp: float = 0.0      # Take profit target

    # Parameters (from hypothesis or defaults)
    base_sl_pct: float = 1.5
    base_tp_pct: float = 3.0
    trail_pct: float = 0.15
    atr: float = 0.0             # ATR at entry for volatility-aware stops

    # Tracking
    max_favorable_pct: float = 0.0
    max_adverse_pct: float = 0.0
    breakeven_activated: bool = False
    partial_taken: bool = False

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "current_sl": self.current_sl,
            "emergency_sl": self.emergency_sl,
            "current_tp": self.current_tp,
            "peak_price": self.peak_price,
            "max_favorable_pct": round(self.max_favorable_pct, 3),
            "max_adverse_pct": round(self.max_adverse_pct, 3),
            "breakeven_activated": self.breakeven_activated,
        }


class DynamicStopManager:
    """
    Manages stop evolution across the 4 phases of a trade.
    """

    def __init__(self):
        self.states: dict[int, StopState] = {}  # trade_id -> StopState

    def init_trade(self, trade_id: int, entry_price: float,
                   direction: str, sl_pct: float = 1.5,
                   tp_pct: float = 3.0, trail_pct: float = 0.15,
                   atr: float = 0.0) -> StopState:
        """Initialize stop state for a new trade."""
        state = StopState(
            phase="initial",
            phase_start_time=time.time(),
            entry_price=entry_price,
            direction=direction,
            peak_price=entry_price,
            base_sl_pct=sl_pct,
            base_tp_pct=tp_pct,
            trail_pct=trail_pct,
            atr=atr,
        )

        # Initial phase: wide stop (2× base) to let price breathe
        wide_sl_pct = sl_pct * config.INITIAL_STOP_MULT
        if direction == "long":
            state.current_sl = entry_price * (1 - wide_sl_pct / 100)
            state.emergency_sl = entry_price * (1 - config.EMERGENCY_STOP_PCT / 100)
            state.current_tp = entry_price * (1 + tp_pct / 100)
        else:
            state.current_sl = entry_price * (1 + wide_sl_pct / 100)
            state.emergency_sl = entry_price * (1 + config.EMERGENCY_STOP_PCT / 100)
            state.current_tp = entry_price * (1 - tp_pct / 100)

        self.states[trade_id] = state
        logger.info(
            f"🛡️ [{trade_id}] Dynamic stops initialized: "
            f"SL={state.current_sl:.6g} (wide {wide_sl_pct:.1f}%) "
            f"TP={state.current_tp:.6g} "
            f"EMERGENCY={state.emergency_sl:.6g}"
        )
        return state

    def update(self, trade_id: int, current_price: float,
               current_vol_z: float = 0) -> Tuple[StopState, Optional[str]]:
        """
        Update stops based on current price. Returns (state, exit_signal).
        exit_signal is None if no exit, or reason string if should exit.
        """
        state = self.states.get(trade_id)
        if not state:
            return StopState(), None

        now = time.time()
        age_sec = now - state.phase_start_time

        # ── Update favorable/adverse ──────────────────────────────────────
        if state.direction == "long":
            fav = (current_price / state.entry_price - 1) * 100
            adv = (1 - current_price / state.entry_price) * 100
            state.peak_price = max(state.peak_price, current_price)
        else:
            fav = (1 - current_price / state.entry_price) * 100
            adv = (current_price / state.entry_price - 1) * 100
            state.peak_price = min(state.peak_price, current_price)

        state.max_favorable_pct = max(state.max_favorable_pct, fav)
        state.max_adverse_pct = max(state.max_adverse_pct, adv)

        # ── EMERGENCY CHECK (always first) ────────────────────────────────
        if state.direction == "long" and current_price <= state.emergency_sl:
            return state, "emergency_stop"
        elif state.direction == "short" and current_price >= state.emergency_sl:
            return state, "emergency_stop"

        # ── TAKE PROFIT CHECK ─────────────────────────────────────────────
        if state.direction == "long" and current_price >= state.current_tp:
            return state, "take_profit"
        elif state.direction == "short" and current_price <= state.current_tp:
            return state, "take_profit"

        # ── PHASE TRANSITIONS ─────────────────────────────────────────────
        if state.phase == "initial":
            exit_signal = self._phase_initial(state, current_price, age_sec)
        elif state.phase == "confirm":
            exit_signal = self._phase_confirm(state, current_price, age_sec)
        elif state.phase == "trail":
            exit_signal = self._phase_trail(state, current_price, age_sec, current_vol_z)
        elif state.phase == "protect":
            exit_signal = self._phase_protect(state, current_price)
        else:
            exit_signal = None

        # ── Regular SL check ──────────────────────────────────────────────
        if exit_signal is None:
            if state.direction == "long" and current_price <= state.current_sl:
                exit_signal = f"dynamic_sl_{state.phase}"
            elif state.direction == "short" and current_price >= state.current_sl:
                exit_signal = f"dynamic_sl_{state.phase}"

        return state, exit_signal

    def _phase_initial(self, state: StopState, price: float,
                       age_sec: float) -> Optional[str]:
        """
        INITIAL phase (0-15 min): Wide stop, observe impulse development.
        Transition to CONFIRM after 15 min if price is favorable.
        """
        if age_sec < config.CONFIRM_WINDOW_SEC:
            return None

        # After 15 min: check if impulse confirmed
        if state.direction == "long":
            favorable = price > state.entry_price
        else:
            favorable = price < state.entry_price

        if favorable:
            # Impulse confirmed → tighten stop
            state.phase = "confirm"
            state.phase_start_time = time.time()

            # Move SL to breakeven + buffer (commission protection)
            buffer_pct = config.COMMISSION_ROUNDTRIP_PCT * 2  # 0.16%
            if state.direction == "long":
                state.current_sl = state.entry_price * (1 + buffer_pct / 100)
            else:
                state.current_sl = state.entry_price * (1 - buffer_pct / 100)

            state.breakeven_activated = True
            logger.info(
                f"🔄 [{id(state)}] → CONFIRM: impulse confirmed, "
                f"SL → breakeven+{buffer_pct:.2f}%"
            )
        else:
            # Impulse NOT confirmed after 15 min → tighten to base SL
            state.phase = "confirm"
            state.phase_start_time = time.time()

            if state.direction == "long":
                state.current_sl = state.entry_price * (1 - state.base_sl_pct / 100)
            else:
                state.current_sl = state.entry_price * (1 + state.base_sl_pct / 100)

            logger.info(
                f"🔄 [{id(state)}] → CONFIRM: impulse weak, "
                f"SL tightened to base {state.base_sl_pct}%"
            )

        return None

    def _phase_confirm(self, state: StopState, price: float,
                       age_sec: float) -> Optional[str]:
        """
        CONFIRM phase (15-30 min): Price confirmed direction.
        Start trailing when favorable > SL.
        """
        if state.direction == "long":
            favorable_pct = (price / state.entry_price - 1) * 100
        else:
            favorable_pct = (1 - price / state.entry_price) * 100

        # Transition to TRAIL when favorable > base_sl (1:1 RR reached)
        if favorable_pct >= state.base_sl_pct:
            state.phase = "trail"
            state.phase_start_time = time.time()
            self._update_trail_stop(state, price)
            logger.info(
                f"🔄 [{id(state)}] → TRAIL: favorable {favorable_pct:.2f}% "
                f"> SL {state.base_sl_pct}%"
            )

        # Also transition after 30 min regardless
        elif age_sec > 1800:
            state.phase = "trail"
            state.phase_start_time = time.time()
            self._update_trail_stop(state, price)

        return None

    def _phase_trail(self, state: StopState, price: float,
                     age_sec: float, vol_z: float) -> Optional[str]:
        """
        TRAIL phase: ATR-based trailing stop following peak price.
        Transition to PROTECT at 1.5× SL profit.
        """
        # Update trail stop
        self._update_trail_stop(state, price)

        # Transition to PROTECT at 1.5× SL profit
        if state.direction == "long":
            favorable_pct = (price / state.entry_price - 1) * 100
        else:
            favorable_pct = (1 - price / state.entry_price) * 100

        protect_threshold = state.base_sl_pct * 1.5
        if favorable_pct >= protect_threshold:
            state.phase = "protect"
            state.phase_start_time = time.time()
            # Tighten trail for profit protection
            self._update_trail_stop(state, price, tighten_factor=0.6)
            logger.info(
                f"🔄 [{id(state)}] → PROTECT: favorable {favorable_pct:.2f}% "
                f"≥ {protect_threshold:.1f}%, trail tightened"
            )

        return None

    def _phase_protect(self, state: StopState, price: float) -> Optional[str]:
        """
        PROTECT phase: Tight trailing to lock profits.
        Trail at 60% of normal width.
        """
        self._update_trail_stop(state, price, tighten_factor=0.6)
        return None

    def _update_trail_stop(self, state: StopState, price: float,
                           tighten_factor: float = 1.0):
        """Update trailing stop based on peak price."""
        # Use ATR if available, otherwise percentage-based
        if state.atr > 0:
            trail_distance = state.atr * config.TRAIL_ATR_MULT * tighten_factor
        else:
            trail_distance = (state.peak_price * state.trail_pct / 100
                             * tighten_factor)

        if state.direction == "long":
            new_sl = state.peak_price - trail_distance
            # Trail only moves UP for longs
            if new_sl > state.current_sl:
                state.current_sl = new_sl
        else:
            new_sl = state.peak_price + trail_distance
            # Trail only moves DOWN for shorts
            if new_sl < state.current_sl:
                state.current_sl = new_sl

    def remove_trade(self, trade_id: int):
        """Remove trade from tracking."""
        self.states.pop(trade_id, None)

    def get_state(self, trade_id: int) -> Optional[StopState]:
        """Get current stop state for a trade."""
        return self.states.get(trade_id)
