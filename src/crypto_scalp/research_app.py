from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
import requests
from pathlib import Path
import re
import sys
from typing import Callable, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st
import streamlit.components.v1 as components
from plotly.subplots import make_subplots

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from crypto_scalp.bybit import (
        SUPPORTED_INTERVALS,
        DownloadProgress,
        INTERVAL_TO_DELTA,
        default_output_path,
        download_bybit_klines,
        find_reusable_bybit_csv,
        save_bybit_klines_csv,
    )
    from crypto_scalp.bybit_live import discover_live_signal_runs, load_matcher_templates, read_live_signal_run
    from crypto_scalp.config import RunConfig
    from crypto_scalp.hypothesis_vault import (
        DEFAULT_CONFIG_PATH,
        DEFAULT_DB_PATH,
        HypothesisRecord,
        add_hypothesis,
        delete_hypothesis,
        get_hypothesis,
        list_hypotheses_full,
        list_summary_memories,
        synthesize_hypotheses_memory,
        update_hypothesis,
    )
    from crypto_scalp.auto_discover import auto_discover_hypotheses
    from crypto_scalp.vertex_client import HybridAIClient
    from crypto_scalp.ollama_local import OllamaClient, load_ollama_config
    from crypto_scalp.paper_trading import discover_paper_runs, read_paper_run, run_paper_trading
    from crypto_scalp.research import (
        discover_artifact_dirs,
        discover_csv_files,
        load_prepared_dataset,
        load_summary,
        score_dataset,
    )
    from crypto_scalp.realtime_matcher import (
        build_pattern_matcher_template,
        evaluate_trade_path,
        evaluate_trade_signal,
        save_realtime_matcher_template,
    )
    from crypto_scalp.impulse_lab_ui import render_impulse_lab
    from crypto_scalp.vps_sync import VPSSyncManager
    from crypto_scalp.data_lake_ui import render_data_lake_tab

else:
    from .bybit import (
        SUPPORTED_INTERVALS,
        DownloadProgress,
        INTERVAL_TO_DELTA,
        default_output_path,
        download_bybit_klines,
        find_reusable_bybit_csv,
        save_bybit_klines_csv,
    )
    from .bybit_live import discover_live_signal_runs, load_matcher_templates, read_live_signal_run
    from .config import RunConfig
    from .hypothesis_vault import (
        DEFAULT_CONFIG_PATH,
        DEFAULT_DB_PATH,
        HypothesisRecord,
        add_hypothesis,
        delete_hypothesis,
        get_hypothesis,
        list_hypotheses_full,
        list_summary_memories,
        synthesize_hypotheses_memory,
        update_hypothesis,
    )
    from .auto_discover import auto_discover_hypotheses
    from .vertex_client import HybridAIClient
    from .ollama_local import OllamaClient, load_ollama_config
    from .paper_trading import discover_paper_runs, read_paper_run, run_paper_trading
    from .research import (
        discover_artifact_dirs,
        discover_csv_files,
        load_prepared_dataset,
        load_summary,
        score_dataset,
    )
    from .realtime_matcher import (
        build_pattern_matcher_template,
        evaluate_trade_path,
        evaluate_trade_signal,
        save_realtime_matcher_template,
    )
    from .impulse_lab_ui import render_impulse_lab
    from .data_lake_ui import render_data_lake_tab


MANAGED_SYMBOL_OPTIONS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "SUIUSDT"]
MANAGED_TIMEFRAME_OPTIONS = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "240",
    "1d": "D",
    "1w": "W",
}
HYPOTHESIS_STATUSES = ["new", "testing", "paper_ready", "paper_testing", "confirmed", "rejected"]
CHART_SCALE_OPTIONS = [
    "1 час",
    "3 часа",
    "4 часа",
    "6 часов",
    "12 часов",
    "1 день",
    "3 дня",
    "7 дней",
    "14 дней",
    "30 дней",
    "90 дней",
    "6 месяцев",
    "Вся история",
]
CHART_SCALE_ZOOM_SEQUENCE = CHART_SCALE_OPTIONS
TIMEFRAME_DEFAULT_SCALE = {
    "1m": "1 день",
    "5m": "7 дней",
    "30m": "30 дней",
    "1h": "90 дней",
    "4h": "6 месяцев",
    "1d": "Вся история",
    "1w": "Вся история",
}

