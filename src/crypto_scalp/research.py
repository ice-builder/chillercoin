from __future__ import annotations

from pathlib import Path
import json
from typing import Optional

import numpy as np
import pandas as pd
import torch

from .config import RunConfig
from .data import load_ohlcv_csv
from .features import PreparedDataset, build_dataset
from .model import load_model


def discover_csv_files(root: Path) -> list[Path]:
    return sorted(root.glob("**/*.csv"))


def discover_artifact_dirs(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob("**/model_meta.json") if path.parent.is_dir())


def load_prepared_dataset(data_path: Path, config: Optional[RunConfig] = None) -> PreparedDataset:
    config = config or RunConfig()
    raw = load_ohlcv_csv(data_path)
    return build_dataset(raw, config.feature, config.label)


def score_dataset(
    prepared: PreparedDataset,
    artifacts_dir: Path,
    config: Optional[RunConfig] = None,
) -> pd.DataFrame:
    config = config or RunConfig()
    frame = prepared.frame.copy()
    model, meta = load_model(
        artifacts_dir / "model.pt",
        artifacts_dir / "model_meta.json",
        hidden_dim=config.train.hidden_dim,
    )

    features = meta["feature_columns"]
    means = np.array(meta["means"], dtype=np.float32)
    stds = np.array(meta["stds"], dtype=np.float32)

    x = frame[features].to_numpy(dtype=np.float32)
    x = (x - means) / stds
    with torch.no_grad():
        logits = model(torch.from_numpy(x))
        probs = torch.softmax(logits, dim=1).numpy()

    frame["prob_short"] = probs[:, 0]
    frame["prob_flat"] = probs[:, 1]
    frame["prob_long"] = probs[:, 2]
    frame["predicted_class"] = probs.argmax(axis=1)
    frame["predicted_signal"] = frame["predicted_class"].map({0: "short", 1: "flat", 2: "long"})
    return frame


def load_summary(artifacts_dir: Path, filename: str) -> Optional[dict]:
    path = artifacts_dir / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
