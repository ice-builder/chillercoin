"""
🔬 Trade Analyzer — HQ Debrief Tool.

Reads paper_state_multi.json and produces a detailed breakdown
of every trade the Soldier has made. Can run locally or on VPS.

Usage:
    python trade_analyzer.py                    # Print analysis to terminal
    python trade_analyzer.py --telegram         # Also send report to Telegram
    python trade_analyzer.py --json             # Export full analysis as JSON
    python trade_analyzer.py --csv              # Export trades as CSV

Reads state from:  .local_ai/paper_trading/paper_state_multi.json
"""
import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

STATE_DIR = Path(__file__).parent / ".local_ai" / "paper_trading"
STATE_FILE = STATE_DIR / "paper_state_multi.json"


# ─── Color helpers for terminal ──────────────────────────────
class C:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def cprint(text: str, color: str = ""):
    print(f"{color}{text}{C.RESET}")


# ─── Data Loading ────────────────────────────────────────────

def load_state(path: Optional[Path] = None) -> dict:
    p = path or STATE_FILE
    if not p.exists():
        print(f"❌ State file not found: {p}")
        sys.exit(1)
    return json.loads(p.read_text(encoding="utf-8"))


# ─── Analysis Functions ─────────────────────────────────────

def analyze_trades(trades: List[Dict]) -> Dict:
    """Full trade analysis — returns structured report dict."""
    if not trades:
        return {"error": "No trades to analyze"}

    total = len(trades)
    wins = [t for t in trades if t.get("realized_pnl_pct", 0) > 0]
    losses = [t for t in trades if t.get("realized_pnl_pct", 0) <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    wr = win_count / max(1, total) * 100
    total_pnl = sum(t.get("realized_pnl_pct", 0) for t in trades)
    avg_pnl = total_pnl / max(1, total)

    # Win/Loss averages
    avg_win = sum(t.get("realized_pnl_pct", 0) for t in wins) / max(1, win_count) if wins else 0
    avg_loss = sum(t.get("realized_pnl_pct", 0) for t in losses) / max(1, loss_count) if losses else 0

    # Profit Factor
    gross_profit = sum(t.get("realized_pnl_pct", 0) for t in wins) if wins else 0
    gross_loss = abs(sum(t.get("realized_pnl_pct", 0) for t in losses)) if losses else 0.001
    profit_factor = gross_profit / max(0.001, gross_loss)

    # Max Drawdown (running PnL)
    running_pnl = 0
    peak_pnl = 0
    max_drawdown = 0
    equity_curve = []
    for t in trades:
        running_pnl += t.get("realized_pnl_pct", 0)
        equity_curve.append(running_pnl)
        peak_pnl = max(peak_pnl, running_pnl)
        drawdown = peak_pnl - running_pnl
        max_drawdown = max(max_drawdown, drawdown)

    # Streaks
    max_win_streak = 0
    max_loss_streak = 0
    current_streak = 0
    streak_type = None
    for t in trades:
        is_win = t.get("realized_pnl_pct", 0) > 0
        if is_win:
            if streak_type == "win":
                current_streak += 1
            else:
                current_streak = 1
                streak_type = "win"
            max_win_streak = max(max_win_streak, current_streak)
        else:
            if streak_type == "loss":
                current_streak += 1
            else:
                current_streak = 1
                streak_type = "loss"
            max_loss_streak = max(max_loss_streak, current_streak)

    # By Exit Reason
    by_exit_reason = {}
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        if reason not in by_exit_reason:
            by_exit_reason[reason] = {"count": 0, "pnl": 0, "wins": 0}
        by_exit_reason[reason]["count"] += 1
        by_exit_reason[reason]["pnl"] += t.get("realized_pnl_pct", 0)
        if t.get("realized_pnl_pct", 0) > 0:
            by_exit_reason[reason]["wins"] += 1

    # By Strategy
    by_strategy = {}
    for t in trades:
        strat = t.get("strategy_name", "default_zscore")
        if strat not in by_strategy:
            by_strategy[strat] = {"count": 0, "pnl": 0, "wins": 0, "trades": []}
        by_strategy[strat]["count"] += 1
        by_strategy[strat]["pnl"] += t.get("realized_pnl_pct", 0)
        if t.get("realized_pnl_pct", 0) > 0:
            by_strategy[strat]["wins"] += 1
        by_strategy[strat]["trades"].append(t)

    # By Symbol
    by_symbol = {}
    for t in trades:
        sym = t.get("symbol", "?")
        if sym not in by_symbol:
            by_symbol[sym] = {"count": 0, "pnl": 0, "wins": 0}
        by_symbol[sym]["count"] += 1
        by_symbol[sym]["pnl"] += t.get("realized_pnl_pct", 0)
        if t.get("realized_pnl_pct", 0) > 0:
            by_symbol[sym]["wins"] += 1

    # By Direction
    longs = [t for t in trades if t.get("direction") == "long"]
    shorts = [t for t in trades if t.get("direction") == "short"]

    # Average bars held
    avg_bars = sum(t.get("bars_held", 0) for t in trades) / max(1, total)
    avg_bars_win = sum(t.get("bars_held", 0) for t in wins) / max(1, win_count) if wins else 0
    avg_bars_loss = sum(t.get("bars_held", 0) for t in losses) / max(1, loss_count) if losses else 0

    # Best and worst trades
    sorted_by_pnl = sorted(trades, key=lambda t: t.get("realized_pnl_pct", 0))
    worst_trade = sorted_by_pnl[0] if sorted_by_pnl else None
    best_trade = sorted_by_pnl[-1] if sorted_by_pnl else None

    return {
        "summary": {
            "total_trades": total,
            "wins": win_count,
            "losses": loss_count,
            "win_rate": wr,
            "total_pnl_pct": total_pnl,
            "avg_pnl_per_trade": avg_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "avg_bars_held": avg_bars,
            "avg_bars_win": avg_bars_win,
            "avg_bars_loss": avg_bars_loss,
        },
        "by_direction": {
            "long": {"count": len(longs), "pnl": sum(t.get("realized_pnl_pct", 0) for t in longs)},
            "short": {"count": len(shorts), "pnl": sum(t.get("realized_pnl_pct", 0) for t in shorts)},
        },
        "by_exit_reason": by_exit_reason,
        "by_strategy": {k: {kk: vv for kk, vv in v.items() if kk != "trades"} for k, v in by_strategy.items()},
        "by_symbol": by_symbol,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "equity_curve": equity_curve,
        "trades": trades,
    }


# ─── Terminal Report ─────────────────────────────────────────

def print_report(analysis: Dict):
    """Print beautiful terminal report."""
    s = analysis["summary"]

    cprint("\n" + "═" * 60, C.CYAN)
    cprint("  🔬 TRADE ANALYSIS — HQ DEBRIEF", C.BOLD + C.CYAN)
    cprint("═" * 60, C.CYAN)

    # Verdict
    pnl = s["total_pnl_pct"]
    if pnl > 0:
        cprint(f"\n  ✅ VERDICT: Солдат в плюсе (+{pnl:.3f}%)", C.GREEN + C.BOLD)
    elif pnl < -1:
        cprint(f"\n  🚨 VERDICT: СОЛДАТ СЛИВАЕТ ({pnl:+.3f}%)", C.RED + C.BOLD)
    else:
        cprint(f"\n  ⚠️  VERDICT: Около нуля ({pnl:+.3f}%)", C.YELLOW + C.BOLD)

    # Key Metrics
    cprint(f"\n{'─' * 40}", C.DIM)
    cprint("  📊 KEY METRICS", C.BOLD)
    cprint(f"{'─' * 40}", C.DIM)

    pnl_color = C.GREEN if pnl >= 0 else C.RED
    print(f"  Total PnL:       {pnl_color}{pnl:+.3f}%{C.RESET}")
    print(f"  Win Rate:        {s['win_rate']:.1f}% ({s['wins']}W / {s['losses']}L)")
    print(f"  Avg Win:         {C.GREEN}{s['avg_win']:+.3f}%{C.RESET}")
    print(f"  Avg Loss:        {C.RED}{s['avg_loss']:+.3f}%{C.RESET}")
    print(f"  Profit Factor:   {s['profit_factor']:.2f}")
    print(f"  Max Drawdown:    {C.RED}{s['max_drawdown']:.3f}%{C.RESET}")
    print(f"  Win Streak:      {s['max_win_streak']} | Loss Streak: {s['max_loss_streak']}")
    print(f"  Avg Bars Held:   {s['avg_bars_held']:.1f} (Win: {s['avg_bars_win']:.1f} / Loss: {s['avg_bars_loss']:.1f})")

    # By Direction
    d = analysis["by_direction"]
    cprint(f"\n{'─' * 40}", C.DIM)
    cprint("  📐 BY DIRECTION", C.BOLD)
    cprint(f"{'─' * 40}", C.DIM)
    for dir_name, info in d.items():
        icon = "🟢" if dir_name == "long" else "🔴"
        color = C.GREEN if info["pnl"] >= 0 else C.RED
        print(f"  {icon} {dir_name.upper():6s}  {info['count']:3d} trades  {color}{info['pnl']:+.3f}%{C.RESET}")

    # By Exit Reason
    cprint(f"\n{'─' * 40}", C.DIM)
    cprint("  🚪 BY EXIT REASON", C.BOLD)
    cprint(f"{'─' * 40}", C.DIM)
    for reason, info in sorted(analysis["by_exit_reason"].items(), key=lambda x: x[1]["pnl"]):
        wr = info["wins"] / max(1, info["count"]) * 100
        color = C.GREEN if info["pnl"] >= 0 else C.RED
        print(f"  {reason:20s}  {info['count']:3d} trades  {color}{info['pnl']:+.3f}%{C.RESET}  WR: {wr:.0f}%")

    # By Strategy
    cprint(f"\n{'─' * 40}", C.DIM)
    cprint("  🧠 BY STRATEGY", C.BOLD)
    cprint(f"{'─' * 40}", C.DIM)
    for strat, info in sorted(analysis["by_strategy"].items(), key=lambda x: x[1]["pnl"]):
        wr = info["wins"] / max(1, info["count"]) * 100
        color = C.GREEN if info["pnl"] >= 0 else C.RED
        icon = "🟢" if info["pnl"] >= 0 else "🔴"
        print(f"  {icon} {strat:30s}  {info['count']:3d} trades  {color}{info['pnl']:+.3f}%{C.RESET}  WR: {wr:.0f}%")

    # By Symbol
    cprint(f"\n{'─' * 40}", C.DIM)
    cprint("  🪙 BY SYMBOL", C.BOLD)
    cprint(f"{'─' * 40}", C.DIM)
    for sym, info in sorted(analysis["by_symbol"].items(), key=lambda x: x[1]["pnl"]):
        color = C.GREEN if info["pnl"] >= 0 else C.RED
        icon = "📈" if info["pnl"] >= 0 else "📉"
        print(f"  {icon} {sym:12s}  {info['count']:3d} trades  {color}{info['pnl']:+.3f}%{C.RESET}")

    # Best / Worst Trade
    cprint(f"\n{'─' * 40}", C.DIM)
    cprint("  🏆 BEST / WORST TRADE", C.BOLD)
    cprint(f"{'─' * 40}", C.DIM)
    best = analysis.get("best_trade")
    worst = analysis.get("worst_trade")
    if best:
        d_icon = "🟢" if best.get("direction") == "long" else "🔴"
        print(f"  ✅ BEST:  {d_icon} {best.get('symbol','?')} {C.GREEN}{best.get('realized_pnl_pct',0):+.3f}%{C.RESET} ({best.get('exit_reason','?')}) [{best.get('strategy_name','?')}]")
    if worst:
        d_icon = "🟢" if worst.get("direction") == "long" else "🔴"
        print(f"  ❌ WORST: {d_icon} {worst.get('symbol','?')} {C.RED}{worst.get('realized_pnl_pct',0):+.3f}%{C.RESET} ({worst.get('exit_reason','?')}) [{worst.get('strategy_name','?')}]")

    # Per-Trade Breakdown
    trades = analysis.get("trades", [])
    cprint(f"\n{'─' * 40}", C.DIM)
    cprint(f"  📋 ALL TRADES ({len(trades)} total)", C.BOLD)
    cprint(f"{'─' * 40}", C.DIM)
    print(f"  {'#':>3s}  {'Symbol':12s} {'Dir':6s} {'PnL':>9s}  {'Reason':15s} {'Bars':>4s}  {'Strategy':25s}")
    print(f"  {'─'*3}  {'─'*12} {'─'*6} {'─'*9}  {'─'*15} {'─'*4}  {'─'*25}")
    for i, t in enumerate(trades, 1):
        pnl_val = t.get("realized_pnl_pct", 0)
        color = C.GREEN if pnl_val > 0 else C.RED
        d = "LONG" if t.get("direction") == "long" else "SHORT"
        icon = "✅" if pnl_val > 0 else "❌"
        sym = t.get("symbol", "?")[:12]
        reason = t.get("exit_reason", "?")[:15]
        bars = t.get("bars_held", 0)
        strat = t.get("strategy_name", "?")[:25]
        print(f"  {icon}{i:2d}  {sym:12s} {d:6s} {color}{pnl_val:+8.3f}%{C.RESET}  {reason:15s} {bars:4d}  {strat}")

    # Equity Curve (ASCII sparkline)
    curve = analysis.get("equity_curve", [])
    if curve:
        cprint(f"\n{'─' * 40}", C.DIM)
        cprint("  📈 EQUITY CURVE", C.BOLD)
        cprint(f"{'─' * 40}", C.DIM)
        _print_ascii_chart(curve)

    cprint(f"\n{'═' * 60}\n", C.CYAN)


def _print_ascii_chart(values: list, width: int = 50, height: int = 10):
    """Simple ASCII chart for equity curve."""
    if not values:
        return
    mn = min(values)
    mx = max(values)
    rng = mx - mn if mx != mn else 1

    # Downsample if too many points
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values

    for row in range(height, -1, -1):
        threshold = mn + (rng * row / height)
        line = "  "
        if row == height:
            line += f" {mx:+.2f}% │"
        elif row == 0:
            line += f" {mn:+.2f}% │"
        else:
            line += f"         │"
        for v in sampled:
            if v >= threshold:
                line += "█" if v >= 0 else "▓"
            else:
                line += " "
        print(line)
    print(f"           └{'─' * len(sampled)}")


# ─── Telegram Report ─────────────────────────────────────────

def send_telegram_report(analysis: Dict):
    """Send compact analysis report to Telegram."""
    try:
        import requests
    except ImportError:
        print("❌ requests library needed for Telegram. pip install requests")
        return

    token = os.getenv("TELEGRAM_SCALPER_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("❌ TELEGRAM_SCALPER_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        return

    s = analysis["summary"]
    pnl = s["total_pnl_pct"]
    verdict = "✅ В плюсе" if pnl > 0 else "🚨 СЛИВАЕТ" if pnl < -1 else "⚠️ Около нуля"

    # Strategy breakdown
    strat_lines = ""
    for strat, info in sorted(analysis["by_strategy"].items(), key=lambda x: x[1]["pnl"]):
        wr = info["wins"] / max(1, info["count"]) * 100
        emoji = "🟢" if info["pnl"] >= 0 else "🔴"
        strat_lines += f"  {emoji} `{strat}`: {info['count']} | `{info['pnl']:+.3f}%` | WR {wr:.0f}%\n"

    # Exit reason breakdown
    reason_lines = ""
    for reason, info in sorted(analysis["by_exit_reason"].items(), key=lambda x: x[1]["pnl"]):
        wr = info["wins"] / max(1, info["count"]) * 100
        emoji = "✅" if info["pnl"] >= 0 else "❌"
        reason_lines += f"  {emoji} `{reason}`: {info['count']} | `{info['pnl']:+.3f}%`\n"

    # Symbol breakdown
    sym_lines = ""
    for sym, info in sorted(analysis["by_symbol"].items(), key=lambda x: x[1]["pnl"]):
        emoji = "📈" if info["pnl"] >= 0 else "📉"
        sym_lines += f"  {emoji} {sym}: {info['count']} | `{info['pnl']:+.3f}%`\n"

    msg = (
        f"🔬 *TRADE ANALYSIS — HQ DEBRIEF*\n"
        f"{'━' * 28}\n\n"
        f"*{verdict}* ({pnl:+.3f}%)\n\n"
        f"📊 WR: `{s['win_rate']:.1f}%` ({s['wins']}W/{s['losses']}L)\n"
        f"💰 Avg: Win `{s['avg_win']:+.3f}%` / Loss `{s['avg_loss']:+.3f}%`\n"
        f"💎 PF: `{s['profit_factor']:.2f}` | DD: `{s['max_drawdown']:.3f}%`\n"
        f"🔥 Streaks: W{s['max_win_streak']} / L{s['max_loss_streak']}\n\n"
        f"*By Strategy:*\n{strat_lines}\n"
        f"*By Exit:*\n{reason_lines}\n"
        f"*By Symbol:*\n{sym_lines}"
    )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id, "text": msg, "parse_mode": "Markdown"
        }, timeout=15)
        if resp.status_code == 200:
            print("✅ Report sent to Telegram")
        else:
            print(f"❌ Telegram error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")


