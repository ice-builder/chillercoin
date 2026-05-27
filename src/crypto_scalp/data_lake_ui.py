"""
data_lake_ui.py — Streamlit Data Manager UI для Data Lake

Показывает:
- Таблицу покрытия 20 монет × 6 TF
- Кнопки "Скачать всё" / "Обновить" / выборочная загрузка
- Live progress bar
- Статистику размера lake
- Экспорт диапазона в CSV
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .data_lake import DataLakeManager, SUPPORTED_COINS, SUPPORTED_INTERVALS
from .bulk_downloader import (
    BulkDownloadJob,
    BulkProgress,
    DownloadResult,
    build_default_jobs,
    run_bulk_download,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TF_LABELS = {
    "1":   "1m",
    "5":   "5m",
    "15":  "15m",
    "60":  "1h",
    "240": "4h",
    "D":   "1D",
}

COIN_SHORT = {c: c.replace("USDT", "").replace("1000", "~") for c in SUPPORTED_COINS}


def _format_size(n_bytes: int) -> str:
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1_048_576:
        return f"{n_bytes/1024:.1f} KB"
    if n_bytes < 1_073_741_824:
        return f"{n_bytes/1_048_576:.1f} MB"
    return f"{n_bytes/1_073_741_824:.2f} GB"


def _get_lake(root: Optional[Path] = None) -> DataLakeManager:
    if "data_lake_manager" not in st.session_state:
        st.session_state["data_lake_manager"] = DataLakeManager(root=root)
    return st.session_state["data_lake_manager"]


def _coverage_color(pct: float) -> str:
    if pct >= 85:
        return "#00c076"
    if pct >= 40:
        return "#ffa500"
    if pct > 0:
        return "#ff5252"
    return "rgba(255,255,255,0.15)"


# ---------------------------------------------------------------------------
# Coverage Matrix (heatmap-style table)
# ---------------------------------------------------------------------------

def _render_coverage_matrix(summary_df: pd.DataFrame) -> None:
    st.markdown("#### 📊 Coverage Matrix — 20 монет × 6 таймфреймов")

    # Build pivot: rows=symbol, cols=interval, values=coverage_pct
    pivot = summary_df.pivot(index="symbol", columns="interval", values="coverage_pct")
    pivot = pivot.reindex(index=SUPPORTED_COINS, columns=SUPPORTED_INTERVALS, fill_value=0)

    # Rows pivot for rows
    fig = go.Figure()

    # Heatmap
    z_vals = pivot.values.tolist()
    x_labels = [TF_LABELS.get(tf, tf) for tf in SUPPORTED_INTERVALS]
    y_labels = [COIN_SHORT.get(sym, sym) for sym in SUPPORTED_COINS]

    # Custom text: show percentage or "—"
    text_vals = []
    for row in z_vals:
        text_row = []
        for v in row:
            if v == 0:
                text_row.append("—")
            elif v >= 100:
                text_row.append("✓")
            else:
                text_row.append(f"{v:.0f}%")
        text_vals.append(text_row)

    fig.add_trace(go.Heatmap(
        z=z_vals,
        x=x_labels,
        y=y_labels,
        text=text_vals,
        texttemplate="%{text}",
        textfont=dict(size=11, color="white"),
        colorscale=[
            [0.0,  "rgba(40,40,60,1)"],
            [0.01, "rgba(255,82,82,0.7)"],
            [0.4,  "rgba(255,165,0,0.8)"],
            [0.85, "rgba(0,192,118,0.9)"],
            [1.0,  "rgba(0,230,140,1)"],
        ],
        zmin=0,
        zmax=100,
        showscale=True,
        colorbar=dict(
            title=dict(text="Coverage %", font=dict(color="rgba(255,255,255,0.7)", size=11)),
            tickfont=dict(color="rgba(255,255,255,0.7)"),
            thickness=12,
        ),
        hovertemplate="<b>%{y}</b> / %{x}<br>Coverage: %{z:.1f}%<extra></extra>",
    ))

    fig.update_layout(
        height=max(420, len(SUPPORTED_COINS) * 22),
        margin=dict(l=10, r=60, t=10, b=40),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            side="top",
            tickfont=dict(color="rgba(255,255,255,0.8)", size=12),
            tickangle=0,
        ),
        yaxis=dict(
            tickfont=dict(color="rgba(255,255,255,0.8)", size=11),
            autorange="reversed",
        ),
        font=dict(color="rgba(255,255,255,0.8)"),
    )
    st.plotly_chart(fig, use_container_width=True, key="coverage_heatmap")


# ---------------------------------------------------------------------------
# Stats bar
# ---------------------------------------------------------------------------

def _render_stats_bar(lake: DataLakeManager, summary_df: pd.DataFrame) -> None:
    total_size = lake.get_lake_size_bytes()
    total_rows = summary_df["rows"].sum()
    covered = (summary_df["coverage_pct"] >= 85).sum()
    total_cells = len(SUPPORTED_COINS) * len(SUPPORTED_INTERVALS)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💾 Размер lake", _format_size(total_size))
    c2.metric("📈 Строк всего", f"{total_rows:,}")
    c3.metric("✅ Ячеек покрыто (≥85%)", f"{covered}/{total_cells}")
    c4.metric("🪙 Монет в lake", f"{summary_df[summary_df['coverage_pct']>0]['symbol'].nunique()}/{len(SUPPORTED_COINS)}")


# ---------------------------------------------------------------------------
# Size distribution chart
# ---------------------------------------------------------------------------

def _render_size_chart(summary_df: pd.DataFrame) -> None:
    size_by_symbol = summary_df.groupby("symbol")["size_mb"].sum().sort_values(ascending=False).head(20)
    fig = go.Figure(go.Bar(
        x=[COIN_SHORT.get(s, s) for s in size_by_symbol.index],
        y=size_by_symbol.values,
        marker_color=["#00c076" if v > 100 else "#ffa500" if v > 10 else "#ff5252" for v in size_by_symbol.values],
        hovertemplate="<b>%{x}</b><br>%{y:.1f} MB<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="Размер по монетам (MB)", font=dict(color="rgba(255,255,255,0.7)", size=13)),
        height=220,
        margin=dict(l=10, r=10, t=40, b=20),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(tickfont=dict(color="rgba(255,255,255,0.7)", size=10)),
        yaxis=dict(gridcolor="rgba(128,128,128,0.1)", tickfont=dict(color="rgba(255,255,255,0.7)")),
    )
    st.plotly_chart(fig, use_container_width=True, key="size_by_symbol")


# ---------------------------------------------------------------------------
# Background download runner
# ---------------------------------------------------------------------------

def _run_download_bg(jobs: list[BulkDownloadJob], lake: DataLakeManager, state_key: str) -> None:
    """Запускает загрузку в фоновом потоке, обновляя st.session_state."""
    def progress_cb(progress: BulkProgress, result: DownloadResult) -> None:
        st.session_state[state_key] = {
            "running": True,
            "progress": progress,
            "last_result": result,
        }

    results = run_bulk_download(jobs=jobs, lake=lake, workers=3, progress_cb=progress_cb)
    # Сброс кэша менеджера (обновить каталог)
    st.session_state["data_lake_manager"] = DataLakeManager(root=lake.root)
    st.session_state[state_key] = {
        "running": False,
        "done": True,
        "results": results,
        "total_rows": sum(r.rows_written for r in results),
    }


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_data_lake_tab(root: Optional[Path] = None) -> None:
    """Главная функция рендера вкладки Data Lake."""

    st.markdown("## 🗄 Data Lake Manager")
    st.caption("Parquet-хранилище исторических данных: 3 года × 20 монет × 6 таймфреймов")

    lake = _get_lake(root)
    DL_STATE_KEY = "dl_task_state"

    # Обновить каталог
    col_refresh, col_rescan, _ = st.columns([1, 1, 4])
    if col_refresh.button("🔄 Обновить статус", key="lake_refresh"):
        st.session_state["data_lake_manager"] = DataLakeManager(root=lake.root)
        lake = _get_lake(root)
        st.rerun()
    if col_rescan.button("🔍 Пересканировать lake", key="lake_rescan"):
        with st.spinner("Пересканирование..."):
            lake.refresh_catalog()
        st.session_state["data_lake_manager"] = DataLakeManager(root=lake.root)
        st.rerun()

    # Загрузить сводку
    summary_df = lake.get_catalog_summary()

    # Stats
    _render_stats_bar(lake, summary_df)
    st.markdown("---")

    # Coverage matrix
    _render_coverage_matrix(summary_df)

    # Size chart
    if summary_df["size_mb"].sum() > 0:
        _render_size_chart(summary_df)

    st.markdown("---")

    # ----------------------------------------------------------------
    # Панель управления загрузкой
    # ----------------------------------------------------------------
    st.markdown("#### ⬇️ Загрузка истории")

    dl_state = st.session_state.get(DL_STATE_KEY, {})
    is_running = dl_state.get("running", False)

    if is_running:
        progress: BulkProgress = dl_state.get("progress")
        if progress:
            st.progress(
                min(1.0, progress.percent / 100),
                text=f"[{progress.completed_jobs}/{progress.total_jobs}] "
                     f"{progress.current_symbol}/{progress.current_interval} | "
                     f"{progress.rows_downloaded_total:,} строк",
            )
            if progress.errors:
                with st.expander(f"⚠️ Ошибки ({len(progress.errors)})", expanded=False):
                    for e in progress.errors[-10:]:
                        st.code(e, language=None)
        st.info("⏳ Загрузка идёт в фоне... Не закрывайте страницу.")
        time.sleep(2)
        st.rerun()
    elif dl_state.get("done"):
        results = dl_state.get("results", [])
        success_count = sum(1 for r in results if r.success)
        total_rows = dl_state.get("total_rows", 0)
        st.success(f"✅ Загрузка завершена: {success_count}/{len(results)} заданий, {total_rows:,} строк записано.")
        if st.button("Очистить статус", key="lake_clear_done"):
            st.session_state[DL_STATE_KEY] = {}
            st.rerun()
    else:
        # Настройка параметров
        col_l, col_r = st.columns([1, 1])

        with col_l:
            st.markdown("**Монеты**")
            select_all_coins = st.checkbox("Все 20 монет", value=True, key="lake_all_coins")
            if not select_all_coins:
                coin_options = [COIN_SHORT.get(c, c) for c in SUPPORTED_COINS]
                selected_short = st.multiselect(
                    "Выберите монеты",
                    options=coin_options,
                    default=coin_options[:5],
                    key="lake_coins_sel",
                )
                short_to_full = {v: k for k, v in COIN_SHORT.items()}
                selected_coins = [short_to_full[s] for s in selected_short if s in short_to_full]
            else:
                selected_coins = SUPPORTED_COINS

        with col_r:
            st.markdown("**Таймфреймы**")
            select_all_tf = st.checkbox("Все 6 TF", value=True, key="lake_all_tf")
            if not select_all_tf:
                tf_options = [TF_LABELS[tf] for tf in SUPPORTED_INTERVALS]
                selected_tf_labels = st.multiselect(
                    "Выберите TF",
                    options=tf_options,
                    default=["5m", "15m", "1h"],
                    key="lake_tf_sel",
                )
                label_to_tf = {v: k for k, v in TF_LABELS.items()}
                selected_tfs = [label_to_tf[l] for l in selected_tf_labels if l in label_to_tf]
            else:
                selected_tfs = SUPPORTED_INTERVALS

        years = st.slider("Глубина истории (лет)", min_value=0.1, max_value=3.0, value=3.0, step=0.1, key="lake_years")

        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 2])

        if btn_col1.button("🚀 Скачать всё", type="primary", key="lake_download_all", use_container_width=True):
            if not selected_coins or not selected_tfs:
                st.warning("Выберите монеты и таймфреймы.")
            else:
                jobs = build_default_jobs(symbols=selected_coins, intervals=selected_tfs, years=years)
                st.session_state[DL_STATE_KEY] = {"running": True}
                t = threading.Thread(
                    target=_run_download_bg,
                    args=(jobs, lake, DL_STATE_KEY),
                    daemon=True,
                )
                t.start()
                st.rerun()

        if btn_col2.button("🔁 Только обновить", key="lake_update_only", use_container_width=True):
            if not selected_coins or not selected_tfs:
                st.warning("Выберите монеты и таймфреймы.")
            else:
                # Инкрементальная логика встроена в bulk_downloader
                jobs = build_default_jobs(symbols=selected_coins, intervals=selected_tfs, years=years)
                st.session_state[DL_STATE_KEY] = {"running": True}
                t = threading.Thread(
                    target=_run_download_bg,
                    args=(jobs, lake, DL_STATE_KEY),
                    daemon=True,
                )
                t.start()
                st.rerun()

    st.markdown("---")

    # ----------------------------------------------------------------
    # Экспорт диапазона
    # ----------------------------------------------------------------
    st.markdown("#### 📤 Экспорт в CSV")

    ex_col1, ex_col2, ex_col3, ex_col4 = st.columns([1.5, 1, 1, 1])
    with ex_col1:
        export_symbol = st.selectbox(
            "Монета",
            options=SUPPORTED_COINS,
            format_func=lambda s: COIN_SHORT.get(s, s),
            key="lake_export_symbol",
        )
    with ex_col2:
        export_tf = st.selectbox(
            "Таймфрейм",
            options=SUPPORTED_INTERVALS,
            format_func=lambda tf: TF_LABELS.get(tf, tf),
            key="lake_export_tf",
        )
    with ex_col3:
        export_start = st.date_input("От", value=datetime.now().date() - timedelta(days=90), key="lake_export_start")
    with ex_col4:
        export_end = st.date_input("До", value=datetime.now().date(), key="lake_export_end")

    if st.button("📥 Сформировать CSV", key="lake_export_btn"):
        start_dt = datetime.combine(export_start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(export_end, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc)
        with st.spinner("Читаю данные..."):
            df = lake.read_range(export_symbol, export_tf, start_dt, end_dt)

        if df.empty:
            st.warning("Нет данных для выбранного диапазона. Сначала скачайте историю.")
        else:
            csv_bytes = df.to_csv(index=False).encode("utf-8")
            filename = f"{export_symbol}_{TF_LABELS.get(export_tf, export_tf)}_{export_start}_{export_end}.csv"
            st.download_button(
                label=f"⬇️ Скачать {filename} ({len(df):,} строк)",
                data=csv_bytes,
                file_name=filename,
                mime="text/csv",
                key="lake_export_download",
            )

    # ----------------------------------------------------------------
    # Быстрый просмотр данных
    # ----------------------------------------------------------------
    with st.expander("🔍 Быстрый просмотр последних свечей", expanded=False):
        view_col1, view_col2 = st.columns([1, 1])
        with view_col1:
            view_sym = st.selectbox(
                "Монета",
                options=SUPPORTED_COINS,
                format_func=lambda s: COIN_SHORT.get(s, s),
                key="lake_view_symbol",
            )
        with view_col2:
            view_tf = st.selectbox(
                "Таймфрейм",
                options=SUPPORTED_INTERVALS,
                format_func=lambda tf: TF_LABELS.get(tf, tf),
                key="lake_view_tf",
            )

        if st.button("Показать 50 свечей", key="lake_view_btn"):
            df_view = lake.read_latest(view_sym, view_tf, n_rows=50)
            if df_view.empty:
                st.info("Нет данных. Скачайте историю для этой монеты.")
            else:
                st.dataframe(df_view.tail(50).reset_index(drop=True), use_container_width=True, hide_index=True)
