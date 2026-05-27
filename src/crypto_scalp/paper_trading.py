from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Optional

import numpy as np
import pandas as pd

from .bybit_live import BYBIT_PUBLIC_WS_URLS, aggregate_trade_bucket, parse_public_trade_payload


@dataclass
class PaperRunSummary:
    symbol: str
    duration_seconds: int
    queue_items_loaded: int
    seconds_built: int
    signals_found: int
    trades_opened: int
    trades_closed: int
    run_path: str

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "duration_seconds": self.duration_seconds,
            "queue_items_loaded": self.queue_items_loaded,
            "seconds_built": self.seconds_built,
            "signals_found": self.signals_found,
            "trades_opened": self.trades_opened,
            "trades_closed": self.trades_closed,
            "run_path": self.run_path,
        }


def run_paper_trading(
    root: Path,
    symbol: str,
    duration_seconds: int,
    deposit_usdt: float = 1_000.0,
    category: str = "linear",
    queue_dir: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> PaperRunSummary:
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("Missing dependency `websocket-client`. Install project dependencies again.") from exc

    url = BYBIT_PUBLIC_WS_URLS.get(category)
    if url is None:
        raise ValueError(f"Unsupported Bybit live category: {category}")

    queued = load_paper_queue(queue_dir or root / ".local_ai" / "paper_queue", symbol=symbol)
    started_at = datetime.now(timezone.utc)
    stamp = started_at.strftime("%Y%m%d_%H%M%S")
    output_path = output_path or root / ".local_ai" / "paper_runs" / f"paper_{symbol.lower()}_{stamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    trade_rows: list[dict] = []
    second_rows: list[dict] = []
    signal_rows: list[dict] = []
    paper_trades: list[dict] = []
    open_positions: list[dict] = []
    pending_buckets: dict[pd.Timestamp, list[dict]] = {}

    ws = websocket.create_connection(url, timeout=5)
    ws.settimeout(1.0)
    ws.send(json.dumps({"op": "subscribe", "args": [f"publicTrade.{symbol.upper()}"]}))
    started_monotonic = time.monotonic()
    try:
        while time.monotonic() - started_monotonic < duration_seconds:
            try:
                raw_message = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw_message:
                continue
            payload = json.loads(raw_message)
            parsed = parse_public_trade_payload(payload, symbol=symbol)
            if not parsed:
                continue
            for trade in parsed:
                trade_rows.append(trade)
                bucket_ts = trade["timestamp"].floor("s")
                pending_buckets.setdefault(bucket_ts, []).append(trade)

            max_bucket = max(pending_buckets.keys()) if pending_buckets else None
            if max_bucket is None:
                continue
            completed = sorted(bucket for bucket in pending_buckets if bucket < max_bucket)
            for bucket in completed:
                second_bar = aggregate_trade_bucket(bucket, pending_buckets.pop(bucket))
                second_rows.append(second_bar)
                process_paper_second(
                    queued=queued,
                    second_rows=second_rows,
                    signal_rows=signal_rows,
                    paper_trades=paper_trades,
                    open_positions=open_positions,
                    deposit_usdt=deposit_usdt,
                )
    finally:
        try:
            ws.close()
        except Exception:
            pass

    for bucket in sorted(pending_buckets):
        second_bar = aggregate_trade_bucket(bucket, pending_buckets[bucket])
        second_rows.append(second_bar)
        process_paper_second(
            queued=queued,
            second_rows=second_rows,
            signal_rows=signal_rows,
            paper_trades=paper_trades,
            open_positions=open_positions,
            deposit_usdt=deposit_usdt,
        )

    finished_at = datetime.now(timezone.utc)
    payload = {
        "symbol": symbol.upper(),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": duration_seconds,
        "deposit_usdt": deposit_usdt,
        "queue_items_loaded": len(queued),
        "seconds_built": len(second_rows),
        "signals": signal_rows,
        "paper_trades": paper_trades,
        "open_positions": open_positions,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return PaperRunSummary(
        symbol=symbol.upper(),
        duration_seconds=duration_seconds,
        queue_items_loaded=len(queued),
        seconds_built=len(second_rows),
        signals_found=len(signal_rows),
        trades_opened=len([item for item in paper_trades if item.get("event") == "open"]),
        trades_closed=len([item for item in paper_trades if item.get("event") == "close"]),
        run_path=str(output_path),
    )


def load_paper_queue(queue_dir: Path, symbol: str = "") -> list[dict]:
    if not queue_dir.exists():
        return []
    symbol_key = symbol.upper().strip()
    items = []
    for path in sorted(queue_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if symbol_key and str(payload.get("symbol", "")).upper() != symbol_key:
            continue
        payload["_path"] = str(path)
        items.append(payload)
    return items


def discover_paper_runs(root: Path, symbol: str = "") -> list[Path]:
    target_dir = root / ".local_ai" / "paper_runs"
    if not target_dir.exists():
        return []
    symbol_key = symbol.lower().strip()
    paths = sorted(target_dir.glob("*.json"), reverse=True)
    if not symbol_key:
        return paths
    return [path for path in paths if symbol_key in path.stem.lower()]


def read_paper_run(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def process_paper_second(
    queued: list[dict],
    second_rows: list[dict],
    signal_rows: list[dict],
    paper_trades: list[dict],
    open_positions: list[dict],
    deposit_usdt: float,
) -> None:
    if not second_rows:
        return
    seconds_frame = pd.DataFrame(second_rows).sort_values("timestamp").reset_index(drop=True)
    current_bar = seconds_frame.iloc[-1]
    close_price = float(current_bar["close"])
    update_open_positions(
        current_bar=current_bar,
        close_price=close_price,
        open_positions=open_positions,
        paper_trades=paper_trades,
    )
    for item in queued:
        if any(position.get("hypothesis_id") == item.get("hypothesis_id") for position in open_positions):
            continue
        signal = detect_live_impulse_signal(seconds_frame, item, deposit_usdt=deposit_usdt)
        if not signal:
            continue
        signal_rows.append(signal)
        open_positions.append(open_paper_position(item, signal, close_price, deposit_usdt))
        paper_trades.append(
            {
                "event": "open",
                "hypothesis_id": item.get("hypothesis_id"),
                "symbol": item.get("symbol", ""),
                "timestamp": str(current_bar["timestamp"]),
                "side": signal["direction"],
                "entry_price": close_price,
                "position_notional_usdt": signal["position_notional_usdt"],
                "risk_usdt": signal["risk_usdt"],
                "stop_loss_pct": signal["stop_loss_pct"],
                "take_profit_pct": signal["take_profit_pct"],
                "reason": "live_impulse_match",
            }
        )


def detect_live_impulse_signal(seconds_frame: pd.DataFrame, queue_item: dict, deposit_usdt: float) -> Optional[dict]:
    params = queue_item.get("strategy_params", {}) or {}
    timeframe = str(queue_item.get("timeframe", "1m") or "1m")
    bar_seconds = timeframe_to_seconds(timeframe)
    lookback = int(params.get("lookback_bars", 80))
    max_sequence = int(params.get("max_sequence_bars", 8))
    min_sequence = int(params.get("min_sequence_bars", 2))
    live_bars = resample_seconds_to_bars(seconds_frame, bar_seconds)
    seed_bars = load_historical_seed_bars(queue_item, lookback + max_sequence + 20)
    bars = pd.concat([seed_bars, live_bars], ignore_index=True)
    if not bars.empty:
        bars = bars.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)
    required_bars = lookback + max_sequence + 2
    if len(bars) < required_bars:
        return None
    bars["dollar_volume"] = bars["close"] * bars["volume"]
    bars["ret_pct"] = bars["close"].pct_change().fillna(0.0) * 100
    bars["dollar_volume_z"] = rolling_zscore_without_lookahead(bars["dollar_volume"], lookback)
    bars["abs_ret_z"] = rolling_zscore_without_lookahead(bars["ret_pct"].abs(), lookback)
    bars["direction"] = np.sign(bars["ret_pct"]).astype(int)
    recent = bars.tail(max_sequence).reset_index(drop=True)
    min_volume_z = float(params.get("min_dollar_volume_z", 3.0))
    min_ret_z = float(params.get("min_price_return_z", 2.0))
    triggered = recent[
        (recent["dollar_volume_z"] >= min_volume_z)
        & (recent["abs_ret_z"] >= min_ret_z)
        & (recent["direction"] != 0)
    ]
    if len(triggered) < min_sequence:
        return None
    direction_values = triggered["direction"].astype(int).tolist()
    direction = 1 if sum(direction_values) > 0 else -1
    if abs(sum(direction_values)) < min_sequence:
        return None
    stop_pct = float(params.get("fixed_stop_loss_pct", 0.35))
    take_pct = stop_pct * float(params.get("take_profit_rr", 1.8))
    risk_pct = float(params.get("account_risk_pct", 0.10)) / 100.0
    risk_usdt = deposit_usdt * risk_pct
    position_notional = risk_usdt / (stop_pct / 100.0) if stop_pct else 0.0
    last_bar = bars.iloc[-1]
    return {
        "signal_ts": str(last_bar["timestamp"]),
        "hypothesis_id": queue_item.get("hypothesis_id"),
        "symbol": queue_item.get("symbol", ""),
        "timeframe": timeframe,
        "direction": "long" if direction > 0 else "short",
        "dollar_volume_z": float(triggered["dollar_volume_z"].max()),
        "abs_ret_z": float(triggered["abs_ret_z"].max()),
        "sequence_bars": int(len(triggered)),
        "stop_loss_pct": stop_pct,
        "take_profit_pct": take_pct,
        "risk_usdt": risk_usdt,
        "position_notional_usdt": position_notional,
    }


def open_paper_position(queue_item: dict, signal: dict, entry_price: float, deposit_usdt: float) -> dict:
    params = queue_item.get("strategy_params", {}) or {}
    risk_pct = float(params.get("account_risk_pct", 0.10)) / 100.0
    risk_usdt = deposit_usdt * risk_pct
    stop_pct = float(signal["stop_loss_pct"])
    return {
        "hypothesis_id": queue_item.get("hypothesis_id"),
        "symbol": queue_item.get("symbol", ""),
        "side": signal["direction"],
        "opened_at": signal["signal_ts"],
        "entry_price": entry_price,
        "stop_loss_pct": stop_pct,
        "take_profit_pct": float(signal["take_profit_pct"]),
        "cancel_if_no_follow_bars": int(params.get("cancel_if_no_follow_bars", 3)),
        "cancel_min_follow_pct": float(params.get("cancel_min_follow_pct", 0.12)),
        "max_hold_bars": int(params.get("max_hold_bars", 20)),
        "bars_seen": 0,
        "best_favorable_pct": 0.0,
        "risk_usdt": risk_usdt,
        "position_notional_usdt": risk_usdt / (stop_pct / 100.0) if stop_pct else 0.0,
    }


def update_open_positions(
    current_bar: pd.Series,
    close_price: float,
    open_positions: list[dict],
    paper_trades: list[dict],
) -> None:
    still_open = []
    for position in open_positions:
        position["bars_seen"] = int(position.get("bars_seen", 0)) + 1
        move_pct = calculate_directional_move(position["side"], position["entry_price"], close_price)
        position["best_favorable_pct"] = max(float(position.get("best_favorable_pct", 0.0)), move_pct)
        exit_reason = ""
        if move_pct <= -float(position["stop_loss_pct"]):
            exit_reason = "fixed_stop"
            realized_pct = -float(position["stop_loss_pct"])
        elif move_pct >= float(position["take_profit_pct"]):
            exit_reason = "take_profit"
            realized_pct = float(position["take_profit_pct"])
        elif (
            int(position["bars_seen"]) >= int(position["cancel_if_no_follow_bars"])
            and float(position["best_favorable_pct"]) < float(position["cancel_min_follow_pct"])
        ):
            exit_reason = "cancel_no_follow"
            realized_pct = move_pct
        elif int(position["bars_seen"]) >= int(position["max_hold_bars"]):
            exit_reason = "time_exit"
            realized_pct = move_pct
        else:
            still_open.append(position)
            continue

        paper_trades.append(
            {
                "event": "close",
                "hypothesis_id": position.get("hypothesis_id"),
                "symbol": position.get("symbol", ""),
                "timestamp": str(current_bar["timestamp"]),
                "side": position["side"],
                "entry_price": position["entry_price"],
                "exit_price": close_price,
                "exit_reason": exit_reason,
                "realized_move_pct": realized_pct,
                "pnl_usdt": position["position_notional_usdt"] * (realized_pct / 100.0),
            }
        )
    open_positions[:] = still_open


def calculate_directional_move(side: str, entry_price: float, close_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    if side == "short":
        return (1.0 - close_price / entry_price) * 100
    return (close_price / entry_price - 1.0) * 100


def timeframe_to_seconds(timeframe: str) -> int:
    normalized = timeframe.lower().strip()
    if normalized.endswith("m"):
        return max(60, int(normalized[:-1]) * 60)
    if normalized.endswith("h"):
        return int(normalized[:-1]) * 3600
    if normalized.endswith("d"):
        return int(normalized[:-1]) * 86400
    try:
        return max(60, int(normalized) * 60)
    except ValueError:
        return 60


def resample_seconds_to_bars(seconds_frame: pd.DataFrame, bar_seconds: int) -> pd.DataFrame:
    frame = seconds_frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if frame.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    epoch_seconds = frame["timestamp"].astype("int64") // 1_000_000_000
    bucket_seconds = (epoch_seconds // bar_seconds) * bar_seconds
    frame["bar_ts"] = pd.to_datetime(bucket_seconds, unit="s", utc=True)
    bars = (
        frame.groupby("bar_ts", as_index=False)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .rename(columns={"bar_ts": "timestamp"})
    )
    return bars


def load_historical_seed_bars(queue_item: dict, max_rows: int) -> pd.DataFrame:
    validation = queue_item.get("validation_result", {}) or {}
    data_path = Path(str(validation.get("data_path", "")))
    if not data_path.exists():
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    try:
        frame = pd.read_csv(data_path)
    except Exception:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"]).sort_values("timestamp")
    return frame[["timestamp", "open", "high", "low", "close", "volume"]].tail(max_rows).reset_index(drop=True)


def rolling_zscore_without_lookahead(series: pd.Series, lookback: int) -> pd.Series:
    baseline = series.shift(1)
    mean = baseline.rolling(lookback, min_periods=max(10, lookback // 4)).mean()
    std = baseline.rolling(lookback, min_periods=max(10, lookback // 4)).std().replace(0, np.nan)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan).fillna(0.0)
