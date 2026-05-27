from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json


@dataclass
class FeatureConfig:
    lookback_fast: int = 3
    lookback_slow: int = 12
    volume_window: int = 20
    volatility_window: int = 20


@dataclass
class LabelConfig:
    horizon: int = 5
    min_move_pct: float = 0.0015


@dataclass
class TrainConfig:
    train_split: float = 0.8
    epochs: int = 30
    batch_size: int = 256
    hidden_dim: int = 64
    lr: float = 0.001
    weight_decay: float = 1e-4
    seed: int = 42


@dataclass
class BacktestConfig:
    fee_bps: float = 4.0
    slippage_bps: float = 1.5
    decision_threshold: float = 0.48
    cooldown_bars: int = 3


@dataclass
class RunConfig:
    feature: FeatureConfig = field(default_factory=FeatureConfig)
    label: LabelConfig = field(default_factory=LabelConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