DEFAULT_STRATEGY_PARAMS = {
    "lookback_bars": 80,
    "min_dollar_volume_z": 3.0,
    "min_price_return_z": 2.0,
    "min_sequence_bars": 2,
    "max_sequence_bars": 8,
    "entry_after_bars": 1,
    "max_hold_bars": 20,
    "fixed_stop_loss_pct": 0.35,
    "take_profit_rr": 1.8,
    "cancel_if_no_follow_bars": 3,
    "cancel_min_follow_pct": 0.12,
    "paper_win_rate_threshold": 0.90,
    "account_risk_pct": 0.10,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--artifacts", type=Path)
    args, _ = parser.parse_known_args()
    return args


def main() -> None:
    args = parse_args()
    root = Path.cwd()
    config = RunConfig()

    st.set_page_config(page_title="Crypto Scalp HQ", layout="wide", initial_sidebar_state="expanded")
    inject_app_styles()

    artifact_dirs = discover_artifact_dirs(root)

    default_artifacts = str(args.artifacts.resolve()) if args.artifacts else (
        str(artifact_dirs[0]) if artifact_dirs else ""
    )

    # ─── Sidebar Navigation ─────────────────────────────────
    main_sections = [
        "📊 Dashboard",
        "📈 Market Research",
        "⚡ Impulse Lab",
        "🧪 Hypothesis Lab",
        "🤖 Soldier Feedback",
        "📜 Strategy History",
        "🗄 Data Lake",
    ]
    _section_migration = {
        "Market Research": "📈 Market Research",
        "Impulse Lab": "⚡ Impulse Lab",
        "Hypothesis Vault": "🧪 Hypothesis Lab",
        "Paper Trading": "🧪 Hypothesis Lab",
    }
    current_stored = st.session_state.get("main_section", "")
    if current_stored in _section_migration:
        st.session_state["main_section"] = _section_migration[current_stored]
    if "main_section" not in st.session_state or st.session_state["main_section"] not in main_sections:
        st.session_state["main_section"] = "📊 Dashboard"

    if st.session_state.pop("_nav_programmatic", False):
        st.session_state["main_section_radio"] = st.session_state["main_section"]
    elif "main_section_radio" not in st.session_state:
        st.session_state["main_section_radio"] = st.session_state["main_section"]

    with st.sidebar:
        st.markdown("## 🎯 HQ Command")
        selected_section = st.radio(
            "Navigation",
            options=main_sections,
            index=main_sections.index(st.session_state.get("main_section_radio", "📊 Dashboard")),
            label_visibility="collapsed",
            key="main_section_radio",
        )
        st.session_state["main_section"] = selected_section

        st.markdown("---")
        st.header("Модель")
        artifacts_raw = st.text_input("Папка артефактов", value=default_artifacts, placeholder="artifacts/demo_run")
        if artifact_dirs:
            picked_artifacts = st.selectbox(
                "Найденные артефакты",
                options=[""] + [str(path) for path in artifact_dirs],
                index=0,
            )
            if picked_artifacts:
                artifacts_raw = picked_artifacts

    # ─── Market Data (shared across pages) ──────────────────
    browser = render_market_browser(root=root, initial_data=args.data)
    data_path_raw = browser["data_path"]
    current_symbol = browser["symbol"]
    current_timeframe = browser["timeframe"]

    if not data_path_raw:
        st.info("Market Browser готовит локальные свечи. После загрузки график откроется автоматически.")
        return

    data_path = Path(data_path_raw).expanduser()
    if not data_path.exists():
        st.error(f"CSV не найден: {data_path}")
        return

    prepared = cached_load_prepared_dataset(str(data_path), data_path.stat().st_mtime, data_path.stat().st_size)
    dataset = prepared.frame.copy()
    feature_columns = prepared.feature_columns
    if dataset.empty or dataset["timestamp"].isna().all():
        dataset = build_chart_fallback_dataset(data_path)
        feature_columns = []
        st.info(
            "Для этого таймфрейма пока мало свечей для полного ML-набора признаков. "
            "Показываю график по сырым свечам с базовыми индикаторами."
        )

    model_frame = None
    artifacts_dir = Path(artifacts_raw).expanduser() if artifacts_raw else None
    if (
        feature_columns
        and artifacts_dir
        and (artifacts_dir / "model.pt").exists()
        and (artifacts_dir / "model_meta.json").exists()
    ):
        model_frame = score_dataset(prepared, artifacts_dir, config)
        dataset = model_frame

    # ─── Page Routing ───────────────────────────────────────
    if selected_section == "📊 Dashboard":
        st.title("📊 Dashboard")
        # Show overview panels only on Dashboard
        render_workspace_overview(
            data_path=data_path,
            artifacts_dir=artifacts_dir,
            current_symbol=current_symbol,
            current_timeframe=current_timeframe,
            total_rows=len(dataset),
            include_model=model_frame is not None,
        )
        render_top_research_dashboard(
            dataset=dataset,
            data_path=data_path,
            current_symbol=current_symbol,
            current_timeframe=current_timeframe,
            include_model=model_frame is not None,
        )
        render_model_metrics_panel(
            dataset=dataset,
            artifacts_dir=artifacts_dir,
            include_model=model_frame is not None,
        )
        render_dashboard(root=root)
    elif selected_section == "📈 Market Research":
        st.title("📈 Market Research")
        market_context = render_market_research(
            dataset,
            feature_columns,
            model_frame is not None,
            artifacts_dir,
            data_path=data_path,
            current_symbol=current_symbol,
            current_timeframe=current_timeframe,
        )
    elif selected_section == "⚡ Impulse Lab":
        st.title("⚡ Impulse Lab")
        render_impulse_lab(
            dataset=dataset,
            data_path=data_path,
            current_symbol=current_symbol,
            current_timeframe=current_timeframe,
            root=root,
        )
    elif selected_section == "🧪 Hypothesis Lab":
        st.title("🧪 Hypothesis Lab")
        market_context = st.session_state.get("market_context") or {
            "data_path": str(data_path),
            "symbol": current_symbol,
            "timeframe": current_timeframe,
            "window_start_idx": 0,
            "window_end_idx": max(0, len(dataset) - 1),
            "window_start_ts": str(dataset["timestamp"].iloc[0]),
            "window_end_ts": str(dataset["timestamp"].iloc[-1]),
        }
        render_hypothesis_vault(
            current_symbol=current_symbol,
            current_timeframe=current_timeframe,
            market_context=market_context,
        )
        st.markdown("---")
        render_paper_trading(root=root)
    elif selected_section == "🤖 Soldier Feedback":
        st.title("🤖 Soldier Feedback")
        render_soldier_feedback()
    elif selected_section == "📜 Strategy History":
        st.title("📜 Strategy History")
        render_strategy_history_page(root=root)
    elif selected_section == "🗄 Data Lake":
        st.title("🗄 Data Lake")
        render_data_lake_tab()


def render_dashboard(root: Path) -> None:
    """Dashboard with Demo (left) | Real (right) split."""

    demo_runs_dir = root / ".local_ai" / "paper_runs"
    real_runs_dir = root / ".local_ai" / "real_trades"

    demo_trades = _load_all_closed_trades(demo_runs_dir)
    real_trades = _load_all_closed_trades(real_runs_dir)
    vps_trades = _load_vps_trades(root / "data" / "vps_sync" / "paper_state_multi.json")
    
    # Merge them
    all_demo_trades = demo_trades + vps_trades

    col_demo, col_divider, col_real = st.columns([1, 0.02, 1])

    with col_demo:
        # Header with Sync Button
        h1, h2 = st.columns([3, 1])
        h1.markdown("### 📋 Demo контур")
        if h2.button("🔄 Sync VPS", key="sync_dash"):
            sync_mgr = VPSSyncManager()
            with st.spinner("Syncing..."):
                if sync_mgr.sync_from_vps():
                    st.rerun()

        _render_account_panel(
            title="",
            trades=all_demo_trades,
            initial_balance=1000.0,
            accent_color="#00c076",
            panel_key="demo",
        )

    with col_divider:
        st.markdown(
            "<div style='width:1px; background: linear-gradient(180deg, transparent, rgba(0,192,118,0.5), "
            "rgba(255,82,82,0.5), transparent); height:100%; min-height:800px; margin:0 auto;'></div>",
            unsafe_allow_html=True,
        )

    with col_real:
        if real_trades:
            _render_account_panel(
                title="💰 Реальный контур",
                trades=real_trades,
                initial_balance=1000.0,
                accent_color="#ff5252",
                panel_key="real",
            )
        else:
            st.markdown("### 💰 Реальный контур")
            st.markdown(
                "<div style='text-align:center; padding:80px 20px; "
                "border:1px dashed rgba(255,82,82,0.3); border-radius:12px; margin:20px 0;'>"
                "<p style='font-size:48px; margin:0;'>🔒</p>"
                "<p style='color:rgba(255,255,255,0.5); font-size:14px; margin-top:12px;'>"
                "Реальный контур не подключён.<br>"
                "Подключите торговый аккаунт для отображения данных."
                "</p></div>",
                unsafe_allow_html=True,
            )


def _load_all_closed_trades(runs_dir: Path) -> list[dict]:
    """Load all closed trades from paper/real runs."""
    if not runs_dir.exists():
        return []
    trades = []
    for path in sorted(runs_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        for item in payload.get("paper_trades", []) or []:
            if item.get("event") == "close":
                item["_run_file"] = path.name
                item["_symbol"] = payload.get("symbol", "")
                item["_deposit"] = payload.get("deposit_usdt", 1000.0)
                trades.append(item)
    return trades


def _load_vps_trades(vps_json_path: Path) -> list[dict]:
    """Load trades from the synced VPS state file."""
    if not vps_json_path.exists():
        return []
    try:
        data = json.loads(vps_json_path.read_text(encoding="utf-8"))
        raw_trades = data.get("completed_trades", [])
        initial_deposit = data.get("deposit", 1000.0)
        
        trades = []
        for t in raw_trades:
            # Map VPS fields to Dashboard fields
            trades.append({
                "timestamp": t.get("exit_time", ""),
                "side": t.get("direction", "long"),
                "_symbol": t.get("symbol", ""),
                "entry_price": t.get("entry_price", 0),
                "exit_price": t.get("exit_price", 0),
                "realized_move_pct": t.get("realized_pnl_pct", 0),
                "pnl_usdt": (t.get("realized_pnl_pct", 0) / 100.0) * initial_deposit,
                "exit_reason": t.get("exit_reason", ""),
                "hypothesis_id": "VPS-Soldier"
            })
        return trades
    except Exception:
        return []


def _render_account_panel(
    title: str,
    trades: list[dict],
    initial_balance: float,
    accent_color: str,
    panel_key: str,
) -> None:
    """Render one side of the dashboard (Demo or Real)."""

    st.markdown(f"### {title}")

    if not trades:
        st.info("Нет завершённых сделок. Запустите paper trading для получения данных.")
        return

    # Compute metrics
    df = pd.DataFrame(trades)
    if "pnl_usdt" in df.columns:
        df["pnl_usdt"] = pd.to_numeric(df["pnl_usdt"], errors="coerce").fillna(0)
    else:
        df["pnl_usdt"] = 0.0
    if "realized_move_pct" in df.columns:
        df["realized_move_pct"] = pd.to_numeric(df["realized_move_pct"], errors="coerce").fillna(0)
    else:
        df["realized_move_pct"] = 0.0

    total_pnl = df["pnl_usdt"].sum()
    final_balance = initial_balance + total_pnl
    pnl_pct = (total_pnl / initial_balance * 100) if initial_balance else 0
    wins = (df["pnl_usdt"] > 0).sum()
    losses = (df["pnl_usdt"] < 0).sum()
    total = len(df)
    win_rate = (wins / total * 100) if total else 0

    long_count = (df.get("side", pd.Series(dtype=str)) == "long").sum()
    short_count = (df.get("side", pd.Series(dtype=str)) == "short").sum()
    long_pnl = df.loc[df.get("side", "") == "long", "pnl_usdt"].sum() if "side" in df.columns else 0
    short_pnl = df.loc[df.get("side", "") == "short", "pnl_usdt"].sum() if "side" in df.columns else 0

    # Balance metric (big)
    st.metric(
        "Баланс",
        f"${final_balance:,.2f}",
        f"{pnl_pct:+.2f}% (${total_pnl:+,.2f})",
        delta_color="normal",
    )

    # Navigation state
    if "view_mode" not in st.session_state:
        st.session_state["view_mode"] = "list"
    
    if st.session_state["view_mode"] == "detail" and "selected_trade" in st.session_state:
        if st.button("⬅ Назад к списку", use_container_width=True):
            st.session_state["view_mode"] = "list"
            st.rerun()
        render_trade_inspector(st.session_state["selected_trade"])
        return

    # Key metrics row
    m1, m2, m3, m4 = st.columns(4)
    win_rate_str = f"{int(win_rate)}%" if win_rate == 100 else f"{win_rate:.1f}%"
    m1.metric("Win Rate", win_rate_str, f"{wins}W / {losses}L")
    m2.metric("Сделок", f"{total}", f"L:{long_count} | S:{short_count}")
    m3.metric("Long PnL", f"${long_pnl:+,.2f}")
    m4.metric("Short PnL", f"${short_pnl:+,.2f}")

    # (Selected trade will be handled via dataframe selection below)

    # Equity curve
    equity = [initial_balance]
    current = initial_balance
    for pnl in df["pnl_usdt"].values:
        current += pnl
        equity.append(current)

    eq_fig = go.Figure()
    eq_fig.add_trace(go.Scatter(
        y=equity,
        mode="lines+markers",
        line=dict(width=3, color=accent_color),
        marker=dict(size=6, color=accent_color),
        fill="tozeroy",
        fillcolor=f"rgba({','.join(str(int(accent_color.lstrip('#')[i:i+2], 16)) for i in (0,2,4))}, 0.08)",
        name="Equity",
        hovertemplate="Trade #%{x}<br>Balance: $%{y:,.2f}<extra></extra>"
    ))
    
    # Calculate Y-axis range to zoom in on equity
    y_min, y_max = min(equity), max(equity)
    y_range = y_max - y_min
    margin = max(y_range * 0.2, 2.0) # At least $2 margin
    
    eq_fig.update_layout(
        height=260,
        margin=dict(l=10, r=10, t=30, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        title=dict(text="Equity Growth ($)", font=dict(size=13, color="rgba(255,255,255,0.7)")),
        xaxis=dict(showgrid=False, title="Trade #", tickmode="linear", tick0=0, dtick=1),
        yaxis=dict(
            gridcolor="rgba(128,128,128,0.1)", 
            title="",
            range=[y_min - margin, y_max + margin],
            tickformat="$,.2f"
        ),
    )
    st.plotly_chart(eq_fig, use_container_width=True, key=f"eq_{panel_key}")

    # Exit reasons breakdown
    if "exit_reason" in df.columns:
        reasons = df["exit_reason"].value_counts()
        reason_cols = st.columns(min(4, len(reasons)))
        for i, (reason, count) in enumerate(reasons.items()):
            if i < len(reason_cols):
                label = {"fixed_stop": "🛑 SL", "take_profit": "🎯 TP",
                         "cancel_no_follow": "⏸ Cancel", "time_exit": "⏱ Time"}.get(reason, reason)
                reason_cols[i].metric(label, count)

    # trade history
    with st.expander(f"📋 Полный список сделок ({total}) — нажмите на строку для разбора", expanded=True):
        history = df.copy()
        history["pnl_usdt"] = history["pnl_usdt"].map(lambda x: f"${x:+,.2f}")
        history["realized_move_pct"] = history["realized_move_pct"].map(lambda x: f"{x:+.2f}%")
        
        event = st.dataframe(
            history[["timestamp", "_symbol", "side", "entry_price", "exit_price", "realized_move_pct", "pnl_usdt", "exit_reason"]], 
            use_container_width=True, 
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key=f"trade_table_{panel_key}"
        )
        
        # Handle selection
        selection = event.get("selection", {}).get("rows", [])
        if selection:
            selected_row_idx = selection[0]
            # Since df was created directly from trades, the index matches
            st.session_state["selected_trade"] = trades[selected_row_idx]
            st.session_state["view_mode"] = "detail"
            st.rerun()

    # turnover section
    with st.expander("📊 Оборот", expanded=False):
        if "side" in df.columns and "entry_price" in df.columns:
            df["entry_price"] = pd.to_numeric(df.get("entry_price", 0), errors="coerce").fillna(0)
            position_notional = df.get("position_notional_usdt", df["entry_price"])
            position_notional = pd.to_numeric(position_notional, errors="coerce").fillna(0)
            long_mask = df["side"] == "long"
            short_mask = df["side"] == "short"
            t1, t2 = st.columns(2)
            t1.metric("Оборот LONG", f"${position_notional[long_mask].sum():,.0f}")
            t2.metric("Оборот SHORT", f"${position_notional[short_mask].sum():,.0f}")


def render_market_browser(root: Path, initial_data: Optional[Path]) -> dict:
    today = date.today()
    default_symbol = st.session_state.get("managed_symbol", "BTCUSDT")
    default_timeframe = st.session_state.get("managed_timeframe", "1m")
    pending_timeframe = st.session_state.pop("pending_market_timeframe", None)
    if pending_timeframe in MANAGED_TIMEFRAME_OPTIONS:
        default_timeframe = pending_timeframe
        st.session_state["managed_timeframe"] = pending_timeframe
        st.session_state.pop("managed_data_path", None)
        st.session_state.pop("managed_request_signature", None)
    default_end = st.session_state.get("market_browser_end_date", today)
    if not isinstance(default_end, date):
        default_end = today

    with st.container():
        st.markdown("### Market Browser")
        control_col1, control_col2, control_col3, control_col4, control_col5, control_col6 = st.columns([1.05, 1.55, 0.9, 0.9, 1.0, 0.9])
        symbol_pick = control_col1.selectbox(
            "Инструмент",
            options=MANAGED_SYMBOL_OPTIONS,
            index=max(0, MANAGED_SYMBOL_OPTIONS.index(default_symbol)) if default_symbol in MANAGED_SYMBOL_OPTIONS else 0,
            key="market_browser_symbol",
        )
        timeframe_widget_nonce = int(st.session_state.get("market_browser_timeframe_nonce", 0))
        timeframe_label = control_col2.radio(
            "Таймфрейм графика",
            options=list(MANAGED_TIMEFRAME_OPTIONS.keys()),
            index=max(0, list(MANAGED_TIMEFRAME_OPTIONS.keys()).index(default_timeframe))
            if default_timeframe in MANAGED_TIMEFRAME_OPTIONS
            else 0,
            horizontal=True,
            key=f"market_browser_timeframe_{timeframe_widget_nonce}",
        )
        history_depth = control_col3.selectbox(
            "История",
            options=["1 месяц", "3 месяца", "6 месяцев"],
            index=2,
            key="market_browser_history_depth",
        )
        end_date = control_col4.date_input("До даты", value=default_end, key="market_browser_end_date")
        prefill_all = control_col5.checkbox(
            "Догружать все TF",
            value=bool(st.session_state.get("market_browser_prefill_all", False)),
            key="market_browser_prefill_all",
            help="После готовности выбранного таймфрейма приложение будет по одному догружать остальные таймфреймы за этот диапазон.",
        )
        if "market_browser_live_mode" not in st.session_state:
            st.session_state["market_browser_live_mode"] = True
        live_mode = control_col6.checkbox(
            "Live",
            value=bool(st.session_state.get("market_browser_live_mode", True)),
            key="market_browser_live_mode",
            help="Для сегодняшнего графика обновляет локальный CSV свежими свечами Bybit.",
        )

        depth_days = {"1 месяц": 30, "3 месяца": 90, "6 месяцев": 180}[history_depth]
        start_date = end_date - timedelta(days=depth_days)
        interval = MANAGED_TIMEFRAME_OPTIONS[timeframe_label]
        start_dt = datetime.combine(start_date, time(0, 0), tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, time(23, 59), tzinfo=timezone.utc)
        signature = build_market_request_signature(symbol_pick, timeframe_label, start_date, end_date)

        st.session_state["managed_symbol"] = symbol_pick
        st.session_state["managed_timeframe"] = timeframe_label
        st.session_state["managed_start_date"] = start_date
        st.session_state["managed_end_date"] = end_date

        cache_hit = cached_find_reusable_bybit_csv(
            root_str=str(root),
            symbol=symbol_pick,
            interval=interval,
            start_iso=start_dt.isoformat(),
            end_iso=end_dt.isoformat(),
        )
        coverage_col, status_col = st.columns([1.6, 1])
        with coverage_col:
            st.caption(
                f"Рабочий диапазон: {start_date.isoformat()} 00:00 UTC -> {end_date.isoformat()} 23:59 UTC. "
                "График использует только локальные данные выбранного таймфрейма. Если файла нет, скачивание запускается отдельной кнопкой."
            )
            with st.expander("Покрытие локальных таймфреймов", expanded=False):
                render_timeframe_coverage(root=root, symbol=symbol_pick, start_dt=start_dt, end_dt=end_dt)

        current_path = ""
        partial_path: Optional[Path] = None
        partial_start: Optional[pd.Timestamp] = None
        partial_end: Optional[pd.Timestamp] = None
        if cache_hit is None:
            partial = find_partial_local_bybit_csv(root=root, symbol=symbol_pick, interval=interval)
            if partial is not None:
                partial_path, partial_start, partial_end = partial
        if cache_hit is not None:
            current_path = str(cache_hit.path)
            st.session_state["managed_data_path"] = current_path
            st.session_state["managed_request_signature"] = signature
            with status_col:
                st.success(f"Готово: {symbol_pick} {timeframe_label}")
                st.caption(
                    f"{cache_hit.coverage_start:%Y-%m-%d %H:%M} -> "
                    f"{cache_hit.coverage_end:%Y-%m-%d %H:%M} UTC | gaps {cache_hit.missing_rows}"
                )
            if prefill_all:
                maybe_prefetch_next_timeframe(
                    root=root,
                    symbol=symbol_pick,
                    active_timeframe=timeframe_label,
                    start_dt=start_dt,
                    end_dt=end_dt,
                )
        elif partial_path is not None:
            current_path = str(partial_path)
            st.session_state["managed_data_path"] = current_path
            st.session_state["managed_request_signature"] = signature
            with status_col:
                st.warning(f"Показываю частичный локальный {symbol_pick} {timeframe_label}")
                st.caption(f"{partial_start} -> {partial_end} UTC")
        else:
            with status_col:
                st.warning(f"Локального {symbol_pick} {timeframe_label} за выбранный диапазон нет.")
            saved = None
            if is_bybit_download_paused(symbol_pick, interval, start_dt, end_dt):
                show_bybit_download_pause(symbol_pick, timeframe_label, interval, start_dt, end_dt)
            elif st.button(
                f"Скачать {symbol_pick} {timeframe_label}",
                type="primary",
                use_container_width=True,
                key=f"download_missing_{symbol_pick}_{timeframe_label}_{start_date}_{end_date}",
            ):
                with status_col:
                    st.info("Начинаю загрузку выбранного таймфрейма из Bybit.")
                saved = auto_download_market_data(
                    root=root,
                    symbol=symbol_pick,
                    timeframe_label=timeframe_label,
                    interval=interval,
                    start_dt=start_dt,
                    end_dt=end_dt,
                )
            if saved:
                cached_find_reusable_bybit_csv.clear()
                cached_load_prepared_dataset.clear()
                activate_market_dataset(
                    path=saved,
                    symbol=symbol_pick,
                    interval=interval,
                    start_date=start_date,
                    end_date=end_date,
                )
                st.rerun()

        if current_path and live_mode and end_date >= today:
            # Отключаем автообновление в лабораториях, чтобы не сбрасывался UI (закрытые expander'ы и вкладки)
            current_section = st.session_state.get("main_section", "")
            if current_section not in ["⚡ Impulse Lab", "🧪 Hypothesis Lab"]:
                with status_col:
                    render_live_tail_refresh(
                        path_str=current_path,
                        symbol=symbol_pick,
                        timeframe_label=timeframe_label,
                        interval=interval,
                    )

    if not current_path and not st.session_state.get("managed_symbol") and initial_data and initial_data.exists():
        current_path = str(initial_data.resolve())

    return {
        "data_path": current_path,
        "symbol": symbol_pick,
        "timeframe": timeframe_label,
    }


def auto_download_market_data(
    root: Path,
    symbol: str,
    timeframe_label: str,
    interval: str,
    start_dt: datetime,
    end_dt: datetime,
) -> Optional[Path]:
    output_hint = build_managed_output_path(root=root, symbol=symbol, interval=interval, start=start_dt, end=end_dt)
    progress_bar = st.progress(0.0, text="Готовим загрузку свечей из Bybit...")
    status_box = st.empty()

    def on_progress(update: DownloadProgress) -> None:
        ratio = min(1.0, update.current_chunk / max(1, update.total_chunks))
        remaining = max(0, update.total_chunks - update.current_chunk)
        progress_bar.progress(
            ratio,
            text=(
                f"{symbol} {timeframe_label}: chunk {update.current_chunk}/{update.total_chunks} | "
                f"осталось {remaining} | rows {update.rows_downloaded:,}"
            ),
        )
        status_box.info(
            f"Загружаю {update.current_start:%Y-%m-%d %H:%M} -> "
            f"{update.current_end:%Y-%m-%d %H:%M} UTC из Bybit."
        )

    try:
        saved = save_bybit_klines_csv(
            output_path=output_hint,
            symbol=symbol,
            interval=interval,
            start=start_dt,
            end=end_dt,
            category="linear",
            progress_callback=on_progress,
        )
    except Exception as exc:
        progress_bar.empty()
        if "10006" in str(exc) or "Too many visits" in str(exc):
            pause_bybit_download(symbol, interval, start_dt, end_dt, minutes=5)
        status_box.error(f"Не удалось загрузить свечи: {exc}")
        return None

    progress_bar.progress(1.0, text=f"Загрузка завершена: {saved.name}")
    status_box.success(f"Сохранено локально: {saved}")
    return saved


def bybit_download_pause_key(symbol: str, interval: str, start_dt: datetime, end_dt: datetime) -> str:
    safe_symbol = re.sub(r"[^a-zA-Z0-9]+", "_", symbol.upper())
    return f"bybit_download_pause_{safe_symbol}_{interval}_{start_dt:%Y%m%d}_{end_dt:%Y%m%d}"


def pause_bybit_download(symbol: str, interval: str, start_dt: datetime, end_dt: datetime, minutes: int) -> None:
    key = bybit_download_pause_key(symbol, interval, start_dt, end_dt)
    st.session_state[key] = datetime.now(timezone.utc) + timedelta(minutes=minutes)


def is_bybit_download_paused(symbol: str, interval: str, start_dt: datetime, end_dt: datetime) -> bool:
    key = bybit_download_pause_key(symbol, interval, start_dt, end_dt)
    until = st.session_state.get(key)
    if not isinstance(until, datetime):
        return False
    if datetime.now(timezone.utc) >= until:
        st.session_state.pop(key, None)
        return False
    return True


def show_bybit_download_pause(symbol: str, timeframe_label: str, interval: str, start_dt: datetime, end_dt: datetime) -> None:
    key = bybit_download_pause_key(symbol, interval, start_dt, end_dt)
    until = st.session_state.get(key)
    if isinstance(until, datetime):
        seconds_left = max(0, int((until - datetime.now(timezone.utc)).total_seconds()))
        st.warning(
            f"Bybit временно ограничил запросы. Пауза для {symbol} {timeframe_label}: "
            f"примерно {max(1, seconds_left // 60)} мин. Автозагрузка не будет повторяться до окончания паузы."
        )


def find_partial_local_bybit_csv(root: Path, symbol: str, interval: str) -> Optional[tuple[Path, pd.Timestamp, pd.Timestamp]]:
    symbol_key = symbol.lower()
    candidates = sorted((root / "data").glob(f"bybit_{symbol_key}_{interval}_*.csv"))
    best: Optional[tuple[Path, pd.Timestamp, pd.Timestamp]] = None
    for path in candidates:
        try:
            frame = pd.read_csv(path, usecols=["timestamp"])
        except Exception:
            continue
        timestamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dropna()
        if timestamps.empty:
            continue
        coverage_start = timestamps.min()
        coverage_end = timestamps.max()
        if best is None or coverage_end > best[2]:
            best = (path, coverage_start, coverage_end)
    return best


def live_reload_seconds(interval: str) -> int:
    delta = INTERVAL_TO_DELTA.get(interval, timedelta(minutes=1))
    interval_seconds = max(60, int(delta.total_seconds()))
    return min(120, max(30, interval_seconds // 2))


def maybe_refresh_live_tail(path: Path, symbol: str, timeframe_label: str, interval: str) -> None:
    try:
        frame = pd.read_csv(path, usecols=["timestamp"])
    except Exception as exc:
        st.warning(f"Live refresh: не смог прочитать локальный CSV: {exc}")
        return
    if frame.empty or "timestamp" not in frame.columns:
        return

    timestamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dropna()
    if timestamps.empty:
        return

    delta = INTERVAL_TO_DELTA.get(interval, timedelta(minutes=1))
    last_ts = timestamps.max().to_pydatetime().replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    refresh_from = last_ts + delta
    refresh_to = now - delta
    reload_seconds = live_reload_seconds(interval)
    if refresh_from >= refresh_to:
        st.caption(f"Live: {symbol} {timeframe_label} актуален до {last_ts:%Y-%m-%d %H:%M} UTC.")
        inject_live_reload_script(seconds=reload_seconds)
        return

    if is_bybit_download_paused(symbol, interval, refresh_from, refresh_to):
        show_bybit_download_pause(symbol, timeframe_label, interval, refresh_from, refresh_to)
        inject_live_reload_script(seconds=max(60, reload_seconds))
        return

    st.info(f"Live: подтягиваю новые свечи {symbol} {timeframe_label} после {last_ts:%Y-%m-%d %H:%M} UTC.")
    try:
        fresh = download_bybit_klines(
            symbol=symbol,
            interval=interval,
            start=refresh_from,
            end=refresh_to,
            category="linear",
        )
    except Exception as exc:
        if "10006" in str(exc) or "Too many visits" in str(exc):
            pause_bybit_download(symbol, interval, refresh_from, refresh_to, minutes=5)
        st.warning(f"Live refresh не удался: {exc}")
        inject_live_reload_script(seconds=max(60, reload_seconds))
        return

    if fresh.empty:
        st.caption("Live: новых закрытых свечей пока нет.")
        inject_live_reload_script(seconds=reload_seconds)
        return

    existing = pd.read_csv(path)
    updated = pd.concat([existing, fresh], ignore_index=True)
    updated["timestamp"] = pd.to_datetime(updated["timestamp"], utc=True, errors="coerce")
    updated = updated.dropna(subset=["timestamp"]).sort_values("timestamp").drop_duplicates("timestamp")
    updated.to_csv(path, index=False)
    cached_find_reusable_bybit_csv.clear()
    cached_load_prepared_dataset.clear()
    st.success(f"Live: добавлено новых свечей: {len(fresh):,}.")
    st.rerun()


@st.fragment(run_every="30s")
def render_live_tail_refresh(path_str: str, symbol: str, timeframe_label: str, interval: str) -> None:
    maybe_refresh_live_tail(
        path=Path(path_str),
        symbol=symbol,
        timeframe_label=timeframe_label,
        interval=interval,
    )


def inject_live_reload_script(seconds: int) -> None:
    components.html(
        f"""
        <script>
        // Streamlit fragment reruns handle live refresh. This is a tiny fallback only.
        window.setTimeout(() => window.parent.dispatchEvent(new Event("resize")), {max(5, seconds) * 1000});
        </script>
        """,
        height=0,
        width=0,
    )


def render_pan_chart_with_price_scale_drag(fig: go.Figure, config: dict, key: str) -> None:
    div_id = re.sub(r"[^a-zA-Z0-9_]+", "_", f"chart_{key}")[-90:]
    drag_script = """
    const plot = document.getElementById('{plot_id}');
    if (plot) {{
        let dragging = false;
        let startY = 0;
        let startRange = null;
        const priceZonePx = 96;

        const inPriceScaleZone = (event) => {{
            const rect = plot.getBoundingClientRect();
            return event.clientX >= rect.right - priceZonePx && event.clientX <= rect.right + 8;
        }};

        const dataRange = () => {{
            const values = [];
            const traces = Array.from((plot._fullData || plot.data || []));
            traces.forEach((trace) => {{
                const axisName = trace.yaxis || 'y';
                if (axisName !== 'y') return;
                ['low', 'high', 'open', 'close', 'y'].forEach((field) => {{
                    const series = trace[field];
                    if (!series || typeof series[Symbol.iterator] !== 'function') return;
                    for (const raw of series) {{
                        const value = Number(raw);
                        if (Number.isFinite(value)) values.push(value);
                    }}
                }});
            }});
            if (!values.length) return null;
            const low = Math.min(...values);
            const high = Math.max(...values);
            if (!Number.isFinite(low) || !Number.isFinite(high) || low === high) return null;
            const padding = Math.max((high - low) * 0.08, Math.abs((high + low) / 2) * 0.0005);
            return [low - padding, high + padding];
        }};

        const numericRange = () => {{
            const axis = (plot._fullLayout && plot._fullLayout.yaxis) || {{}};
            const layoutAxis = (plot.layout && plot.layout.yaxis) || {{}};
            const candidates = [
                axis.range,
                axis._rangeInitial,
                axis._rl,
                axis._input && axis._input.range,
                layoutAxis.range,
            ];
            for (const range of candidates) {{
                if (!range || range.length !== 2) continue;
                const low = Number(range[0]);
                const high = Number(range[1]);
                if (Number.isFinite(low) && Number.isFinite(high) && low !== high) {{
                    return low < high ? [low, high] : [high, low];
                }}
            }}
            return dataRange();
        }};

        const applyRange = (range) => {{
            Plotly.relayout(plot, {{
                'yaxis.autorange': false,
                'yaxis.fixedrange': false,
                'yaxis.range[0]': range[0],
                'yaxis.range[1]': range[1],
            }});
        }};

        document.addEventListener('mousedown', (event) => {{
            if (!inPriceScaleZone(event)) return;
            const range = numericRange();
            if (!range) return;
            dragging = true;
            startY = event.clientY;
            startRange = range;
            plot.style.cursor = 'ns-resize';
            event.preventDefault();
            event.stopPropagation();
        }}, true);

        document.addEventListener('mousemove', (event) => {{
            if (!dragging) {{
                plot.style.cursor = inPriceScaleZone(event) ? 'ns-resize' : '';
                return;
            }}
            const dy = event.clientY - startY;
            const factor = Math.exp(dy / 220);
            const center = (Number(startRange[0]) + Number(startRange[1])) / 2;
            const halfRange = (Number(startRange[1]) - Number(startRange[0])) * factor / 2;
            applyRange([center - halfRange, center + halfRange]);
            event.preventDefault();
            event.stopPropagation();
        }}, true);

        document.addEventListener('mouseup', () => {{
            dragging = false;
            startRange = null;
            plot.style.cursor = '';
        }}, true);
    }}
    """
    drag_script = drag_script.replace("{{", "{").replace("}}", "}")
    html = pio.to_html(
        fig,
        include_plotlyjs=True,
        full_html=False,
        config=config,
        div_id=div_id,
        post_script=drag_script,
    )
    components.html(html, height=1000, scrolling=False)


def inject_price_axis_drag_script() -> None:
    components.html(
        """
        <script>
        (() => {
            const root = window.parent.document;
            const Plotly = window.parent.Plotly;
            if (!root || !Plotly) return;
            if (root.dataset.cryptoPriceScaleGlobalDrag === "1") return;
            root.dataset.cryptoPriceScaleGlobalDrag = "1";

            let activePlot = null;
            let dragging = false;
            let startY = 0;
            let startRange = null;
            let nextRange = null;
            let rafPending = false;
            const priceZonePx = 96;

            const plots = () => Array.from(root.querySelectorAll(".js-plotly-plot"));

            const plotAt = (event) => {
                return plots().find((plot) => {
                    const rect = plot.getBoundingClientRect();
                    return (
                        event.clientX >= rect.left &&
                        event.clientX <= rect.right &&
                        event.clientY >= rect.top &&
                        event.clientY <= rect.bottom
                    );
                }) || null;
            };

            const inPriceScaleZone = (plot, event) => {
                if (!plot) return false;
                const rect = plot.getBoundingClientRect();
                return event.clientX >= rect.right - priceZonePx && event.clientX <= rect.right + 10;
            };

            const dataRange = (plot) => {
                const values = [];
                const traces = Array.from((plot._fullData || plot.data || []));
                traces.forEach((trace) => {
                    const axisName = trace.yaxis || "y";
                    if (axisName !== "y") return;
                    ["low", "high", "open", "close", "y"].forEach((field) => {
                        const series = trace[field];
                        if (!series || typeof series[Symbol.iterator] !== "function") return;
                        for (const raw of series) {
                            const value = Number(raw);
                            if (Number.isFinite(value)) values.push(value);
                        }
                    });
                });
                if (!values.length) return null;
                const low = Math.min(...values);
                const high = Math.max(...values);
                if (!Number.isFinite(low) || !Number.isFinite(high) || low === high) return null;
                const padding = Math.max((high - low) * 0.08, Math.abs((high + low) / 2) * 0.0005);
                return [low - padding, high + padding];
            };

            const numericRange = (plot) => {
                const axis = (plot._fullLayout && plot._fullLayout.yaxis) || {};
                const layoutAxis = (plot.layout && plot.layout.yaxis) || {};
                const candidates = [
                    axis.range,
                    axis._rangeInitial,
                    axis._rl,
                    axis._input && axis._input.range,
                    layoutAxis.range,
                ];
                for (const range of candidates) {
                    if (!range || range.length !== 2) continue;
                    const low = Number(range[0]);
                    const high = Number(range[1]);
                    if (Number.isFinite(low) && Number.isFinite(high) && low !== high) {
                        return low < high ? [low, high] : [high, low];
                    }
                }
                return dataRange(plot);
            };

            const applyRange = (plot, range) => {
                nextRange = range;
                if (rafPending) return;
                rafPending = true;
                window.parent.requestAnimationFrame(() => {
                    rafPending = false;
                    if (!activePlot || !nextRange) return;
                    Plotly.relayout(activePlot, {
                        "yaxis.autorange": false,
                        "yaxis.fixedrange": false,
                        "yaxis.range[0]": nextRange[0],
                        "yaxis.range[1]": nextRange[1],
                    });
                });
            };

            root.addEventListener("pointerdown", (event) => {
                const plot = plotAt(event);
                if (!inPriceScaleZone(plot, event)) return;
                const range = numericRange(plot);
                if (!range) return;
                activePlot = plot;
                dragging = true;
                startY = event.clientY;
                startRange = range;
                event.preventDefault();
                event.stopImmediatePropagation();
                plot.style.cursor = "ns-resize";
            }, true);

            root.addEventListener("pointermove", (event) => {
                const hoverPlot = activePlot || plotAt(event);
                if (!dragging) {
                    if (hoverPlot) {
                        hoverPlot.style.cursor = inPriceScaleZone(hoverPlot, event) ? "ns-resize" : "";
                    }
                    return;
                }
                if (!activePlot || !startRange) return;
                const dy = event.clientY - startY;
                const factor = Math.exp(dy / 220);
                const center = (Number(startRange[0]) + Number(startRange[1])) / 2;
                const halfRange = (Number(startRange[1]) - Number(startRange[0])) * factor / 2;
                applyRange(activePlot, [center - halfRange, center + halfRange]);
                event.preventDefault();
                event.stopImmediatePropagation();
            }, true);

            const stopDrag = () => {
                dragging = false;
                startRange = null;
                nextRange = null;
                if (activePlot) activePlot.style.cursor = "";
                activePlot = null;
            };
            root.addEventListener("pointerup", stopDrag, true);
            root.addEventListener("pointercancel", stopDrag, true);

            const bindPriceScale = () => {
                const plotList = plots();
                const plot = plotList[plotList.length - 1];
                if (!plot || plot.dataset.cryptoPriceScaleDrag === "1") return;
                plot.dataset.cryptoPriceScaleDrag = "1";

                plot.addEventListener("mouseleave", () => {
                    if (!dragging) plot.style.cursor = "";
                }, true);
            };

            window.setTimeout(bindPriceScale, 250);
            new MutationObserver(bindPriceScale).observe(root.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def maybe_prefetch_next_timeframe(
    root: Path,
    symbol: str,
    active_timeframe: str,
    start_dt: datetime,
    end_dt: datetime,
) -> None:
    for label, interval in MANAGED_TIMEFRAME_OPTIONS.items():
        if label == active_timeframe:
            continue
        hit = cached_find_reusable_bybit_csv(
            root_str=str(root),
            symbol=symbol,
            interval=interval,
            start_iso=start_dt.isoformat(),
            end_iso=end_dt.isoformat(),
        )
        if hit is not None:
            continue

        if is_bybit_download_paused(symbol, interval, start_dt, end_dt):
            show_bybit_download_pause(symbol, label, interval, start_dt, end_dt)
            return
        st.info(f"Автодогрузка полного набора: сейчас подтягиваю {symbol} {label}.")
        saved = auto_download_market_data(
            root=root,
            symbol=symbol,
            timeframe_label=label,
            interval=interval,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        if saved:
            st.rerun()
        return


def render_timeframe_coverage(root: Path, symbol: str, start_dt: datetime, end_dt: datetime) -> None:
    rows = []
    for label, interval in MANAGED_TIMEFRAME_OPTIONS.items():
        hit = cached_find_reusable_bybit_csv(
            root_str=str(root),
            symbol=symbol,
            interval=interval,
            start_iso=start_dt.isoformat(),
            end_iso=end_dt.isoformat(),
        )
        rows.append(
            {
                "timeframe": label,
                "status": "local" if hit else "missing",
                "coverage_end": hit.coverage_end.strftime("%Y-%m-%d %H:%M") if hit else "-",
                "rows": f"{hit.requested_rows:,}" if hit else "-",
                "small_gaps": str(hit.missing_rows) if hit else "-",
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=285)


def render_bybit_download_panel(root: Path) -> None:
    with st.expander("Download Bybit candles", expanded=False):
        instrument_options = [
            "BTCUSDT",
            "ETHUSDT",
            "SOLUSDT",
            "XRPUSDT",
            "SUIUSDT",
            "Custom",
        ]
        col1, col2, col3 = st.columns(3)
        instrument_pick = col1.selectbox("Instrument", options=instrument_options, index=0)
        symbol_default = instrument_pick if instrument_pick != "Custom" else st.session_state.get(
            "bybit_custom_symbol",
            "BTCUSDT",
        )
        symbol = col2.text_input("Symbol", value=symbol_default).upper().strip()
        if instrument_pick == "Custom":
            st.session_state["bybit_custom_symbol"] = symbol
        interval = col3.selectbox("Interval", options=SUPPORTED_INTERVALS, index=0)
        category = st.selectbox("Category", options=["linear"], index=0)

        default_start = date.today().replace(day=max(1, date.today().day - 7))
        default_end = date.today()
        date_col1, date_col2 = st.columns(2)
        start_date = date_col1.date_input("Start date", value=default_start)
        end_date = date_col2.date_input("End date", value=default_end)
        time_col1, time_col2 = st.columns(2)
        start_time = time_col1.time_input("Start time (UTC)", value=time(0, 0))
        end_time = time_col2.time_input("End time (UTC)", value=time(23, 59))

        start_dt = datetime.combine(start_date, start_time, tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date, end_time, tzinfo=timezone.utc)

        output_hint = default_output_path(
            root=root,
            symbol=symbol,
            interval=interval,
            start=start_dt,
            end=end_dt,
        )
        output_raw = st.text_input("Output CSV", value=str(output_hint))
        cache_hit = find_reusable_bybit_csv(
            root=root,
            symbol=symbol,
            interval=interval,
            start=start_dt,
            end=end_dt,
        )
        if cache_hit is not None:
            st.success(
                "Локальный валидный CSV уже найден. "
                f"Можно использовать его без новой загрузки: {cache_hit.path}"
            )
            st.caption(
                f"Coverage: {cache_hit.coverage_start} -> {cache_hit.coverage_end} | "
                f"validated until: {cache_hit.effective_end} | "
                f"requested candles: {cache_hit.requested_rows}/{cache_hit.expected_rows} | "
                f"small gaps: {cache_hit.missing_rows}"
            )
        else:
            st.caption("Под выбранный диапазон полного локального CSV пока не найдено. Будет загрузка из Bybit.")

        if st.button("Download from Bybit", type="primary"):
            if cache_hit is not None:
                st.success(f"Используем локальный кэш без новой загрузки: {cache_hit.path}")
                st.info("Этот CSV уже покрывает выбранный диапазон полностью и без пропусков.")
                activate_market_dataset(
                    path=cache_hit.path,
                    symbol=symbol,
                    interval=interval,
                    start_date=start_date,
                    end_date=end_date,
                )
                st.rerun()
            else:
                progress_bar = st.progress(0.0, text="Готовим загрузку из Bybit...")
                status_box = st.empty()

                def on_progress(update: DownloadProgress) -> None:
                    ratio = min(1.0, update.current_chunk / max(1, update.total_chunks))
                    progress_bar.progress(
                        ratio,
                        text=(
                            f"Chunk {update.current_chunk}/{update.total_chunks} | "
                            f"rows {update.rows_downloaded} | "
                            f"{update.current_start:%Y-%m-%d %H:%M} -> {update.current_end:%Y-%m-%d %H:%M} UTC"
                        ),
                    )
                    status_box.info(
                        "Тянем свежие данные из Bybit. "
                        f"Прогресс: {update.current_chunk}/{update.total_chunks} чанков."
                    )

                try:
                    saved = save_bybit_klines_csv(
                        output_path=Path(output_raw).expanduser(),
                        symbol=symbol,
                        interval=interval,
                        start=start_dt,
                        end=end_dt,
                        category=category,
                        progress_callback=on_progress,
                    )
                except Exception as exc:
                    progress_bar.empty()
                    status_box.empty()
                    st.error(f"Download failed: {exc}")
                else:
                    progress_bar.progress(1.0, text="Загрузка завершена")
                    status_box.success("Данные успешно загружены и сохранены локально.")
                    st.success(f"Saved candles to {saved}")
                    st.info("Файл сразу выбран как активный источник данных.")
                    activate_market_dataset(
                        path=saved,
                        symbol=symbol,
                        interval=interval,
                        start_date=start_date,
                        end_date=end_date,
                    )
                    st.rerun()


def render_managed_market_source(root: Path, initial_data: Optional[Path]) -> dict:
    default_symbol = st.session_state.get("managed_symbol", "BTCUSDT")
    default_timeframe = st.session_state.get("managed_timeframe", "1m")
    today = date.today()
    default_start = st.session_state.get("managed_start_date", today - timedelta(days=7))
    default_end = st.session_state.get("managed_end_date", today)

    symbol_pick = st.selectbox("Инструмент", options=MANAGED_SYMBOL_OPTIONS, index=max(0, MANAGED_SYMBOL_OPTIONS.index(default_symbol)) if default_symbol in MANAGED_SYMBOL_OPTIONS else 0)
    timeframe_label = st.selectbox(
        "Таймфрейм",
        options=list(MANAGED_TIMEFRAME_OPTIONS.keys()),
        index=max(0, list(MANAGED_TIMEFRAME_OPTIONS.keys()).index(default_timeframe)) if default_timeframe in MANAGED_TIMEFRAME_OPTIONS else 0,
    )
    date_col1, date_col2 = st.columns(2)
    start_date = date_col1.date_input("Начало", value=default_start, key="managed_start_date")
    end_date = date_col2.date_input("Конец", value=default_end, key="managed_end_date")

    st.session_state["managed_symbol"] = symbol_pick
    st.session_state["managed_timeframe"] = timeframe_label

    interval = MANAGED_TIMEFRAME_OPTIONS[timeframe_label]
    start_dt = datetime.combine(start_date, time(0, 0), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date, time(23, 59), tzinfo=timezone.utc)
    output_hint = build_managed_output_path(root=root, symbol=symbol_pick, interval=interval, start=start_dt, end=end_dt)
    cache_hit = find_reusable_bybit_csv(root=root, symbol=symbol_pick, interval=interval, start=start_dt, end=end_dt)
    signature = build_market_request_signature(symbol_pick, timeframe_label, start_date, end_date)

    current_path = ""
    if cache_hit is not None:
        current_path = str(cache_hit.path)
        st.success(f"Локальные данные уже есть: {cache_hit.path.name}")
        st.caption(
            f"Coverage: {cache_hit.coverage_start:%Y-%m-%d %H:%M} -> {cache_hit.coverage_end:%Y-%m-%d %H:%M} UTC | "
            f"validated until {cache_hit.effective_end:%Y-%m-%d %H:%M} UTC | "
            f"small gaps: {cache_hit.missing_rows}"
        )
    elif st.session_state.get("managed_request_signature") == signature:
        remembered_path = st.session_state.get("managed_data_path", "")
        if remembered_path and Path(remembered_path).exists():
            current_path = remembered_path

    if not current_path:
        st.warning("Под выбранный символ, таймфрейм и даты локальный CSV пока не найден.")

    fetch_now = st.button("Load Market Data", type="primary", use_container_width=True)
    if fetch_now:
        if cache_hit is not None:
            st.session_state["managed_data_path"] = str(cache_hit.path)
            st.session_state["managed_request_signature"] = signature
            st.rerun()
        progress_bar = st.progress(0.0, text="Готовим загрузку market data...")
        status_box = st.empty()

        def on_progress(update: DownloadProgress) -> None:
            ratio = min(1.0, update.current_chunk / max(1, update.total_chunks))
            progress_bar.progress(
                ratio,
                text=(
                    f"Chunk {update.current_chunk}/{update.total_chunks} | "
                    f"rows {update.rows_downloaded} | "
                    f"{update.current_start:%Y-%m-%d %H:%M} -> {update.current_end:%Y-%m-%d %H:%M} UTC"
                ),
            )
            status_box.info(f"Тянем {symbol_pick} {timeframe_label} из Bybit...")

        try:
            saved = save_bybit_klines_csv(
                output_path=output_hint,
                symbol=symbol_pick,
                interval=interval,
                start=start_dt,
                end=end_dt,
                category="linear",
                progress_callback=on_progress,
            )
        except Exception as exc:
            progress_bar.empty()
            status_box.empty()
            st.error(f"Не удалось загрузить market data: {exc}")
        else:
            progress_bar.progress(1.0, text="Загрузка завершена")
            status_box.success("Данные готовы.")
            st.session_state["managed_data_path"] = str(saved)
            st.session_state["managed_request_signature"] = signature
            st.rerun()

    if not current_path and initial_data:
        current_path = str(initial_data.resolve())

    return {
        "data_path": current_path,
        "symbol": symbol_pick,
        "timeframe": timeframe_label,
    }


def activate_market_dataset(path: Path, symbol: str, interval: str, start_date: date, end_date: date) -> None:
    timeframe_label = interval_to_timeframe_label(interval)
    st.session_state["source_mode"] = "Managed Market Data"
    st.session_state["managed_symbol"] = symbol.upper()
    st.session_state["managed_timeframe"] = timeframe_label
    st.session_state["managed_start_date"] = start_date
    st.session_state["managed_end_date"] = end_date
    st.session_state["managed_data_path"] = str(path)
    st.session_state["managed_request_signature"] = build_market_request_signature(
        symbol.upper(),
        timeframe_label,
        start_date,
        end_date,
    )
    st.session_state.pop("selected_market_context", None)
    st.session_state.pop("draft_market_context", None)
    st.session_state.pop("jump_to_market_context", None)
    st.session_state["main_section"] = "📈 Market Research"
    st.session_state["_nav_programmatic"] = True


def interval_to_timeframe_label(interval: str) -> str:
    normalized = str(interval)
    for label, value in MANAGED_TIMEFRAME_OPTIONS.items():
        if value == normalized:
            return label
    if normalized == "D":
        return "1d"
    return f"{normalized}m"


def build_market_request_signature(symbol: str, timeframe: str, start_date: date, end_date: date) -> str:
    return f"{symbol}|{timeframe}|{start_date.isoformat()}|{end_date.isoformat()}"


def build_managed_output_path(root: Path, symbol: str, interval: str, start: datetime, end: datetime) -> Path:
    return root / "data" / f"bybit_{symbol.lower()}_{interval}_{start:%Y%m%d}_{end:%Y%m%d}.csv"


def inject_app_styles() -> None:
    """Inject the unified OneProp-style dark theme and all custom component styles."""
    css = _build_theme_css()
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _build_theme_css() -> str:
    """OneProp-style dark theme — единый стиль приложения."""

    # --- OneProp Dark Theme tokens ---
    bg_primary = "#0b0d12"
    bg_secondary = "#12151d"
    bg_tertiary = "#1c212e"
    bg_card = "linear-gradient(180deg, rgba(18,21,29,0.98), rgba(11,13,18,0.98))"
    bg_metric = "linear-gradient(180deg, rgba(18,21,29,0.95), rgba(11,13,18,0.95))"
    bg_metric_accent = f"radial-gradient(circle at 18% 0%, rgba(14,165,105,0.18), transparent 34%), {bg_metric}"
    text_primary = "#f0f2f5"
    text_secondary = "#8c93a1"
    text_muted = "rgba(255,255,255,0.55)"
    accent = "#0ea569"
    accent_dim = "rgba(14,165,105,0.35)"
    accent_glow = "rgba(14,165,105,0.15)"
    border = "rgba(255,255,255,0.08)"
    border_hover = "rgba(14,165,105,0.35)"
    shadow_sm = "0 4px 12px rgba(0,0,0,0.3)"
    shadow_md = "0 4px 20px rgba(0,255,136,0.10)"
    kicker = "#0ea569"
    sidebar_bg = "#0f1117"
    input_bg = "rgba(255,255,255,0.04)"
    input_border = "rgba(255,255,255,0.10)"
    tab_active_bg = accent
    tab_active_text = "#fff"
    tab_inactive_bg = "rgba(255,255,255,0.04)"
    tab_inactive_text = text_secondary
    btn_primary_bg = accent
    btn_primary_text = "#000"
    btn_secondary_bg = "rgba(255,255,255,0.06)"
    btn_secondary_text = text_primary
    btn_secondary_border = border
    expander_bg = "rgba(255,255,255,0.02)"
    success_bg = "rgba(14,165,105,0.12)"
    success_border = "rgba(14,165,105,0.30)"
    success_text = "#10b981"
    info_bg = "rgba(59,130,246,0.08)"
    info_border = "rgba(59,130,246,0.25)"
    info_text = "#60a5fa"
    warning_bg = "rgba(242,201,76,0.10)"
    warning_border = "rgba(242,201,76,0.25)"
    warning_text = "#F2C94C"
    error_bg = "rgba(255,51,102,0.10)"
    error_border = "rgba(255,51,102,0.25)"
    error_text = "#ff3366"
    signal_long_bg = "#0ea569"
    signal_long_text = "#0b1518"
    signal_short_bg = "#ff3366"
    signal_short_text = "#fff"
    signal_flat_bg = "#d1d4dc"
    signal_flat_text = "#111827"
    scrollbar_thumb = "rgba(255,255,255,0.12)"
    scrollbar_track = "transparent"

    return f"""
    /* ===== STREAMLIT CSS VARIABLE OVERRIDES ===== */
    :root {{
        --primary-color: {accent};
        --font: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }}

    /* ===== GLOBAL ===== */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

    html, body, .stApp {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }}

    [data-testid="stToolbar"] {{
        background-color: transparent !important;
    }}

    /* Popover / dropdown menus */
    div[data-baseweb="popover"] > div,
    ul[data-baseweb="menu"],
    div[data-baseweb="popover"] {{
        border-radius: 12px !important;
    }}

    .block-container {{
        padding-top: 1.25rem;
        padding-bottom: 2rem;
        max-width: 1780px;
    }}

    /* ===== SCROLLBAR ===== */
    ::-webkit-scrollbar {{
        width: 6px;
        height: 6px;
    }}
    ::-webkit-scrollbar-thumb {{
        background: {scrollbar_thumb};
        border-radius: 3px;
    }}
    ::-webkit-scrollbar-track {{
        background: {scrollbar_track};
    }}

    /* ===== SIDEBAR ===== */
    section[data-testid="stSidebar"] .stMarkdown {{
        color: var(--text-color);
    }}

    /* ===== HEADINGS ===== */
    .stApp h1, .stApp h2, .stApp h3 {{
        color: var(--text-color) !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em;
    }}
    .stApp h1 {{
        font-size: 1.75rem !important;
    }}
    .stApp h2 {{
        font-size: 1.35rem !important;
    }}
    .stApp h3 {{
        font-size: 1.10rem !important;
    }}
    .stCaption, .stApp .stMarkdown p {{
        color: var(--text-color) !important;
        opacity: 0.7;
    }}

    /* ===== METRICS ===== */
    div[data-testid="stMetric"] {{
        background: var(--secondary-background-color);
        border: 1px solid rgba(128,128,128,0.15);
        border-radius: 14px;
        padding: 0.7rem 0.9rem;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        transition: all 0.2s ease;
    }}
    div[data-testid="stMetric"]:hover {{
        border-color: var(--primary-color);
        box-shadow: 0 4px 16px rgba(0,0,0,0.12);
    }}
    div[data-testid="stMetric"] label {{
        color: var(--text-color) !important;
        opacity: 0.55;
        font-size: 0.72rem !important;
        font-weight: 650 !important;
        letter-spacing: 0.04em !important;
        text-transform: uppercase !important;
    }}
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {{
        color: var(--text-color) !important;
        font-weight: 760 !important;
    }}
    div[data-testid="stMetric"] [data-testid="stMetricDelta"] {{
        font-weight: 600 !important;
    }}

    /* ===== TABS ===== */
    div[data-baseweb="tab-list"] {{
        gap: 0.35rem;
        border-radius: 12px;
        padding: 4px;
    }}
    button[data-baseweb="tab"] {{
        border-radius: 10px !important;
        padding: 0.45rem 0.85rem !important;
        font-weight: 600 !important;
        font-size: 0.82rem !important;
        letter-spacing: 0.01em !important;
        transition: all 0.15s ease !important;
        border: 1px solid transparent !important;
    }}
    button[data-baseweb="tab"][aria-selected="true"] {{
        background: var(--primary-color) !important;
        color: #fff !important;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15) !important;
    }}

    /* ===== RADIO (horizontal section switcher) ===== */
    div[data-testid="stRadio"] > div {{
        gap: 0.3rem !important;
    }}
    div[data-testid="stRadio"] label {{
        border: 1px solid rgba(128,128,128,0.15) !important;
        border-radius: 10px !important;
        padding: 0.45rem 0.95rem !important;
        font-weight: 600 !important;
        font-size: 0.82rem !important;
        transition: all 0.15s ease !important;
        cursor: pointer !important;
    }}
    div[data-testid="stRadio"] label:has(input:checked) {{
        background: var(--primary-color) !important;
        color: #fff !important;
        border-color: var(--primary-color) !important;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15) !important;
    }}

    /* ===== BUTTONS ===== */
    .stButton > button {{
        border-radius: 10px !important;
        font-weight: 600 !important;
        font-size: 0.82rem !important;
        letter-spacing: 0.01em !important;
        padding: 0.48rem 1rem !important;
        transition: all 0.15s ease !important;
        border: 1px solid rgba(128,128,128,0.15) !important;
    }}
    .stButton > button[kind="primary"],
    .stButton > button[data-testid*="primary"] {{
        background: var(--primary-color) !important;
        color: #000 !important;
        border-color: var(--primary-color) !important;
    }}
    .stButton > button[kind="primary"]:hover,
    .stButton > button[data-testid*="primary"]:hover {{
        box-shadow: 0 4px 16px rgba(0,0,0,0.15) !important;
        transform: translateY(-1px);
    }}

    /* ===== INPUTS ===== */
    .stTextInput input, .stNumberInput input,
    .stDateInput input, .stTextArea textarea {{
        border-radius: 10px !important;
    }}
    .stTextInput input:focus, .stNumberInput input:focus,
    .stDateInput input:focus, .stTextArea textarea:focus {{
        border-color: var(--primary-color) !important;
        box-shadow: 0 0 0 2px rgba(14,165,105,0.2) !important;
    }}

    /* ===== SELECTBOX ===== */
    div[data-baseweb="select"] {{
        border-radius: 10px !important;
    }}
    div[data-baseweb="select"] > div {{
        border-radius: 10px !important;
    }}

    /* ===== SLIDER ===== */
    .stSlider [data-baseweb="slider"] div[role="slider"] {{
        background: var(--primary-color) !important;
    }}

    /* ===== EXPANDER ===== */
    details[data-testid="stExpander"] {{
        border: 1px solid rgba(128,128,128,0.12) !important;
        border-radius: 14px !important;
        overflow: hidden;
    }}
    details[data-testid="stExpander"] summary {{
        font-weight: 600 !important;
    }}

    /* ===== DATAFRAME ===== */
    .stDataFrame {{
        border: 1px solid rgba(128,128,128,0.12) !important;
        border-radius: 12px !important;
        overflow: hidden;
    }}

    /* ===== ALERTS ===== */
    div[data-testid="stAlert"][data-baseweb*="success"],
    .stSuccess {{
        background: {success_bg} !important;
        border-color: {success_border} !important;
        color: {success_text} !important;
        border-radius: 12px !important;
    }}
    div[data-testid="stAlert"][data-baseweb*="info"],
    .stInfo {{
        background: {info_bg} !important;
        border-color: {info_border} !important;
        color: {info_text} !important;
        border-radius: 12px !important;
    }}
    div[data-testid="stAlert"][data-baseweb*="warning"],
    .stWarning {{
        background: {warning_bg} !important;
        border-color: {warning_border} !important;
        color: {warning_text} !important;
        border-radius: 12px !important;
    }}
    div[data-testid="stAlert"][data-baseweb*="error"],
    .stError {{
        background: {error_bg} !important;
        border-color: {error_border} !important;
        color: {error_text} !important;
        border-radius: 12px !important;
    }}

    /* ===== CUSTOM CARDS ===== */
    .workspace-card {{
        background: var(--secondary-background-color);
        border: 1px solid rgba(128,128,128,0.15);
        border-radius: 18px;
        padding: 0.9rem 1rem;
        margin-bottom: 1rem;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        transition: all 0.2s ease;
    }}
    .workspace-card:hover {{
        border-color: var(--primary-color);
        box-shadow: 0 4px 16px rgba(0,0,0,0.12);
    }}
    .workspace-kicker {{
        font-size: 0.78rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--primary-color);
        margin-bottom: 0.25rem;
        font-weight: 700;
    }}
    .workspace-title {{
        font-size: 1.1rem;
        font-weight: 700;
        margin-bottom: 0.2rem;
        color: var(--text-color);
    }}
    .workspace-meta {{
        color: var(--text-color);
        opacity: 0.6;
        font-size: 0.88rem;
    }}

    /* ===== TOP DASHBOARD ===== */
    .top-dashboard {{
        display: grid;
        grid-template-columns: repeat(6, minmax(0, 1fr));
        gap: 0.75rem;
        margin: 0.85rem 0 1rem;
    }}
    .top-dashboard-card {{
        min-height: 82px;
        background: var(--secondary-background-color);
        border: 1px solid rgba(128,128,128,0.15);
        border-radius: 16px;
        padding: 0.75rem 0.85rem;
        overflow: hidden;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        transition: all 0.2s ease;
    }}
    .top-dashboard-card:hover {{
        border-color: var(--primary-color);
        box-shadow: 0 4px 16px rgba(0,0,0,0.12);
        transform: translateY(-2px);
    }}
    .top-dashboard-label {{
        color: var(--text-color);
        opacity: 0.55;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        margin-bottom: 0.35rem;
    }}
    .top-dashboard-value {{
        color: var(--text-color);
        font-size: 1.55rem;
        font-weight: 780;
        line-height: 1.08;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .top-dashboard-sub {{
        color: var(--text-color);
        opacity: 0.45;
        font-size: 0.78rem;
        margin-top: 0.35rem;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    @media (max-width: 1100px) {{
        .top-dashboard {{
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }}
    }}

    /* ===== MODEL PANEL ===== */
    .model-panel {{
        border: 1px solid rgba(128,128,128,0.15);
        border-radius: 20px;
        background: var(--secondary-background-color);
        padding: 0.95rem;
        margin: 0 0 1.1rem;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }}
    .model-panel-head {{
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: 1rem;
        margin-bottom: 0.75rem;
    }}
    .model-panel-title {{
        font-size: 1.03rem;
        font-weight: 760;
        letter-spacing: 0.01em;
        color: var(--text-color);
    }}
    .model-panel-note {{
        color: var(--text-color);
        opacity: 0.6;
        font-size: 0.82rem;
    }}
    .model-grid {{
        display: grid;
        grid-template-columns: 1.25fr repeat(6, minmax(0, 1fr));
        gap: 0.65rem;
    }}
    .model-card {{
        background: var(--secondary-background-color);
        border: 1px solid rgba(128,128,128,0.12);
        border-radius: 15px;
        padding: 0.72rem 0.78rem;
        min-height: 88px;
        transition: all 0.2s ease;
    }}
    .model-card:hover {{
        border-color: var(--primary-color);
    }}
    .model-card-primary {{
        background: var(--secondary-background-color);
    }}
    .model-label {{
        color: var(--text-color);
        opacity: 0.55;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        margin-bottom: 0.35rem;
    }}
    .model-value {{
        color: var(--text-color);
        font-size: 1.35rem;
        font-weight: 780;
        line-height: 1.08;
    }}
    .model-sub {{
        color: var(--text-color);
        opacity: 0.45;
        font-size: 0.74rem;
        margin-top: 0.38rem;
    }}
    @media (max-width: 1350px) {{
        .model-grid {{
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }}
    }}

    /* ===== SIGNAL PILLS ===== */
    .signal-pill {{
        display: inline-block;
        border-radius: 999px;
        padding: 0.24rem 0.58rem;
        font-size: 0.82rem;
        font-weight: 780;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }}
    .signal-long {{
        color: {signal_long_text};
        background: {signal_long_bg};
    }}
    .signal-short {{
        color: {signal_short_text};
        background: {signal_short_bg};
    }}
    .signal-flat {{
        color: {signal_flat_text};
        background: {signal_flat_bg};
    }}

    /* ===== CHECKBOX ===== */
    .stCheckbox label span {{
        color: var(--text-color) !important;
    }}

    /* ===== PROGRESS ===== */
    .stProgress > div > div > div {{
        background-color: var(--primary-color) !important;
    }}

    /* ===== DIVIDER ===== */
    hr {{
        border-color: rgba(128,128,128,0.15) !important;
    }}
    """


def render_workspace_overview(
    data_path: Path,
    artifacts_dir: Optional[Path],
    current_symbol: str,
    current_timeframe: str,
    total_rows: int,
    include_model: bool,
) -> None:
    col1, col2 = st.columns([1.4, 1])
    with col1:
        st.markdown(
            (
                '<div class="workspace-card">'
                '<div class="workspace-kicker">Current Workspace</div>'
                f'<div class="workspace-title">{current_symbol or "Instrument"} · {current_timeframe or "timeframe"}</div>'
                f'<div class="workspace-meta">{data_path}</div>'
                "</div>"
            ),
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            (
                '<div class="workspace-card">'
                '<div class="workspace-kicker">Execution State</div>'
                f'<div class="workspace-title">{"Model attached" if include_model else "Chart research mode"}</div>'
                f'<div class="workspace-meta">Rows: {total_rows:,} · Artifacts: {artifacts_dir if artifacts_dir else "-"}</div>'
                "</div>"
            ),
            unsafe_allow_html=True,
        )


def render_top_research_dashboard(
    dataset: pd.DataFrame,
    data_path: Path,
    current_symbol: str,
    current_timeframe: str,
    include_model: bool,
) -> None:
    if dataset.empty:
        return
    first_close = float(dataset["close"].iloc[0])
    last_close = float(dataset["close"].iloc[-1])
    price_change = (last_close / first_close - 1) * 100 if first_close else 0.0
    avg_volume = float(dataset["volume"].mean()) if "volume" in dataset.columns else 0.0
    avg_range = float(dataset["range_pct"].mean() * 100) if "range_pct" in dataset.columns else 0.0
    ts_start = dataset["timestamp"].iloc[0]
    ts_end = dataset["timestamp"].iloc[-1]
    cards = [
        ("Инструмент", f"{current_symbol} · {current_timeframe}", "активный рабочий график"),
        ("Свечей", f"{len(dataset):,}", f"{ts_start} → {ts_end}"),
        ("Изменение", f"{price_change:.2f}%", "по текущему локальному датасету"),
        ("Средний объем", f"{avg_volume:,.0f}", "volume per candle"),
        ("Средний range", f"{avg_range:.3f}%", "high-low / close"),
        ("Режим", "Model" if include_model else "Chart", str(data_path)),
    ]
    html = '<div class="top-dashboard">'
    for label, value, sub in cards:
        html += (
            '<div class="top-dashboard-card">'
            f'<div class="top-dashboard-label">{label}</div>'
            f'<div class="top-dashboard-value">{value}</div>'
            f'<div class="top-dashboard-sub">{sub}</div>'
            '</div>'
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def render_model_metrics_panel(dataset: pd.DataFrame, artifacts_dir: Optional[Path], include_model: bool) -> None:
    train_summary = cached_load_summary(str(artifacts_dir), "train_summary.json") if artifacts_dir else None
    backtest_summary = cached_load_summary(str(artifacts_dir), "backtest_summary.json") if artifacts_dir else None
    has_probs = {"prob_short", "prob_flat", "prob_long", "predicted_signal"}.issubset(dataset.columns)

    def pct(value: object, digits: int = 1) -> str:
        try:
            return f"{float(value) * 100:.{digits}f}%"
        except (TypeError, ValueError):
            return "-"

    def num(value: object, digits: int = 2) -> str:
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return "-"

    if has_probs and not dataset.empty:
        latest = dataset.iloc[-1]
        signal = str(latest.get("predicted_signal", "flat"))
        signal_class = f"signal-{signal}" if signal in {"long", "short", "flat"} else "signal-flat"
        signal_text = f'<span class="signal-pill {signal_class}">{signal}</span>'
        confidence = max(float(latest["prob_short"]), float(latest["prob_flat"]), float(latest["prob_long"]))
        recent = dataset.tail(min(120, len(dataset)))
        long_prob = float(recent["prob_long"].mean())
        short_prob = float(recent["prob_short"].mean())
        flat_prob = float(recent["prob_flat"].mean())
        bias = long_prob - short_prob
        active_rate = float((recent["predicted_signal"] != "flat").mean())
        bias_label = "LONG" if bias > 0.03 else ("SHORT" if bias < -0.03 else "FLAT")
        model_note = "последняя свеча + последние 120 баров"
    else:
        signal_text = '<span class="signal-pill signal-flat">offline</span>'
        confidence = None
        long_prob = short_prob = flat_prob = active_rate = None
        bias_label = "NO MODEL"
        model_note = "подключи artifacts/model.pt, чтобы видеть live probabilities"

    final_metrics = (train_summary or {}).get("final_metrics", {}) if isinstance(train_summary, dict) else {}
    cards = [
        ("Сигнал", signal_text, model_note, True),
        ("Confidence", pct(confidence), "max(prob short/flat/long)", False),
        ("Bias", bias_label, f"L {pct(long_prob)} · S {pct(short_prob)}", False),
        ("Flat prob", pct(flat_prob), "среднее за окно", False),
        ("Active rate", pct(active_rate), "не flat за окно", False),
        ("Hit rate", pct((backtest_summary or {}).get("hit_rate") if isinstance(backtest_summary, dict) else None), "backtest", False),
        ("Max DD", pct((backtest_summary or {}).get("max_drawdown") if isinstance(backtest_summary, dict) else None), "backtest drawdown", False),
    ]

    secondary = [
        ("Return", pct((backtest_summary or {}).get("total_return") if isinstance(backtest_summary, dict) else None), "total backtest"),
        ("Trades", f"{int((backtest_summary or {}).get('trades', 0)):,}" if isinstance(backtest_summary, dict) else "-", "backtest count"),
        ("Valid acc", pct(final_metrics.get("valid_accuracy")), "train validation"),
        ("Long P", pct(final_metrics.get("long_precision")), "precision"),
        ("Short P", pct(final_metrics.get("short_precision")), "precision"),
        ("Sharpe-like", num((backtest_summary or {}).get("sharpe_like") if isinstance(backtest_summary, dict) else None, 2), "rough quality"),
    ]

    html = (
        '<div class="model-panel">'
        '<div class="model-panel-head">'
        '<div class="model-panel-title">Model Intelligence</div>'
        '<div class="model-panel-note">probabilities, bias, backtest health</div>'
        '</div>'
        '<div class="model-grid">'
    )
    for label, value, sub, primary in cards:
        html += (
            f'<div class="model-card {"model-card-primary" if primary else ""}">'
            f'<div class="model-label">{label}</div>'
            f'<div class="model-value">{value}</div>'
            f'<div class="model-sub">{sub}</div>'
            '</div>'
        )
    html += "</div>"
    html += '<div class="model-grid" style="grid-template-columns: repeat(6, minmax(0, 1fr)); margin-top: 0.65rem;">'
    for label, value, sub in secondary:
        html += (
            '<div class="model-card">'
            f'<div class="model-label">{label}</div>'
            f'<div class="model-value">{value}</div>'
            f'<div class="model-sub">{sub}</div>'
            '</div>'
        )
    html += "</div></div>"
    st.markdown(html, unsafe_allow_html=True)


def resolve_view_window_bars(timeframe: str, scale: str) -> int:
    minutes_per_bar = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }.get(timeframe, 1)
    scale_minutes = {
        "3 часа": 180,
        "1 час": 60,
        "4 часа": 240,
        "6 часов": 360,
        "12 часов": 720,
        "1 день": 1440,
        "3 дня": 4320,
        "7 дней": 10080,
        "14 дней": 20160,
        "30 дней": 43200,
        "90 дней": 129600,
        "6 месяцев": 259200,
        "Вся история": 10**12,
    }.get(scale, 1440)
    return max(50, int(scale_minutes / minutes_per_bar))


def find_start_index_for_date(dataset: pd.DataFrame, target_date: date) -> int:
    target_ts = pd.Timestamp(datetime.combine(target_date, time(0, 0), tzinfo=timezone.utc))
    index = int(dataset["timestamp"].searchsorted(target_ts, side="left"))
    return max(0, min(index, max(0, len(dataset) - 1)))


def find_end_index_for_date(dataset: pd.DataFrame, target_date: date) -> int:
    next_day_ts = pd.Timestamp(datetime.combine(target_date + timedelta(days=1), time(0, 0), tzinfo=timezone.utc))
    index = int(dataset["timestamp"].searchsorted(next_day_ts, side="left")) - 1
    return max(0, min(index, max(0, len(dataset) - 1)))


def resolve_default_start_for_scale(dataset: pd.DataFrame, target_date: date, scale: str, window: int) -> int:
    if scale in {"6 месяцев", "Вся история"}:
        return 0
    end_index = find_end_index_for_date(dataset, target_date)
    return max(0, end_index - window + 1)


def set_market_view_date(state_key: str, input_key: str, value: date) -> None:
    st.session_state[state_key] = value
    st.session_state[input_key] = value
    st.session_state.pop("selected_market_context", None)


def set_market_scale(scale_key: str, input_key: str, value: str) -> None:
    st.session_state[scale_key] = value
    st.session_state[input_key] = value
    st.session_state.pop("selected_market_context", None)
    for key in list(st.session_state.keys()):
        if str(key).startswith(("market_window_", "market_start_")):
            st.session_state.pop(key, None)


def set_market_timeframe(timeframe: str) -> None:
    st.session_state["managed_timeframe"] = timeframe
    st.session_state["pending_market_timeframe"] = timeframe
    st.session_state["market_browser_timeframe_nonce"] = int(
        st.session_state.get("market_browser_timeframe_nonce", 0)
    ) + 1
    st.session_state["pending_market_scale"] = TIMEFRAME_DEFAULT_SCALE.get(timeframe, "7 дней")
    st.session_state.pop("managed_data_path", None)
    st.session_state.pop("managed_request_signature", None)
    st.session_state.pop("selected_market_context", None)
    for key in list(st.session_state.keys()):
        if str(key).startswith(("market_view_scale_", "market_window_", "market_start_")):
            st.session_state.pop(key, None)
    cached_lookup = globals().get("cached_find_reusable_bybit_csv")
    if cached_lookup is not None and hasattr(cached_lookup, "clear"):
        cached_lookup.clear()


def adjacent_market_scale(current_scale: str, direction: int) -> str:
    if current_scale not in CHART_SCALE_ZOOM_SEQUENCE:
        current_scale = "6 месяцев"
    index = CHART_SCALE_ZOOM_SEQUENCE.index(current_scale)
    next_index = max(0, min(len(CHART_SCALE_ZOOM_SEQUENCE) - 1, index + direction))
    return CHART_SCALE_ZOOM_SEQUENCE[next_index]


def build_display_chart_frame(view: pd.DataFrame, max_points: int = 4000) -> tuple[pd.DataFrame, int]:
    if len(view) <= max_points:
        if "bucket_end_index" not in view.columns:
            view = view.copy()
            view["bucket_end_index"] = view["absolute_index"]
        return view, 1

    compression_ratio = int(np.ceil(len(view) / max_points))
    working = view.copy()
    working["_display_bucket"] = np.arange(len(working)) // compression_ratio

    aggregations: dict[str, object] = {
        "timestamp": ("timestamp", "first"),
        "open": ("open", "first"),
        "high": ("high", "max"),
        "low": ("low", "min"),
        "close": ("close", "last"),
        "volume": ("volume", "sum"),
        "absolute_index": ("absolute_index", "first"),
        "bucket_end_index": ("absolute_index", "last"),
    }
    for column in ["ret_fast", "volume_z", "range_pct", "prob_short", "prob_flat", "prob_long"]:
        if column in working.columns:
            aggregations[column] = (column, "mean")

    display = working.groupby("_display_bucket", as_index=False).agg(**aggregations)
    return display.drop(columns=["_display_bucket"], errors="ignore"), compression_ratio


def build_chart_fallback_dataset(data_path: Path) -> pd.DataFrame:
    frame = pd.read_csv(data_path)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"CSV не содержит нужные колонки для графика: {', '.join(sorted(missing))}")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    frame = frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if frame.empty:
        return frame

    frame["ret_1"] = frame["close"].pct_change().fillna(0.0)
    frame["ret_fast"] = frame["close"].pct_change(min(3, max(1, len(frame) - 1))).fillna(0.0)
    frame["ret_slow"] = frame["close"].pct_change(min(12, max(1, len(frame) - 1))).fillna(0.0)
    frame["range_pct"] = ((frame["high"] - frame["low"]) / frame["close"]).replace([np.inf, -np.inf], 0.0).fillna(0.0)
    frame["body_pct"] = ((frame["close"] - frame["open"]) / frame["open"]).replace([np.inf, -np.inf], 0.0).fillna(0.0)

    volume_window = min(20, max(2, len(frame)))
    volume_mean = frame["volume"].rolling(volume_window, min_periods=1).mean()
    volume_std = frame["volume"].rolling(volume_window, min_periods=2).std().replace(0, np.nan)
    frame["volume_z"] = ((frame["volume"] - volume_mean) / volume_std).replace([np.inf, -np.inf], 0.0).fillna(0.0)
    frame["volatility"] = frame["ret_1"].rolling(volume_window, min_periods=1).std().fillna(0.0)
    frame["volatility_z"] = 0.0
    frame["ema_gap_pct"] = 0.0
    frame["distance_fast_ema"] = 0.0
    frame["breakout_fast"] = 0.0
    frame["breakout_slow"] = 0.0
    frame["drawdown_fast"] = 0.0
    frame["target"] = 1
    return frame


def render_market_research(
    dataset: pd.DataFrame,
    feature_columns: list[str],
    include_model: bool,
    artifacts_dir: Optional[Path],
    data_path: Path,
    current_symbol: str,
    current_timeframe: str,
) -> dict:
    st.subheader("Диапазон исследования")
    total_rows = len(dataset)
    data_key = re.sub(r"[^a-zA-Z0-9]+", "_", str(data_path))[-80:]
    if "selection_mode" not in st.session_state:
        st.session_state["selection_mode"] = "pan"
    jump_context = st.session_state.get("jump_to_market_context")
    jump_for_this_dataset = bool(jump_context and jump_context.get("data_path") == str(data_path))
    min_chart_date = dataset["timestamp"].min().date()
    max_chart_date = dataset["timestamp"].max().date()
    view_date_key = f"market_view_date_{data_key}"
    view_date_input_key = f"{view_date_key}_input"
    view_scale_key = f"market_view_scale_{data_key}"
    view_scale_input_key = f"{view_scale_key}_input_v2"
    saved_scale = st.session_state.get(view_scale_key, "6 месяцев")
    pending_scale = st.session_state.pop("pending_market_scale", None)
    if pending_scale in CHART_SCALE_OPTIONS:
        saved_scale = pending_scale
        st.session_state[view_scale_key] = saved_scale
    if saved_scale is None:
        saved_scale = "6 месяцев"
        st.session_state[view_scale_key] = saved_scale
    if view_date_key not in st.session_state:
        st.session_state[view_date_key] = max_chart_date
    selected_view_date = st.session_state[view_date_key]
    if selected_view_date < min_chart_date or selected_view_date > max_chart_date:
        selected_view_date = max_chart_date
        st.session_state[view_date_key] = selected_view_date

    if jump_for_this_dataset:
        target_start = max(0, min(int(jump_context.get("window_start_idx", 0)), max(0, total_rows - 1)))
        target_end = max(target_start, min(int(jump_context.get("window_end_idx", target_start)), max(0, total_rows - 1)))
        default_window = max(50, min(1500, target_end - target_start + 1))
        default_window = min(default_window, total_rows)
        default_start = min(target_start, max(0, total_rows - default_window))
    else:
        selected_scale = saved_scale
        default_window = min(resolve_view_window_bars(current_timeframe, selected_scale), total_rows)
        default_start = resolve_default_start_for_scale(dataset, selected_view_date, selected_scale, default_window)

    nav_col1, nav_col2, nav_col3, nav_col4, nav_col5, nav_col6 = st.columns([1, 1, 1, 1.4, 0.65, 0.65])
    nav_col1.button(
        "← Previous day",
        use_container_width=True,
        key=f"market_prev_day_{data_key}",
        on_click=set_market_view_date,
        args=(view_date_key, view_date_input_key, max(min_chart_date, selected_view_date - timedelta(days=1))),
    )
    selected_view_date = nav_col2.date_input(
        "День на графике",
        value=selected_view_date,
        min_value=min_chart_date,
        max_value=max_chart_date,
        key=view_date_input_key,
    )
    st.session_state[view_date_key] = selected_view_date
    nav_col3.button(
        "Next day →",
        use_container_width=True,
        key=f"market_next_day_{data_key}",
        on_click=set_market_view_date,
        args=(view_date_key, view_date_input_key, min(max_chart_date, selected_view_date + timedelta(days=1))),
    )
    selected_scale = nav_col4.selectbox(
        "Масштаб окна",
        options=CHART_SCALE_OPTIONS,
        index=CHART_SCALE_OPTIONS.index(
            saved_scale
        )
        if saved_scale in CHART_SCALE_OPTIONS
        else CHART_SCALE_OPTIONS.index("6 месяцев"),
        key=view_scale_input_key,
    )
    st.session_state[view_scale_key] = selected_scale
    nav_col5.button(
        "Zoom +",
        use_container_width=True,
        key=f"market_zoom_in_{data_key}",
        on_click=set_market_scale,
        args=(view_scale_key, view_scale_input_key, adjacent_market_scale(selected_scale, -1)),
        help="Приблизить: открыть более короткий и детальный интервал.",
    )
    nav_col6.button(
        "Zoom -",
        use_container_width=True,
        key=f"market_zoom_out_{data_key}",
        on_click=set_market_scale,
        args=(view_scale_key, view_scale_input_key, adjacent_market_scale(selected_scale, 1)),
        help="Отдалить: показать больше истории.",
    )
    quick_scale_cols = st.columns([0.7, 0.75, 0.85, 0.85, 0.85, 0.9, 1.0, 1.15, 2.2])
    for index, (label, timeframe) in enumerate(
        [
            ("1m", "1m"),
            ("5m", "5m"),
            ("30m", "30m"),
            ("1h", "1h"),
            ("4h", "4h"),
            ("1D", "1d"),
            ("1W", "1w"),
        ]
    ):
        quick_scale_cols[index].button(
            label,
            type="primary" if current_timeframe == timeframe else "secondary",
            use_container_width=True,
            key=f"market_quick_timeframe_{label}_{data_key}",
            on_click=set_market_timeframe,
            args=(timeframe,),
            help=f"Переключить график на свечи {label} и показать всю доступную историю.",
        )
    quick_scale_cols[7].button(
        "Вся история",
        type="primary" if selected_scale == "Вся история" else "secondary",
        use_container_width=True,
        key=f"market_quick_scale_all_{data_key}",
        on_click=set_market_scale,
        args=(view_scale_key, view_scale_input_key, "Вся история"),
        help="Показать весь локально загруженный диапазон текущего таймфрейма.",
    )
    quick_scale_cols[-1].caption(
        "Быстрые кнопки меняют свечной таймфрейм. Вся история меняет только масштаб окна."
    )
    if not jump_for_this_dataset:
        default_window = min(resolve_view_window_bars(current_timeframe, selected_scale), total_rows)
        default_start = resolve_default_start_for_scale(dataset, selected_view_date, selected_scale, default_window)

    min_window = min(50, total_rows)
    max_window = total_rows
    default_window = max(min_window, min(default_window, max_window))
    window = default_window
    default_start = max(0, min(default_start, max(0, total_rows - window)))
    start = default_start
    with st.expander("Advanced navigation", expanded=False):
        st.caption(
            "Сейчас окно считается автоматически от выбранной даты и масштаба. "
            "Навигация внутри окна идет мышью на графике: drag/pan, колесо/trackpad для zoom."
        )
        debug_col1, debug_col2, debug_col3 = st.columns(3)
        debug_col1.metric("Window candles", f"{window:,}")
        debug_col2.metric("Start index", f"{start:,}")
        debug_col3.metric("End index", f"{min(start + window, total_rows):,}")
    end = start + window
    view = dataset.iloc[start:end].copy()
    view["absolute_index"] = list(range(start, end))
    chart_view, compression_ratio = build_display_chart_frame(view)
    if not view.empty:
        st.caption(
            f"На графике: {len(view):,}/{total_rows:,} свечей | "
            f"{view['timestamp'].iloc[0]} -> {view['timestamp'].iloc[-1]}"
        )
        if compression_ratio > 1:
            st.info(
                f"Для плавного TradingView-режима экран сжат в {compression_ratio}x: "
                f"отрисовано {len(chart_view):,} OHLC-бакетов из {len(view):,} свечей. "
                "История не обрезана, сжатие только визуальное."
            )

    if jump_for_this_dataset:
        st.info(
            f"Opened linked window: rows {jump_context['window_start_idx']}..{jump_context['window_end_idx']}, "
            f"time {jump_context.get('window_start_ts', '')} -> {jump_context.get('window_end_ts', '')}"
        )
        st.session_state.pop("jump_to_market_context", None)

    has_selection = bool(st.session_state.get("selected_market_context"))
    controls_col1, controls_col2, controls_col3, controls_col4 = st.columns([1.2, 1, 1.2, 0.8])
    if controls_col1.button(
        "🔍 Выделить импульс",
        type="primary" if st.session_state.get("selection_mode") == "box" else "secondary",
        use_container_width=True,
        key="market_box_select",
    ):
        st.session_state["selection_mode"] = "box"
        st.rerun()
    if controls_col2.button(
        "✋ Навигация",
        type="primary" if st.session_state.get("selection_mode") == "pan" else "secondary",
        use_container_width=True,
        key="market_pan_select",
    ):
        st.session_state["selection_mode"] = "pan"
        st.rerun()
    if controls_col3.button(
        "⚡ В лабораторию",
        type="primary" if has_selection else "secondary",
        use_container_width=True,
        key="market_send_to_lab",
    ):
        if has_selection:
            ctx = st.session_state.get("selected_market_context", {})
            st.session_state["impulse_lab_selection"] = {
                "start_idx": int(ctx.get("window_start_idx", 0)),
                "end_idx": int(ctx.get("window_end_idx", 0)),
                "data_path": str(ctx.get("data_path", "")),
                "symbol": ctx.get("symbol", ""),
                "timeframe": ctx.get("timeframe", ""),
                "start_ts": str(ctx.get("window_start_ts", "")),
                "end_ts": str(ctx.get("window_end_ts", "")),
            }
            st.session_state["main_section"] = "⚡ Impulse Lab"
            st.session_state["_nav_programmatic"] = True
            st.rerun()
        else:
            st.toast("⚠️ Сначала выдели участок импульса на графике")
    if controls_col4.button(
        "🗑 Сброс",
        type="secondary",
        use_container_width=True,
        key="market_clear_selection",
    ):
        st.session_state.pop("selected_market_context", None)
        st.session_state.pop("hypothesis_draft", None)
        st.session_state.pop("draft_market_context", None)
        st.rerun()
    if has_selection:
        st.success("✅ Участок выделен. Нажми «⚡ В лабораторию» для анализа импульса.")
    else:
        st.info("💡 Нажми «Выделить импульс» и обведи участок импульса на графике.")

    chart_col, desk_col = st.columns([2.35, 1.0], gap="large")
    with chart_col:
        active_selection_mode = st.session_state.get("selection_mode", "pan")
        chart = build_chart(
            chart_view,
            include_model=include_model,
            selection_mode=active_selection_mode,
        )
        chart_config = {
            "scrollZoom": True,
            "displaylogo": False,
            "displayModeBar": True,
            "modeBarButtonsToAdd": ["drawline", "drawrect", "eraseshape"],
        }
        if active_selection_mode == "box":
            chart_event = st.plotly_chart(
                chart,
                use_container_width=True,
                on_select="rerun",
                selection_mode=("box",),
                key=f"market_chart_select_{data_path}",
                config=chart_config,
            )
        else:
            render_pan_chart_with_price_scale_drag(chart, chart_config, key=f"market_chart_pan_{data_path}")
            chart_event = None
    selection_context = extract_selection_context(
        chart_event=chart_event,
        dataset=dataset,
        data_path=data_path,
        current_symbol=current_symbol,
        current_timeframe=current_timeframe,
        visible_start_idx=start,
        visible_end_idx=end - 1,
    )

    if selection_context is None:
        selection_context = st.session_state.get("selected_market_context")
        if selection_context and selection_context.get("data_path") != str(data_path):
            selection_context = None
        if selection_context and not is_context_inside_visible_window(
            selection_context,
            visible_start_idx=start,
            visible_end_idx=end - 1,
        ):
            selection_context = None
    else:
        st.session_state["selected_market_context"] = selection_context

    with desk_col:
        if selection_context is not None:
            render_selection_lab(
                dataset=dataset,
                selection_context=selection_context,
                current_symbol=current_symbol,
                current_timeframe=current_timeframe,
            )
        else:
            render_selection_empty_state(current_symbol=current_symbol, current_timeframe=current_timeframe)

    with st.expander("Feature Overlay", expanded=False):
        feature_pick = st.multiselect(
            "Показывать признаки",
            options=feature_columns,
            default=[],
        )
        if feature_pick:
            available_features = [column for column in feature_pick if column in chart_view.columns]
            if available_features:
                st.line_chart(chart_view.set_index("timestamp")[available_features])

    columns = ["timestamp", "open", "high", "low", "close", "volume", "target"] + feature_pick
    if include_model:
        columns += ["prob_short", "prob_flat", "prob_long", "predicted_signal"]
    with st.expander("Candles And Features Table", expanded=False):
        preview_columns = [column for column in columns if column in chart_view.columns]
        st.caption("Для скорости таблица показывает экранные OHLC-бакеты, а не все сырые свечи.")
        st.dataframe(chart_view[preview_columns].tail(2000), use_container_width=True, hide_index=True)

    if artifacts_dir:
        with st.expander("Training And Backtest Summary", expanded=False):
            train_summary = cached_load_summary(str(artifacts_dir), "train_summary.json")
            backtest_summary = cached_load_summary(str(artifacts_dir), "backtest_summary.json")
            summary_col1, summary_col2 = st.columns(2)
            with summary_col1:
                st.write("Train summary")
                st.json(train_summary or {"status": "not found"})
            with summary_col2:
                st.write("Backtest summary")
                st.json(backtest_summary or {"status": "not found"})

    context = {
        "data_path": str(data_path),
        "symbol": current_symbol,
        "timeframe": current_timeframe,
        "window_start_idx": int(start),
        "window_end_idx": int(end - 1),
        "window_start_ts": str(view["timestamp"].iloc[0]),
        "window_end_ts": str(view["timestamp"].iloc[-1]),
    }
    st.session_state["market_context"] = context
    return context


def render_hypothesis_vault(current_symbol: str, current_timeframe: str, market_context: dict) -> None:
    st.subheader("Hypothesis Vault")

    config_exists = DEFAULT_CONFIG_PATH.exists()
    db_exists = DEFAULT_DB_PATH.exists()
    config = cached_load_local_ai_config(str(DEFAULT_CONFIG_PATH)) if config_exists else None
    ollama_config = load_ollama_config(DEFAULT_CONFIG_PATH) if config_exists else None
    draft = st.session_state.get("hypothesis_draft", {})
    draft_market_context = st.session_state.get("draft_market_context")
    effective_market_context = draft_market_context or market_context
    rows = cached_list_hypotheses_full(200) if db_exists else []
    summary_memories = cached_list_summary_memories(100) if db_exists else []

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Vault status", "ready" if db_exists else "not initialized")
    col2.metric("Stored hypotheses", len(rows))
    col3.metric("Active model", config.get("reasoning_model", "-") if config else "-")
    col4.metric("Saved summaries", len(summary_memories))

    if config:
        with st.expander("Local AI Config", expanded=False):
            st.json(config)

    with st.expander("Current Market Window", expanded=False):
        st.json(effective_market_context)

    if not db_exists:
        st.info("Vault еще не инициализирован. Запусти `crypto-scalp ai-init`.")
        return

    vault_sections = ["Capture", "Browse", "Synthesis", "Memory", "Auto-Discover"]
    if "vault_section" not in st.session_state or st.session_state["vault_section"] not in vault_sections:
        st.session_state["vault_section"] = "Capture"
    selected_vault_section = st.radio(
        "Vault section",
        options=vault_sections,
        index=vault_sections.index(st.session_state["vault_section"]),
        horizontal=True,
        label_visibility="collapsed",
        key="vault_section_radio",
    )
    st.session_state["vault_section"] = selected_vault_section

    if selected_vault_section == "Capture":
        vault_notice = st.session_state.pop("vault_notice", "")
        if vault_notice:
            st.info(vault_notice)
        if draft:
            st.info("В форму подставлен черновик гипотезы из выделенной формации.")
        with st.form("hypothesis_form", clear_on_submit=True):
            title = st.text_input("Title", value=draft.get("title", ""), placeholder="BTC impulse after breakout")
            thesis = st.text_area(
                "Thesis",
                value=draft.get("thesis", ""),
                placeholder="Что именно ты считаешь рабочим паттерном или закономерностью",
                height=100,
            )
            evidence = st.text_area(
                "Evidence",
                value=draft.get("evidence", ""),
                placeholder="Наблюдения, контекст рынка, время сессии, цифры, слабые сигналы",
                height=100,
            )
            tags = st.text_input("Tags", value=draft.get("tags", ""), placeholder="btc, breakout, volume, impulse")
            form_col1, form_col2, form_col3 = st.columns(3)
            symbol = form_col1.text_input("Symbol", value=draft.get("symbol", current_symbol))
            timeframe = form_col2.text_input("Timeframe", value=draft.get("timeframe", current_timeframe))
            status = form_col3.selectbox("Status", options=HYPOTHESIS_STATUSES, index=0)
            score = st.slider("Score", min_value=0.0, max_value=1.0, value=0.0, step=0.05)
            use_embeddings = st.checkbox("Try embeddings via local Ollama", value=False)
            attach_market_window = st.checkbox("Attach current market window", value=True)
            submitted = st.form_submit_button("Save Hypothesis", type="primary")

        if submitted:
            if not title.strip() or not thesis.strip():
                st.error("Нужны как минимум `Title` и `Thesis`.")
            else:
                client = OllamaClient(ollama_config) if (use_embeddings and ollama_config) else (OllamaClient() if use_embeddings else None)
                try:
                    new_id = add_hypothesis(
                        record=HypothesisRecord(
                            title=title.strip(),
                            thesis=thesis.strip(),
                            evidence=evidence.strip(),
                            tags=tags.strip(),
                            symbol=symbol.strip(),
                            timeframe=timeframe.strip(),
                            status=status,
                            score=score,
                            data_path=effective_market_context["data_path"] if attach_market_window else "",
                            window_start_idx=effective_market_context["window_start_idx"] if attach_market_window else -1,
                            window_end_idx=effective_market_context["window_end_idx"] if attach_market_window else -1,
                            window_start_ts=effective_market_context["window_start_ts"] if attach_market_window else "",
                            window_end_ts=effective_market_context["window_end_ts"] if attach_market_window else "",
                        ),
                        client=client,
                    )
                except Exception as exc:
                    st.error(f"Не удалось сохранить гипотезу: {exc}")
                else:
                    st.session_state.pop("hypothesis_draft", None)
                    st.session_state.pop("draft_market_context", None)
                    cached_list_hypotheses_full.clear()
                    cached_list_summary_memories.clear()
                    st.success(f"Гипотеза сохранена в vault как #{new_id}")
                    st.rerun()

    if not rows:
        st.info("В vault пока нет гипотез.")
        return

    frame = pd.DataFrame(rows)
    frame["validation_win_rate"] = frame["validation_result"].apply(lambda raw: extract_validation_metric(raw, "win_rate"))
    frame["validation_trades"] = frame["validation_result"].apply(lambda raw: extract_validation_metric(raw, "trades"))
    symbol_options = ["All"] + sorted([value for value in frame["symbol"].fillna("").unique() if value])
    timeframe_options = ["All"] + sorted([value for value in frame["timeframe"].fillna("").unique() if value])
    status_options = ["All"] + sorted([value for value in frame["status"].fillna("").unique() if value])

    filter_col1, filter_col2, filter_col3 = st.columns(3)
    selected_symbol = filter_col1.selectbox("Filter by symbol", options=symbol_options, index=0)
    selected_timeframe = filter_col2.selectbox("Filter by timeframe", options=timeframe_options, index=0)
    selected_status = filter_col3.selectbox("Filter by status", options=status_options, index=0)

    filtered = frame.copy()
    if selected_symbol != "All":
        filtered = filtered[filtered["symbol"] == selected_symbol]
    if selected_timeframe != "All":
        filtered = filtered[filtered["timeframe"] == selected_timeframe]
    if selected_status != "All":
        filtered = filtered[filtered["status"] == selected_status]

    if selected_vault_section == "Browse":
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 📊 RESULTS DASHBOARD — самое важное сверху
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        st.subheader("📊 Results Dashboard")

        if not filtered.empty:
            # Собираем сводку по всем гипотезам с результатами
            dashboard_rows = []
            for _, row in filtered.iterrows():
                validation = load_json_dict(row.get("validation_result", ""))
                params_data = load_strategy_params(row.get("strategy_params", ""))
                win_rate_val = float(validation.get("win_rate", 0.0)) * 100 if validation else 0.0
                trades_val = int(validation.get("trades", 0)) if validation else 0
                avg_pnl = float(validation.get("avg_realized_move_pct", 0.0)) if validation else 0.0
                decision = validation.get("decision", "not studied") if validation else "not studied"
                exit_reasons = validation.get("exit_reasons", {}) if validation else {}

                # Определяем иконку статуса
                if win_rate_val >= 70:
                    status_icon = "🟢"
                elif win_rate_val >= 60:
                    status_icon = "🟡"
                elif trades_val > 0:
                    status_icon = "🔴"
                else:
                    status_icon = "⚪"

                # Smart filters status
                filters_on = []
                if params_data.get("use_dynamic_stop"):
                    filters_on.append("DynStop")
                if float(params_data.get("breakeven_at_rr", 0)) > 0:
                    filters_on.append(f"BE@{params_data['breakeven_at_rr']}R")
                if params_data.get("partial_tp_at_be"):
                    filters_on.append("PartialTP")
                if float(params_data.get("entry_pullback_pct", 0)) > 0:
                    filters_on.append(f"Pull{params_data['entry_pullback_pct']}")
                if int(params_data.get("trend_ema_period", 0)) > 0:
                    filters_on.append(f"EMA{params_data['trend_ema_period']}")

                dashboard_rows.append({
                    "": status_icon,
                    "ID": int(row["id"]),
                    "Title": str(row["title"])[:35],
                    "Win Rate": f"{win_rate_val:.1f}%",
                    "Trades": trades_val,
                    "Avg PnL": f"{avg_pnl:+.3f}%",
                    "Status": decision,
                    "Filters": ", ".join(filters_on) if filters_on else "None",
                    "Stops": int(exit_reasons.get("fixed_stop", 0)),
                    "TPs": int(exit_reasons.get("take_profit", 0)),
                    "Partials": int(exit_reasons.get("partial_tp_breakeven", 0)),
                })

            if dashboard_rows:
                dash_df = pd.DataFrame(dashboard_rows)

                # Summary metrics at top
                tested = [r for r in dashboard_rows if r["Trades"] > 0]
                winners = [r for r in tested if float(r["Win Rate"].replace("%", "")) >= 70]
                avg_wr = sum(float(r["Win Rate"].replace("%", "")) for r in tested) / len(tested) if tested else 0

                sum_col1, sum_col2, sum_col3, sum_col4 = st.columns(4)
                sum_col1.metric("Всего гипотез", len(dashboard_rows))
                sum_col2.metric("Протестировано", len(tested))
                sum_col3.metric("Win Rate ≥ 70%", f"{len(winners)} 🟢")
                sum_col4.metric("Средний Win Rate", f"{avg_wr:.1f}%")

                st.dataframe(dash_df, use_container_width=True, hide_index=True, height=min(400, 35 * len(dash_df) + 38))

                # Выделяем лидеров
                if winners:
                    st.success(f"🏆 {len(winners)} гипотез готовы к Paper Trading (Win Rate ≥ 70%)!")
                elif tested:
                    st.warning("Ни одна гипотеза пока не достигла 70%. Попробуйте включить smart-фильтры и перепрогнать.")
        else:
            st.info("Нет гипотез. Используйте Auto-Discover для генерации.")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 🚀 BATCH ACTIONS — массовые операции
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        st.subheader("🚀 Batch Actions")
        if not filtered.empty:
            all_ids = sorted(filtered["id"].tolist())
            batch_col1, batch_col2, batch_col3 = st.columns([1, 1, 2])
            batch_from = batch_col1.selectbox("От ID", options=all_ids, index=0, key="batch_from_id")
            batch_to = batch_col2.selectbox("До ID", options=all_ids, index=len(all_ids) - 1, key="batch_to_id")
            batch_ids = [i for i in all_ids if batch_from <= i <= batch_to]
            batch_col3.write(f"Выбрано: **{len(batch_ids)}** гипотез")

            if st.button("▶️ Запустить Batch Backtest", key="run_batch_backtest", type="primary", use_container_width=True):
                progress_bar = st.progress(0, text="Запуск...")
                for idx, hyp_id in enumerate(batch_ids):
                    progress_bar.progress((idx + 1) / len(batch_ids), text=f"Тест #{hyp_id} ({idx+1}/{len(batch_ids)})...")
                    hyp_row = get_hypothesis(int(hyp_id))
                    if not hyp_row:
                        continue
                    hyp_params = load_strategy_params(hyp_row.get("strategy_params", ""))
                    try:
                        result = validate_impulse_hypothesis_on_history(hyp_row, hyp_params)
                        save_validation_result(hyp_row, hyp_params, result)
                    except Exception:
                        pass
                progress_bar.progress(1.0, text="✅ Готово!")
                st.success(f"Batch backtest завершён! Обновляю результаты...")
                st.rerun()

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 📋 HYPOTHESIS CARDS — детали по каждой
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        st.subheader("📋 Hypothesis Cards")
        for row in filtered.to_dict(orient="records"):
            validation = load_json_dict(row.get("validation_result", ""))
            wr_pct = float(validation.get("win_rate", 0.0)) * 100 if validation else 0.0
            trades_count = int(validation.get("trades", 0)) if validation else 0
            wr_icon = "🟢" if wr_pct >= 70 else ("🟡" if wr_pct >= 60 else ("🔴" if trades_count > 0 else "⚪"))
            wr_str = f"{wr_pct:.0f}%" if trades_count > 0 else "—"
            title = f"{wr_icon} #{row['id']} | {row['title']} | {wr_str} win rate | {trades_count} trades | {row['status']}"
            with st.expander(title, expanded=False):
                render_hypothesis_quick_actions(row)
                render_hypothesis_strategy_lab(row)

                with st.expander("📝 Details", expanded=False):
                    st.markdown("**Thesis**")
                    st.write(row["thesis"] or "-")
                    st.markdown("**Evidence**")
                    st.write(row.get("evidence") or "-")

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ✏️ EDIT — редактирование (скрыто)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        with st.expander("✏️ Edit Hypothesis", expanded=False):
            if filtered.empty:
                st.info("Нет гипотез под текущими фильтрами для редактирования.")
            else:
                hypothesis_ids = filtered["id"].tolist()
                selected_id = st.selectbox(
                    "Select hypothesis",
                    options=hypothesis_ids,
                    format_func=lambda item: format_hypothesis_option(filtered, item),
                )
                selected_row = get_hypothesis(int(selected_id))
                if selected_row:
                    with st.form(f"hypothesis_edit_form_{selected_id}"):
                        edit_title = st.text_input("Edit title", value=selected_row["title"])
                        edit_thesis = st.text_area("Edit thesis", value=selected_row["thesis"], height=100)
                        edit_evidence = st.text_area("Edit evidence", value=selected_row["evidence"], height=100)
                        edit_tags = st.text_input("Edit tags", value=selected_row["tags"])
                        edit_col1, edit_col2, edit_col3 = st.columns(3)
                        edit_symbol = edit_col1.text_input("Edit symbol", value=selected_row["symbol"])
                        edit_timeframe = edit_col2.text_input("Edit timeframe", value=selected_row["timeframe"])
                        status_index = HYPOTHESIS_STATUSES.index(selected_row["status"]) if selected_row["status"] in HYPOTHESIS_STATUSES else 0
                        edit_status = edit_col3.selectbox(
                            "Edit status",
                            options=HYPOTHESIS_STATUSES,
                            index=status_index,
                        )
                        edit_score = st.slider(
                            "Edit score",
                            min_value=0.0,
                            max_value=1.0,
                            value=float(selected_row["score"]),
                            step=0.05,
                            key=f"edit_score_{selected_id}",
                        )
                        preserve_market_window = st.checkbox(
                            "Keep attached market window",
                            value=bool(selected_row.get("data_path")),
                            key=f"keep_window_{selected_id}",
                        )
                        reembed = st.checkbox("Recompute embedding via local Ollama", value=False)
                        action_col1, action_col2 = st.columns(2)
                        updated = action_col1.form_submit_button("Update Hypothesis")
                        deleted = action_col2.form_submit_button("Delete Hypothesis")

                    if deleted:
                        try:
                            delete_hypothesis(int(selected_id))
                        except Exception as exc:
                            st.error(f"Не удалось удалить гипотезу: {exc}")
                        else:
                            cached_list_hypotheses_full.clear()
                            cached_list_summary_memories.clear()
                            st.success(f"Гипотеза #{selected_id} удалена")
                            st.rerun()
                    elif updated:
                        if not edit_title.strip() or not edit_thesis.strip():
                            st.error("Нужны как минимум `Title` и `Thesis`.")
                        else:
                            client = OllamaClient(ollama_config) if (reembed and ollama_config) else (OllamaClient() if reembed else None)
                            try:
                                update_hypothesis(
                                    hypothesis_id=int(selected_id),
                                    record=HypothesisRecord(
                                        title=edit_title.strip(),
                                        thesis=edit_thesis.strip(),
                                        evidence=edit_evidence.strip(),
                                        tags=edit_tags.strip(),
                                        symbol=edit_symbol.strip(),
                                        timeframe=edit_timeframe.strip(),
                                        status=edit_status,
                                        score=edit_score,
                                        data_path=selected_row.get("data_path", "") if preserve_market_window else "",
                                        window_start_idx=int(selected_row.get("window_start_idx", -1)) if preserve_market_window else -1,
                                        window_end_idx=int(selected_row.get("window_end_idx", -1)) if preserve_market_window else -1,
                                        window_start_ts=selected_row.get("window_start_ts", "") if preserve_market_window else "",
                                        window_end_ts=selected_row.get("window_end_ts", "") if preserve_market_window else "",
                                        strategy_params=selected_row.get("strategy_params", ""),
                                        validation_result=selected_row.get("validation_result", ""),
                                        paper_status=selected_row.get("paper_status", ""),
                                    ),
                                    client=client,
                                )
                            except Exception as exc:
                                st.error(f"Не удалось обновить гипотезу: {exc}")
                            else:
                                cached_list_hypotheses_full.clear()
                                cached_list_summary_memories.clear()
                                st.success(f"Гипотеза #{selected_id} обновлена")
                                st.rerun()

    if selected_vault_section == "Synthesis":
        default_query = build_default_query(
            symbol=selected_symbol,
            timeframe=selected_timeframe,
            status=selected_status,
        )
        setup_col1, setup_col2 = st.columns(2)
        retrieval_k = setup_col1.slider("Retrieval top-k", min_value=2, max_value=24, value=8, step=1)
        batch_size = setup_col2.slider("Batch size", min_value=1, max_value=8, value=4, step=1)
        synthesis_query = st.text_area(
            "Synthesis query",
            value=default_query,
            height=110,
        )
        if st.button("Synthesize from current hypotheses", type="primary"):
            if filtered.empty:
                st.warning("Нет гипотез под текущими фильтрами.")
            elif not config:
                st.error("Не найден Local AI config.")
            else:
                try:
                    result = synthesize_hypotheses_memory(
                        query=synthesis_query,
                        client=OllamaClient(ollama_config) if ollama_config else OllamaClient(),
                        retrieval_k=retrieval_k,
                        batch_size=batch_size,
                        symbol="" if selected_symbol == "All" else selected_symbol,
                        timeframe="" if selected_timeframe == "All" else selected_timeframe,
                        status="" if selected_status == "All" else selected_status,
                    )
                except Exception as exc:
                    st.error(f"Synthesis failed: {exc}")
                else:
                    cached_list_summary_memories.clear()
                    summary_memories = cached_list_summary_memories(100)
                    st.markdown("### Retrieval")
                    retrieval_frame = pd.DataFrame(result.retrieved)
                    if retrieval_frame.empty:
                        st.info("Под запрос ничего не поднялось из памяти.")
                    else:
                        present_columns = [
                            column
                            for column in [
                                "id",
                                "source_type",
                                "memory_type",
                                "title",
                                "symbol",
                                "timeframe",
                                "status",
                                "score",
                                "lexical_similarity",
                                "semantic_similarity",
                                "retrieval_score",
                            ]
                            if column in retrieval_frame.columns
                        ]
                        st.dataframe(
                            retrieval_frame[present_columns],
                            use_container_width=True,
                            hide_index=True,
                        )

                    st.markdown("### Batch Summaries")
                    if not result.batch_summaries:
                        st.write("Нет промежуточных summaries.")
                    else:
                        for item in result.batch_summaries:
                            with st.expander(
                                f"Batch #{item['batch_index']} | items {item.get('source_labels', item['ids'])}",
                                expanded=False,
                            ):
                                st.write(item["summary"])
                                if item.get("memory_id"):
                                    st.caption(f"Saved as memory #{item['memory_id']}")

                    st.markdown("### Local synthesis")
                    st.write(result.final_answer)
                    if result.saved_memory_ids:
                        st.caption(f"Saved memory ids: {result.saved_memory_ids}")

    if selected_vault_section == "Memory":
        if not summary_memories:
            st.info("Пока нет сохраненных summary memories.")
        else:
            memory_frame = pd.DataFrame(summary_memories)
            memory_symbol_options = ["All"] + sorted(
                [value for value in memory_frame["symbol"].fillna("").unique() if value]
            )
            memory_timeframe_options = ["All"] + sorted(
                [value for value in memory_frame["timeframe"].fillna("").unique() if value]
            )
            memory_type_options = ["All"] + sorted(
                [value for value in memory_frame["memory_type"].fillna("").unique() if value]
            )

            memory_col1, memory_col2, memory_col3 = st.columns(3)
            memory_symbol = memory_col1.selectbox("Memory symbol", options=memory_symbol_options, index=0)
            memory_timeframe = memory_col2.selectbox("Memory timeframe", options=memory_timeframe_options, index=0)
            memory_type = memory_col3.selectbox("Memory type", options=memory_type_options, index=0)

            memory_filtered = memory_frame.copy()
            if memory_symbol != "All":
                memory_filtered = memory_filtered[memory_filtered["symbol"] == memory_symbol]
            if memory_timeframe != "All":
                memory_filtered = memory_filtered[memory_filtered["timeframe"] == memory_timeframe]
            if memory_type != "All":
                memory_filtered = memory_filtered[memory_filtered["memory_type"] == memory_type]

            st.dataframe(
                memory_filtered[
                    [
                        "id",
                        "created_at",
                        "memory_type",
                        "query",
                        "symbol",
                        "timeframe",
                        "status_filter",
                        "retrieval_k",
                        "batch_size",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

            for row in memory_filtered.to_dict(orient="records")[:20]:
                with st.expander(
                    f"Memory #{row['id']} | {row['memory_type']} | {row['symbol'] or '-'} | {row['timeframe'] or '-'}",
                    expanded=False,
                ):
                    st.caption(
                        f"Query: {row['query']} | status={row['status_filter'] or '-'} | "
                        f"top_k={row['retrieval_k']} | batch_size={row['batch_size']}"
                    )
                    st.caption(f"Source hypotheses: {row['source_hypothesis_ids']}")
                    st.write(row["content"])

    if selected_vault_section == "Auto-Discover":
        st.subheader("Auto-Discover Anomalies")
        st.markdown(
            "Эта функция автоматически ищет самые мощные импульсы (выбросы объема и цены) "
            "на текущем загруженном графике и просит AI составить скальперские гипотезы по каждому из них."
        )
        
        ad_col1, ad_col2 = st.columns(2)
        limit = ad_col1.slider("Сколько топ-аномалий проанализировать?", min_value=1, max_value=10, value=5, step=1)
        
        data_path_str = effective_market_context.get("data_path")
        current_symbol_context = effective_market_context.get("symbol", current_symbol)
        
        if not data_path_str:
            st.warning("⚠️ Нет загруженного графика. Сначала откройте вкладку **Market Research** и загрузите CSV.")
        elif not config:
            st.error("⚠️ Не найден Local AI config. Выполните `crypto-scalp ai-init`.")
        else:
            st.info(f"Будет просканирован файл: `{data_path_str}`")
            if st.button("🚀 Запустить AI сканирование (Gemini / Ollama)", type="primary"):
                with st.spinner(f"Ищем аномалии на графике {current_symbol_context} и опрашиваем нейросеть (логи в терминале)..."):
                    try:
                        client = HybridAIClient(ollama_config) if ollama_config else OllamaClient()
                        saved_ids = auto_discover_hypotheses(
                            data_path=Path(data_path_str),
                            client=client,
                            symbol=current_symbol_context,
                            limit=limit,
                            db_path=DEFAULT_DB_PATH
                        )
                        st.success(f"🎉 Готово! Сохранено {len(saved_ids)} новых гипотез в Vault (ids: {saved_ids}).")
                        
                        # Очищаем кэш, чтобы новые гипотезы сразу появились во вкладке Browse
                        cached_list_hypotheses_full.clear()
                    except Exception as e:
                        st.error(f"Произошла ошибка при анализе аномалий: {e}")


def load_local_ai_config(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data(ttl=15, show_spinner=False)
def cached_load_local_ai_config(path_str: str) -> Optional[dict]:
    return load_local_ai_config(Path(path_str))


@st.cache_data(ttl=15, show_spinner=False)
def cached_list_hypotheses_full(limit: int) -> list[dict]:
    return list_hypotheses_full(limit=limit)


@st.cache_data(ttl=15, show_spinner=False)
def cached_list_summary_memories(limit: int) -> list[dict]:
    return list_summary_memories(limit=limit)


@st.cache_data(ttl=30, show_spinner=False)
def cached_load_summary(artifacts_dir_str: str, filename: str) -> Optional[dict]:
    return load_summary(Path(artifacts_dir_str), filename)


@st.cache_data(ttl=3600, show_spinner="Готовлю локальные свечи и признаки...")
def cached_load_prepared_dataset(path_str: str, mtime: float, size: int):
    _ = (mtime, size)
    return load_prepared_dataset(Path(path_str), RunConfig())


@st.cache_data(ttl=120, show_spinner=False)
def cached_find_reusable_bybit_csv(root_str: str, symbol: str, interval: str, start_iso: str, end_iso: str):
    return find_reusable_bybit_csv(
        root=Path(root_str),
        symbol=symbol,
        interval=interval,
        start=datetime.fromisoformat(start_iso),
        end=datetime.fromisoformat(end_iso),
    )


@st.cache_data(ttl=10, show_spinner=False)
def cached_discover_live_signal_runs(root_str: str, symbol: str) -> list[str]:
    return [str(path) for path in discover_live_signal_runs(Path(root_str), symbol=symbol)]


@st.cache_data(ttl=10, show_spinner=False)
def cached_read_live_signal_run(path_str: str) -> Optional[dict]:
    return read_live_signal_run(Path(path_str))


def render_hypothesis_strategy_lab(row: dict) -> None:
    params = load_strategy_params(row.get("strategy_params", ""))
    validation = load_json_dict(row.get("validation_result", ""))
    with st.expander("Strategy Lab: tuning & backtest", expanded=False):
        if validation:
            render_validation_metrics(validation)
        
        tab_params, tab_chart, tab_logs = st.tabs(["⚙️ Parameters", "📊 Chart", "📋 Trade Logs"])
        
        with tab_params:
            st.caption("Тюнинг входа, стопа и тайм-стопа. Изменяйте параметры, чтобы добиться Win Rate >70%.")
            with st.form(f"strategy_params_{row['id']}"):
                col1, col2, col3 = st.columns(3)
                lookback_bars = col1.number_input("Обычный режим, bars", min_value=20, max_value=500, value=int(params["lookback_bars"]), step=10)
                min_dollar_volume_z = col2.slider("$ volume z >=", 0.5, 8.0, float(params.get("min_dollar_volume_z", 3.0)), 0.1)
                min_price_return_z = col3.slider("price return z >=", 0.5, 8.0, float(params.get("min_price_return_z", 2.0)), 0.1)

                seq_col1, seq_col2, seq_col3 = st.columns(3)
                min_sequence_bars = seq_col1.number_input("Min impulse bars", min_value=1, max_value=10, value=int(params["min_sequence_bars"]), step=1)
                max_sequence_bars = seq_col2.number_input("Max impulse bars", min_value=2, max_value=30, value=int(params["max_sequence_bars"]), step=1)
                entry_after_bars = seq_col3.number_input("Entry after bars", min_value=0, max_value=10, value=int(params["entry_after_bars"]), step=1)

                risk_col1, risk_col2, risk_col3 = st.columns(3)
                fixed_stop_loss_pct = risk_col1.slider("Working stop %", 0.05, 10.0, float(params["fixed_stop_loss_pct"]), 0.05)
                take_profit_rr = risk_col2.slider("Take profit R", 0.5, 6.0, float(params.get("take_profit_rr", 0.8)), 0.1)
                max_hold_bars = risk_col3.number_input("Max hold bars", min_value=3, max_value=200, value=int(params["max_hold_bars"]), step=1)

                cancel_col1, cancel_col2, cancel_col3 = st.columns(3)
                cancel_if_no_follow_bars = cancel_col1.number_input("Cancel if no follow bars", min_value=1, max_value=50, value=int(params.get("cancel_if_no_follow_bars", 4)), step=1)
                cancel_min_follow_pct = cancel_col2.slider("Min follow %", 0.01, 5.0, float(params.get("cancel_min_follow_pct", 0.1)), 0.01)
                account_risk_pct = cancel_col3.slider("Deposit risk %", 0.01, 1.0, float(params["account_risk_pct"]), 0.01)

                smart_col1, smart_col2, smart_col3 = st.columns(3)
                trend_ema_period = smart_col1.number_input("Trend EMA Period (0=Off)", min_value=0, max_value=500, value=int(params.get("trend_ema_period", 0)), step=10)
                breakeven_at_rr = smart_col2.slider("Move Breakeven at R", 0.0, 5.0, float(params.get("breakeven_at_rr", 0.0)), 0.1)
                use_dynamic_stop = smart_col3.checkbox("Use Dynamic Impulse Stop", value=bool(params.get("use_dynamic_stop", False)))

                pro_col1, pro_col2 = st.columns(2)
                partial_tp_at_be = pro_col1.checkbox("Take 50% profit at Breakeven", value=bool(params.get("partial_tp_at_be", False)))
                entry_pullback_pct = pro_col2.slider("Limit Entry Pullback % (0=None)", 0.0, 1.0, float(params.get("entry_pullback_pct", 0.0)), 0.05)

                threshold_col1, threshold_col2 = st.columns(2)
                paper_win_rate_threshold = threshold_col1.slider("Paper gate win rate", 0.50, 0.99, float(params["paper_win_rate_threshold"]), 0.01)
                preserve_status = threshold_col2.checkbox("Preserve current status on save", value=True)
                save_params = st.form_submit_button("Save strategy parameters")

            if save_params:
                updated_params = {
                    "lookback_bars": int(lookback_bars),
                    "min_dollar_volume_z": float(min_dollar_volume_z),
                    "min_price_return_z": float(min_price_return_z),
                    "min_sequence_bars": int(min_sequence_bars),
                    "max_sequence_bars": int(max_sequence_bars),
                    "entry_after_bars": int(entry_after_bars),
                    "max_hold_bars": int(max_hold_bars),
                    "fixed_stop_loss_pct": float(fixed_stop_loss_pct),
                    "take_profit_rr": float(take_profit_rr),
                    "cancel_if_no_follow_bars": int(cancel_if_no_follow_bars),
                    "cancel_min_follow_pct": float(cancel_min_follow_pct),
                    "paper_win_rate_threshold": float(paper_win_rate_threshold),
                    "account_risk_pct": float(account_risk_pct),
                    "trend_ema_period": int(trend_ema_period),
                    "breakeven_at_rr": float(breakeven_at_rr),
                    "use_dynamic_stop": bool(use_dynamic_stop),
                    "partial_tp_at_be": bool(partial_tp_at_be),
                    "entry_pullback_pct": float(entry_pullback_pct),
                }
                save_strategy_params(row, updated_params, status=row.get("status", "new") if preserve_status else "testing")
                st.success("Параметры гипотезы сохранены.")
                st.rerun()

        with tab_chart:
            if not validation or not validation.get("sample_trades"):
                st.info("Нет данных для графика. Сначала запустите 'Study on history'.")
            else:
                data_path_str = str(row.get("data_path", "")).strip()
                if not data_path_str:
                    current_context = st.session_state.get("draft_market_context") or st.session_state.get("market_context", {})
                    data_path_str = current_context.get("data_path", "")
                
                if data_path_str and Path(data_path_str).exists():
                    fig = build_backtest_trades_chart(Path(data_path_str), validation["sample_trades"])
                    if fig:
                        st.plotly_chart(fig, use_container_width=True, key=f"trades_chart_{row['id']}")
                    else:
                        st.warning("Не удалось построить график.")
                else:
                    st.warning(f"Файл данных не найден: {data_path_str}")
        
        with tab_logs:
            if validation:
                exit_reasons = validation.get("exit_reasons", {})
                if exit_reasons:
                    st.markdown("**Сводка по причинам закрытия (Exit Reasons)**")
                    st.dataframe(pd.DataFrame(list(exit_reasons.items()), columns=["Reason", "Count"]), use_container_width=True, hide_index=True)
                
                sample_trades = validation.get("sample_trades", [])
                if sample_trades:
                    st.markdown("**Журнал сделок (Top 50)**")
                    df_trades = pd.DataFrame(sample_trades)
                    if not df_trades.empty:
                        cols = ["entry_ts", "entry_price", "exit_ts", "exit_reason", "realized_move_pct", "is_profitable"]
                        available_cols = [c for c in cols if c in df_trades.columns]
                        st.dataframe(df_trades[available_cols], use_container_width=True, hide_index=True)
            else:
                st.info("Нет данных. Запустите Study on history.")


def render_hypothesis_quick_actions(row: dict) -> None:
    params = load_strategy_params(row.get("strategy_params", ""))
    validation = load_json_dict(row.get("validation_result", ""))
    if validation:
        render_validation_metrics(validation)

    paper_status = row.get("paper_status", "") or "not queued"
    st.caption(f"Paper status: `{paper_status}`")
    action_col1, action_col2, action_col3 = st.columns(3)

    if action_col1.button("Study on history", key=f"quick_study_history_{row['id']}", type="primary", use_container_width=True):
        result = validate_impulse_hypothesis_on_history(row, params)
        save_validation_result(row, params, result)
        st.success(
            f"Проверка сохранена: найдено {int(result.get('events_found', 0))} похожих паттернов, "
            f"сделок {int(result.get('trades', 0))}, win rate {float(result.get('win_rate', 0.0)) * 100:.1f}%."
        )
        st.rerun()

    can_send = can_send_to_paper(row, params, validation)
    if action_col2.button(
        "Send to paper test",
        key=f"quick_send_paper_{row['id']}",
        disabled=not can_send,
        use_container_width=True,
    ):
        queue_path = send_hypothesis_to_paper(row, params, validation)
        st.session_state["main_section"] = "🧪 Hypothesis Lab"
        st.session_state["_nav_programmatic"] = True
        st.success(f"Гипотеза отправлена в paper queue: {queue_path}")
        st.rerun()

    if action_col3.button("Open Paper Trading", key=f"open_paper_tab_{row['id']}", use_container_width=True):
        st.session_state["main_section"] = "🧪 Hypothesis Lab"
        st.session_state["_nav_programmatic"] = True
        st.rerun()

    if not can_send:
        st.caption("Paper test станет доступен после исторической проверки с win rate выше заданного порога и хотя бы одной сделкой.")


def render_validation_metrics(validation: dict) -> None:
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("Похожих паттернов", int(validation.get("events_found", 0)))
    v2.metric("Сделок", int(validation.get("trades", 0)))
    v3.metric("Win rate", f"{float(validation.get('win_rate', 0.0)) * 100:.1f}%")
    v4.metric("Status", validation.get("decision", "not studied"))


def can_send_to_paper(row: dict, params: dict, validation: dict) -> bool:
    if str(row.get("paper_status", "")) == "queued":
        return False
    return (
        bool(validation)
        and int(validation.get("trades", 0)) > 0
        and float(validation.get("win_rate", 0.0)) >= float(params["paper_win_rate_threshold"])
    )


def send_hypothesis_to_paper(row: dict, params: dict, validation: dict) -> Path:
    queue_path = enqueue_hypothesis_for_paper(row, params, validation)
    update_hypothesis(
        hypothesis_id=int(row["id"]),
        record=build_record_from_row(
            row,
            strategy_params=json.dumps(params, ensure_ascii=False),
            validation_result=json.dumps(validation, ensure_ascii=False),
            paper_status="queued",
            status="paper_testing",
            score=float(validation.get("win_rate", row.get("score", 0.0))),
        ),
    )
    cached_list_hypotheses_full.clear()
    return queue_path


def render_paper_trading(root: Path) -> None:
    st.subheader("Paper Trading")
    st.caption("Очередь гипотез, которые прошли исторический фильтр и готовы к realtime demo/paper проверке.")
    queue_items = discover_paper_queue(root)
    queued_symbols = sorted({str(item.get("symbol", "")).upper() for item in queue_items if item.get("symbol")})
    paper_runs = [str(path) for path in discover_paper_runs(root)]
    metric_col1, metric_col2, metric_col3 = st.columns(3)
    metric_col1.metric("Queued hypotheses", len(queue_items))
    active_items = [item for item in queue_items if item.get("paper_state", "queued") in {"queued", "running"}]
    metric_col2.metric("Active / queued", len(active_items))
    metric_col3.metric("Saved paper runs", len(paper_runs))

    if not queue_items:
        st.info("Пока нет гипотез в paper queue. В `Hypothesis Vault` нажми `Study on history`, а затем `Send to paper test`.")
        return

    st.markdown("**Run Realtime Demo Loop**")
    st.caption("Demo-loop работает от минутных импульсов: для baseline он использует CSV из исторической проверки гипотезы, а live-поток агрегирует в `1m+` бары.")
    run_col1, run_col2, run_col3 = st.columns(3)
    run_symbol = run_col1.selectbox("Symbol", options=queued_symbols or ["SOLUSDT"], index=0)
    run_minutes = run_col2.number_input("Run duration, minutes", min_value=1, max_value=60, value=5, step=1)
    run_deposit = run_col3.number_input("Paper deposit USDT", min_value=10.0, max_value=1_000_000.0, value=1000.0, step=100.0)
    run_duration_seconds = int(run_minutes) * 60
    command_col, button_col = st.columns([2, 1])
    command_col.code(
        (
            f"crypto-scalp paper-trade-live \\\n"
            f"  --symbol {run_symbol} \\\n"
            f"  --duration-seconds {run_duration_seconds} \\\n"
            f"  --deposit-usdt {float(run_deposit):.2f}"
        ),
        language="bash",
    )
    if button_col.button("Run demo now", type="primary", use_container_width=True):
        with st.spinner(f"Running paper demo for {run_symbol}..."):
            try:
                summary = run_paper_trading(
                    root=root,
                    symbol=run_symbol,
                    duration_seconds=run_duration_seconds,
                    deposit_usdt=float(run_deposit),
                )
            except Exception as exc:
                st.error(f"Paper demo failed: {exc}")
            else:
                st.success("Paper demo finished.")
                st.json(summary.to_dict())

    queue_frame = pd.DataFrame(
        [
            {
                "hypothesis_id": item.get("hypothesis_id"),
                "queued_at": item.get("queued_at"),
                "symbol": item.get("symbol"),
                "timeframe": item.get("timeframe"),
                "title": item.get("title"),
                "win_rate": item.get("validation_result", {}).get("win_rate", 0.0),
                "trades": item.get("validation_result", {}).get("trades", 0),
                "events_found": item.get("validation_result", {}).get("events_found", 0),
                "paper_state": item.get("paper_state", "queued"),
                "path": item.get("_path", ""),
            }
            for item in queue_items
        ]
    )
    st.dataframe(queue_frame, use_container_width=True, hide_index=True)

    selected_path = st.selectbox("Open paper hypothesis", options=[item["_path"] for item in queue_items])
    selected = next((item for item in queue_items if item["_path"] == selected_path), queue_items[0])
    st.markdown("**Paper hypothesis details**")
    st.json(selected)
    st.info(
        "Demo-loop уже подключен: он читает эту очередь, слушает live trades Bybit, "
        "создает paper-вход при совпадении импульса и сохраняет журнал вход/стоп/отмена/выход."
    )

    st.markdown("**Saved Paper Runs**")
    paper_runs = [str(path) for path in discover_paper_runs(root, symbol=str(selected.get("symbol", "")))]
    if not paper_runs:
        st.info("По этому символу пока нет сохраненных paper-runs.")
        return
    selected_run = st.selectbox("Open paper run", options=paper_runs)
    run_payload = read_paper_run(Path(selected_run)) or {}
    run_col1, run_col2, run_col3 = st.columns(3)
    run_col1.metric("Signals", len(run_payload.get("signals", []) or []))
    run_col2.metric("Paper events", len(run_payload.get("paper_trades", []) or []))
    closed = [item for item in run_payload.get("paper_trades", []) or [] if item.get("event") == "close"]
    run_col3.metric("Closed trades", len(closed))
    if closed:
        closed_frame = pd.DataFrame(closed)
        st.dataframe(closed_frame, use_container_width=True, hide_index=True)
        st.metric("Paper PnL USDT", f"{closed_frame['pnl_usdt'].sum():.2f}")
    with st.expander("Raw paper run", expanded=False):
        st.json(run_payload)


def render_live_signals(root: Path, current_symbol: str) -> None:
    st.subheader("Live Signals")
    st.caption("Локальный realtime слой: raw trades с Bybit -> 1s candles -> matcher templates -> live signal events.")
    live_notice = st.session_state.pop("live_notice", "")
    if live_notice:
        st.info(live_notice)

    templates = load_matcher_templates(root=root, symbol=current_symbol)
    signal_runs = cached_discover_live_signal_runs(str(root), current_symbol)

    metric_col1, metric_col2, metric_col3 = st.columns(3)
    metric_col1.metric("Matchers loaded", len(templates))
    metric_col2.metric("Saved live runs", len(signal_runs))
    latest_signal_count = 0
    if signal_runs:
        latest_payload = cached_read_live_signal_run(signal_runs[0]) or {}
        latest_signal_count = len(latest_payload.get("signals", []) or [])
    metric_col3.metric("Signals in latest run", latest_signal_count)

    st.markdown("**CLI Workflow**")
    command_col1, command_col2 = st.columns(2)
    command_col1.code(
        (
            f"crypto-scalp collect-bybit-trades \\\n"
            f"  --symbol {current_symbol or 'SOLUSDT'} \\\n"
            f"  --duration-seconds 300"
        ),
        language="bash",
    )
    command_col2.code(
        (
            f"crypto-scalp watch-bybit-live \\\n"
            f"  --symbol {current_symbol or 'SOLUSDT'} \\\n"
            f"  --duration-seconds 300"
        ),
        language="bash",
    )

    st.markdown("**Loaded Matcher Templates**")
    if not templates:
        st.info("Для этого символа пока нет сохраненных matcher templates. Сначала собери их через Formation Lab -> `Собрать 1s паттерн`.")
    else:
        template_frame = pd.DataFrame(
            [
                {
                    "symbol": item.get("symbol", ""),
                    "trigger_side": item.get("trigger_side", ""),
                    "template_seconds": item.get("template_seconds", 0),
                    "selection_start_ts": item.get("selection_start_ts", ""),
                    "selection_end_ts": item.get("selection_end_ts", ""),
                    "preferred_phase": (item.get("entry_rule", {}) or {}).get("preferred_phase", ""),
                    "min_total_score": (item.get("entry_rule", {}) or {}).get("min_total_score", 0.0),
                    "template_path": item.get("template_path", ""),
                }
                for item in templates
            ]
        )
        st.dataframe(template_frame, use_container_width=True, hide_index=True)

    st.markdown("**Saved Live Runs**")
    if not signal_runs:
        st.info("Live signal runs пока не найдены. Запусти `watch-bybit-live`, и здесь появятся результаты.")
        return

    selected_run = st.selectbox(
        "Open live run",
        options=signal_runs,
        index=0,
    )
    payload = cached_read_live_signal_run(selected_run)
    if not payload:
        st.warning("Не удалось прочитать выбранный live run.")
        return

    st.json(
        {
            "symbol": payload.get("symbol", ""),
            "started_at": payload.get("started_at", ""),
            "finished_at": payload.get("finished_at", ""),
            "duration_seconds": payload.get("duration_seconds", 0),
            "templates_loaded": payload.get("templates_loaded", 0),
        }
    )
    signals = payload.get("signals", []) or []
    if not signals:
        st.info("В этом live run сигналов пока не найдено.")
        return

    signal_frame = pd.DataFrame(signals)
    signal_col1, signal_col2, signal_col3 = st.columns(3)
    signal_col1.metric("Signals", len(signal_frame))
    signal_col2.metric("Avg score", f"{signal_frame['score'].mean():.3f}")
    signal_col3.metric("Long share", f"{(signal_frame['action'] == 'open_long').mean() * 100:.1f}%")
    st.dataframe(signal_frame, use_container_width=True, hide_index=True)


def build_default_query(symbol: str, timeframe: str, status: str) -> str:
    parts = ["Сделай synthesis по текущим торговым гипотезам."]
    if symbol != "All":
        parts.append(f"Сфокусируйся на символе {symbol}.")
    if timeframe != "All":
        parts.append(f"Сфокусируйся на таймфрейме {timeframe}.")
    if status != "All":
        parts.append(f"Учитывай только гипотезы со статусом {status}.")
    parts.append("Выдели подтверждающиеся идеи, конфликты и следующие эксперименты.")
    return " ".join(parts)


def infer_research_context(data_path: Path) -> tuple[str, str]:
    name = data_path.stem.lower()
    symbol = ""
    timeframe = ""

    symbol_match = re.search(r"(btcusdt|ethusdt|solusdt|[a-z]{2,10}usdt)", name)
    if symbol_match:
        symbol = symbol_match.group(1).upper()

    timeframe_match = re.search(r"_(\d+m|\d+h|\d+d)$", name)
    if timeframe_match:
        timeframe = timeframe_match.group(1)
    else:
        bybit_match = re.search(r"bybit_[a-z0-9]+_(\d+|d|w)_", name)
        if bybit_match:
            timeframe = normalize_interval_label(bybit_match.group(1))

    return symbol, timeframe


def normalize_interval_label(raw: str) -> str:
    if raw.isdigit():
        return f"{raw}m"
    return raw.upper()


def format_hypothesis_option(frame: pd.DataFrame, hypothesis_id: int) -> str:
    row = frame.loc[frame["id"] == hypothesis_id].iloc[0]
    return f"#{hypothesis_id} | {row['title']}"


def extract_selection_context(
    chart_event: object,
    dataset: pd.DataFrame,
    data_path: Path,
    current_symbol: str,
    current_timeframe: str,
    visible_start_idx: int,
    visible_end_idx: int,
) -> Optional[dict]:
    points = extract_plotly_points(chart_event)

    indices = []
    for point in points:
        trace_name = str(point.get("trace_name") or point.get("name") or "")
        if trace_name and trace_name != "selector":
            continue
        customdata = point.get("customdata")
        if customdata is None:
            continue
        try:
            if isinstance(customdata, (list, tuple, np.ndarray)) and len(customdata) >= 2:
                indices.append(int(customdata[0]))
                indices.append(int(customdata[1]))
            elif isinstance(customdata, (list, tuple, np.ndarray)) and customdata:
                indices.append(int(customdata[0]))
            else:
                indices.append(int(customdata))
        except (TypeError, ValueError):
            continue

    # Fallback: if no points captured (e.g. compressed chart), use box x-range
    if not indices:
        box_range = _extract_box_x_range(chart_event)
        if box_range and "timestamp" in dataset.columns:
            x0_str, x1_str = box_range
            try:
                ts_col = pd.to_datetime(dataset["timestamp"])
                # Strip timezone from both sides to avoid UTC vs naive mismatch
                if ts_col.dt.tz is not None:
                    ts_col = ts_col.dt.tz_convert(None)
                x0_ts = pd.to_datetime(x0_str)
                x1_ts = pd.to_datetime(x1_str)
                if hasattr(x0_ts, "tz") and x0_ts.tz is not None:
                    x0_ts = x0_ts.tz_localize(None)
                if hasattr(x1_ts, "tz") and x1_ts.tz is not None:
                    x1_ts = x1_ts.tz_localize(None)
                if x0_ts > x1_ts:
                    x0_ts, x1_ts = x1_ts, x0_ts
                mask = (ts_col >= x0_ts) & (ts_col <= x1_ts)
                matched_idx = dataset.index[mask]
                if len(matched_idx) > 0:
                    indices = [int(matched_idx[0]), int(matched_idx[-1])]
            except Exception:
                pass

    if not indices:
        return None

    start_idx = max(0, min(indices))
    end_idx = min(len(dataset) - 1, max(indices))
    start_idx = max(start_idx, visible_start_idx)
    end_idx = min(end_idx, visible_end_idx)
    if start_idx > end_idx:
        return None
    selected = dataset.iloc[start_idx:end_idx + 1]
    if selected.empty:
        return None

    return {
        "data_path": str(data_path),
        "symbol": current_symbol,
        "timeframe": current_timeframe,
        "window_start_idx": int(start_idx),
        "window_end_idx": int(end_idx),
        "window_start_ts": str(selected["timestamp"].iloc[0]),
        "window_end_ts": str(selected["timestamp"].iloc[-1]),
    }


def _extract_box_x_range(chart_event: object) -> Optional[tuple]:
    """Extract x0, x1 from Plotly box selection event."""
    if chart_event is None:
        return None
    selection = None
    if isinstance(chart_event, dict):
        selection = chart_event.get("selection")
    else:
        selection = getattr(chart_event, "selection", None)
    if selection is None:
        return None

    # Check for box coordinates
    if isinstance(selection, dict):
        box_list = selection.get("box", [])
    else:
        box_list = getattr(selection, "box", [])
    if box_list:
        for box in box_list:
            if isinstance(box, dict):
                x_range = box.get("x")
                if x_range and len(x_range) >= 2:
                    return (str(x_range[0]), str(x_range[1]))

    # Also check for range in selection
    if isinstance(selection, dict):
        x_range = selection.get("range", {}).get("x")
        if x_range and len(x_range) >= 2:
            return (str(x_range[0]), str(x_range[1]))

    return None


def extract_plotly_points(chart_event: object) -> list[dict]:
    if chart_event is None:
        return []
    selection = None
    if isinstance(chart_event, dict):
        selection = chart_event.get("selection")
    else:
        selection = getattr(chart_event, "selection", None)
    if selection is None:
        return []
    if isinstance(selection, dict):
        points = selection.get("points", []) or []
    else:
        points = getattr(selection, "points", []) or []

    normalized = []
    for point in points:
        if not isinstance(point, dict):
            continue
        trace_name = ""
        data_block = point.get("data")
        if isinstance(data_block, dict):
            trace_name = str(data_block.get("name") or "")
        if not trace_name:
            trace_name = str(point.get("fullData", {}).get("name") or point.get("name") or "")
        normalized.append({**point, "trace_name": trace_name})
    return normalized


def is_context_inside_visible_window(selection_context: dict, visible_start_idx: int, visible_end_idx: int) -> bool:
    try:
        start_idx = int(selection_context.get("window_start_idx", -1))
        end_idx = int(selection_context.get("window_end_idx", -1))
    except (TypeError, ValueError):
        return False
    return visible_start_idx <= start_idx <= end_idx <= visible_end_idx


def render_selection_empty_state(current_symbol: str, current_timeframe: str) -> None:
    st.subheader("Анализ формации")
    st.info(
        "Выдели участок импульса на графике — здесь появится разбор, метрики и кнопка отправки в лабораторию."
    )
    st.caption(
        f"Контекст: {current_symbol or 'Инструмент'} {current_timeframe or ''}".strip()
    )
    st.markdown(
        """
        **Что появится после выделения**

        - `Обзор`: метрики формации и выход после неё
        - `Похожие`: похожие ситуации из истории
        - `Черновик`: черновик гипотезы для vault
        - `Импульс`: минутный паттерн dollar volume
        """
    )


def render_selection_lab(
    dataset: pd.DataFrame,
    selection_context: dict,
    current_symbol: str,
    current_timeframe: str,
) -> None:
    start_idx = int(selection_context["window_start_idx"])
    end_idx = int(selection_context["window_end_idx"])
    selected = dataset.iloc[start_idx:end_idx + 1].copy()
    if selected.empty:
        return

    if len(selected) > 600:
        st.subheader("Selection Desk")
        st.warning(
            "Сейчас выделено слишком большое окно для локальной формации. "
            "Выдели более компактный участок, лучше примерно от 5 до 200 свечей."
        )
        st.caption(
            f"Текущее окно: {len(selected)} свечей | "
            f"{selection_context['window_start_ts']} -> {selection_context['window_end_ts']}"
        )
        return

    st.subheader("Анализ формации")
    st.caption(
        f"Выделено: {start_idx}..{end_idx} | "
        f"{selection_context['window_start_ts']} → {selection_context['window_end_ts']}"
    )

    forward_bars = st.slider("Свечей вперёд для анализа", min_value=3, max_value=50, value=10, step=1)
    forward = dataset.iloc[end_idx + 1:end_idx + 1 + forward_bars].copy()

    selected_open = float(selected["open"].iloc[0])
    selected_close = float(selected["close"].iloc[-1])
    selected_high = float(selected["high"].max())
    selected_low = float(selected["low"].min())
    selected_volume = float(selected["volume"].sum())
    selected_volume_mean = float(selected["volume"].mean())
    selected_volume_z = float(selected["volume_z"].mean()) if "volume_z" in selected else 0.0
    price_change_pct = (selected_close / selected_open - 1) * 100
    range_pct = (selected_high / selected_low - 1) * 100 if selected_low else 0.0

    if forward.empty:
        st.info("После выделенного участка пока нет достаточного числа следующих свечей для анализа выхода.")
        return

    forward_close = float(forward["close"].iloc[-1])
    forward_high = float(forward["high"].max())
    forward_low = float(forward["low"].min())
    breakout_up_pct = (forward_high / selected_high - 1) * 100 if selected_high else 0.0
    breakout_down_pct = (forward_low / selected_low - 1) * 100 if selected_low else 0.0
    forward_change_pct = (forward_close / selected_close - 1) * 100 if selected_close else 0.0
    forward_volume_mean = float(forward["volume"].mean())
    volume_ratio = forward_volume_mean / selected_volume_mean if selected_volume_mean else 0.0
    similar_count = int(st.session_state.get("selection_similar_count", 8))
    similar_cache_key = build_selection_similarity_cache_key(
        selection_context=selection_context,
        forward_bars=forward_bars,
        top_k=similar_count,
    )
    cached_similar = st.session_state.get("selection_similar_cache", {})
    similar_matches = cached_similar.get(similar_cache_key, [])

    draft = build_hypothesis_draft_from_selection(
        selection_context=selection_context,
        selected=selected,
        forward=forward,
        current_symbol=current_symbol,
        current_timeframe=current_timeframe,
        forward_bars=forward_bars,
        price_change_pct=price_change_pct,
        forward_change_pct=forward_change_pct,
        breakout_up_pct=breakout_up_pct,
        breakout_down_pct=breakout_down_pct,
        volume_ratio=volume_ratio,
        selected_volume_z=selected_volume_z,
        similar_matches=similar_matches,
    )
    # ===== TOP CTA: immediately visible after selection =====
    cta_col1, cta_col2 = st.columns([1.6, 1])
    cta_col1.metric("Свечей", len(selected))
    cta_col2.metric("Движение", f"{price_change_pct:+.2f}%")

    if st.button(
        "⚡ В лабораторию",
        type="primary",
        use_container_width=True,
        key="selection_top_cta_impulse_lab",
    ):
        st.session_state["impulse_lab_selection"] = {
            "start_idx": start_idx,
            "end_idx": end_idx,
            "data_path": str(selection_context.get("data_path", "")),
            "symbol": current_symbol,
            "timeframe": current_timeframe,
            "start_ts": str(selection_context.get("window_start_ts", "")),
            "end_ts": str(selection_context.get("window_end_ts", "")),
        }
        st.session_state["main_section"] = "⚡ Impulse Lab"
        st.session_state["_nav_programmatic"] = True
        st.rerun()

    overview_tab, similar_tab, draft_tab, impulse_tab = st.tabs(["Обзор", "Похожие", "Черновик", "⚡ Импульс"])

    with overview_tab:
        col1, col2 = st.columns(2)
        col1.metric("Candles", len(selected))
        col2.metric("Price change", f"{price_change_pct:.2f}%")
        col3, col4 = st.columns(2)
        col3.metric("Total volume", f"{selected_volume:,.0f}")
        col4.metric("Avg volume z", f"{selected_volume_z:.2f}")

        exit_col1, exit_col2 = st.columns(2)
        exit_col1.metric("Forward close change", f"{forward_change_pct:.2f}%")
        exit_col2.metric("Forward volume ratio", f"{volume_ratio:.2f}x")
        exit_col3, exit_col4 = st.columns(2)
        exit_col3.metric("Breakout above high", f"{breakout_up_pct:.2f}%")
        exit_col4.metric("Breakdown below low", f"{breakout_down_pct:.2f}%")

        metrics_frame = pd.DataFrame(
            [
                {
                    "selected_open": selected_open,
                    "selected_close": selected_close,
                    "selected_high": selected_high,
                    "selected_low": selected_low,
                    "range_pct": range_pct,
                    "volume_mean": selected_volume_mean,
                    "volume_sum": selected_volume,
                }
            ]
        )
        st.dataframe(metrics_frame, use_container_width=True, hide_index=True)

    with similar_tab:
        similar_count = st.slider(
            "Similar formations to find",
            min_value=3,
            max_value=20,
            value=similar_count,
            step=1,
            key="selection_similar_count",
        )
        similar_cache_key = build_selection_similarity_cache_key(
            selection_context=selection_context,
            forward_bars=forward_bars,
            top_k=similar_count,
        )
        cached_similar = st.session_state.get("selection_similar_cache", {})
        similar_matches = cached_similar.get(similar_cache_key)

        find_col1, find_col2 = st.columns([1, 1])
        if find_col1.button("Find similar formations", type="primary", use_container_width=True):
            progress_bar = st.progress(0.0, text="Готовлю поиск похожих формаций...")
            status_box = st.empty()

            def on_similarity_progress(current: int, total: int, best_score: Optional[float]) -> None:
                ratio = min(1.0, current / max(1, total))
                best_text = f" | best score {best_score:.3f}" if best_score is not None else ""
                progress_bar.progress(
                    ratio,
                    text=f"Проверено {current:,}/{total:,} окон{best_text}",
                )
                status_box.info("Ищу похожие участки в локальной истории. График не завис, идет перебор окон.")

            with st.spinner("Ищу похожие формации по локальной истории..."):
                similar_matches = find_similar_formations(
                    dataset=dataset,
                    start_idx=start_idx,
                    end_idx=end_idx,
                    forward_bars=forward_bars,
                    top_k=similar_count,
                    progress_callback=on_similarity_progress,
                )
            progress_bar.progress(1.0, text=f"Поиск завершен. Найдено: {len(similar_matches)}")
            status_box.success(f"Готово. Похожих формаций: {len(similar_matches)}")
            cached_similar = dict(cached_similar)
            cached_similar[similar_cache_key] = similar_matches
            st.session_state["selection_similar_cache"] = cached_similar
        find_col2.caption("Поиск запускается вручную, чтобы график не зависал после каждого выделения.")

        if similar_matches is None:
            st.info("Нажми `Find similar formations`, когда захочешь проверить похожие участки истории.")
        elif not similar_matches:
            st.warning("Похожих формаций по текущему выделению не найдено. Попробуй выделить больше свечей или изменить окно исследования.")
        else:
            similar_frame = pd.DataFrame(similar_matches)
            summary_col1, summary_col2 = st.columns(2)
            summary_col1.metric("Avg next move", f"{similar_frame['forward_change_pct'].mean():.2f}%")
            summary_col2.metric("Positive follow-through", f"{(similar_frame['forward_change_pct'] > 0).mean() * 100:.1f}%")
            summary_col3, summary_col4 = st.columns(2)
            summary_col3.metric("Avg breakout up", f"{similar_frame['breakout_up_pct'].mean():.2f}%")
            summary_col4.metric("Avg breakdown", f"{similar_frame['breakout_down_pct'].mean():.2f}%")
            st.dataframe(
                similar_frame[
                    [
                        "similarity_score",
                        "start_idx",
                        "end_idx",
                        "start_ts",
                        "end_ts",
                        "price_change_pct",
                        "forward_change_pct",
                        "breakout_up_pct",
                        "breakout_down_pct",
                        "volume_ratio",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
            match_options = list(range(len(similar_matches)))
            selected_match_idx = st.selectbox(
                "Open similar formation",
                options=match_options,
                format_func=lambda idx: (
                    f"#{idx + 1} | {similar_matches[idx]['start_ts']} -> {similar_matches[idx]['end_ts']} | "
                    f"sim={similar_matches[idx]['similarity_score']:.3f}"
                ),
            )
            if st.button("Open selected similar window", use_container_width=True, key="selection_open_similar_window"):
                selected_match = similar_matches[selected_match_idx]
                st.session_state["jump_to_market_context"] = {
                    "data_path": selection_context["data_path"],
                    "window_start_idx": int(selected_match["start_idx"]),
                    "window_end_idx": int(selected_match["end_idx"]),
                    "window_start_ts": selected_match["start_ts"],
                    "window_end_ts": selected_match["end_ts"],
                }
                st.rerun()

    with draft_tab:
        st.markdown("**Draft title**")
        st.code(draft["title"] or "-", language="text")
        st.markdown("**Draft thesis**")
        st.write(draft["thesis"] or "-")
        st.markdown("**Draft evidence**")
        st.write(draft["evidence"] or "-")
        action_col1, action_col2 = st.columns(2)
        if action_col1.button(
            "Send To Hypothesis Draft",
            type="primary",
            use_container_width=True,
            key="selection_send_hypothesis_draft",
        ):
            st.session_state["hypothesis_draft"] = draft
            st.session_state["draft_market_context"] = selection_context
            st.success("Черновик гипотезы подготовлен. Его можно сразу сохранить во вкладке Hypothesis Vault.")
        if action_col2.button("🗑 Сбросить выделение", use_container_width=True, key="selection_draft_clear_selection"):
            st.session_state.pop("selected_market_context", None)
            st.session_state.pop("draft_market_context", None)
            st.rerun()

    with impulse_tab:
        st.caption("Bybit дает минимальные готовые свечи `1m`, поэтому основной паттерн строим на минутном dollar volume и изменении цены.")
        impulse = build_minute_impulse_snapshot(selected, forward)
        metric_col1, metric_col2 = st.columns(2)
        metric_col1.metric("Dollar volume", f"{impulse['selected_dollar_volume']:,.0f} USDT")
        metric_col2.metric("Dollar volume ratio", f"{impulse['forward_dollar_volume_ratio']:.2f}x")
        metric_col3, metric_col4 = st.columns(2)
        metric_col3.metric("Impulse move", f"{price_change_pct:.2f}%")
        metric_col4.metric("Forward move", f"{forward_change_pct:.2f}%")
        st.dataframe(pd.DataFrame([impulse]), use_container_width=True, hide_index=True)
        st.info(
            "Чтобы проверить такой паттерн на всей истории, сохрани его в Hypothesis Vault и нажми `Study on history`. "
            "Там можно подкрутить `$ volume z`, `price return z`, длину последовательности, стоп и отмену."
        )
        impulse_lab_col, hypothesis_col = st.columns(2)
        if impulse_lab_col.button(
            "⚡ В лабораторию",
            type="primary",
            use_container_width=True,
            key="selection_send_to_impulse_lab",
        ):
            st.session_state["impulse_lab_selection"] = {
                "start_idx": start_idx,
                "end_idx": end_idx,
                "data_path": str(selection_context.get("data_path", "")),
                "symbol": current_symbol,
                "timeframe": current_timeframe,
                "start_ts": str(selection_context.get("window_start_ts", "")),
                "end_ts": str(selection_context.get("window_end_ts", "")),
            }
            st.session_state["main_section"] = "⚡ Impulse Lab"
            st.session_state["_nav_programmatic"] = True
            st.rerun()
        if hypothesis_col.button(
            "📝 В черновик гипотезы",
            type="secondary",
            use_container_width=True,
            key="selection_send_minute_impulse_draft",
        ):
            st.session_state["hypothesis_draft"] = {
                **draft,
                "tags": f"{draft.get('tags', '')},1m-impulse,dollar-volume".strip(","),
                "evidence": (
                    f"{draft.get('evidence', '')}\n"
                    f"Minute impulse snapshot: dollar_volume={impulse['selected_dollar_volume']:.2f}, "
                    f"avg_dollar_volume={impulse['avg_dollar_volume']:.2f}, "
                    f"forward_dollar_volume_ratio={impulse['forward_dollar_volume_ratio']:.2f}."
                ).strip(),
            }
            st.session_state["draft_market_context"] = selection_context
            st.session_state["main_section"] = "🧪 Hypothesis Lab"
            st.session_state["_nav_programmatic"] = True
            st.session_state["vault_section"] = "Capture"
            st.rerun()


def build_hypothesis_draft_from_selection(
    selection_context: dict,
    selected: pd.DataFrame,
    forward: pd.DataFrame,
    current_symbol: str,
    current_timeframe: str,
    forward_bars: int,
    price_change_pct: float,
    forward_change_pct: float,
    breakout_up_pct: float,
    breakout_down_pct: float,
    volume_ratio: float,
    selected_volume_z: float,
    similar_matches: list[dict],
) -> dict:
    direction = "bullish" if forward_change_pct >= 0 else "bearish"
    title = (
        f"{current_symbol or 'Market'} {current_timeframe or ''} {direction} "
        f"follow-through after selected formation"
    ).strip()
    thesis = (
        f"После формации на {len(selected)} свечей по {current_symbol or 'instrument'} {current_timeframe or ''} "
        f"рынок показал {forward_change_pct:.2f}% на следующих {forward_bars} свечах. "
        f"Нужно проверить, повторяется ли такой выход после схожего баланса объема и диапазона."
    )
    evidence = (
        f"Formation window: {selection_context['window_start_ts']} -> {selection_context['window_end_ts']}.\n"
        f"Price change inside formation: {price_change_pct:.2f}%.\n"
        f"Average volume z-score in formation: {selected_volume_z:.2f}.\n"
        f"Forward {forward_bars} candles close change: {forward_change_pct:.2f}%.\n"
        f"Breakout above formation high: {breakout_up_pct:.2f}%.\n"
        f"Breakdown below formation low: {breakout_down_pct:.2f}%.\n"
        f"Forward/formation volume ratio: {volume_ratio:.2f}x.\n"
        f"Similar history matches found: {len(similar_matches)}."
    )
    if similar_matches:
        avg_forward = sum(item["forward_change_pct"] for item in similar_matches) / len(similar_matches)
        avg_breakout = sum(item["breakout_up_pct"] for item in similar_matches) / len(similar_matches)
        positive_ratio = sum(1 for item in similar_matches if item["forward_change_pct"] > 0) / len(similar_matches)
        evidence += (
            f"\nAverage forward move across similar formations: {avg_forward:.2f}%."
            f"\nAverage breakout above high across similar formations: {avg_breakout:.2f}%."
            f"\nPositive follow-through ratio across similar formations: {positive_ratio * 100:.1f}%."
        )
    tags = f"{(current_symbol or '').lower()},formation,volume,breakout,{direction}"
    return {
        "title": title,
        "thesis": thesis,
        "evidence": evidence,
        "tags": tags,
        "symbol": current_symbol,
        "timeframe": current_timeframe,
    }


def build_selection_similarity_cache_key(selection_context: dict, forward_bars: int, top_k: int) -> str:
    return "|".join(
        [
            str(selection_context.get("data_path", "")),
            str(selection_context.get("window_start_idx", "")),
            str(selection_context.get("window_end_idx", "")),
            str(forward_bars),
            str(top_k),
        ]
    )


def build_minute_impulse_snapshot(selected: pd.DataFrame, forward: pd.DataFrame) -> dict:
    selected_turnover = estimate_dollar_volume(selected)
    forward_turnover = estimate_dollar_volume(forward)
    selected_sum = float(selected_turnover.sum()) if len(selected_turnover) else 0.0
    forward_sum = float(forward_turnover.sum()) if len(forward_turnover) else 0.0
    selected_mean = float(selected_turnover.mean()) if len(selected_turnover) else 0.0
    forward_mean = float(forward_turnover.mean()) if len(forward_turnover) else 0.0
    selected_open = float(selected["open"].iloc[0]) if len(selected) else 0.0
    selected_close = float(selected["close"].iloc[-1]) if len(selected) else 0.0
    forward_close = float(forward["close"].iloc[-1]) if len(forward) else selected_close
    return {
        "selected_bars": int(len(selected)),
        "forward_bars": int(len(forward)),
        "selected_dollar_volume": selected_sum,
        "avg_dollar_volume": selected_mean,
        "forward_dollar_volume": forward_sum,
        "forward_avg_dollar_volume": forward_mean,
        "forward_dollar_volume_ratio": forward_mean / selected_mean if selected_mean else 0.0,
        "selected_price_change_pct": (selected_close / selected_open - 1) * 100 if selected_open else 0.0,
        "forward_price_change_pct": (forward_close / selected_close - 1) * 100 if selected_close else 0.0,
        "max_selected_dollar_volume": float(selected_turnover.max()) if len(selected_turnover) else 0.0,
    }


def estimate_dollar_volume(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=float)
    if "turnover" in frame.columns:
        turnover = pd.to_numeric(frame["turnover"], errors="coerce")
        if turnover.notna().any():
            return turnover.fillna(0.0)
    close = pd.to_numeric(frame["close"], errors="coerce").fillna(0.0)
    volume = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    return close * volume


def find_similar_formations(
    dataset: pd.DataFrame,
    start_idx: int,
    end_idx: int,
    forward_bars: int,
    top_k: int,
    progress_callback: Optional[Callable[[int, int, Optional[float]], None]] = None,
) -> list[dict]:
    selected = dataset.iloc[start_idx:end_idx + 1].copy()
    window_len = len(selected)
    min_gap = max(3, window_len // 2)
    if window_len < 2:
        return []

    target_signature = build_formation_signature(selected)
    matches = []
    max_start = len(dataset) - window_len - forward_bars
    total_candidates = max(0, max_start + 1)
    best_score: Optional[float] = None
    for candidate_start in range(0, max_start + 1):
        candidate_end = candidate_start + window_len - 1
        processed = candidate_start + 1
        if progress_callback is not None and (processed == 1 or processed == total_candidates or processed % 250 == 0):
            progress_callback(processed, total_candidates, best_score)
        if abs(candidate_start - start_idx) < min_gap:
            continue
        candidate = dataset.iloc[candidate_start:candidate_end + 1].copy()
        if len(candidate) != window_len:
            continue
        candidate_signature = build_formation_signature(candidate)
        similarity = signature_distance(target_signature, candidate_signature)
        if not np.isfinite(similarity):
            continue
        best_score = similarity if best_score is None else min(best_score, similarity)
        forward = dataset.iloc[candidate_end + 1:candidate_end + 1 + forward_bars].copy()
        if len(forward) < max(3, min(forward_bars, 5)):
            continue
        matches.append(
            build_similar_match_record(
                candidate=candidate,
                forward=forward,
                start_idx=candidate_start,
                end_idx=candidate_end,
                similarity=similarity,
            )
        )

    matches.sort(key=lambda item: item["similarity_score"])
    if progress_callback is not None:
        progress_callback(total_candidates, total_candidates, best_score)
    return matches[:top_k]


def build_formation_signature(frame: pd.DataFrame) -> dict:
    close_path = frame["close"].to_numpy(dtype=float)
    base_close = close_path[0] if len(close_path) else 1.0
    normalized_close = ((close_path / base_close) - 1.0).tolist() if base_close else close_path.tolist()

    volume_path = frame["volume"].to_numpy(dtype=float)
    volume_mean = float(volume_path.mean()) if len(volume_path) else 1.0
    normalized_volume = ((volume_path / volume_mean) - 1.0).tolist() if volume_mean else volume_path.tolist()

    range_path = ((frame["high"] / frame["low"]) - 1.0).replace([np.inf, -np.inf], 0.0).fillna(0.0).to_numpy(dtype=float).tolist()
    volume_z_mean = safe_float(frame["volume_z"].mean()) if "volume_z" in frame else 0.0
    ret_fast_mean = safe_float(frame["ret_fast"].mean()) if "ret_fast" in frame else 0.0
    return {
        "close_path": resample_series(normalized_close, 12),
        "volume_path": resample_series(normalized_volume, 12),
        "range_path": resample_series(range_path, 12),
        "volume_z_mean": volume_z_mean,
        "ret_fast_mean": ret_fast_mean,
    }


def resample_series(values: list[float], target_size: int) -> list[float]:
    if not values:
        return [0.0] * target_size
    if len(values) == 1:
        return [values[0]] * target_size
    values = [safe_float(value) for value in values]
    source_index = np.linspace(0.0, 1.0, num=len(values))
    target_index = np.linspace(0.0, 1.0, num=target_size)
    return np.interp(target_index, source_index, values).tolist()


def signature_distance(left: dict, right: dict) -> float:
    left_vec = np.array(
        left["close_path"] + left["volume_path"] + left["range_path"] + [left["volume_z_mean"], left["ret_fast_mean"]],
        dtype=float,
    )
    right_vec = np.array(
        right["close_path"] + right["volume_path"] + right["range_path"] + [right["volume_z_mean"], right["ret_fast_mean"]],
        dtype=float,
    )
    return float(np.linalg.norm(left_vec - right_vec))


def safe_float(value: object) -> float:
    try:
        output = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(output):
        return 0.0
    return output


def find_second_level_source(root: Path, symbol: str, selection_context: dict) -> Optional[Path]:
    symbol_key = (symbol or "").lower()
    candidates = []
    for path in root.glob("**/*.csv"):
        name = path.stem.lower()
        if symbol_key and symbol_key not in name:
            continue
        if "1s" not in name and "sec" not in name and "second" not in name:
            continue
        candidates.append(path)
    if not candidates:
        return None

    target_start = pd.Timestamp(selection_context["window_start_ts"], tz="UTC")
    target_end = pd.Timestamp(selection_context["window_end_ts"], tz="UTC")
    for path in sorted(candidates):
        try:
            frame = pd.read_csv(path, usecols=["timestamp"])
        except Exception:
            continue
        if frame.empty:
            continue
        timestamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dropna()
        if timestamps.empty:
            continue
        if timestamps.min() <= target_start and timestamps.max() >= target_end:
            return path
    return candidates[0] if candidates else None


def analyze_second_exit(source_path: Path, selection_context: dict, horizon_seconds: int) -> Optional[dict]:
    frame = pd.read_csv(source_path)
    if "timestamp" not in frame.columns or "close" not in frame.columns or "volume" not in frame.columns:
        return None
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if frame.empty:
        return None

    start_ts = pd.Timestamp(selection_context["window_end_ts"], tz="UTC")
    end_ts = start_ts + pd.Timedelta(seconds=horizon_seconds)
    second_slice = frame[(frame["timestamp"] >= start_ts) & (frame["timestamp"] <= end_ts)].copy()
    if len(second_slice) < max(20, horizon_seconds // 6):
        return None

    second_slice["close"] = pd.to_numeric(second_slice["close"], errors="coerce")
    second_slice["volume"] = pd.to_numeric(second_slice["volume"], errors="coerce").fillna(0.0)
    second_slice = second_slice.dropna(subset=["close"])
    if second_slice.empty:
        return None

    start_close = float(second_slice["close"].iloc[0])
    end_close = float(second_slice["close"].iloc[-1])
    max_up = float(second_slice["close"].max())
    max_down = float(second_slice["close"].min())
    volume_mean = float(second_slice["volume"].mean()) if len(second_slice) else 0.0
    burst_volume = float(second_slice["volume"].quantile(0.95))
    burst_ratio = burst_volume / volume_mean if volume_mean else 0.0
    close_change_pct = (end_close / start_close - 1) * 100 if start_close else 0.0
    max_up_pct = (max_up / start_close - 1) * 100 if start_close else 0.0
    max_down_pct = (max_down / start_close - 1) * 100 if start_close else 0.0

    per_second_returns = second_slice["close"].pct_change().fillna(0.0).to_numpy(dtype=float)
    per_second_volume = second_slice["volume"].to_numpy(dtype=float)
    matcher_template = {
        "symbol": selection_context.get("symbol", ""),
        "source_path": str(source_path),
        "selection_end_ts": selection_context["window_end_ts"],
        "horizon_seconds": horizon_seconds,
        "returns_signature": resample_series(per_second_returns.tolist(), 60),
        "volume_signature": resample_series(
            (((per_second_volume / volume_mean) - 1.0).tolist() if volume_mean else per_second_volume.tolist()),
            60,
        ),
        "close_change_pct": close_change_pct,
        "max_up_pct": max_up_pct,
        "max_down_pct": max_down_pct,
        "burst_volume_ratio": burst_ratio,
    }
    return {
        "close_change_pct": close_change_pct,
        "max_up_pct": max_up_pct,
        "max_down_pct": max_down_pct,
        "burst_volume_ratio": burst_ratio,
        "summary_row": {
            "source_path": str(source_path),
            "seconds_loaded": int(len(second_slice)),
            "start_ts": str(second_slice["timestamp"].iloc[0]),
            "end_ts": str(second_slice["timestamp"].iloc[-1]),
            "close_change_pct": close_change_pct,
            "max_up_pct": max_up_pct,
            "max_down_pct": max_down_pct,
            "burst_volume_ratio": burst_ratio,
        },
        "matcher_template": matcher_template,
    }


def analyze_second_pattern(source_path: Path, selection_context: dict, expected_move_pct: float) -> Optional[dict]:
    frame = pd.read_csv(source_path)
    if "timestamp" not in frame.columns or "close" not in frame.columns or "volume" not in frame.columns:
        return None
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if frame.empty:
        return None

    start_ts = pd.Timestamp(selection_context["window_start_ts"], tz="UTC")
    end_ts = pd.Timestamp(selection_context["window_end_ts"], tz="UTC")
    second_slice = frame[(frame["timestamp"] >= start_ts) & (frame["timestamp"] <= end_ts)].copy()
    if len(second_slice) < 20:
        return None

    second_slice["close"] = pd.to_numeric(second_slice["close"], errors="coerce")
    second_slice["volume"] = pd.to_numeric(second_slice["volume"], errors="coerce").fillna(0.0)
    second_slice = second_slice.dropna(subset=["close"])
    if second_slice.empty:
        return None

    trigger_side = "long" if expected_move_pct >= 0 else "short"
    matcher_template = build_pattern_matcher_template(
        symbol=selection_context.get("symbol", ""),
        source_path=source_path,
        selection_start_ts=selection_context["window_start_ts"],
        selection_end_ts=selection_context["window_end_ts"],
        horizon_seconds=int((second_slice["timestamp"].iloc[-1] - second_slice["timestamp"].iloc[0]).total_seconds()),
        close_values=second_slice["close"].tolist(),
        volume_values=second_slice["volume"].tolist(),
        trigger_side=trigger_side,
        expected_move_pct=expected_move_pct,
    )
    volume_mean = float(second_slice["volume"].mean()) if len(second_slice) else 0.0
    burst_ratio = float(second_slice["volume"].max() / volume_mean) if volume_mean else 0.0
    pattern_frame = build_second_pattern_frame(second_slice)
    return {
        "summary_row": {
            "source_path": str(source_path),
            "seconds_loaded": int(len(second_slice)),
            "start_ts": str(second_slice["timestamp"].iloc[0]),
            "end_ts": str(second_slice["timestamp"].iloc[-1]),
            "trigger_side": trigger_side,
            "expected_move_pct": expected_move_pct,
            "burst_volume_ratio": burst_ratio,
        },
        "pattern_frame": pattern_frame,
        "profile_frame": pd.DataFrame(matcher_template.get("phase_profile", [])),
        "matcher_template": matcher_template,
    }


def build_second_pattern_frame(second_slice: pd.DataFrame) -> pd.DataFrame:
    frame = second_slice.copy()
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame["second_offset"] = np.arange(len(frame), dtype=int)
    base_close = float(frame["close"].iloc[0]) if len(frame) else 0.0
    volume_mean = float(frame["volume"].mean()) if len(frame) else 0.0
    volume_std = float(frame["volume"].std()) if len(frame) else 0.0
    frame["close_change_pct"] = ((frame["close"] / base_close) - 1.0) * 100 if base_close else 0.0
    if volume_std:
        frame["volume_z"] = (frame["volume"] - volume_mean) / volume_std
    else:
        frame["volume_z"] = 0.0
    return frame[["timestamp", "second_offset", "close", "volume", "close_change_pct", "volume_z"]]


def build_second_pattern_chart(pattern_frame: pd.DataFrame) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.55, 0.45],
    )
    fig.add_trace(
        go.Scatter(
            x=pattern_frame["second_offset"],
            y=pattern_frame["close_change_pct"],
            mode="lines",
            name="close_change_pct",
            line=dict(color="#4ecdc4", width=2),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=pattern_frame["second_offset"],
            y=pattern_frame["volume"],
            name="volume",
            marker_color="#ff8c42",
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=pattern_frame["second_offset"],
            y=pattern_frame["volume_z"],
            mode="lines",
            name="volume_z",
            line=dict(color="#ffd166", width=2),
        ),
        row=2,
        col=1,
    )
    fig.update_layout(
        height=420,
        margin=dict(l=20, r=20, t=20, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0.0),
    )
    fig.update_xaxes(title_text="Second offset", row=2, col=1)
    fig.update_yaxes(title_text="Close change %", row=1, col=1)
    fig.update_yaxes(title_text="Volume / z", row=2, col=1)
    return fig


def flatten_risk_plan(risk_plan: dict) -> dict:
    cancel_rule = risk_plan.get("cancel_rule", {}) or {}
    return {
        "trigger_side": risk_plan.get("trigger_side", ""),
        "fixed_stop_loss_pct": safe_float(risk_plan.get("fixed_stop_loss_pct", 0.0)),
        "take_profit_pct": safe_float(risk_plan.get("take_profit_pct", 0.0)),
        "min_reward_risk": safe_float(risk_plan.get("min_reward_risk", 0.0)),
        "cancel_no_follow_seconds": int(cancel_rule.get("no_follow_seconds", 0) or 0),
        "cancel_no_follow_move_pct": safe_float(cancel_rule.get("no_follow_move_pct", 0.0)),
        "pattern_invalidation_move_pct": safe_float(cancel_rule.get("pattern_invalidation_move_pct", 0.0)),
    }


def render_trade_validation_summary(future_frame: pd.DataFrame) -> None:
    val_col1, val_col2 = st.columns(2)
    val_col1.metric("Matches", len(future_frame))
    val_col2.metric("Win rate", f"{(future_frame['is_profitable'].mean() * 100):.1f}%")
    val_col3, val_col4 = st.columns(2)
    val_col3.metric("Avg realized after stop/cancel", f"{future_frame['realized_move_pct'].mean():.2f}%")
    val_col4.metric("Avg adverse before exit", f"{future_frame['adverse_move_pct'].mean():.2f}%")
    if "exit_reason" in future_frame:
        reason_frame = (
            future_frame["exit_reason"]
            .value_counts()
            .rename_axis("exit_reason")
            .reset_index(name="count")
        )
        st.dataframe(reason_frame, use_container_width=True, hide_index=True)


def load_json_dict(raw: object) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        payload = json.loads(str(raw))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_strategy_params(raw: object) -> dict:
    params = DEFAULT_STRATEGY_PARAMS.copy()
    params.update(load_json_dict(raw))
    return params


def extract_validation_metric(raw: object, key: str) -> float:
    payload = load_json_dict(raw)
    value = payload.get(key, 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_record_from_row(row: dict, **overrides: object) -> HypothesisRecord:
    payload = {**row, **overrides}
    return HypothesisRecord(
        title=str(payload.get("title", "")),
        thesis=str(payload.get("thesis", "")),
        evidence=str(payload.get("evidence", "")),
        tags=str(payload.get("tags", "")),
        symbol=str(payload.get("symbol", "")),
        timeframe=str(payload.get("timeframe", "")),
        status=str(payload.get("status", "new")),
        score=float(payload.get("score", 0.0) or 0.0),
        data_path=str(payload.get("data_path", "")),
        window_start_idx=int(payload.get("window_start_idx", -1) or -1),
        window_end_idx=int(payload.get("window_end_idx", -1) or -1),
        window_start_ts=str(payload.get("window_start_ts", "")),
        window_end_ts=str(payload.get("window_end_ts", "")),
        strategy_params=str(payload.get("strategy_params", "")),
        validation_result=str(payload.get("validation_result", "")),
        paper_status=str(payload.get("paper_status", "")),
    )


def save_strategy_params(row: dict, params: dict, status: str) -> None:
    update_hypothesis(
        hypothesis_id=int(row["id"]),
        record=build_record_from_row(
            row,
            strategy_params=json.dumps(params, ensure_ascii=False),
            status=status,
        ),
    )
    cached_list_hypotheses_full.clear()


def save_validation_result(row: dict, params: dict, result: dict) -> None:
    update_hypothesis(
        hypothesis_id=int(row["id"]),
        record=build_record_from_row(
            row,
            strategy_params=json.dumps(params, ensure_ascii=False),
            validation_result=json.dumps(result, ensure_ascii=False),
            score=float(result.get("win_rate", 0.0)),
            status="paper_ready" if result.get("paper_ready") else "testing",
        ),
    )
    cached_list_hypotheses_full.clear()


def enqueue_hypothesis_for_paper(row: dict, params: dict, validation: dict) -> Path:
    target_dir = Path.cwd() / ".local_ai" / "paper_queue"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"hypothesis_{int(row['id'])}_paper.json"
    target_path.write_text(
        json.dumps(
            {
                "hypothesis_id": int(row["id"]),
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "symbol": row.get("symbol", ""),
                "timeframe": row.get("timeframe", ""),
                "title": row.get("title", ""),
                "paper_state": "queued",
                "strategy_params": params,
                "validation_result": validation,
                "risk_rule": {
                    "max_deposit_risk_pct": params.get("account_risk_pct", 0.10),
                    "position_sizing": "position_notional = deposit * risk_pct / stop_loss_pct",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target_path


def discover_paper_queue(root: Path) -> list[dict]:
    target_dir = root / ".local_ai" / "paper_queue"
    if not target_dir.exists():
        return []
    items = []
    for path in sorted(target_dir.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            payload["_path"] = str(path)
            items.append(payload)
    return items


def validate_impulse_hypothesis_on_history(row: dict, params: dict) -> dict:
    data_path_str = str(row.get("data_path", "")).strip()
    
    # Если гипотеза была сохранена без пути к файлу, пробуем использовать текущий загруженный график
    if not data_path_str:
        current_context = st.session_state.get("draft_market_context") or st.session_state.get("market_context", {})
        data_path_str = current_context.get("data_path", "")
        
    data_path = Path(data_path_str) if data_path_str else None
    
    if not data_path or not data_path.is_file():
        return {
            "decision": "no_data",
            "trades": 0,
            "win_rate": 0.0,
            "error": f"Linked CSV not found or invalid: {data_path_str or 'None'}",
        }

    frame = pd.read_csv(data_path)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return {
            "decision": "invalid_data",
            "trades": 0,
            "win_rate": 0.0,
            "error": f"CSV must contain {sorted(required)}",
        }

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"]).sort_values("timestamp").reset_index(drop=True)
    if len(frame) < int(params["lookback_bars"]) + int(params["max_hold_bars"]) + 10:
        return {
            "decision": "not_enough_data",
            "trades": 0,
            "win_rate": 0.0,
            "error": "Not enough bars for selected lookback/hold settings.",
        }

    turnover = pd.to_numeric(frame["turnover"], errors="coerce") if "turnover" in frame else frame["close"] * frame["volume"]
    frame["dollar_volume"] = turnover.fillna(frame["close"] * frame["volume"])
    frame["ret_pct"] = frame["close"].pct_change().fillna(0.0) * 100
    lookback = int(params["lookback_bars"])
    frame["dollar_volume_z"] = rolling_zscore_without_lookahead(frame["dollar_volume"], lookback)
    frame["abs_ret_z"] = rolling_zscore_without_lookahead(frame["ret_pct"].abs(), lookback)
    frame["direction"] = np.sign(frame["ret_pct"]).astype(int)

    if int(params.get("trend_ema_period", 0)) > 0:
        frame["trend_ema"] = frame["close"].ewm(span=int(params["trend_ema_period"]), adjust=False).mean()

    events = find_impulse_sequences(frame, params)
    outcomes = [simulate_impulse_trade(frame, event, params) for event in events]
    outcomes = [item for item in outcomes if item.get("entry_ts")]
    wins = [item for item in outcomes if item.get("is_profitable")]
    win_rate = len(wins) / len(outcomes) if outcomes else 0.0
    paper_ready = bool(outcomes) and win_rate >= float(params["paper_win_rate_threshold"])
    exit_reasons = pd.Series([item.get("exit_reason", "") for item in outcomes]).value_counts().to_dict() if outcomes else {}

    return {
        "decision": "paper_ready" if paper_ready else "needs_work",
        "paper_ready": paper_ready,
        "studied_at": datetime.now(timezone.utc).isoformat(),
        "symbol": row.get("symbol", ""),
        "timeframe": row.get("timeframe", ""),
        "data_path": str(data_path),
        "params": params,
        "events_found": len(events),
        "trades": len(outcomes),
        "win_rate": win_rate,
        "avg_realized_move_pct": float(np.mean([item["realized_move_pct"] for item in outcomes])) if outcomes else 0.0,
        "avg_adverse_move_pct": float(np.mean([item["adverse_move_pct"] for item in outcomes])) if outcomes else 0.0,
        "stop_hit_rate": float(np.mean([item["exit_reason"] == "fixed_stop" for item in outcomes])) if outcomes else 0.0,
        "cancel_rate": float(np.mean([item["exit_reason"] == "cancel_no_follow" for item in outcomes])) if outcomes else 0.0,
        "exit_reasons": exit_reasons,
        "sample_trades": outcomes[:50],
    }


def rolling_zscore_without_lookahead(series: pd.Series, lookback: int) -> pd.Series:
    baseline = series.shift(1)
    mean = baseline.rolling(lookback, min_periods=max(10, lookback // 4)).mean()
    std = baseline.rolling(lookback, min_periods=max(10, lookback // 4)).std().replace(0, np.nan)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def find_impulse_sequences(frame: pd.DataFrame, params: dict) -> list[dict]:
    min_volume_z = float(params["min_dollar_volume_z"])
    min_ret_z = float(params["min_price_return_z"])
    min_sequence = int(params["min_sequence_bars"])
    max_sequence = int(params["max_sequence_bars"])
    events = []
    idx = int(params["lookback_bars"])
    last_entry_idx = len(frame) - int(params["max_hold_bars"]) - 2
    while idx < last_entry_idx:
        row = frame.iloc[idx]
        direction = int(row["direction"])
        is_trigger = row["dollar_volume_z"] >= min_volume_z and row["abs_ret_z"] >= min_ret_z and direction != 0
        
        if is_trigger and "trend_ema" in frame.columns and pd.notna(row["trend_ema"]):
            if direction > 0 and row["close"] < row["trend_ema"]:
                is_trigger = False
            elif direction < 0 and row["close"] > row["trend_ema"]:
                is_trigger = False

        if not is_trigger:
            idx += 1
            continue

        end_idx = idx
        while end_idx + 1 < last_entry_idx and end_idx - idx + 1 < max_sequence:
            next_row = frame.iloc[end_idx + 1]
            if int(next_row["direction"]) != direction:
                break
            if next_row["dollar_volume_z"] < min_volume_z * 0.55 and next_row["abs_ret_z"] < min_ret_z * 0.55:
                break
            end_idx += 1

        if end_idx - idx + 1 >= min_sequence:
            events.append(
                {
                    "start_idx": int(idx),
                    "end_idx": int(end_idx),
                    "direction": "long" if direction > 0 else "short",
                    "start_ts": str(frame["timestamp"].iloc[idx]),
                    "end_ts": str(frame["timestamp"].iloc[end_idx]),
                    "impulse_start_price": float(frame["open"].iloc[idx]),
                    "impulse_high": float(frame["high"].iloc[idx:end_idx + 1].max()),
                    "impulse_low": float(frame["low"].iloc[idx:end_idx + 1].min()),
                    "max_dollar_volume_z": float(frame["dollar_volume_z"].iloc[idx:end_idx + 1].max()),
                    "max_abs_ret_z": float(frame["abs_ret_z"].iloc[idx:end_idx + 1].max()),
                    "sequence_return_pct": float((frame["close"].iloc[end_idx] / frame["open"].iloc[idx] - 1) * 100),
                }
            )
            idx = end_idx + int(params["max_hold_bars"])
            continue
        idx += 1
    return events


def simulate_impulse_trade(frame: pd.DataFrame, event: dict, params: dict) -> dict:
    entry_idx = int(event["start_idx"]) + int(params["entry_after_bars"])
    max_hold = int(params["max_hold_bars"])
    if entry_idx >= len(frame) - 1:
        return {}
    exit_end_idx = min(len(frame) - 1, entry_idx + max_hold)
    direction = str(event["direction"])
    close_at_entry = float(frame["close"].iloc[entry_idx])
    impulse_start = float(event["impulse_start_price"])
    entry_price = close_at_entry
    
    pullback_pct = float(params.get("entry_pullback_pct", 0.0))
    if pullback_pct > 0:
        if direction == "long":
            entry_price = close_at_entry - (close_at_entry - impulse_start) * pullback_pct
        else:
            entry_price = close_at_entry + (impulse_start - close_at_entry) * pullback_pct

    actual_entry_idx = -1
    for i in range(entry_idx, exit_end_idx + 1):
        if direction == "long" and float(frame["low"].iloc[i]) <= entry_price:
            actual_entry_idx = i
            break
        if direction == "short" and float(frame["high"].iloc[i]) >= entry_price:
            actual_entry_idx = i
            break
            
    if actual_entry_idx == -1:
        return {} # Order never filled
    
    entry_idx = actual_entry_idx

    if entry_price <= 0:
        return {}

    stop_pct = float(params["fixed_stop_loss_pct"])
    if params.get("use_dynamic_stop", False):
        if direction == "long":
            impulse_low = float(event.get("impulse_low", entry_price))
            stop_pct = max(0.1, (entry_price - impulse_low) / entry_price * 100)
        else:
            impulse_high = float(event.get("impulse_high", entry_price))
            stop_pct = max(0.1, (impulse_high - entry_price) / entry_price * 100)

    take_profit_pct = stop_pct * float(params["take_profit_rr"])
    cancel_bars = int(params["cancel_if_no_follow_bars"])
    cancel_min_follow_pct = float(params["cancel_min_follow_pct"])
    
    breakeven_at_rr = float(params.get("breakeven_at_rr", 0.0))
    breakeven_pct = stop_pct * breakeven_at_rr if breakeven_at_rr > 0 else float('inf')

    exit_idx = exit_end_idx
    exit_reason = "time_exit"
    best_favorable = 0.0
    worst_adverse = 0.0
    realized = 0.0
    adverse_trigger = stop_pct
    
    partial_tp_at_be = bool(params.get("partial_tp_at_be", False))
    partial_profit_taken = False

    for idx in range(entry_idx + 1, exit_end_idx + 1):
        high = float(frame["high"].iloc[idx])
        low = float(frame["low"].iloc[idx])
        close = float(frame["close"].iloc[idx])
        if direction == "short":
            favorable = (1.0 - low / entry_price) * 100
            adverse = (high / entry_price - 1.0) * 100
            close_move = (1.0 - close / entry_price) * 100
        else:
            favorable = (high / entry_price - 1.0) * 100
            adverse = (1.0 - low / entry_price) * 100
            close_move = (close / entry_price - 1.0) * 100
        best_favorable = max(best_favorable, favorable)
        worst_adverse = max(worst_adverse, adverse)

        bars_held = idx - entry_idx
        
        if best_favorable >= breakeven_pct and adverse_trigger > 0:
            adverse_trigger = -0.05 # small buffer
            if partial_tp_at_be and not partial_profit_taken:
                partial_profit_taken = True
                realized += breakeven_pct * 0.5 # 50% volume closed in profit

        if adverse >= adverse_trigger:
            exit_idx = idx
            if adverse_trigger <= 0:
                exit_reason = "partial_tp_breakeven" if partial_profit_taken else "breakeven"
            else:
                exit_reason = "fixed_stop"
                
            if partial_profit_taken:
                realized += -adverse_trigger * 0.5
            else:
                realized = -adverse_trigger
            break
        if favorable >= take_profit_pct:
            exit_idx = idx
            exit_reason = "take_profit"
            if partial_profit_taken:
                realized += take_profit_pct * 0.5
            else:
                realized = take_profit_pct
            break
        if bars_held >= cancel_bars and best_favorable < cancel_min_follow_pct:
            exit_idx = idx
            exit_reason = "cancel_no_follow"
            if partial_profit_taken:
                realized += close_move * 0.5
            else:
                realized = close_move
            break
        realized = close_move

    return {
        **event,
        "entry_idx": int(entry_idx),
        "entry_ts": str(frame["timestamp"].iloc[entry_idx]),
        "entry_price": entry_price,
        "exit_idx": int(exit_idx),
        "exit_ts": str(frame["timestamp"].iloc[exit_idx]),
        "exit_reason": exit_reason,
        "stop_loss_pct": stop_pct,
        "take_profit_pct": take_profit_pct,
        "realized_move_pct": float(realized),
        "favorable_move_pct": float(best_favorable),
        "adverse_move_pct": float(worst_adverse),
        "bars_held": int(exit_idx - entry_idx),
        "is_profitable": realized > 0,
    }


def scan_future_second_pattern_matches(
    source_path: Path,
    matcher_template: dict,
    selection_context: dict,
    validation_horizon_seconds: int,
    top_k: int,
) -> list[dict]:
    frame = pd.read_csv(source_path)
    if "timestamp" not in frame.columns or "close" not in frame.columns or "volume" not in frame.columns:
        return []
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)
    frame = frame.dropna(subset=["timestamp", "close"]).sort_values("timestamp").reset_index(drop=True)
    if frame.empty:
        return []

    selection_end_ts = pd.Timestamp(selection_context["window_end_ts"], tz="UTC")
    future = frame[frame["timestamp"] > selection_end_ts].reset_index(drop=True)
    window_seconds = int(matcher_template.get("template_seconds", 0))
    if len(future) < window_seconds + validation_horizon_seconds or window_seconds < 20:
        return []

    matches = []
    step = max(5, window_seconds // 4)
    index = 0
    while index + window_seconds + validation_horizon_seconds <= len(future):
        candidate = future.iloc[index:index + window_seconds].copy()
        after = future.iloc[index + window_seconds:index + window_seconds + validation_horizon_seconds].copy()
        signal = evaluate_trade_signal(
            matcher_template,
            close_values=candidate["close"].tolist(),
            volume_values=candidate["volume"].tolist(),
        )
        if signal["is_signal"]:
            entry_price = float(candidate["close"].iloc[-1])
            path_result = evaluate_trade_path(
                trigger_side=str(matcher_template.get("trigger_side", "long")),
                entry_price=entry_price,
                future_close_values=after["close"].tolist(),
                risk_plan=matcher_template.get("risk_plan", {}) or {},
            )
            matches.append(
                {
                    "match_start_ts": str(candidate["timestamp"].iloc[0]),
                    "match_end_ts": str(candidate["timestamp"].iloc[-1]),
                    "action": signal["action"],
                    "score": signal["score"],
                    "close_score": signal["close_score"],
                    "volume_score": signal["volume_score"],
                    "burst_volume_ratio": signal["burst_volume_ratio"],
                    "dominant_phase": signal["dominant_phase"],
                    "stop_loss_pct": signal.get("stop_loss_pct", 0.0),
                    "take_profit_pct": signal.get("take_profit_pct", 0.0),
                    "exit_reason": path_result["exit_reason"],
                    "realized_move_pct": path_result["realized_move_pct"],
                    "favorable_move_pct": path_result["favorable_move_pct"],
                    "adverse_move_pct": path_result["adverse_move_pct"],
                    "seconds_to_exit": path_result["seconds_to_exit"],
                    "is_profitable": path_result["realized_move_pct"] > 0,
                }
            )
            index += window_seconds
            continue
        index += step

    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:top_k]


def build_similar_match_record(
    candidate: pd.DataFrame,
    forward: pd.DataFrame,
    start_idx: int,
    end_idx: int,
    similarity: float,
) -> dict:
    candidate_open = float(candidate["open"].iloc[0])
    candidate_close = float(candidate["close"].iloc[-1])
    candidate_high = float(candidate["high"].max())
    candidate_low = float(candidate["low"].min())
    candidate_volume_mean = float(candidate["volume"].mean())
    forward_close = float(forward["close"].iloc[-1])
    forward_high = float(forward["high"].max())
    forward_low = float(forward["low"].min())
    forward_volume_mean = float(forward["volume"].mean())
    return {
        "similarity_score": similarity,
        "start_idx": int(start_idx),
        "end_idx": int(end_idx),
        "start_ts": str(candidate["timestamp"].iloc[0]),
        "end_ts": str(candidate["timestamp"].iloc[-1]),
        "price_change_pct": (candidate_close / candidate_open - 1) * 100 if candidate_open else 0.0,
        "forward_change_pct": (forward_close / candidate_close - 1) * 100 if candidate_close else 0.0,
        "breakout_up_pct": (forward_high / candidate_high - 1) * 100 if candidate_high else 0.0,
        "breakout_down_pct": (forward_low / candidate_low - 1) * 100 if candidate_low else 0.0,
        "volume_ratio": forward_volume_mean / candidate_volume_mean if candidate_volume_mean else 0.0,
    }


def build_chart(view: pd.DataFrame, include_model: bool, selection_mode: str = "box") -> go.Figure:
    rows = 4 if include_model else 3
    heights = [0.5, 0.17, 0.17, 0.16] if include_model else [0.6, 0.2, 0.2]
    up_color = "#22ab94"
    down_color = "#f23645"
    volume_colors = np.where(view["close"] >= view["open"], up_color, down_color)
    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=heights,
    )

    fig.add_trace(
        go.Candlestick(
            x=view["timestamp"],
            open=view["open"],
            high=view["high"],
            low=view["low"],
            close=view["close"],
            name="OHLC",
            increasing_line_color=up_color,
            increasing_fillcolor=up_color,
            decreasing_line_color=down_color,
            decreasing_fillcolor=down_color,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=view["timestamp"],
            y=view["close"],
            mode="markers",
            marker=dict(size=14, opacity=0.02, color="#1f77b4"),
            customdata=view[["absolute_index", "bucket_end_index"]].to_numpy()
            if "bucket_end_index" in view.columns
            else view["absolute_index"],
            name="selector",
            showlegend=False,
            hoverinfo="skip",
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Bar(
            x=view["timestamp"],
            y=view["volume"],
            name="Volume",
            marker_color=volume_colors,
            opacity=0.72,
        ),
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Scatter(x=view["timestamp"], y=view["ret_fast"], name="ret_fast", mode="lines"),
        row=3,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=view["timestamp"], y=view["volume_z"], name="volume_z", mode="lines"),
        row=3,
        col=1,
    )

    if include_model:
        fig.add_trace(
            go.Scatter(x=view["timestamp"], y=view["prob_short"], name="prob_short", mode="lines"),
            row=4,
            col=1,
        )
        fig.add_trace(
            go.Scatter(x=view["timestamp"], y=view["prob_flat"], name="prob_flat", mode="lines"),
            row=4,
            col=1,
        )
        fig.add_trace(
            go.Scatter(x=view["timestamp"], y=view["prob_long"], name="prob_long", mode="lines"),
            row=4,
            col=1,
        )

    last_close = float(view["close"].iloc[-1]) if len(view) else 0.0
    fig.add_hline(
        y=last_close,
        line_width=1,
        line_dash="dot",
        line_color="#f0b90b",
        annotation_text=f"{last_close:.6g}",
        annotation_position="right",
        row=1,
        col=1,
    )
    add_utc_day_boundaries(fig, view)

    fig.update_layout(
        height=980,
        xaxis_rangeslider_visible=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        legend_orientation="h",
        margin=dict(l=12, r=88, t=16, b=20),
        dragmode="select" if selection_mode == "box" else "pan",
        uirevision="market-chart-price-scale",
    )
    fig.update_xaxes(
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="rgba(240,185,11,0.55)",
        spikethickness=1,
        showgrid=True,
        gridcolor="rgba(128,128,128,0.15)",
        rangeslider_visible=False,
    )
    fig.update_yaxes(
        side="right",
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="rgba(240,185,11,0.55)",
        spikethickness=1,
        showgrid=True,
        gridcolor="rgba(128,128,128,0.15)",
        fixedrange=False,
        automargin=True,
    )
    if len(view):
        price_low = pd.to_numeric(view["low"], errors="coerce").min()
        price_high = pd.to_numeric(view["high"], errors="coerce").max()
        if pd.notna(price_low) and pd.notna(price_high) and price_high > price_low:
            last_close = float(view["close"].iloc[-1])
            padding = max((float(price_high) - float(price_low)) * 0.08, abs(last_close) * 0.0005)
            fig.update_yaxes(
                range=[float(price_low) - padding, float(price_high) + padding],
                autorange=False,
                fixedrange=False,
                row=1,
                col=1,
            )
    return fig


def build_backtest_trades_chart(data_path: Path, sample_trades: list[dict]) -> Optional[go.Figure]:
    try:
        frame = pd.read_csv(data_path)
        if "timestamp" not in frame.columns:
            return None
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp").reset_index(drop=True)
        if frame.empty or not sample_trades:
            return None

        # Ограничим отображение графиком от первой до последней сделки + небольшой отступ
        first_entry_ts = pd.to_datetime(sample_trades[0]["entry_ts"], utc=True)
        last_exit_ts = pd.to_datetime(sample_trades[-1].get("exit_ts", sample_trades[-1]["entry_ts"]), utc=True)
        
        start_ts = first_entry_ts - pd.Timedelta(hours=4)
        end_ts = last_exit_ts + pd.Timedelta(hours=4)
        view = frame[(frame["timestamp"] >= start_ts) & (frame["timestamp"] <= end_ts)].copy()
        
        if view.empty:
            view = frame.tail(1000)

        fig = make_subplots(rows=1, cols=1, shared_xaxes=True)
        up_color = "#22ab94"
        down_color = "#f23645"
        
        fig.add_trace(
            go.Candlestick(
                x=view["timestamp"], open=view["open"], high=view["high"], low=view["low"], close=view["close"],
                name="OHLC", increasing_line_color=up_color, increasing_fillcolor=up_color,
                decreasing_line_color=down_color, decreasing_fillcolor=down_color,
            ),
            row=1, col=1,
        )

        entry_x, entry_y = [], []
        exit_profit_x, exit_profit_y = [], []
        exit_loss_x, exit_loss_y = [], []

        for t in sample_trades:
            if "entry_ts" not in t or "entry_price" not in t:
                continue
            entry_ts = pd.to_datetime(t["entry_ts"], utc=True)
            entry_price = float(t["entry_price"])
            entry_x.append(entry_ts)
            entry_y.append(entry_price)
            
            if "exit_ts" in t:
                exit_ts = pd.to_datetime(t["exit_ts"], utc=True)
                # Примерно вычисляем цену выхода по realized_move_pct
                move = float(t.get("realized_move_pct", 0.0))
                # Если направление неизвестно (отсутствует), предполагаем long
                direction = t.get("direction", "long")
                if direction == "short":
                    exit_price = entry_price * (1 - move / 100)
                else:
                    exit_price = entry_price * (1 + move / 100)
                
                if t.get("is_profitable", False):
                    exit_profit_x.append(exit_ts)
                    exit_profit_y.append(exit_price)
                else:
                    exit_loss_x.append(exit_ts)
                    exit_loss_y.append(exit_price)

        if entry_x:
            fig.add_trace(go.Scatter(x=entry_x, y=entry_y, mode="markers", marker=dict(symbol="triangle-right", size=14, color="blue", line=dict(width=1, color="white")), name="Entry"), row=1, col=1)
        if exit_profit_x:
            fig.add_trace(go.Scatter(x=exit_profit_x, y=exit_profit_y, mode="markers", marker=dict(symbol="triangle-up", size=14, color="green", line=dict(width=1, color="white")), name="Take Profit"), row=1, col=1)
        if exit_loss_x:
            fig.add_trace(go.Scatter(x=exit_loss_x, y=exit_loss_y, mode="markers", marker=dict(symbol="triangle-down", size=14, color="red", line=dict(width=1, color="white")), name="Stop / Cancel"), row=1, col=1)

        fig.update_layout(
            height=600, xaxis_rangeslider_visible=False, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            hovermode="x unified", legend_orientation="h", margin=dict(l=12, r=12, t=16, b=20),
        )
        return fig
    except Exception as e:
        print(f"Error building backtest chart: {e}")
        return None


def add_utc_day_boundaries(fig: go.Figure, view: pd.DataFrame) -> None:
    if view.empty or "timestamp" not in view.columns:
        return
    timestamps = pd.to_datetime(view["timestamp"], utc=True, errors="coerce").dropna()
    if timestamps.empty:
        return

    start = timestamps.min().floor("D")
    end = timestamps.max().ceil("D")
    boundaries = pd.date_range(start=start, end=end, freq="D", tz="UTC")
    if len(boundaries) > 90:
        boundaries = pd.date_range(start=start, end=end, freq="MS", tz="UTC")
    if len(boundaries) <= 1:
        return

    x_values = []
    for boundary in boundaries:
        boundary_time = boundary.to_pydatetime()
        x_values.extend([boundary_time, boundary_time, None])

    row_ranges = [
        (1, ["low", "high"]),
        (2, ["volume"]),
        (3, ["ret_fast", "volume_z"]),
    ]
    if {"prob_short", "prob_flat", "prob_long"}.issubset(view.columns):
        row_ranges.append((4, ["prob_short", "prob_flat", "prob_long"]))

    for row, columns in row_ranges:
        y_min, y_max = get_boundary_y_range(view, columns)
        if y_min is None or y_max is None:
            continue
        y_values = []
        for _ in boundaries:
            y_values.extend([y_min, y_max, None])
        fig.add_trace(
            go.Scatter(
                x=x_values,
                y=y_values,
                mode="lines",
                line=dict(color="rgba(255,255,255,0.20)", width=1, dash="dot"),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=row,
            col=1,
        )


def get_boundary_y_range(view: pd.DataFrame, columns: list[str]) -> tuple[Optional[float], Optional[float]]:
    available = [column for column in columns if column in view.columns]
    if not available:
        return None, None
    values = pd.to_numeric(view[available].stack(), errors="coerce").dropna()
    if values.empty:
        return None, None
    y_min = float(values.min())
    y_max = float(values.max())
    if y_min == y_max:
        padding = abs(y_min) * 0.01 or 1.0
        return y_min - padding, y_max + padding
    padding = (y_max - y_min) * 0.03
    return y_min - padding, y_max + padding


def render_strategy_history_page(root: Path) -> None:
    """📜 Strategy History — version timeline with performance comparison."""

    history_path = root / "deploy_vps" / ".local_ai" / "paper_trading" / "strategy_history.json"
    state_path = root / "data" / "vps_sync" / "paper_state_multi.json"
    archive_path = root / "deploy_vps" / ".local_ai" / "paper_trading" / "v1_trades_archive.json"

    # Load data
    history = {}
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    versions = history.get("versions", [])
    current_ver = history.get("current_version", "?")

    if not versions:
        st.info("📜 No strategy version history yet. Deploy and run the Soldier to start tracking.")
        return

    # Overview metrics
    st.markdown("### 📋 Version Overview")
    cols = st.columns(len(versions))
    for i, v in enumerate(versions):
        ver = v.get("version", "?")
        perf = v.get("performance", {})
        trades_count = perf.get("trades", 0)
        pnl = perf.get("total_pnl_pct", 0)
        is_active = ver == current_ver

        with cols[i]:
            st.metric(
                f"{'🟢 ' if is_active else ''}{ver}",
                f"{pnl:+.3f}%" if trades_count > 0 else "⏳",
                f"{trades_count} trades" if trades_count > 0 else "awaiting",
                delta_color="normal" if pnl >= 0 else "inverse",
            )

    st.markdown("---")

    # Version timeline
    st.markdown("### 📜 Version Timeline")
    for v in reversed(versions):
        ver = v.get("version", "?")
        is_active = ver == current_ver
        desc = v.get("description", "")
        ts = str(v.get("timestamp", ""))[:19]
        verdict = v.get("verdict", "")
        perf = v.get("performance", {})
        changes = v.get("changes", [])
        params = v.get("params", {})

        header = f"{'🟢' if is_active else '⚪'} **{ver}**{' ← ACTIVE' if is_active else ''} — {desc}"
        with st.expander(header, expanded=is_active):
            st.caption(f"Timestamp: {ts}")
            if verdict:
                st.markdown(f"**Verdict:** {verdict}")

            # Performance
            perf_trades = perf.get("trades", 0)
            if perf_trades > 0:
                p1, p2, p3, p4 = st.columns(4)
                p1.metric("Trades", perf_trades)
                p2.metric("PnL", f"{perf.get('total_pnl_pct', 0):+.3f}%")
                wr = perf.get("win_rate", 0)
                wr_display = f"{wr*100:.0f}%" if isinstance(wr, float) and wr < 1 else f"{wr:.0f}%"
                p3.metric("Win Rate", wr_display)
                p4.metric("Profit Factor", f"{perf.get('profit_factor', 0):.2f}" if perf.get("profit_factor") else "—")

            # Changes
            if changes:
                st.markdown("**🔧 Changes:**")
                for ch in changes:
                    st.markdown(f"- `{ch.get('param', '?')}`: `{ch.get('old', '?')}` → `{ch.get('new', '?')}` — _{ch.get('reason', '')}_")

            # Notes
            notes = perf.get("notes", "")
            if notes:
                st.info(notes)

            # Params
            if params:
                st.markdown("**📋 Parameters:**")
                param_df = pd.DataFrame([{"Parameter": k, "Value": str(val)} for k, val in params.items()])
                st.dataframe(param_df, use_container_width=True, hide_index=True)

    st.markdown("---")

    # Live per-version comparison from current trades
    st.markdown("### 📊 Live Performance Comparison")

    all_trades = []
    # Load archived trades
    if archive_path.exists():
        try:
            arch = json.loads(archive_path.read_text(encoding="utf-8"))
            archived = arch.get("completed_trades", [])
            for t in archived:
                t.setdefault("config_version", "v1")
            all_trades.extend(archived)
        except Exception:
            pass

    # Load current trades
    if state_path.exists():
        try:
            st_data = json.loads(state_path.read_text(encoding="utf-8"))
            current_trades = st_data.get("completed_trades", [])
            all_trades.extend(current_trades)
        except Exception:
            pass

    if all_trades:
        # Group by version
        by_ver: dict[str, list] = {}
        for t in all_trades:
            ver = t.get("config_version", "v1")
            by_ver.setdefault(ver, []).append(t)

        comparison_data = []
        for ver, trades in sorted(by_ver.items()):
            pnls = [t.get("realized_pnl_pct", 0) for t in trades]
            wins = sum(1 for p in pnls if p > 0)
            total_pnl = sum(pnls)
            comparison_data.append({
                "Version": ver,
                "Trades": len(trades),
                "Wins": wins,
                "Losses": len(trades) - wins,
                "Win Rate": f"{wins/max(1,len(trades))*100:.1f}%",
                "Total PnL": f"{total_pnl:+.3f}%",
                "Avg PnL": f"{total_pnl/max(1,len(trades)):+.3f}%",
                "Active": "🟢" if ver == current_ver else "",
            })

        st.dataframe(pd.DataFrame(comparison_data), use_container_width=True, hide_index=True)

        # Detailed trade table
        st.markdown("### 📋 All Trades by Version")
        selected_ver = st.selectbox("Filter by version", ["All"] + sorted(by_ver.keys()))

        filtered = all_trades if selected_ver == "All" else by_ver.get(selected_ver, [])
        if filtered:
            trade_rows = []
            for i, t in enumerate(reversed(filtered), 1):
                pnl = t.get("realized_pnl_pct", 0)
                trade_rows.append({
                    "#": i,
                    "Symbol": t.get("symbol", "?"),
                    "Direction": t.get("direction", "?"),
                    "PnL %": f"{pnl:+.3f}%",
                    "Exit": t.get("exit_reason", "?"),
                    "Strategy": t.get("strategy_name", "?"),
                    "Version": t.get("config_version", "v1"),
                    "Time": str(t.get("entry_time", ""))[:16],
                })
            st.dataframe(pd.DataFrame(trade_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No trades recorded yet across any version.")


def render_soldier_feedback() -> None:
    st.header("🤖 Soldier Feedback & VPS Analysis")
    st.markdown("Здесь Штаб (Локальный ИИ) анализирует работу Солдата (VPS-бот) и дает рекомендации.")

    sync_mgr = VPSSyncManager()
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("📡 Связь с VPS")
        if st.button("🔄 Синхронизировать данные", use_container_width=True):
            with st.spinner("Подключение к VPS через SSH..."):
                if sync_mgr.sync_from_vps():
                    st.success("Данные успешно синхронизированы!")
                else:
                    st.error("Ошибка синхронизации. Проверьте SSH-ключи и доступность VPS.")

        state = sync_mgr.load_state()
        if state:
            # Format last update time to Moscow (UTC+3)
            raw_time = state.get("last_update") or state.get("last_updated") or "???"
            display_time = "???"
            if raw_time != "???":
                try:
                    dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                    msk_dt = dt + timedelta(hours=3)
                    display_time = msk_dt.strftime("%H:%M %d.%m.%Y")
                except:
                    display_time = raw_time
            
            st.info(f"🛰 **Последнее обновление на VPS:** {display_time} (МСК)")
            st.markdown(f"**Активные символы на VPS:**")
            st.code(", ".join(state.get("symbols", [])))
            
            st.metric("Всего сделок", len(state.get("completed_trades", [])))
            st.metric("Общий PnL", f"{state.get('total_pnl_pct', 0):+.3f}%")
        else:
            st.warning("Нет локальных данных. Нажмите 'Синхронизировать'.")

    with col2:
        st.subheader("🧐 Анализ упущенной выгоды (Regret Analysis)")
        if not state:
            st.write("Синхронизируйте данные для начала анализа.")
        else:
            trades = state.get("completed_trades", [])
            if not trades:
                st.info("Нет завершенных сделок для анализа.")
            else:
                if st.button("🔍 Запустить анализ последних 10 сделок", use_container_width=True):
                    with st.spinner("Загрузка исторических данных Bybit и расчет..."):
                        analysis = sync_mgr.perform_regret_analysis(trades[-10:])
                        st.session_state["vps_analysis"] = analysis
                
                analysis = st.session_state.get("vps_analysis")
                if analysis:
                    df_analysis = pd.DataFrame(analysis)
                    
                    # Make Regret Analysis table interactive
                    event = st.dataframe(
                        df_analysis, 
                        use_container_width=True,
                        hide_index=True,
                        on_select="rerun",
                        selection_mode="single-row",
                        key="vps_regret_table"
                    )
                    
                    selection = event.get("selection", {}).get("rows", [])
                    if selection:
                        selected_idx = selection[0]
                        trade_data = analysis[selected_idx]
                        
                        # Show Drill-down for Soldier
                        st.markdown("---")
                        if st.button("⬅ К общему анализу", key="back_vps"):
                            # This is a bit tricky with on_select, but st.rerun clears state if handled
                            st.session_state.pop("vps_regret_table_selection", None)
                            st.rerun()
                        
                        # We need to map VPS trade fields to what render_trade_inspector expects
                        inspector_trade = {
                            "_symbol": trade_data["symbol"],
                            "timestamp": trade_data["exit_time"],
                            "entry_time": trade_data["entry_time"],
                            "entry_price": trade_data["entry_price"],
                            "exit_price": trade_data["exit_price"],
                            "side": trade_data["direction"],
                            "realized_move_pct": trade_data.get("realized_pnl", 0),
                            "exit_reason": trade_data.get("exit_reason", "unknown")
                        }
                        render_trade_inspector(inspector_trade)
                        
                        # Show specific recommendation for THIS trade if it exists
                        st.subheader("💡 Рекомендация для этой ситуации")
                        recs = sync_mgr.generate_recommendations([trade_data])
                        for r in recs["recommendations"]:
                             render_recommendation_card(r, sync_mgr)
                        
                        return # Don't show the full list below
                    
                    st.subheader("🎯 Общие рекомендации по стратегии")
                    recs = sync_mgr.generate_recommendations(analysis)
                    
                    if not recs["recommendations"]:
                        st.success("✅ Текущие параметры работают оптимально. Изменений не требуется.")
                    else:
                        for r in recs["recommendations"]:
                            render_recommendation_card(r, sync_mgr)

    st.markdown("---")
    # ─── 🧠 AUTO-DISCOVERY SCAN ──────────────────────────────────
    st.subheader("🧠 Авто-Скан паттернов по монетам Солдата")
    st.markdown(
        "Штаб берёт список монет, которые отслеживает Солдат, скачивает по ним "
        "исторические данные и запускает **Auto-Discovery** — поиск аномальных импульсов "
        "с генерацией гипотез через LLM. Найденные паттерны сохраняются в **Hypothesis Vault**."
    )

    state_for_scan = sync_mgr.load_state()
    vps_symbols = state_for_scan.get("symbols", []) if state_for_scan else []

    if not vps_symbols:
        st.warning("⚠️ Нет данных о символах Солдата. Сначала нажмите **«🔄 Синхронизировать данные»**.")
    else:
        st.info(f"🛰 Солдат сейчас следит за **{len(vps_symbols)}** монетами: `{', '.join(vps_symbols)}`")

        scan_col1, scan_col2, scan_col3 = st.columns([2, 1, 1])
        with scan_col1:
            symbols_to_scan = st.multiselect(
                "Выберите монеты для сканирования",
                options=vps_symbols,
                default=vps_symbols[:5],
                help="По умолчанию — первые 5 из списка Солдата. Сканирование каждой монеты занимает ~10-20 сек."
            )
        with scan_col2:
            scan_timeframe = st.selectbox(
                "Таймфрейм",
                options=["1", "5", "15", "60"],
                format_func=lambda x: {"1": "1m", "5": "5m", "15": "15m", "60": "1h"}[x],
                index=1,
                help="Таймфрейм для загрузки свечей Bybit"
            )
        with scan_col3:
            days_of_history = st.slider(
                "Дней истории",
                min_value=1,
                max_value=30,
                value=7,
                step=1,
                help="Сколько дней исторических данных скачать для бэктеста. "
                     "Больше дней = больше трейдов в бэктесте = статистически значимый результат. "
                     "7 дней ≈ 2000 свечей 5m, 30 дней ≈ 8640 свечей 5m."
            )

        # Рассчитываем примерное кол-во свечей
        _tf_minutes = {"1": 1, "5": 5, "15": 15, "60": 60}
        _est_candles = days_of_history * 24 * 60 // _tf_minutes.get(scan_timeframe, 5)
        st.caption(f"📊 ~{_est_candles:,} свечей × {len(symbols_to_scan or [])} монет — данные будут загружены постранично из Bybit.")

        hypotheses_per_coin = st.slider(
            "Максимум гипотез с одной монеты", min_value=1, max_value=10, value=3,
            help="Топ-N самых мощных импульсов, по которым будет сгенерирована гипотеза"
        )

        if st.button("🚀 Запустить Авто-Скан", use_container_width=True, type="primary", key="run_auto_scan"):
            if not symbols_to_scan:
                st.error("Выберите хотя бы одну монету!")
            else:
                root = Path(".").resolve()
                scan_data_dir = root / "data" / "auto_scan_cache"
                scan_data_dir.mkdir(parents=True, exist_ok=True)

                # Load HybridAIClient — автоматически маршрутизирует на Gemini (Vertex AI)
                # если в config.json reasoning_model начинается с "gemini-", иначе — Ollama
                config_exists = DEFAULT_CONFIG_PATH.exists()
                ollama_config = load_ollama_config(DEFAULT_CONFIG_PATH) if config_exists else None
                ai_client = HybridAIClient(ollama_config) if ollama_config else None

                if ai_client is None:
                    st.warning("⚠️ Не найден конфиг AI (.local_ai/config.json). Запустите `crypto-scalp ai-init`.")
                else:
                    model_name = ollama_config.reasoning_model if ollama_config else "?"
                    st.info(f"🤖 Используется модель: **{model_name}** ({'Vertex AI / Gemini' if model_name.startswith('gemini-') else 'Ollama (локально)'})")

                total_saved = 0
                scan_log = []

                progress_bar = st.progress(0, text="Подготовка...")
                status_box = st.empty()

                for idx, symbol in enumerate(symbols_to_scan):
                    progress_pct = idx / len(symbols_to_scan)
                    progress_bar.progress(progress_pct, text=f"⏳ Сканирую {symbol} ({idx+1}/{len(symbols_to_scan)})...")
                    tf_names = {"1": "1m", "5": "5m", "15": "15m", "60": "1h"}
                    tf_display = tf_names.get(scan_timeframe, scan_timeframe)
                    _tf_min_map = {"1": 1, "5": 5, "15": 15, "60": 60}
                    _est_total = days_of_history * 24 * 60 // _tf_min_map.get(scan_timeframe, 5)
                    status_box.info(f"📡 Скачиваю ~{_est_total:,} свечей {tf_display} для **{symbol}** ({days_of_history} дн.)...")

                    try:
                        # ── Paginated Bybit download ─────────────────────────────────
                        # Bybit max = 1000 candles per request; we page backwards in time
                        url = "https://api.bybit.com/v5/market/kline"
                        all_rows: list = []
                        page_end_ts: int = 0  # 0 = latest
                        pages_needed = max(1, (_est_total + 999) // 1000)

                        for _page in range(pages_needed):
                            req_params: dict = {
                                "category": "linear",
                                "symbol": symbol,
                                "interval": scan_timeframe,
                                "limit": 1000,
                            }
                            if page_end_ts:
                                req_params["end"] = page_end_ts
                            resp = requests.get(url, params=req_params, timeout=15)
                            data = resp.json()
                            if data.get("retCode") != 0:
                                scan_log.append(f"❌ {symbol}: Bybit ошибка — {data.get('retMsg')}")
                                break
                            page_rows = data["result"]["list"]
                            if not page_rows:
                                break
                            all_rows.extend(page_rows)
                            # oldest candle in this batch → use as next page end
                            oldest_ts = int(page_rows[-1][0])  # already ms
                            page_end_ts = oldest_ts - 1
                            if len(page_rows) < 1000:
                                break  # ran out of data

                        if not all_rows:
                            scan_log.append(f"❌ {symbol}: Bybit вернул пустой ответ")
                            continue

                        # Bybit returns newest-first per page; sort ascending
                        all_rows.sort(key=lambda r: int(r[0]))

                        df_raw = pd.DataFrame(
                            all_rows,
                            columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"]
                        )
                        for col in ["open", "high", "low", "close", "volume"]:
                            df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")
                        df_raw["timestamp"] = pd.to_datetime(
                            pd.to_numeric(df_raw["timestamp"]), unit="ms", utc=True
                        )
                        df_raw = df_raw.drop_duplicates(subset=["timestamp"]).dropna().reset_index(drop=True)
                        status_box.info(f"✅ {symbol}: скачано {len(df_raw):,} свечей. Ищу паттерны...")

                        # Save temp CSV for auto_discover_hypotheses
                        tf_label = {"1": "1m", "5": "5m", "15": "15m", "60": "1h"}[scan_timeframe]
                        csv_path = scan_data_dir / f"bybit_{symbol.lower()}_{tf_label}_latest.csv"
                        df_raw.to_csv(csv_path, index=False)

                        status_box.info(f"🧠 Ищу импульсы и генерирую гипотезы для **{symbol}**...")


                        saved_ids = auto_discover_hypotheses(
                            data_path=csv_path,
                            client=ai_client,
                            symbol=symbol,
                            limit=hypotheses_per_coin,
                        )
                        total_saved += len(saved_ids)
                        scan_log.append(
                            f"✅ {symbol}: найдено и сохранено **{len(saved_ids)}** гипотез "
                            f"(ID: {', '.join(str(i) for i in saved_ids)})"
                        )

                    except Exception as e:
                        scan_log.append(f"❌ {symbol}: ошибка — {e}")

                progress_bar.progress(1.0, text="✅ Сканирование завершено!")
                status_box.empty()

                # Show summary
                if total_saved > 0:
                    st.success(f"🎉 Авто-Скан завершён! Сохранено **{total_saved}** гипотез в Hypothesis Vault.")
                    st.balloons()
                else:
                    st.warning("Гипотезы не были сохранены. Проверьте что Ollama запущена (`ollama serve`).")

                with st.expander("📋 Подробный лог сканирования", expanded=True):
                    for log_line in scan_log:
                        st.markdown(log_line)

                st.info("💡 Перейдите на вкладку **📝 Hypothesis Lab**, чтобы просмотреть все найденные паттерны.")

    # ─── Ручное управление ────────────────────────────────────────
    st.markdown("---")
    with st.expander("🛠 Ручное управление настройками VPS", expanded=False):
        # Fetch current params if not in session or refresh requested
        if "vps_params" not in st.session_state or st.button("🔄 Обновить текущие значения", key="refresh_params"):
            with st.spinner("Загрузка текущих настроек с сервера..."):
                st.session_state["vps_params"] = sync_mgr.fetch_remote_params()
        
        current_p = st.session_state.get("vps_params", {})
        if current_p:
            st.write("**Текущие настройки на сервере:**")
            cols = st.columns(len(current_p))
            for i, (k, v) in enumerate(current_p.items()):
                display_val = round(float(v), 3) if isinstance(v, (int, float)) else v
                cols[i % len(cols)].metric(k, display_val)
        else:
            st.warning("⚠️ Не удалось загрузить текущие параметры. Используются значения по умолчанию.")

        st.write("---")
        st.write("Изменение параметров:")
        c1, c2, c3 = st.columns([2, 1, 1])
        param_options = [
            "take_profit_rr", "breakeven_activation_rr", "fixed_stop_loss_pct",
            "trailing_stop_activation_rr", "max_positions", "z_score_threshold"
        ]
        param_to_change = c1.selectbox("Выберите параметр", options=param_options)
        default_val = float(current_p.get(param_to_change, 1.0))
        new_val = c2.number_input("Новое значение", value=default_val, step=0.1, key=f"val_{param_to_change}")
        if c3.button("🚀 Применить на VPS", use_container_width=True):
            with st.spinner(f"Обновление {param_to_change}..."):
                rounded_val = round(new_val, 4)
                if sync_mgr.apply_remote_parameter(param_to_change, rounded_val):
                    st.success(f"✅ {param_to_change} изменен на {rounded_val}")
                    st.session_state.pop("vps_params", None)
                    st.rerun()
                else:
                    st.error("❌ Ошибка при обновлении.")

    st.caption("Обратная связь: Штаб анализирует, пошла ли цена дальше после выхода бота по Take Profit. Если 'Regret' высокий — мы выходим слишком рано.")


def render_recommendation_card(r: dict, sync_mgr: VPSSyncManager) -> None:
    """Renders a single recommendation with state-aware Apply button."""
    rec_id = f"applied_{r['parameter']}_{r.get('suggested_value')}"
    
    with st.expander(f"⚠️ Изменить {r['parameter']}", expanded=True):
        st.write(f"**Действие:** {r['action'].upper()}")
        st.write(f"**Причина:** {r['reason']}")
        
        if "suggested_value" in r:
            st.write(f"**Рекомендуемое значение:** `{r['suggested_value']}`")
            
            if st.session_state.get(rec_id):
                st.success(f"✅ Параметр {r['parameter']} уже применён и будет активен в течение часа.")
            else:
                if st.button(f"🚀 Применить `{r['suggested_value']}` на VPS", key=f"btn_{rec_id}"):
                    with st.spinner(f"Обновление {r['parameter']} на VPS..."):
                        if sync_mgr.apply_remote_parameter(r['parameter'], r['suggested_value']):
                            st.session_state[rec_id] = True
                            st.success(f"✅ Параметр {r['parameter']} успешно обновлен!")
                            st.rerun()
                        else:
                            st.error("❌ Не удалось обновить параметр. Проверьте SSH-соединение.")
        else:
            st.info("Рекомендуется изменить этот параметр в настройках бота на VPS вручную.")


def render_trade_inspector(trade: dict) -> None:
    """Detailed visual breakdown of a single trade."""
    symbol = trade.get("_symbol")
    side = trade.get("side", "long").upper()
    pnl_pct = trade.get("realized_move_pct", 0)
    
    st.markdown(f"#### 🔍 Разбор: {symbol} | {side} ({pnl_pct:+.2f}%)")
    
    entry_time_str = trade.get("entry_time") or trade.get("timestamp")
    exit_time_str = trade.get("timestamp")
    entry_price = trade.get("entry_price")
    exit_price = trade.get("exit_price")
    exit_reason = trade.get("exit_reason", "unknown")
    
    # 1. Fetch data around the trade
    sync = VPSSyncManager()
    with st.spinner("Загрузка свечей..."):
        try:
            exit_dt = datetime.fromisoformat(exit_time_str.replace("Z", "+00:00"))
            start_dt = exit_dt - timedelta(minutes=90)
            
            url = "https://api.bybit.com/v5/market/kline"
            params = {
                "category": "linear", "symbol": symbol, "interval": "1",
                "start": int(start_dt.timestamp() * 1000),
                "limit": 150
            }
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if data.get("retCode") != 0:
                st.warning(f"Биржа не вернула данные: {data.get('retMsg')}")
                return
                
            rows = data["result"]["list"]
            rows.reverse()
            df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v", "t"])
            for col in ["o", "h", "l", "c"]:
                df[col] = pd.to_numeric(df[col])
            df["time"] = pd.to_datetime(pd.to_numeric(df["ts"]), unit="ms")
        except Exception as e:
            st.warning(f"Ошибка при загрузке графика: {e}")
            return

    # 2. Build Candlestick Chart
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["time"],
        open=df["o"], high=df["h"],
        low=df["l"], close=df["c"],
        name="Market",
        increasing_line_color='#00c076', decreasing_line_color='#ff5252'
    ))
    
    # Entry/Exit markers — anchored directly on candles with smart offset
    # triangle-up is anchored by its top, so placing it BELOW price makes tip touch the price
    # triangle-down is anchored by its bottom, so placing it ABOVE price makes tip touch the price
    price_range = df["h"].max() - df["l"].min()
    pin_offset = price_range * 0.012  # small ~1.2% nudge so tip overlaps candle body

    if side == "LONG":
        # LONG: BUY (green up-triangle) at entry, SELL (red down-triangle) at exit
        entry_y_pos = float(entry_price) - pin_offset  # tip points UP to entry_price
        exit_y_pos  = float(exit_price)  + pin_offset  # tip points DOWN to exit_price
        entry_marker = dict(symbol="triangle-up",   size=20, color="#00ff88", line=dict(width=1.5, color="white"))
        exit_marker  = dict(symbol="triangle-down", size=20, color="#ff5252", line=dict(width=1.5, color="white"))
        entry_text, exit_text = "BUY", "SELL"
        entry_text_color, exit_text_color = "#00ff88", "#ff5252"
    else:
        # SHORT: SELL (red down-triangle) at entry, BUY (green up-triangle) at exit
        entry_y_pos = float(entry_price) + pin_offset  # tip points DOWN to entry_price
        exit_y_pos  = float(exit_price)  - pin_offset  # tip points UP to exit_price
        entry_marker = dict(symbol="triangle-down", size=20, color="#ff5252", line=dict(width=1.5, color="white"))
        exit_marker  = dict(symbol="triangle-up",   size=20, color="#00ff88", line=dict(width=1.5, color="white"))
        entry_text, exit_text = "SELL", "BUY"
        entry_text_color, exit_text_color = "#ff5252", "#00ff88"

    entry_dt = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
    exit_dt  = datetime.fromisoformat(exit_time_str.replace("Z",  "+00:00"))

    # Entry marker — tip lands exactly on entry_price
    fig.add_trace(go.Scatter(
        x=[entry_dt], y=[entry_y_pos],
        mode="markers+text",
        marker=entry_marker,
        text=[f"<b>{entry_text}</b>"],
        textfont=dict(color=entry_text_color, size=11),
        textposition="middle right",
        name="Entry",
        hovertemplate=f"<b>ENTRY ({side})</b><br>Price: {entry_price}<extra></extra>"
    ))
    # Exit marker — tip lands exactly on exit_price
    fig.add_trace(go.Scatter(
        x=[exit_dt], y=[exit_y_pos],
        mode="markers+text",
        marker=exit_marker,
        text=[f"<b>{exit_text}</b>"],
        textfont=dict(color=exit_text_color, size=11),
        textposition="middle right",
        name="Exit",
        hovertemplate=f"<b>EXIT</b><br>Price: {exit_price}<br>Reason: {exit_reason}<extra></extra>"
    ))

    # Subtle Level Lines
    fig.add_hline(y=entry_price, line_dash="dot", line_color="rgba(255,255,255,0.2)", line_width=1)
    fig.add_hline(y=exit_price, line_dash="dot", line_color="rgba(255,255,255,0.2)", line_width=1)
    
    if side == "LONG":
        max_favorable = df["h"].max()
        regret = max(0, (max_favorable / entry_price - 1) * 100 - pnl_pct)
    else:
        max_favorable = df["l"].min()
        regret = max(0, (1 - max_favorable / entry_price) * 100 - pnl_pct)

    if regret > 0.05:
        fig.add_hline(y=max_favorable, line_dash="dash", line_color="#58a6ff", 
                      annotation_text=f"AI Potential (+{regret:.2f}%)")

    fig.update_layout(
        height=500, margin=dict(l=0,r=0,t=30,b=0), template="plotly_dark",
        xaxis=dict(showgrid=False, rangeslider=dict(visible=False)),
        yaxis=dict(gridcolor="rgba(255,255,255,0.05)", side="right"),
    )
    st.plotly_chart(fig, use_container_width=True)
    
    c1, c2, c3 = st.columns(3)
    with c1:
        st.write("**Детали входа**")
        st.info(f"Тип: {side}\nЦена: {entry_price}")
    with c2:
        st.write("**Детали выхода**")
        color = "green" if pnl_pct > 0 else "red"
        st.markdown(f"Причина: {exit_reason.upper()}\nРезультат: :{color}[{pnl_pct:+.2f}%]")
    with c3:
        st.write("**Анализ Штаба**")
        if regret > 0.3:
            st.warning(f"Упущено: {regret:.2f}%\nРекомендуется увеличить TP.")
        else:
            st.success("Выход был оптимальным.")


if __name__ == "__main__":
    main()
