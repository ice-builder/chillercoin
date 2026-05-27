"""
Scalper Pro — Adaptive Position Sizer

Scales position in/out during a trade based on:
  - Price movement direction and magnitude
  - Impulse development (vol_z persistence)
  - Hypothesis recommendations

Scale IN (x2): Price +0.3-0.5% in our favor + impulse continues
Scale OUT (x2): Price -0.2% against us + impulse weakening
EMERGENCY: Rapid move >1% against → close 100%

Position always bounded: [MIN_POSITION_MULT, MAX_POSITION_MULT] × initial.
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

import config

logger = logging.getLogger("scalper.sizer")


@dataclass
class ScaleEvent:
    """Record of a scaling action."""
    timestamp: float = 0.0
    event_type: str = ""    # "scale_in" / "scale_out" / "emergency_cut"
    trigger: str = ""       # What triggered the scale
    old_mult: float = 1.0
    new_mult: float = 1.0
    price_at_event: float = 0.0
    pnl_at_event: float = 0.0

    def to_dict(self) -> dict:
        return {
            "time": self.timestamp,
            "type": self.event_type,
            "trigger": self.trigger,
            "old_mult": self.old_mult,
            "new_mult": self.new_mult,
            "price": self.price_at_event,
            "pnl": self.pnl_at_event,
        }


@dataclass
class SizerState:
    """Position sizing state for one trade."""
    trade_id: int = 0
    direction: str = "long"
    entry_price: float = 0.0
    initial_size_usdt: float = 0.0

    current_mult: float = 1.0    # Current position multiplier
    events: List[ScaleEvent] = field(default_factory=list)

    # Cooldowns
    last_scale_time: float = 0.0
    scale_in_count: int = 0
    scale_out_count: int = 0

    # Hypothesis hints
    should_scale_in: bool = False
    scale_in_trigger_pct: float = 0.3
    should_cut_early: bool = False


class AdaptiveSizer:
    """
    Manages position scaling during open trades.
    """

    SCALE_COOLDOWN_SEC = 300  # 5 min between scale events
    MAX_SCALE_IN_COUNT = 2    # Max scale-in events per trade
    MAX_SCALE_OUT_COUNT = 2   # Max scale-out events per trade

    def __init__(self):
        self.states: dict[int, SizerState] = {}

    def init_trade(self, trade_id: int, direction: str,
                   entry_price: float, initial_size_usdt: float,
                   hypothesis_hints: dict = None) -> SizerState:
        """Initialize sizing state for a new trade."""
        state = SizerState(
            trade_id=trade_id,
            direction=direction,
            entry_price=entry_price,
            initial_size_usdt=initial_size_usdt,
        )

        if hypothesis_hints:
            state.should_scale_in = hypothesis_hints.get("should_scale_in", False)
            state.scale_in_trigger_pct = hypothesis_hints.get(
                "scale_in_trigger", config.SCALE_IN_TRIGGER_PCT
            )
            state.should_cut_early = hypothesis_hints.get("should_cut_early", False)

        self.states[trade_id] = state
        return state

    def evaluate(self, trade_id: int, current_price: float,
                 vol_z: float = 0) -> Tuple[SizerState, Optional[ScaleEvent]]:
        """
        Evaluate if position should be scaled.
        Returns (state, event) where event is None or a ScaleEvent to execute.
        """
        state = self.states.get(trade_id)
        if not state:
            return SizerState(), None

        now = time.time()

        # Cooldown check
        if now - state.last_scale_time < self.SCALE_COOLDOWN_SEC:
            return state, None

        # Calculate P&L
        if state.direction == "long":
            pnl_pct = (current_price / state.entry_price - 1) * 100
        else:
            pnl_pct = (1 - current_price / state.entry_price) * 100

        # ── EMERGENCY CUT: rapid move against us ──────────────────────────
        emergency_threshold = -1.0  # -1% rapid adverse
        if pnl_pct <= emergency_threshold and state.current_mult > 0.5:
            event = self._create_event(
                state, "emergency_cut", "rapid_adverse",
                target_mult=config.MIN_POSITION_MULT,
                price=current_price, pnl=pnl_pct,
            )
            return state, event

        # ── SCALE OUT: unfavorable movement ───────────────────────────────
        scale_out_trigger = -abs(config.SCALE_OUT_TRIGGER_PCT)
        if (pnl_pct <= scale_out_trigger
                and state.current_mult > config.MIN_POSITION_MULT
                and state.scale_out_count < self.MAX_SCALE_OUT_COUNT):

            # Additional check: is impulse really weakening?
            impulse_weak = vol_z < 2.0  # vol_z dropping below significance

            if impulse_weak or pnl_pct <= scale_out_trigger * 2:
                new_mult = max(
                    state.current_mult / config.SCALE_OUT_DIVISOR,
                    config.MIN_POSITION_MULT,
                )
                event = self._create_event(
                    state, "scale_out",
                    f"adverse_{pnl_pct:.1f}%_vol_z={vol_z:.1f}",
                    target_mult=new_mult,
                    price=current_price, pnl=pnl_pct,
                )
                return state, event

        # ── SCALE IN: favorable movement ──────────────────────────────────
        trigger_pct = state.scale_in_trigger_pct
        if (state.should_scale_in
                and pnl_pct >= trigger_pct
                and state.current_mult < config.MAX_POSITION_MULT
                and state.scale_in_count < self.MAX_SCALE_IN_COUNT):

            # Additional check: impulse still developing
            impulse_strong = vol_z >= 2.5

            if impulse_strong:
                new_mult = min(
                    state.current_mult * config.SCALE_IN_MULTIPLIER,
                    config.MAX_POSITION_MULT,
                )
                event = self._create_event(
                    state, "scale_in",
                    f"favorable_{pnl_pct:.1f}%_vol_z={vol_z:.1f}",
                    target_mult=new_mult,
                    price=current_price, pnl=pnl_pct,
                )
                return state, event

        return state, None

    def _create_event(self, state: SizerState, event_type: str,
                      trigger: str, target_mult: float,
                      price: float, pnl: float) -> ScaleEvent:
        """Create a scale event and update state."""
        event = ScaleEvent(
            timestamp=time.time(),
            event_type=event_type,
            trigger=trigger,
            old_mult=state.current_mult,
            new_mult=target_mult,
            price_at_event=price,
            pnl_at_event=pnl,
        )

        state.current_mult = target_mult
        state.last_scale_time = time.time()
        state.events.append(event)

        if event_type == "scale_in":
            state.scale_in_count += 1
        elif event_type in ("scale_out", "emergency_cut"):
            state.scale_out_count += 1

        icon = "🔼" if event_type == "scale_in" else "🔽"
        logger.info(
            f"{icon} [{state.trade_id}] {event_type}: "
            f"mult {event.old_mult:.1f}→{event.new_mult:.1f} "
            f"({trigger}) PnL={pnl:+.2f}%"
        )
        return event

    def get_effective_size(self, trade_id: int) -> float:
        """Get current effective position size in USDT."""
        state = self.states.get(trade_id)
        if not state:
            return 0
        return state.initial_size_usdt * state.current_mult

    def get_events_json(self, trade_id: int) -> str:
        """Get scaling events as JSON string for DB storage."""
        import json
        state = self.states.get(trade_id)
        if not state:
            return "[]"
        return json.dumps([e.to_dict() for e in state.events])

    def remove_trade(self, trade_id: int):
        """Remove trade from tracking."""
        self.states.pop(trade_id, None)
