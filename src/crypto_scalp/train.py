from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from .config import RunConfig
from .data import load_ohlcv_csv
from .features import build_dataset
from .model import MLPClassifier, save_model, set_seed


def run_training(data_path: Path, artifacts_dir: Path, config: RunConfig) -> dict:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    config.save(artifacts_dir / "config.json")

    raw = load_ohlcv_csv(data_path)
    prepared = build_dataset(raw, config.feature, config.label)
    frame = prepared.frame
    features = prepared.feature_columns

    split_idx = int(len(frame) * config.train.train_split)
    train_df = frame.iloc[:split_idx].copy()
    valid_df = frame.iloc[split_idx:].copy()

    x_train = train_df[features].to_numpy(dtype=np.float32)
    y_train = train_df["target"].to_numpy(dtype=np.int64)
    x_valid = valid_df[features].to_numpy(dtype=np.float32)
    y_valid = valid_df["target"].to_numpy(dtype=np.int64)

    means = x_train.mean(axis=0)
    stds = np.where(x_train.std(axis=0) == 0, 1.0, x_train.std(axis=0))
    x_train = (x_train - means) / stds
    x_valid = (x_valid - means) / stds

    set_seed(config.train.seed)
    model = MLPClassifier(input_dim=len(features), hidden_dim=config.train.hidden_dim)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.train.lr,
        weight_decay=config.train.weight_decay,
    )
    criterion = nn.CrossEntropyLoss()

    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(x_train), torch.from_numpy(y_train)),
        batch_size=config.train.batch_size,
        shuffle=True,
    )

    history: list[dict] = []
    for epoch in range(1, config.train.epochs + 1):
        model.train()
        epoch_losses = []
        for xb, yb in train_loader:
            optimizer.zero_grad()
            logits = model(xb)
            loss = criterion(logits, yb)
            loss.backward()
            optimizer.step()
            epoch_losses.append(loss.item())

        metrics = evaluate(model, x_valid, y_valid)
        history.append(
            {
                "epoch": epoch,
                "train_loss": float(np.mean(epoch_losses)),
                **metrics,
            }
        )

    save_model(
        artifacts_dir / "model.pt",
        artifacts_dir / "model_meta.json",
        model,
        features,
        means,
        stds,
    )
    (artifacts_dir / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")

    summary = {
        "rows": int(len(frame)),
        "train_rows": int(len(train_df)),
        "valid_rows": int(len(valid_df)),
        "class_balance": frame["target"].value_counts(normalize=True).sort_index().to_dict(),
        "final_metrics": history[-1],
    }
    (artifacts_dir / "train_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


@torch.no_grad()
def evaluate(model: nn.Module, x_valid: np.ndarray, y_valid: np.ndarray) -> dict:
    model.eval()
    logits = model(torch.from_numpy(x_valid))
    probs = torch.softmax(logits, dim=1).numpy()
    preds = probs.argmax(axis=1)
    accuracy = float((preds == y_valid).mean())

    long_precision = precision_for_class(preds, y_valid, target_class=2)
    short_precision = precision_for_class(preds, y_valid, target_class=0)
    active_rate = float(np.isin(preds, [0, 2]).mean())

    return {
        "valid_accuracy": accuracy,
        "long_precision": long_precision,
        "short_precision": short_precision,
        "active_rate": active_rate,
    }


def precision_for_class(preds: np.ndarray, target: np.ndarray, target_class: int) -> float:
    chosen = preds == target_class
    if chosen.sum() == 0:
        return 0.0
    return float((target[chosen] == target_class).mean())
