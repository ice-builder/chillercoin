from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Optional

import pandas as pd

from .realtime_matcher import evaluate_trade_signal


BYBIT_PUBLIC_WS_URLS = {
    "linear": "wss://stream.bybit.com/v5/public/linear",
}


@dataclass
class LiveRunSummary:
    symbol: str
    duration_seconds: int
    trades_collected: int
    seconds_built: int
    signals_found: int
    trades_path: str
    seconds_path: str
    signals_path: str

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "duration_seconds": self.duration_seconds,
            "trades_collected": self.trades_collected,
            "seconds_built": self.seconds_built,
            "signals_found": self.signals_found,
            "trades_path": self.trades_path,
            "seconds_path": self.seconds_path,
            "signals_path": self.signals_path,
        }


def stream_bybit_public_trades(
    symbol: str,
    duration_seconds: int,
    root: Path,
    category: str = "linear",
    matcher_root: Optional[Path] = None,
    trades_output_path: Optional[Path] = None,
    seconds_output_path: Optional[Path] = None,
    signals_output_path: Optional[Path] = None,
) -> LiveRunSummary:
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("Missing dependency `websocket-client`. Install project dependencies again.") from exc

    url = BYBIT_PUBLIC_WS_URLS.get(category)
    if url is None:
        raise ValueError(f"Unsupported Bybit live category: {category}")

    started_at = datetime.now(timezone.utc)
    stamp = started_at.strftime("%Y%m%d_%H%M%S")
    safe_symbol = symbol.lower()
    trades_output_path = trades_output_path or root / "data" / f"bybit_{safe_symbol}_trades_live_{stamp}.csv"
    seconds_output_path = seconds_output_path or root / "data" / f"bybit_{safe_symbol}_1s_live_{stamp}.csv"
    signals_output_path = signals_output_path or root / ".local_ai" / "live_signals" / f"bybit_{safe_symbol}_live_signals_{stamp}.json"

    trades_output_path.parent.mkdir(parents=True, exist_ok=True)
    seconds_output_path.parent.mkdir(parents=True, exist_ok=True)
    signals_output_path.parent.mkdir(parents=True, exist_ok=True)

    templates = load_matcher_templates(matcher_root or root, symbol=symbol)
    trade_rows: list[dict] = []
    second_rows: list[dict] = []
    signal_rows: list[dict] = []
    pending_buckets: dict[pd.Timestamp, list[dict]] = {}
    emitted_keys: set[tuple[str, str]] = set()

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
                maybe_emit_live_signals(
                    second_rows=second_rows,
                    templates=templates,
                    signal_rows=signal_rows,
                    emitted_keys=emitted_keys,
                    current_timestamp=bucket,
                )
    finally:
        try:
            ws.close()
        except Exception:
            pass

    for bucket in sorted(pending_buckets):
        second_bar = aggregate_trade_bucket(bucket, pending_buckets[bucket])
        second_rows.append(second_bar)
        maybe_emit_live_signals(
            second_rows=second_rows,
            templates=templates,
            signal_rows=signal_rows,
            emitted_keys=emitted_keys,
            current_timestamp=bucket,
        )

    trades_frame = pd.DataFrame(trade_rows)
    seconds_frame = pd.DataFrame(second_rows)
    if not trades_frame.empty:
        trades_frame = trades_frame.sort_values("timestamp").reset_index(drop=True)
        trades_frame.to_csv(trades_output_path, index=False)
    else:
        pd.DataFrame(columns=["timestamp", "symbol", "price", "size", "side", "trade_id", "seq"]).to_csv(
            trades_output_path,
            index=False,
        )
    if not seconds_frame.empty:
        seconds_frame = seconds_frame.sort_values("timestamp").reset_index(drop=True)
        seconds_frame.to_csv(seconds_output_path, index=False)
    else:
        pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "trade_count",
                "taker_buy_volume",
                "taker_sell_volume",
            ]
        ).to_csv(seconds_output_path, index=False)

    signals_payload = {
        "symbol": symbol.upper(),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration_seconds,
        "templates_loaded": len(templates),
        "signals": signal_rows,
    }
    signals_output_path.write_text(json.dumps(signals_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return LiveRunSummary(
        symbol=symbol.upper(),
        duration_seconds=duration_seconds,
        trades_collected=len(trade_rows),
        seconds_built=len(second_rows),
        signals_found=len(signal_rows),
        trades_path=str(trades_output_path),
        seconds_path=str(seconds_output_path),
        signals_path=str(signals_output_path),
    )


def parse_public_trade_payload(payload: dict, symbol: str) -> list[dict]:
    topic = payload.get("topic", "")
    if topic and topic != f"publicTrade.{symbol.upper()}":
        return []
    rows = []
    for item in payload.get("data", []) or []:
        timestamp_ms = item.get("T")
        price = item.get("p")
        size = item.get("v")
        if timestamp_ms is None or price is None or size is None:
            continue
        rows.append(
            {
                "timestamp": pd.to_datetime(int(timestamp_ms), unit="ms", utc=True),
                "symbol": str(item.get("s", symbol.upper())),
                "price": float(price),
                "size": float(size),
                "side": str(item.get("S", "")),
                "trade_id": str(item.get("i", "")),
                "seq": int(item.get("seq", 0) or 0),
            }
        )
    return rows


def aggregate_trade_bucket(bucket_ts: pd.Timestamp, trades: list[dict]) -> dict:
    sorted_trades = sorted(trades, key=lambda item: item["timestamp"])
    prices = [float(item["price"]) for item in sorted_trades]
    sizes = [float(item["size"]) for item in sorted_trades]
    return {
        "timestamp": bucket_ts,
        "open": prices[0],
        "high": max(prices),
        "low": min(prices),
        "close": prices[-1],
        "volume": float(sum(sizes)),
        "trade_count": int(len(sorted_trades)),
        "taker_buy_volume": float(sum(item["size"] for item in sorted_trades if item["side"] == "Buy")),
        "taker_sell_volume": float(sum(item["size"] for item in sorted_trades if item["side"] == "Sell")),
    }


def load_matcher_templates(root: Path, symbol: str) -> list[dict]:
    target_dir = root / ".local_ai" / "realtime_matchers"
    if not target_dir.exists():
        return []
    templates = []
    for path in sorted(target_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("template_kind") != "formation_volume_1s":
            continue
        if payload.get("symbol", "").upper() != symbol.upper():
            continue
        payload["template_path"] = str(path)
        templates.append(payload)
    return templates


def maybe_emit_live_signals(
    second_rows: list[dict],
    templates: list[dict],
    signal_rows: list[dict],
    emitted_keys: set[tuple[str, str]],
    current_timestamp: pd.Timestamp,
) -> None:
    if not templates or not second_rows:
        return
    frame = pd.DataFrame(second_rows).sort_values("timestamp").reset_index(drop=True)
    for template in templates:
        window_seconds = int(template.get("template_seconds", 0))
        if window_seconds < 20 or len(frame) < window_seconds:
            continue
        candidate = frame.tail(window_seconds)
        signal = evaluate_trade_signal(
            template,
            close_values=candidate["close"].tolist(),
            volume_values=candidate["volume"].tolist(),
        )
        if not signal.get("is_signal"):
            continue
        dedup_key = (str(template.get("template_path", "")), str(candidate["timestamp"].iloc[-1]))
        if dedup_key in emitted_keys:
            continue
        emitted_keys.add(dedup_key)
        signal_rows.append(
            {
                "signal_ts": str(current_timestamp),
                "window_start_ts": str(candidate["timestamp"].iloc[0]),
                "window_end_ts": str(candidate["timestamp"].iloc[-1]),
                "action": signal["action"],
                "symbol": template.get("symbol", ""),
                "trigger_side": template.get("trigger_side", ""),
                "score": signal["score"],
                "close_score": signal["close_score"],
                "volume_score": signal["volume_score"],
                "burst_volume_ratio": signal["burst_volume_ratio"],
                "dominant_phase": signal["dominant_phase"],
                "preferred_phase": signal["preferred_phase"],
                "stop_loss_pct": signal.get("stop_loss_pct", 0.0),
                "take_profit_pct": signal.get("take_profit_pct", 0.0),
                "cancel_if_no_follow_seconds": signal.get("cancel_if_no_follow_seconds", 0),
                "cancel_if_no_follow_move_pct": signal.get("cancel_if_no_follow_move_pct", 0.0),
                "invalidated_by_path": signal.get("invalidated_by_path", False),
                "template_path": template.get("template_path", ""),
                "selection_start_ts": template.get("selection_start_ts", ""),
                "selection_end_ts": template.get("selection_end_ts", ""),
            }
        )


def discover_live_signal_runs(root: Path, symbol: str = "") -> list[Path]:
    signal_dir = root / ".local_ai" / "live_signals"
    if not signal_dir.exists():
        return []
    symbol_key = symbol.lower().strip()
    paths = sorted(signal_dir.glob("*.json"), reverse=True)
    if not symbol_key:
        return paths
    return [path for path in paths if symbol_key in path.stem.lower()]


def read_live_signal_run(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
