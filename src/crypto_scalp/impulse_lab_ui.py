"""Impulse Lab — Streamlit UI вкладка для визуализации, ручной разметки
и поиска импульсов на исторических данных.

Workflow:
    1. Пользователь находит импульс на графике Market Research
    2. Нажимает «🔍 Выделить импульс», обводит участок, жмёт «⚡ В лабораторию»
    3. Impulse Lab автоматически строит кирпичики для выделенного участка,
       раскладывает на Birth/Drive/Decay, показывает графики и метрики
    4. Пользователь верифицирует импульс, подкручивает параметры
    5. Поиск похожих в истории → блок подтверждения (win rate, SL/TP)
    6. При win rate >80% — отправка в Paper Trading с оптимальными SL/TP

Используется как самостоятельный модуль, подключаемый в research_app.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from .quant_brick import QuantBrick, build_bricks_from_ohlcv, bricks_to_dataframe, get_brick_statistics
from .impulse_detector import (
    DetectedImpulse,
    ImpulseDetectorConfig,
    detect_impulses,
    detect_impulses_from_selection,
    get_impulse_context_comparison,
    impulses_to_dataframe,
)
from .impulse_decomposer import (
    DecomposedImpulse,
    decompose_impulse,
    match_birth_signature,
    cosine_similarity,
)
from .pattern_library import (
    init_pattern_library,
    save_impulse,
    list_impulses,
    get_impulse_phases,
    get_impulse_signatures,
    find_similar_impulses,
    delete_impulse,
    get_library_stats,
)


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

PHASE_COLORS = {
    "birth": "rgba(0, 230, 118, 0.35)",     # зелёный
    "drive": "rgba(41, 121, 255, 0.35)",     # синий
    "decay": "rgba(255, 152, 0, 0.35)",      # оранжевый
}
PHASE_LINE_COLORS = {
    "birth": "rgb(0, 230, 118)",
    "drive": "rgb(41, 121, 255)",
    "decay": "rgb(255, 152, 0)",
}
ENERGY_COLOR_MAP = {
    "dormant": "rgba(100, 100, 100, 0.3)",
    "active": "rgba(255, 193, 7, 0.7)",
    "explosive": "rgba(244, 67, 54, 0.9)",
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def render_impulse_lab(
    dataset: pd.DataFrame,
    data_path: Path,
    current_symbol: str,
    current_timeframe: str,
    root: Path,
) -> None:
    """Главная функция вкладки Impulse Lab."""

    st.markdown("## ⚡ Impulse Lab")
    st.caption(
        "Квантовая декомпозиция импульсов: размечай на графике Market Research → "
        "анализируй фазы Birth/Drive/Decay → сохраняй в библиотеку → ищи похожие."
    )

    # --- Построение кирпичиков ---
    lookback = st.sidebar.slider(
        "Z-score lookback (бары)", 20, 200, 80,
        key="impulse_lab_lookback",
        help="Окно для расчёта z-score объёма и цены. Больше окно = более строгие пороги."
    )
    duration_map = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}
    duration_seconds = duration_map.get(current_timeframe, 60)

    bricks = _cached_build_bricks(
        data_path_str=str(data_path),
        data_mtime=data_path.stat().st_mtime,
        lookback=lookback,
        duration_seconds=duration_seconds,
    )

    if not bricks:
        st.warning("Не удалось построить кирпичики. Проверьте данные.")
        return

    brick_df = bricks_to_dataframe(bricks)
    brick_df["timestamp"] = pd.to_datetime(brick_df["timestamp"], utc=True, errors="coerce")

    # --- Проверяем: пришло ли выделение из Market Research ---
    incoming_selection = st.session_state.get("impulse_lab_selection")
    if incoming_selection:
        _handle_incoming_selection(incoming_selection, bricks, brick_df, dataset, current_symbol, current_timeframe)

    # --- Статистика по кирпичикам ---
    with st.expander("📊 Распределение энергии кирпичиков", expanded=False):
        stats = get_brick_statistics(bricks)
        stat_cols = st.columns(6)
        stat_cols[0].metric("Всего кирпичиков", f"{stats['count']:,}")
        stat_cols[1].metric("💤 Dormant", f"{stats['dormant_pct']:.1f}%")
        stat_cols[2].metric("⚡ Active", f"{stats['active_pct']:.1f}%")
        stat_cols[3].metric("🔥 Explosive", f"{stats['explosive_pct']:.1f}%")
        stat_cols[4].metric("Energy P95", f"{stats['energy_p95']:.2f}")
        stat_cols[5].metric("Energy P99", f"{stats['energy_p99']:.2f}")

    # --- Показываем результат анализа (если есть) ---
    if "current_decomposed" in st.session_state:
        _render_analysis_results(
            bricks=bricks,
            dataset=dataset,
            root=root,
            data_path=data_path,
            current_symbol=current_symbol,
            current_timeframe=current_timeframe,
        )
    else:
        st.info(
            "👆 Перейди на **Market Research**, нажми **🔍 Выделить импульс**, "
            "обведи участок на графике, и жми **⚡ В лабораторию**."
        )
        st.markdown("---")
        st.markdown("### 🔭 Автоматический поиск по всей истории")
        _render_auto_scan(bricks=bricks, dataset=dataset, current_symbol=current_symbol)

    # --- Навигатор по кирпичикам ---
    st.markdown("---")
    with st.expander("🔍 Навигатор по кирпичикам (ручной обзор)", expanded=False):
        _render_brick_navigator(bricks, brick_df, dataset, current_symbol, current_timeframe)

    # --- Поиск похожих ---
    st.markdown("---")
    st.markdown("### 🔎 Поиск похожих импульсов")
    _render_similar_search(bricks=bricks, brick_df=brick_df, dataset=dataset, root=root,
                           current_symbol=current_symbol, current_timeframe=current_timeframe)

    # --- Библиотека паттернов ---
    st.markdown("---")
    st.markdown("### 📚 Библиотека паттернов")
    _render_pattern_library(root=root, current_symbol=current_symbol)


# ---------------------------------------------------------------------------
# Incoming selection from Market Research
# ---------------------------------------------------------------------------

def _handle_incoming_selection(
    selection: dict,
    bricks: List[QuantBrick],
    brick_df: pd.DataFrame,
    dataset: pd.DataFrame,
    current_symbol: str,
    current_timeframe: str,
) -> None:
    """Обрабатывает выделение, пришедшее из Market Research."""
    start_idx = int(selection["start_idx"])
    end_idx = int(selection["end_idx"])
    start_ts = selection.get("start_ts", "")
    end_ts = selection.get("end_ts", "")

    st.success(
        f"📌 Получено выделение из Market Research: "
        f"**{start_ts}** → **{end_ts}** ({end_idx - start_idx + 1} свечей)"
    )

    # Кирпичики = 1:1 со свечами, индексы совпадают
    sel_start = max(0, start_idx)
    sel_end = min(len(bricks), end_idx + 1)

    if sel_end - sel_start < 2:
        st.error("Слишком маленький участок — нужно минимум 2 свечи.")
        return

    # Auto-analyze при входе
    if st.session_state.get("_impulse_lab_auto_analyzed") != (sel_start, sel_end):
        selected_impulse = detect_impulses_from_selection(
            bricks=bricks,
            start_index=sel_start,
            end_index=sel_end,
            all_bricks=bricks,
        )
        if selected_impulse is not None:
            st.session_state["current_impulse"] = selected_impulse
            st.session_state["current_decomposed"] = decompose_impulse(selected_impulse)
            st.session_state.pop("_similar_results", None)
            st.session_state.pop("_backtest_results", None)
            st.session_state["_impulse_lab_auto_analyzed"] = (sel_start, sel_end)
        else:
            st.warning("Не удалось построить квантовый импульс из выделенного участка. Попробуйте другой.")


# ---------------------------------------------------------------------------
# Analysis results
# ---------------------------------------------------------------------------

def _render_analysis_results(
    bricks: List[QuantBrick],
    dataset: pd.DataFrame,
    root: Path,
    data_path: Path,
    current_symbol: str,
    current_timeframe: str,
) -> None:
    """Показывает результат анализа импульса."""
    decomposed: DecomposedImpulse = st.session_state["current_decomposed"]
    impulse: DetectedImpulse = decomposed.impulse

    # Кнопка сброса
    reset_col1, reset_col2 = st.columns([3, 1])
    reset_col1.markdown(f"### 📐 Декомпозиция импульса")
    if reset_col2.button("🗑 Сбросить анализ", key="reset_analysis_btn"):
        st.session_state.pop("current_impulse", None)
        st.session_state.pop("current_decomposed", None)
        st.session_state.pop("impulse_lab_selection", None)
        st.session_state.pop("_impulse_lab_auto_analyzed", None)
        st.session_state.pop("_similar_results", None)
        st.session_state.pop("_backtest_results", None)
        st.rerun()

    # Карточка импульса
    imp_cols = st.columns(5)
    imp_cols[0].metric(
        "Направление",
        "🟢 LONG" if impulse.direction > 0 else "🔴 SHORT"
    )
    imp_cols[1].metric("Ход цены", f"{impulse.total_price_move_pct:.3f}%")
    imp_cols[2].metric("Длительность", f"{impulse.duration_bricks} баров")
    imp_cols[3].metric("Пиковая энергия", f"{impulse.peak_energy:.2f}")
    imp_cols[4].metric("Quality", f"{decomposed.quality_score:.2f}")

    # Фазы (горизонтально)
    phase_summary = decomposed.get_phase_summary()
    st.markdown("#### Фазы импульса")
    phase_cols = st.columns(3)

    for col, (phase_name, emoji) in zip(
        phase_cols,
        [("birth", "🟢"), ("drive", "🔵"), ("decay", "🟠")]
    ):
        with col:
            st.markdown(f"**{emoji} {phase_name.upper()}**")
            st.write(f"Баров: {phase_summary[f'{phase_name}_bricks']}")
            st.write(f"Ход цены: {phase_summary[f'{phase_name}_move_pct']:.4f}%")
            st.write(f"Ср. энергия: {phase_summary[f'{phase_name}_energy']:.3f}")
            st.write(f"Доля объёма: {phase_summary[f'{phase_name}_volume_share']:.1f}%")

    # График декомпозиции
    _render_decomposition_chart(decomposed, dataset, current_symbol)

    # Сравнение impulse vs flat
    st.markdown("#### 📊 Импульс vs Контекст (флет)")
    comparison = get_impulse_context_comparison(impulse, bricks, context_window=60)
    _render_comparison_table(comparison)

    # Кнопка сохранения
    st.markdown("---")
    save_cols = st.columns([2, 1, 1])
    notes = save_cols[0].text_input("Заметки", key="impulse_save_notes", placeholder="Мощный пробой уровня сопротивления")
    tags = save_cols[1].text_input("Теги", key="impulse_save_tags", placeholder="strong,breakout")

    if save_cols[2].button("💾 Сохранить в библиотеку", type="primary", key="save_impulse_btn"):
        impulse_id = save_impulse(
            root=root,
            decomposed=decomposed,
            symbol=current_symbol,
            timeframe=current_timeframe,
            source="manual",
            tags=tags,
            notes=notes,
            data_path=str(data_path),
        )
        st.success(f"✅ Импульс сохранён: `{impulse_id}`")

    # Кнопки навигации
    nav_cols = st.columns([1, 1, 1])
    if nav_cols[0].button("← Назад на Market Research", key="back_to_market_btn"):
        st.session_state.pop("current_impulse", None)
        st.session_state.pop("current_decomposed", None)
        st.session_state.pop("impulse_lab_selection", None)
        st.session_state.pop("_impulse_lab_auto_analyzed", None)
        st.session_state["main_section"] = "📈 Market Research"
        st.session_state["_nav_programmatic"] = True
        st.rerun()
    if nav_cols[2].button("🔎 Искать похожие в истории ↓", type="primary", key="scroll_to_similar_btn"):
        st.toast("⬇️ Прокрути вниз к секции 'Поиск похожих импульсов'")


# ---------------------------------------------------------------------------
# Auto-scan (when no selection yet)
# ---------------------------------------------------------------------------

def _render_auto_scan(
    bricks: List[QuantBrick],
    dataset: pd.DataFrame,
    current_symbol: str,
) -> None:
    """Автоматический поиск самых мощных импульсов на всей истории."""

    auto_cols = st.columns([1, 1, 1, 1])
    min_energy = auto_cols[0].slider("Мин. энергия", 0.5, 5.0, 1.5, 0.1, key="autoscan_min_energy")
    min_move = auto_cols[1].slider("Мин. |ход| %", 0.05, 2.0, 0.3, 0.05, key="autoscan_min_move")
    min_bricks = auto_cols[2].slider("Мин. баров", 2, 15, 3, key="autoscan_min_bricks")
    top_n = auto_cols[3].number_input("Top-N", 5, 100, 20, key="autoscan_top_n")

    if st.button("🔭 Найти мощнейшие импульсы", type="primary", key="autoscan_btn"):
        config = ImpulseDetectorConfig(
            min_energy=min_energy,
            min_impulse_bricks=min_bricks,
            max_gap_bricks=2,
            min_direction_ratio=0.6,
            min_total_move_pct=min_move,
        )
        with st.spinner("Сканирую всю историю..."):
            auto_impulses = detect_impulses(bricks, config)

        if not auto_impulses:
            st.warning("Импульсов не найдено. Попробуйте снизить пороги.")
            return

        # Сортируем по абсолютному ходу цены
        auto_impulses.sort(key=lambda x: abs(x.total_price_move_pct), reverse=True)
        auto_impulses = auto_impulses[:top_n]

        rows = []
        for i, imp in enumerate(auto_impulses):
            dec = decompose_impulse(imp)
            rows.append({
                "#": i + 1,
                "start_ts": str(imp.start_timestamp),
                "end_ts": str(imp.end_timestamp),
                "direction": "🟢 LONG" if imp.direction > 0 else "🔴 SHORT",
                "price_move_%": round(imp.total_price_move_pct, 4),
                "bars": imp.duration_bricks,
                "peak_energy": round(imp.peak_energy, 2),
                "mean_energy": round(imp.mean_energy, 2),
                "quality": round(dec.quality_score, 2),
                "birth_bars": dec.birth.duration_bricks,
                "drive_bars": dec.drive.duration_bricks,
                "start_idx": imp.start_index,
                "end_idx": imp.end_index,
            })

        st.success(f"Найдено {len(auto_impulses)} импульсов (показано до {top_n} самых мощных)")
        results_df = pd.DataFrame(rows)
        st.dataframe(results_df, use_container_width=True, hide_index=True)

        # Можно выбрать импульс из таблицы для детального анализа
        pick_idx = st.selectbox(
            "Выбери импульс для анализа",
            options=list(range(len(auto_impulses))),
            format_func=lambda i: f"#{i+1}: {rows[i]['start_ts']} | {rows[i]['price_move_%']}% | {rows[i]['direction']}",
            key="autoscan_pick",
        )
        if st.button("📐 Анализировать выбранный", type="primary", key="autoscan_analyze_btn"):
            picked = auto_impulses[pick_idx]
            st.session_state["current_impulse"] = picked
            st.session_state["current_decomposed"] = decompose_impulse(picked)
            st.rerun()


# ---------------------------------------------------------------------------
# Brick navigator
# ---------------------------------------------------------------------------

def _render_brick_navigator(
    bricks: List[QuantBrick],
    brick_df: pd.DataFrame,
    dataset: pd.DataFrame,
    current_symbol: str,
    current_timeframe: str,
) -> None:
    """Навигатор по истории кирпичиков — для ручного обзора без выделения из графика."""
    total_bricks = len(brick_df)
    duration_map = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}
    duration_seconds = duration_map.get(current_timeframe, 60)
    view_sizes = {"1 час": 60, "4 часа": 240, "12 часов": 720, "1 день": 1440, "3 дня": 4320, "7 дней": 10080, "Всё": total_bricks}
    if current_timeframe != "1m":
        tf_minutes = duration_seconds // 60
        view_sizes = {k: max(30, v // tf_minutes) for k, v in view_sizes.items()}
        view_sizes["Всё"] = total_bricks

    nav_cols = st.columns([2, 3, 1, 1])
    view_label = nav_cols[0].selectbox(
        "Масштаб просмотра", list(view_sizes.keys()), index=3, key="impulse_lab_view_scale"
    )
    view_size = min(view_sizes[view_label], total_bricks)
    max_start = max(0, total_bricks - view_size)
    start_idx = nav_cols[1].slider(
        "Начало окна", 0, max(1, max_start), max_start,
        key="impulse_lab_start_idx",
    )
    end_idx = min(start_idx + view_size, total_bricks)

    step = max(1, view_size // 4)
    if nav_cols[2].button("◀ Назад", key="impulse_lab_back"):
        st.session_state["impulse_lab_start_idx"] = max(0, start_idx - step)
        st.rerun()
    if nav_cols[3].button("Вперёд ▶", key="impulse_lab_forward"):
        st.session_state["impulse_lab_start_idx"] = min(max_start, start_idx + step)
        st.rerun()

    view_df = brick_df.iloc[start_idx:end_idx].reset_index(drop=True)
    if view_df.empty:
        return

    ts_start = view_df["timestamp"].iloc[0]
    ts_end = view_df["timestamp"].iloc[-1]
    st.caption(f"📅 {ts_start} → {ts_end} | {len(view_df)} кирпичиков")

    _render_brick_chart(view_df, dataset, start_idx, end_idx, current_symbol, current_timeframe)

    # Ручной ввод индексов для анализа
    sel_cols = st.columns([1, 1, 1])
    sel_start = sel_cols[0].number_input(
        "Начало (индекс)", min_value=0, max_value=len(bricks) - 2,
        value=start_idx, key="impulse_nav_sel_start",
    )
    sel_end = sel_cols[1].number_input(
        "Конец (индекс)", min_value=sel_start + 1, max_value=len(bricks),
        value=min(sel_start + 10, len(bricks)), key="impulse_nav_sel_end",
    )
    if sel_cols[2].button("🔬 Анализировать", key="impulse_nav_analyze_btn"):
        selected_impulse = detect_impulses_from_selection(
            bricks=bricks, start_index=sel_start, end_index=sel_end, all_bricks=bricks,
        )
        if selected_impulse:
            st.session_state["current_impulse"] = selected_impulse
            st.session_state["current_decomposed"] = decompose_impulse(selected_impulse)
            st.rerun()
        else:
            st.error("Не удалось построить импульс из выбранного участка.")


# ---------------------------------------------------------------------------
# Chart rendering
# ---------------------------------------------------------------------------

def _render_brick_chart(
    view_df: pd.DataFrame,
    full_dataset: pd.DataFrame,
    start_idx: int,
    end_idx: int,
    symbol: str,
    timeframe: str,
) -> None:
    """Рисует основной график: свечи + энергия + volume_z."""

    ts_min = view_df["timestamp"].min()
    ts_max = view_df["timestamp"].max()
    chart_data = full_dataset[
        (full_dataset["timestamp"] >= ts_min) & (full_dataset["timestamp"] <= ts_max)
    ].copy()

    if chart_data.empty:
        chart_data = view_df

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.5, 0.25, 0.25],
        subplot_titles=[
            f"{symbol} {timeframe} — OHLC",
            "Energy (volume × price z-score)",
            "Dollar Volume Z-score",
        ],
    )

    fig.add_trace(
        go.Candlestick(
            x=chart_data["timestamp"],
            open=chart_data["open"],
            high=chart_data["high"],
            low=chart_data["low"],
            close=chart_data["close"],
            name="OHLC",
            increasing_line_color="rgb(0, 230, 118)",
            decreasing_line_color="rgb(244, 67, 54)",
        ),
        row=1, col=1,
    )

    colors = [ENERGY_COLOR_MAP.get(c, "gray") for c in view_df["brick_class"]]
    fig.add_trace(
        go.Bar(
            x=view_df["timestamp"],
            y=view_df["energy"],
            name="Energy",
            marker_color=colors,
            opacity=0.85,
        ),
        row=2, col=1,
    )

    fig.add_trace(
        go.Bar(
            x=view_df["timestamp"],
            y=view_df["dollar_volume_z"],
            name="$ Vol Z",
            marker_color=np.where(
                view_df["dollar_volume_z"] > 0,
                "rgba(41, 121, 255, 0.7)",
                "rgba(244, 67, 54, 0.5)"
            ),
        ),
        row=3, col=1,
    )

    fig.add_hline(y=1.0, line_dash="dash", line_color="yellow", opacity=0.5, row=2, col=1,
                  annotation_text="active")
    fig.add_hline(y=2.5, line_dash="dash", line_color="red", opacity=0.5, row=2, col=1,
                  annotation_text="explosive")
    fig.add_hline(y=2.0, line_dash="dash", line_color="orange", opacity=0.4, row=3, col=1,
                  annotation_text="z=2")

    fig.update_layout(
        height=800,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        xaxis_rangeslider_visible=False,
        margin=dict(l=50, r=50, t=40, b=20),
        font=dict(size=11),
    )
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.15)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)")

    st.plotly_chart(fig, use_container_width=True, key="brick_main_chart")


def _render_decomposition_chart(
    decomposed: DecomposedImpulse,
    full_dataset: pd.DataFrame,
    symbol: str,
) -> None:
    """График с подсветкой фаз Birth/Drive/Decay."""
    impulse = decomposed.impulse
    imp_bricks = impulse.bricks

    all_ts = full_dataset["timestamp"]
    imp_start_ts = imp_bricks[0].timestamp
    imp_end_ts = imp_bricks[-1].timestamp

    # Контекст: +30 баров до и после
    context_mask = (all_ts >= pd.Timestamp(imp_start_ts) - pd.Timedelta(minutes=30)) & \
                   (all_ts <= pd.Timestamp(imp_end_ts) + pd.Timedelta(minutes=30))
    context_data = full_dataset[context_mask].copy()

    if context_data.empty:
        context_data = pd.DataFrame([{
            "timestamp": b.timestamp, "open": b.price_open,
            "high": b.price_high, "low": b.price_low, "close": b.price_close,
        } for b in imp_bricks])

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.65, 0.35],
        subplot_titles=[f"{symbol} — Фазы импульса", "Профиль энергии"],
    )

    fig.add_trace(
        go.Candlestick(
            x=context_data["timestamp"],
            open=context_data["open"],
            high=context_data["high"],
            low=context_data["low"],
            close=context_data["close"],
            name="OHLC",
            increasing_line_color="rgb(0, 230, 118)",
            decreasing_line_color="rgb(244, 67, 54)",
        ),
        row=1, col=1,
    )

    y_min = float(context_data["low"].min()) * 0.999
    y_max = float(context_data["high"].max()) * 1.001

    for phase in [decomposed.birth, decomposed.drive, decomposed.decay]:
        if not phase.bricks:
            continue
        fig.add_shape(
            type="rect",
            x0=phase.bricks[0].timestamp,
            x1=phase.bricks[-1].timestamp,
            y0=y_min, y1=y_max,
            fillcolor=PHASE_COLORS[phase.phase_type],
            line=dict(color=PHASE_LINE_COLORS[phase.phase_type], width=1),
            row=1, col=1,
        )
        mid_idx = len(phase.bricks) // 2
        fig.add_annotation(
            x=phase.bricks[mid_idx].timestamp,
            y=y_max,
            text=f"{phase.phase_type.upper()}<br>{phase.price_move_pct:.3f}%",
            showarrow=False,
            font=dict(size=10, color=PHASE_LINE_COLORS[phase.phase_type]),
            row=1, col=1,
        )

    for phase in [decomposed.birth, decomposed.drive, decomposed.decay]:
        if not phase.bricks:
            continue
        timestamps = [b.timestamp for b in phase.bricks]
        energies = [b.energy for b in phase.bricks]
        fig.add_trace(
            go.Bar(
                x=timestamps,
                y=energies,
                name=phase.phase_type,
                marker_color=PHASE_COLORS[phase.phase_type],
                marker_line_color=PHASE_LINE_COLORS[phase.phase_type],
                marker_line_width=1,
            ),
            row=2, col=1,
        )

    fig.update_layout(
        height=600,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=True,
        xaxis_rangeslider_visible=False,
        margin=dict(l=50, r=50, t=40, b=20),
        font=dict(size=11),
    )
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.15)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)")

    st.plotly_chart(fig, use_container_width=True, key="decomposition_chart")


def _render_comparison_table(comparison: dict) -> None:
    """Таблица сравнения метрик импульса vs контекста."""
    imp = comparison.get("impulse", {})
    pre = comparison.get("pre_context", {})
    post = comparison.get("post_context", {})

    if not imp or not pre:
        st.info("Недостаточно данных для сравнения (нужен контекст до импульса).")
        return

    metrics = [
        ("Ср. энергия", "energy_mean"),
        ("Макс. энергия", "energy_max"),
        ("Energy P95", "energy_p95"),
        ("$ Vol Z mean", "dollar_volume_z_mean"),
        ("$ Vol Z max", "dollar_volume_z_max"),
        ("Price Z mean", "price_change_z_mean"),
        ("Price Z max", "price_change_z_max"),
        ("🔥 Explosive %", "explosive_pct"),
        ("⚡ Active %", "active_pct"),
    ]

    rows = []
    for label, key in metrics:
        imp_val = imp.get(key, 0)
        pre_val = pre.get(key, 0)
        post_val = post.get(key, 0) if post else 0
        ratio = imp_val / pre_val if pre_val and pre_val != 0 else 0
        rows.append({
            "Метрика": label,
            "Импульс": f"{imp_val:.3f}",
            "До (флет)": f"{pre_val:.3f}",
            "После": f"{post_val:.3f}" if post else "—",
            "Ratio (имп/флет)": f"{ratio:.1f}x" if ratio else "—",
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    energy_ratio = comparison.get("energy_ratio_vs_pre", 0)
    if energy_ratio > 0:
        st.info(f"⚡ Энергия импульса в **{energy_ratio:.1f}x** раз выше, чем в предшествующем флете.")


# ---------------------------------------------------------------------------
# Similar impulse search
# ---------------------------------------------------------------------------

def _render_similar_search(
    bricks: List[QuantBrick],
    brick_df: pd.DataFrame,
    dataset: pd.DataFrame,
    root: Path,
    current_symbol: str,
    current_timeframe: str,
) -> None:
    """UI для поиска похожих импульсов на истории."""

    decomposed = st.session_state.get("current_decomposed")
    if decomposed is None:
        st.info("Сначала проанализируй импульс, чтобы искать похожие.")
        return

    search_cols = st.columns([1, 1, 1])
    min_score = search_cols[0].slider(
        "Мин. similarity", 0.3, 0.95, 0.55, 0.05, key="similar_min_score"
    )
    top_k = search_cols[1].number_input("Top-K", 5, 50, 15, key="similar_top_k")
    search_scope = search_cols[2].selectbox(
        "Искать в", ["Библиотеке", "Текущая история (auto-scan)"], key="similar_scope"
    )

    if st.button("🔎 Найти похожие", key="find_similar_btn"):
        if search_scope == "Библиотеке":
            results = find_similar_impulses(
                root=root,
                birth_energy_sig=decomposed.birth.energy_signature,
                birth_volume_sig=decomposed.birth.volume_signature,
                birth_price_sig=decomposed.birth.price_signature,
                symbol=current_symbol,
                direction=decomposed.impulse.direction,
                top_k=top_k,
                min_score=min_score,
            )
            if results:
                st.session_state["_similar_results"] = {
                    "matches": results,
                    "auto_impulses": [],
                    "total_scanned": 0,
                    "source": "library",
                }
            else:
                st.session_state["_similar_results"] = {
                    "matches": [],
                    "auto_impulses": [],
                    "total_scanned": 0,
                    "source": "library",
                }
        else:
            # --- Auto-scan with progress bar ---
            progress_bar = st.progress(0, text="Детектирую импульсы на истории...")
            config = ImpulseDetectorConfig(
                min_energy=1.5,
                min_impulse_bricks=3,
                max_gap_bricks=2,
                min_direction_ratio=0.6,
                min_total_move_pct=0.2,
            )
            auto_impulses = detect_impulses(bricks, config)
            progress_bar.progress(30, text=f"Найдено {len(auto_impulses)} импульсов. Сравниваю сигнатуры...")

            if not auto_impulses:
                progress_bar.empty()
                st.warning("Автодетектор не нашёл импульсов. Попробуйте снизить пороги.")
                return

            matches = []
            total = len(auto_impulses)
            for idx, imp in enumerate(auto_impulses):
                if idx % max(1, total // 20) == 0:
                    pct = 30 + int(70 * idx / total)
                    progress_bar.progress(
                        min(pct, 99),
                        text=f"Сравниваю {idx+1}/{total} импульсов..."
                    )
                dec = decompose_impulse(imp)
                match_result = match_birth_signature(
                    candidate_bricks=dec.birth.bricks,
                    reference=decomposed,
                )
                if match_result["birth_score"] >= min_score:
                    matches.append({
                        "start_ts": str(imp.start_timestamp),
                        "end_ts": str(imp.end_timestamp),
                        "direction": "long" if imp.direction > 0 else "short",
                        "price_move_pct": round(imp.total_price_move_pct, 4),
                        "duration": imp.duration_bricks,
                        "birth_similarity": round(match_result["birth_score"], 3),
                        "energy_sim": round(match_result["energy_similarity"], 3),
                        "volume_sim": round(match_result["volume_similarity"], 3),
                        "quality": round(dec.quality_score, 3),
                        "peak_energy": round(imp.peak_energy, 2),
                    })

            matches.sort(key=lambda x: x["birth_similarity"], reverse=True)
            matches = matches[:top_k]
            progress_bar.progress(100, text="Готово!")

            st.session_state["_similar_results"] = {
                "matches": matches,
                "auto_impulses": auto_impulses,
                "total_scanned": total,
                "source": "auto-scan",
            }
            st.rerun()

    # --- Display cached results ---
    cached = st.session_state.get("_similar_results")
    if cached:
        matches = cached["matches"]
        source = cached.get("source", "auto-scan")

        if source == "library":
            if matches:
                st.success(f"Найдено {len(matches)} похожих импульсов в библиотеке")
                st.dataframe(pd.DataFrame(matches), use_container_width=True, hide_index=True)
            else:
                st.warning("Похожих импульсов в библиотеке не найдено. Сохрани текущий как эталон!")
        else:
            total_scanned = cached.get("total_scanned", 0)
            auto_impulses = cached.get("auto_impulses", [])

            if matches:
                st.success(
                    f"Найдено {len(matches)} похожих импульсов из {total_scanned} "
                    f"обнаруженных автодетектором"
                )
                st.dataframe(pd.DataFrame(matches), use_container_width=True, hide_index=True)

                # --- Confirmation block: win rate + SL/TP ---
                _render_confirmation_block(
                    matches=matches,
                    auto_impulses=auto_impulses,
                    bricks=bricks,
                    dataset=dataset,
                    decomposed=decomposed,
                    root=root,
                    current_symbol=current_symbol,
                    current_timeframe=current_timeframe,
                )
            else:
                st.warning(
                    f"Из {total_scanned} обнаруженных импульсов ни один не прошёл порог "
                    f"similarity {min_score}. Попробуйте снизить порог."
                )

        if st.button("🗑️ Очистить результаты", key="clear_similar_results"):
            st.session_state.pop("_similar_results", None)
            st.rerun()


# ---------------------------------------------------------------------------
# Confirmation block: win rate, SL/TP, paper trading
# ---------------------------------------------------------------------------

def _render_confirmation_block(
    matches: list,
    auto_impulses: list,
    bricks: List[QuantBrick],
    dataset: pd.DataFrame,
    decomposed,
    root: Path,
    current_symbol: str,
    current_timeframe: str,
) -> None:
    """Блок Strategy Builder + Transparent Backtester."""

    st.markdown("---")
    st.markdown("### 🛠 Strategy Builder")
    st.caption("Настройте стратегию входа/выхода и запустите бэктест по историческим совпадениям.")

    direction = decomposed.impulse.direction  # +1 = long, -1 = short
    dir_label = "Long" if direction > 0 else "Short"

    # ── Strategy configurator ────────────────────────────────
    cfg_col1, cfg_col2, cfg_col3, cfg_col4 = st.columns(4)

    entry_type = cfg_col1.selectbox(
        "📍 Точка входа",
        ["На закрытии импульса", "На откате (pullback)", "На пробое Drive max"],
        key="strat_entry_type",
    )
    pullback_pct = 0.0
    if entry_type == "На откате (pullback)":
        pullback_pct = cfg_col1.slider("Откат %", 0.1, 2.0, 0.3, 0.05, key="strat_pullback_pct")

    sl_type = cfg_col2.selectbox(
        "🛑 Stop-Loss",
        ["Фиксированный %", "За минимумом Birth", "ATR × N", "Max Adverse P75"],
        key="strat_sl_type",
    )
    sl_pct_fixed = 0.5
    sl_atr_mult = 2.0
    if sl_type == "Фиксированный %":
        sl_pct_fixed = cfg_col2.slider("SL %", 0.1, 3.0, 0.5, 0.05, key="strat_sl_pct")
    elif sl_type == "ATR × N":
        sl_atr_mult = cfg_col2.slider("ATR множитель", 0.5, 5.0, 2.0, 0.5, key="strat_sl_atr")

    tp_type = cfg_col3.selectbox(
        "🎯 Take-Profit",
        ["R:R мультипликатор", "Фиксированный %", "Длина Drive фазы", "Trailing Stop"],
        key="strat_tp_type",
    )
    tp_rr_mult = 2.0
    tp_pct_fixed = 1.0
    trailing_pct = 0.3
    if tp_type == "R:R мультипликатор":
        tp_rr_mult = cfg_col3.slider("R:R множитель", 1.0, 5.0, 2.0, 0.5, key="strat_tp_rr")
    elif tp_type == "Фиксированный %":
        tp_pct_fixed = cfg_col3.slider("TP %", 0.1, 5.0, 1.0, 0.1, key="strat_tp_pct")
    elif tp_type == "Trailing Stop":
        trailing_pct = cfg_col3.slider("Trail %", 0.1, 2.0, 0.3, 0.05, key="strat_trail_pct")

    forward_bars = cfg_col4.selectbox("⏱ Форвард-окно (баров)", [30, 60, 120, 240], index=1, key="strat_fwd_bars")
    pullback_timeout = 10
    if entry_type == "На откате (pullback)":
        pullback_timeout = cfg_col4.number_input("Timeout ожидания входа", 5, 60, 10, key="strat_pb_timeout")

    # ── Run backtest button ──────────────────────────────────
    if st.button("🚀 Запустить бэктест", type="primary", key="run_backtest_btn"):
        strategy_config = {
            "entry_type": entry_type,
            "pullback_pct": pullback_pct,
            "pullback_timeout": pullback_timeout,
            "sl_type": sl_type,
            "sl_pct_fixed": sl_pct_fixed,
            "sl_atr_mult": sl_atr_mult,
            "tp_type": tp_type,
            "tp_rr_mult": tp_rr_mult,
            "tp_pct_fixed": tp_pct_fixed,
            "trailing_pct": trailing_pct,
            "forward_bars": forward_bars,
            "direction": direction,
        }

        progress = st.progress(0, text="Бэктест: подготовка данных...")
        trades = _backtest_strategy(
            matches=matches,
            dataset=dataset,
            decomposed=decomposed,
            bricks=bricks,
            config=strategy_config,
            progress_callback=progress,
        )
        progress.progress(100, text="Готово!")

        st.session_state["_backtest_results"] = {
            "trades": trades,
            "config": strategy_config,
            "direction_label": dir_label,
        }
        st.rerun()

    # ── Display cached results ───────────────────────────────
    cached_bt = st.session_state.get("_backtest_results")
    if cached_bt:
        _render_backtest_results(
            trades=cached_bt["trades"],
            config=cached_bt["config"],
            direction_label=cached_bt["direction_label"],
            decomposed=decomposed,
            root=root,
            current_symbol=current_symbol,
            current_timeframe=current_timeframe,
            matches=matches,
        )


def _backtest_strategy(
    matches: list,
    dataset: pd.DataFrame,
    decomposed,
    bricks: List[QuantBrick],
    config: dict,
    progress_callback=None,
) -> list:
    """Bar-by-bar бэктест каждого исторического совпадения."""
    import numpy as np

    direction = config["direction"]
    trades = []
    total = len(matches)

    # Precompute ATR if needed
    atr_value = None
    if config["sl_type"] == "ATR × N":
        tr = np.maximum(
            dataset["high"].values - dataset["low"].values,
            np.maximum(
                np.abs(dataset["high"].values - np.roll(dataset["close"].values, 1)),
                np.abs(dataset["low"].values - np.roll(dataset["close"].values, 1)),
            ),
        )
        atr_value = float(np.nanmean(tr[-100:])) if len(tr) > 100 else float(np.nanmean(tr))

    # Max adverse from history for P75-based SL
    adverse_p75 = None
    if config["sl_type"] == "Max Adverse P75":
        adverse_vals = []
        for m in matches:
            # Quick rough estimate from price_move_pct
            adverse_vals.append(abs(m.get("price_move_pct", 0.5)))
        adverse_p75 = float(np.percentile(adverse_vals, 75)) if adverse_vals else 0.5

    ts_col = pd.to_datetime(dataset["timestamp"])
    if ts_col.dt.tz is not None:
        ts_col_naive = ts_col.dt.tz_convert(None)
    else:
        ts_col_naive = ts_col

    for idx, match in enumerate(matches):
        if progress_callback and idx % max(1, total // 10) == 0:
            pct = int(100 * idx / total)
            progress_callback.progress(min(pct, 99), text=f"Бэктест: {idx+1}/{total} сделок...")

        match_end_ts = match.get("end_ts", "")
        if not match_end_ts:
            continue

        try:
            match_ts = pd.to_datetime(match_end_ts)
            if hasattr(match_ts, "tz") and match_ts.tz is not None:
                match_ts = match_ts.tz_localize(None)
            diffs = (ts_col_naive - match_ts).abs()
            end_iloc = int(diffs.argmin())

            forward_window = config["forward_bars"]
            if end_iloc + forward_window >= len(dataset):
                continue

            # ── Determine entry ──────────────────────────────
            entry_price = None
            entry_iloc = None
            entry_type_used = config["entry_type"]

            if entry_type_used == "На закрытии импульса":
                entry_price = float(dataset.iloc[end_iloc]["close"])
                entry_iloc = end_iloc

            elif entry_type_used == "На откате (pullback)":
                pullback_target = config["pullback_pct"] / 100
                found = False
                for bi in range(end_iloc + 1, min(end_iloc + 1 + config["pullback_timeout"], len(dataset))):
                    bar = dataset.iloc[bi]
                    ref_price = float(dataset.iloc[end_iloc]["close"])
                    if direction > 0:  # Long — ждём откат вниз
                        if float(bar["low"]) <= ref_price * (1 - pullback_target):
                            entry_price = ref_price * (1 - pullback_target)
                            entry_iloc = bi
                            found = True
                            break
                    else:  # Short — ждём откат вверх
                        if float(bar["high"]) >= ref_price * (1 + pullback_target):
                            entry_price = ref_price * (1 + pullback_target)
                            entry_iloc = bi
                            found = True
                            break
                if not found:
                    continue  # Пропускаем — откат не произошёл

            elif entry_type_used == "На пробое Drive max":
                drive_max = decomposed.drive.bricks[-1].price_high if decomposed.drive.bricks else None
                drive_min = decomposed.drive.bricks[-1].price_low if decomposed.drive.bricks else None
                found = False
                for bi in range(end_iloc + 1, min(end_iloc + 1 + config["forward_bars"], len(dataset))):
                    bar = dataset.iloc[bi]
                    if direction > 0 and drive_max:
                        if float(bar["high"]) >= drive_max:
                            entry_price = drive_max
                            entry_iloc = bi
                            found = True
                            break
                    elif direction < 0 and drive_min:
                        if float(bar["low"]) <= drive_min:
                            entry_price = drive_min
                            entry_iloc = bi
                            found = True
                            break
                if not found:
                    continue

            if entry_price is None or entry_price <= 0:
                continue

            # ── Determine SL price ───────────────────────────
            if config["sl_type"] == "Фиксированный %":
                sl_pct = config["sl_pct_fixed"] / 100
            elif config["sl_type"] == "За минимумом Birth":
                birth_bricks = decomposed.birth.bricks
                if direction > 0:
                    birth_extreme = min(b.price_low for b in birth_bricks) if birth_bricks else entry_price * 0.99
                    sl_pct = max(0.001, (entry_price - birth_extreme) / entry_price + 0.001)
                else:
                    birth_extreme = max(b.price_high for b in birth_bricks) if birth_bricks else entry_price * 1.01
                    sl_pct = max(0.001, (birth_extreme - entry_price) / entry_price + 0.001)
            elif config["sl_type"] == "ATR × N" and atr_value:
                sl_pct = (atr_value * config["sl_atr_mult"]) / entry_price
            elif config["sl_type"] == "Max Adverse P75" and adverse_p75:
                sl_pct = adverse_p75 / 100
            else:
                sl_pct = config["sl_pct_fixed"] / 100

            if direction > 0:
                sl_price = entry_price * (1 - sl_pct)
            else:
                sl_price = entry_price * (1 + sl_pct)

            # ── Determine TP price ───────────────────────────
            if config["tp_type"] == "R:R мультипликатор":
                tp_pct = sl_pct * config["tp_rr_mult"]
            elif config["tp_type"] == "Фиксированный %":
                tp_pct = config["tp_pct_fixed"] / 100
            elif config["tp_type"] == "Длина Drive фазы":
                tp_pct = abs(decomposed.drive.price_move_pct) / 100
                if tp_pct < 0.001:
                    tp_pct = sl_pct * 2
            else:  # Trailing
                tp_pct = sl_pct * 3  # будем управлять trailing'ом ниже

            if direction > 0:
                tp_price = entry_price * (1 + tp_pct)
            else:
                tp_price = entry_price * (1 - tp_pct)

            # ── Bar-by-bar simulation ────────────────────────
            exit_price = None
            exit_reason = None
            exit_bar = None
            mae = 0.0  # Max Adverse Excursion
            mfe = 0.0  # Max Favorable Excursion
            trailing_high = entry_price if direction > 0 else entry_price
            trailing_active = False

            sim_start = entry_iloc + 1
            sim_end = min(entry_iloc + 1 + forward_window, len(dataset))

            for bi in range(sim_start, sim_end):
                bar = dataset.iloc[bi]
                bar_high = float(bar["high"])
                bar_low = float(bar["low"])
                bar_close = float(bar["close"])

                if direction > 0:  # LONG
                    current_pnl_high = (bar_high / entry_price - 1) * 100
                    current_pnl_low = (bar_low / entry_price - 1) * 100
                    mfe = max(mfe, current_pnl_high)
                    mae = min(mae, current_pnl_low)

                    # Check SL
                    if bar_low <= sl_price:
                        exit_price = sl_price
                        exit_reason = "SL ❌"
                        exit_bar = bi
                        break

                    # Check TP (or trailing)
                    if config["tp_type"] == "Trailing Stop":
                        trailing_high = max(trailing_high, bar_high)
                        trail_stop = trailing_high * (1 - config["trailing_pct"] / 100)
                        if trailing_high > entry_price * 1.001:
                            trailing_active = True
                        if trailing_active and bar_low <= trail_stop:
                            exit_price = trail_stop
                            exit_reason = "TRAIL ✅"
                            exit_bar = bi
                            break
                    else:
                        if bar_high >= tp_price:
                            exit_price = tp_price
                            exit_reason = "TP ✅"
                            exit_bar = bi
                            break

                else:  # SHORT
                    current_pnl_high = (1 - bar_low / entry_price) * 100
                    current_pnl_low = (1 - bar_high / entry_price) * 100
                    mfe = max(mfe, current_pnl_high)
                    mae = min(mae, current_pnl_low)

                    # Check SL
                    if bar_high >= sl_price:
                        exit_price = sl_price
                        exit_reason = "SL ❌"
                        exit_bar = bi
                        break

                    # Check TP (or trailing)
                    if config["tp_type"] == "Trailing Stop":
                        trailing_high = min(trailing_high, bar_low)
                        trail_stop = trailing_high * (1 + config["trailing_pct"] / 100)
                        if trailing_high < entry_price * 0.999:
                            trailing_active = True
                        if trailing_active and bar_high >= trail_stop:
                            exit_price = trail_stop
                            exit_reason = "TRAIL ✅"
                            exit_bar = bi
                            break
                    else:
                        if bar_low <= tp_price:
                            exit_price = tp_price
                            exit_reason = "TP ✅"
                            exit_bar = bi
                            break

            # Timeout — не вышли по SL/TP
            if exit_price is None:
                last_bar = dataset.iloc[min(sim_end - 1, len(dataset) - 1)]
                exit_price = float(last_bar["close"])
                exit_reason = "TIMEOUT ⏱"
                exit_bar = min(sim_end - 1, len(dataset) - 1)

            # Calculate PnL
            if direction > 0:
                pnl_pct = (exit_price / entry_price - 1) * 100
            else:
                pnl_pct = (1 - exit_price / entry_price) * 100

            entry_ts = str(dataset.iloc[entry_iloc]["timestamp"]) if entry_iloc < len(dataset) else ""
            exit_ts = str(dataset.iloc[exit_bar]["timestamp"]) if exit_bar < len(dataset) else ""

            trades.append({
                "entry_ts": entry_ts,
                "exit_ts": exit_ts,
                "direction": dir_label if direction > 0 else "Short",
                "entry_price": round(entry_price, 2),
                "sl_price": round(sl_price, 2),
                "tp_price": round(tp_price, 2),
                "exit_price": round(exit_price, 2),
                "exit_reason": exit_reason,
                "pnl_pct": round(pnl_pct, 4),
                "mae_pct": round(mae, 4),
                "mfe_pct": round(mfe, 4),
                "bars_held": exit_bar - entry_iloc if exit_bar else 0,
                "sl_pct": round(sl_pct * 100, 4),
                "tp_pct": round(tp_pct * 100, 4),
            })

        except Exception:
            continue

    return trades


def _render_backtest_results(
    trades: list,
    config: dict,
    direction_label: str,
    decomposed,
    root: Path,
    current_symbol: str,
    current_timeframe: str,
    matches: list,
) -> None:
    """Отображение результатов бэктеста."""
    import plotly.graph_objects as go

    if not trades:
        st.warning("Ни одна сделка не была смоделирована. Проверьте параметры стратегии или выберите другой импульс.")
        return

    st.markdown("---")
    st.markdown("### 📊 Результаты бэктеста")

    # ── Aggregate statistics ─────────────────────────────────
    total = len(trades)
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    losses = total - wins
    win_rate = wins / total * 100 if total > 0 else 0

    pnl_values = [t["pnl_pct"] for t in trades]
    avg_pnl = float(np.mean(pnl_values))
    median_pnl = float(np.median(pnl_values))
    total_pnl = sum(pnl_values)

    sl_hits = sum(1 for t in trades if "SL" in t["exit_reason"])
    tp_hits = sum(1 for t in trades if "TP" in t["exit_reason"] or "TRAIL" in t["exit_reason"])
    timeouts = sum(1 for t in trades if "TIMEOUT" in t["exit_reason"])

    avg_win = float(np.mean([t["pnl_pct"] for t in trades if t["pnl_pct"] > 0])) if wins > 0 else 0
    avg_loss = float(np.mean([t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0])) if losses > 0 else 0
    profit_factor = abs(avg_win * wins / (avg_loss * losses)) if losses > 0 and avg_loss != 0 else float('inf')

    # Max drawdown
    cumulative = np.cumsum(pnl_values)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = cumulative - running_max
    max_dd = float(drawdowns.min()) if len(drawdowns) > 0 else 0

    rr_actual = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')

    # Confidence
    if win_rate >= 80:
        confidence = "🟢 HIGH"
        conf_color = "green"
    elif win_rate >= 60:
        confidence = "🟡 MEDIUM"
        conf_color = "orange"
    else:
        confidence = "🔴 LOW"
        conf_color = "red"

    # ── Metrics row ──────────────────────────────────────────
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Win Rate", f"{win_rate:.0f}%")
    m2.metric("Сделок", f"{total} ({wins}W / {losses}L)")
    m3.metric("Avg PnL", f"{avg_pnl:+.3f}%")
    m4.metric("R:R actual", f"{rr_actual:.1f}")
    m5.metric("Profit Factor", f"{profit_factor:.1f}" if profit_factor < 100 else "∞")
    m6.metric("Confidence", confidence)

    m7, m8, m9, m10 = st.columns(4)
    m7.metric("TP hits", f"{tp_hits}")
    m8.metric("SL hits", f"{sl_hits}")
    m9.metric("Timeouts", f"{timeouts}")
    m10.metric("Max DD", f"{max_dd:+.2f}%")

    # ── Strategy summary ─────────────────────────────────────
    with st.expander("📋 Параметры стратегии", expanded=False):
        st.json(config)

    # ── Equity curve ─────────────────────────────────────────
    st.markdown("#### 📈 Equity Curve")
    cumulative_pnl = np.cumsum(pnl_values)

    fig = go.Figure()
    colors = ["#22c55e" if p > 0 else "#ef4444" for p in pnl_values]

    fig.add_trace(go.Scatter(
        x=list(range(1, total + 1)),
        y=cumulative_pnl.tolist(),
        mode="lines+markers",
        line=dict(color="#3b82f6", width=2),
        marker=dict(size=8, color=colors, line=dict(width=1, color="#1e293b")),
        name="Cumulative PnL",
        hovertemplate="Trade %{x}<br>Cum. PnL: %{y:.3f}%<br>PnL: %{customdata:.3f}%<extra></extra>",
        customdata=pnl_values,
    ))
    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
    fig.update_layout(
        template="plotly_dark",
        height=300,
        margin=dict(l=40, r=20, t=20, b=40),
        xaxis_title="Сделка #",
        yaxis_title="Cum. PnL %",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Trades table ─────────────────────────────────────────
    st.markdown("#### 📋 Таблица сделок")
    trades_df = pd.DataFrame(trades)
    display_cols = [
        "entry_ts", "direction", "entry_price", "sl_price", "tp_price",
        "exit_price", "exit_reason", "pnl_pct", "mae_pct", "mfe_pct", "bars_held",
    ]
    available = [c for c in display_cols if c in trades_df.columns]

    # Color PnL column
    def highlight_pnl(val):
        if isinstance(val, (int, float)):
            if val > 0:
                return "color: #22c55e"
            elif val < 0:
                return "color: #ef4444"
        return ""

    styled = trades_df[available].style.applymap(
        highlight_pnl, subset=["pnl_pct"]
    )
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # ── PnL distribution ─────────────────────────────────────
    with st.expander("📊 Распределение PnL", expanded=False):
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Histogram(
            x=pnl_values,
            nbinsx=20,
            marker_color="#3b82f6",
            opacity=0.8,
        ))
        fig_hist.add_vline(x=0, line_dash="dash", line_color="white", opacity=0.5)
        fig_hist.add_vline(x=avg_pnl, line_dash="dot", line_color="#22c55e", opacity=0.8,
                           annotation_text=f"Avg: {avg_pnl:+.3f}%")
        fig_hist.update_layout(
            template="plotly_dark",
            height=250,
            margin=dict(l=40, r=20, t=20, b=40),
            xaxis_title="PnL %",
            yaxis_title="Частота",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_hist, use_container_width=True)

    # ── MAE/MFE scatter ──────────────────────────────────────
    with st.expander("🎯 MAE / MFE анализ", expanded=False):
        fig_mae = go.Figure()
        win_trades = [t for t in trades if t["pnl_pct"] > 0]
        loss_trades = [t for t in trades if t["pnl_pct"] <= 0]

        if win_trades:
            fig_mae.add_trace(go.Scatter(
                x=[t["mae_pct"] for t in win_trades],
                y=[t["mfe_pct"] for t in win_trades],
                mode="markers",
                marker=dict(size=10, color="#22c55e", opacity=0.8),
                name="Wins",
            ))
        if loss_trades:
            fig_mae.add_trace(go.Scatter(
                x=[t["mae_pct"] for t in loss_trades],
                y=[t["mfe_pct"] for t in loss_trades],
                mode="markers",
                marker=dict(size=10, color="#ef4444", opacity=0.8),
                name="Losses",
            ))
        fig_mae.update_layout(
            template="plotly_dark",
            height=300,
            margin=dict(l=40, r=20, t=20, b=40),
            xaxis_title="MAE % (макс. убыток внутри сделки)",
            yaxis_title="MFE % (макс. прибыль внутри сделки)",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_mae, use_container_width=True)
        st.caption(
            "**MAE** (Maximum Adverse Excursion) — максимальный убыток внутри сделки до выхода. "
            "**MFE** (Maximum Favorable Excursion) — максимальная прибыль внутри сделки до выхода. "
            "Если MFE высокий у проигрышных сделок — стоп слишком далёкий."
        )

    # ── Action buttons ───────────────────────────────────────
    st.markdown("---")

    if win_rate >= 80:
        st.success(
            f"🎉 **Паттерн подтверждён!** Win rate {win_rate:.0f}% (≥80%). "
            f"R:R = {rr_actual:.1f}. Рекомендуется отправить в Paper Trading."
        )
        paper_cols = st.columns([2, 1, 1])
        if paper_cols[0].button(
            "📈 Отправить в Paper Trading",
            type="primary",
            use_container_width=True,
            key="send_to_paper_btn",
        ):
            optimal_sl = float(np.mean([t["sl_pct"] for t in trades]))
            optimal_tp = float(np.mean([t["tp_pct"] for t in trades]))
            _send_to_paper_trading(
                decomposed=decomposed,
                confirmation={
                    "win_rate": round(win_rate, 1), "total_matches": total,
                    "avg_pnl": round(avg_pnl, 4),
                    "optimal_sl_pct": round(optimal_sl, 4),
                    "optimal_tp_pct": round(optimal_tp, 4),
                    "rr_ratio": round(rr_actual, 2),
                    "confidence": confidence,
                    "direction": direction_label.lower(),
                    "strategy_config": config,
                },
                root=root, current_symbol=current_symbol, current_timeframe=current_timeframe,
            )
        if paper_cols[1].button("💾 Сохранить + Paper", key="save_and_paper_btn"):
            from .pattern_library import save_impulse
            save_impulse(root=root, decomposed=decomposed, symbol=current_symbol,
                         timeframe=current_timeframe, source="confirmed", tags="confirmed,paper",
                         notes=f"WR: {win_rate:.0f}%, R/R: {rr_actual:.1f}", data_path="")
            optimal_sl = float(np.mean([t["sl_pct"] for t in trades]))
            optimal_tp = float(np.mean([t["tp_pct"] for t in trades]))
            _send_to_paper_trading(
                decomposed=decomposed,
                confirmation={
                    "win_rate": round(win_rate, 1), "total_matches": total,
                    "avg_pnl": round(avg_pnl, 4),
                    "optimal_sl_pct": round(optimal_sl, 4),
                    "optimal_tp_pct": round(optimal_tp, 4),
                    "rr_ratio": round(rr_actual, 2),
                    "confidence": confidence,
                    "direction": direction_label.lower(),
                    "strategy_config": config,
                },
                root=root, current_symbol=current_symbol, current_timeframe=current_timeframe,
            )
    elif win_rate >= 60:
        st.info(
            f"🟡 Win rate {win_rate:.0f}% — выше среднего, но ниже 80%. "
            f"Попробуйте подкрутить SL/TP или используйте Auto-Optimizer."
        )
    else:
        st.warning(
            f"⚠️ Win rate {win_rate:.0f}% ниже 60%. "
            f"Попробуйте другую стратегию входа/выхода или другой импульс."
        )

    if st.button("🗑️ Очистить результаты бэктеста", key="clear_backtest_results"):
        st.session_state.pop("_backtest_results", None)
        st.rerun()


def _send_to_paper_trading(decomposed, confirmation: dict, root: Path,
                           current_symbol: str, current_timeframe: str) -> None:
    """Создаёт запись в paper trading queue."""
    import json
    import datetime as _dt
    paper_dir = root / "paper_queue"
    paper_dir.mkdir(parents=True, exist_ok=True)
    impulse = decomposed.impulse
    entry = {
        "hypothesis_id": f"impulse_{_dt.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        "queued_at": _dt.datetime.utcnow().isoformat(),
        "symbol": current_symbol,
        "timeframe": current_timeframe,
        "title": (f"{'Long' if impulse.direction > 0 else 'Short'} impulse | "
                  f"WR {confirmation['win_rate']}% | R/R {confirmation['rr_ratio']}"),
        "direction": confirmation["direction"],
        "impulse_signature": {
            "total_move_pct": round(impulse.total_price_move_pct, 4),
            "duration_bricks": impulse.duration_bricks,
            "peak_energy": round(impulse.peak_energy, 2),
            "quality_score": round(decomposed.quality_score, 2),
        },
        "confirmation": confirmation,
        "trade_params": {
            "stop_loss_pct": confirmation["optimal_sl_pct"],
            "take_profit_pct": confirmation["optimal_tp_pct"],
            "risk_reward": confirmation["rr_ratio"],
        },
        "paper_state": "queued",
        "validation_result": {
            "win_rate": confirmation["win_rate"] / 100,
            "trades": confirmation["total_matches"],
            "events_found": confirmation["total_matches"],
        },
    }
    filepath = paper_dir / f"{entry['hypothesis_id']}.json"
    filepath.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
    st.success(
        f"✅ Отправлено в Paper Trading: **{entry['title']}**\n\n"
        f"SL: {confirmation['optimal_sl_pct']:.3f}% | TP: {confirmation['optimal_tp_pct']:.3f}%\n\n"
        f"Файл: `{filepath.name}`"
    )


# ---------------------------------------------------------------------------
# Pattern library UI
# ---------------------------------------------------------------------------

def _render_pattern_library(root: Path, current_symbol: str) -> None:
    """Показывает сохранённые паттерны."""
    stats = get_library_stats(root, symbol=current_symbol)

    lib_cols = st.columns(5)
    lib_cols[0].metric("Всего паттернов", stats.get("total", 0))
    lib_cols[1].metric("Ручных", stats.get("manual", 0))
    lib_cols[2].metric("Long / Short", f"{stats.get('longs', 0)} / {stats.get('shorts', 0)}")
    lib_cols[3].metric("Ср. quality", stats.get("avg_quality", 0))
    lib_cols[4].metric("Ср. |move|", f"{stats.get('avg_abs_move_pct', 0):.4f}%")

    saved_impulses = list_impulses(root, symbol=current_symbol)
    if saved_impulses:
        display_df = pd.DataFrame(saved_impulses)
        display_cols = [
            "impulse_id", "direction_label", "total_price_move_pct",
            "duration_bricks", "peak_energy", "mean_energy",
            "quality_score", "source", "tags", "start_timestamp",
        ]
        available_cols = [c for c in display_cols if c in display_df.columns]
        st.dataframe(display_df[available_cols], use_container_width=True, hide_index=True)

        with st.expander("🗑 Управление паттернами"):
            del_id = st.selectbox(
                "Выберите для удаления",
                options=[""] + [imp["impulse_id"] for imp in saved_impulses],
                key="delete_impulse_select",
            )
            if del_id and st.button("Удалить", key="delete_impulse_btn"):
                delete_impulse(root, del_id)
                st.success(f"Удалён: {del_id}")
                st.rerun()
    else:
        st.info("Библиотека пуста. Разметь и сохрани первый импульс!")


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Строю кирпичики...", ttl=300)
def _cached_build_bricks(
    data_path_str: str,
    data_mtime: float,
    lookback: int,
    duration_seconds: int,
) -> List[QuantBrick]:
    """Кешированное построение кирпичиков."""
    from .data import load_ohlcv_csv
    try:
        df = load_ohlcv_csv(Path(data_path_str))
    except Exception:
        return []
    return build_bricks_from_ohlcv(df, lookback=lookback, duration_seconds=duration_seconds)
