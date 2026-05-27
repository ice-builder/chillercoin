from __future__ import annotations

import json
from pathlib import Path
import random

import numpy as np
import torch
from torch import nn


class MLPClassifier(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, output_dim: int = 3) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(0.15),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def save_model(
    model_path: Path,
    meta_path: Path,
    model: nn.Module,
    feature_columns: list[str],
    means: np.ndarray,
    stds: np.ndarray,
) -> None:
    torch.save(model.state_dict(), model_path)
    meta = {
        "feature_columns": feature_columns,
        "means": means.tolist(),
        "stds": stds.tolist(),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_model(
    model_path: Path,
    meta_path: Path,
    hidden_dim: int,
) -> tuple[nn.Module, dict]:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    model = MLPClassifier(
        input_dim=len(meta["feature_columns"]),
        hidden_dim=hidden_dim,
        output_dim=3,
    )
    model.load_state_dict(torch.load(model_path, map_location="cpu"))
    model.eval()
    return model, meta
