"""
🧠 Impulse Intelligence Engine (IIE)

Self-learning system for impulse analysis across all trading bots.
Collects impulse data → builds coin profiles → adapts trading params.

Components:
    impulse_db       — SQLite storage layer
    impulse_collector — Background impulse scanner
    post_trade_tracker — Post-exit outcome analysis
    market_phase     — BTC/ETH macro phase detector
    coin_scorer      — Per-coin scoring engine
    impulse_predictor — ML prediction model
    adaptive_manager — Decision engine for bots
    iie_daemon       — Background orchestrator
"""
__version__ = "0.1.0"
