"""
OneProp Signal Hub — Pydantic Models
Soldier-only signal system with entry/exit targets and trade close tracking.
"""
from pydantic import BaseModel, Field
from typing import Optional


class SignalCreate(BaseModel):
    """Incoming signal from Soldier bot (trade opened)."""
    source: str = Field(default="soldier", description="Signal source (soldier only)")
    signal_type: str = Field(default="iie_impulse", description="Strategy type")
    symbol: str = Field(..., description="e.g. BTCUSDT")
    exchange: str = Field(default="bybit")
    direction: str = Field(..., description="long | short")
    price_at_signal: float
    entry_target: float = Field(default=0.0, description="Точка входа")
    exit_target: float = Field(default=0.0, description="Целевой выход (TP)")
    strength: float = Field(default=0.0, ge=0, le=1)
    description: str = ""
    metadata: Optional[dict] = None


class SignalClose(BaseModel):
    """Trade close notification from Soldier bot."""
    symbol: str = Field(..., description="e.g. BTCUSDT")
    direction: str = Field(..., description="long | short")
    entry_price: float
    exit_price: float
    exit_reason: str = Field(..., description="take_profit | iie_trailing_stop | iie_stop_loss | breakeven | catastrophic_stop")
    pnl_pct: float
    bars_held: int = 0


class SignalResponse(BaseModel):
    id: int
    source: str
    signal_type: str
    symbol: str
    exchange: str
    direction: str
    price_at_signal: float
    entry_target: float = 0.0
    exit_target: float = 0.0
    strength: float
    description: str
    created_at: str
    # Trade close data
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    exit_pnl_pct: Optional[float] = None
    closed_at: Optional[str] = None
    # P&L checkpoints
    pnl_15m: Optional[float] = None
    win_15m: Optional[int] = None
    pnl_1h: Optional[float] = None
    win_1h: Optional[int] = None
    pnl_4h: Optional[float] = None
    win_4h: Optional[int] = None