# ─── CSV Export ──────────────────────────────────────────────

def export_csv(trades: List[Dict], path: Optional[Path] = None):
    """Export trades as CSV for spreadsheet analysis."""
    if not trades:
        print("No trades to export.")
        return

    out = path or (STATE_DIR / f"trades_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    cols = ["symbol", "direction", "strategy_name", "strategy_id",
            "entry_price", "exit_price", "realized_pnl_pct",
            "stop_pct", "tp_pct", "exit_reason", "bars_held",
            "entry_time", "exit_time"]

    lines = [",".join(cols)]
    for t in trades:
        row = [str(t.get(c, "")) for c in cols]
        lines.append(",".join(row))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ Exported {len(trades)} trades to {out}")
    return out


# ─── CLI ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="🔬 Trade Analyzer — HQ Debrief")
    parser.add_argument("--state", type=str, default=None, help="Path to paper_state JSON file")
    parser.add_argument("--telegram", action="store_true", help="Send report to Telegram")
    parser.add_argument("--json", action="store_true", help="Export analysis as JSON")
    parser.add_argument("--csv", action="store_true", help="Export trades as CSV")
    args = parser.parse_args()

    state_path = Path(args.state) if args.state else STATE_FILE
    state = load_state(state_path)
    trades = state.get("completed_trades", [])

    if not trades:
        cprint("❌ No completed trades found in state file.", C.RED)
        sys.exit(1)

    analysis = analyze_trades(trades)

    # Terminal report (always)
    print_report(analysis)

    # Optional: Telegram
    if args.telegram:
        send_telegram_report(analysis)

    # Optional: JSON export
    if args.json:
        out = STATE_DIR / f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        # Remove raw trades from JSON output (too large), keep summary
        export_data = {k: v for k, v in analysis.items() if k != "trades"}
        export_data["trade_count"] = len(trades)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(export_data, indent=2, default=str), encoding="utf-8")
        cprint(f"✅ Analysis exported to {out}", C.GREEN)

    # Optional: CSV export
    if args.csv:
        export_csv(trades)
