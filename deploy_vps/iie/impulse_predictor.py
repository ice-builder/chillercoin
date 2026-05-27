"""
IIE — Impulse Predictor (ML)

XGBoost model that predicts impulse outcomes:
  1. will_continue: probability that price continues in impulse direction
  2. max_favorable_pct: expected maximum favorable move
  3. is_stop_hunt: probability this is a stop hunt (false breakout)

Retrains every 24h on all accumulated data.
Requires minimum 100 completed outcomes for first training.
"""
import time
import json
import logging
import numpy as np
import pickle
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from . import config
from .impulse_db import ImpulseDB, Impulse

logger = logging.getLogger("iie.predictor")

# Feature columns used by the model
FEATURE_COLS = [
    "vol_z", "ret_z", "combined_score",
    "rsi_at_impulse", "ema_deviation_pct",
    "candle_body_pct", "wick_ratio_top", "wick_ratio_bottom",
    "is_long",            # 1 for long, 0 for short
    "loc_at_high",        # one-hot: impulse at range high
    "loc_at_low",         # one-hot: impulse at range low
    "loc_mid_range",      # one-hot: impulse at mid range
    "hour_sin", "hour_cos",  # cyclical time encoding
    "atr_at_impulse",
    # Coin profile features (if available)
    "coin_quality",
    "coin_stop_hunt_freq",
    "coin_momentum",
    "coin_level_respect",
]


class ImpulsePredictor:
    """XGBoost-based impulse outcome predictor."""

    def __init__(self, db: ImpulseDB, model_dir: Optional[Path] = None):
        self.db = db
        self.model_dir = model_dir or config.MODEL_DIR
        self.model_continue = None
        self.model_favorable = None
        self.model_hunt = None
        self.feature_importance: Dict[str, float] = {}
        self.last_train_time = 0
        self.train_samples = 0
        self._load_models()

    def _load_models(self):
        """Load trained models from disk if available."""
        try:
            path = self.model_dir / "predictor_state.pkl"
            if path.exists():
                with open(path, "rb") as f:
                    state = pickle.load(f)
                self.model_continue = state.get("model_continue")
                self.model_favorable = state.get("model_favorable")
                self.model_hunt = state.get("model_hunt")
                self.feature_importance = state.get("feature_importance", {})
                self.last_train_time = state.get("last_train_time", 0)
                self.train_samples = state.get("train_samples", 0)
                logger.info(
                    f"📚 Loaded predictor models (trained on {self.train_samples} samples)")
        except Exception as e:
            logger.warning(f"Could not load models: {e}")

    def _save_models(self):
        """Save trained models to disk."""
        try:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            path = self.model_dir / "predictor_state.pkl"
            state = {
                "model_continue": self.model_continue,
                "model_favorable": self.model_favorable,
                "model_hunt": self.model_hunt,
                "feature_importance": self.feature_importance,
                "last_train_time": self.last_train_time,
                "train_samples": self.train_samples,
            }
            with open(path, "wb") as f:
                pickle.dump(state, f)
            logger.info(f"💾 Models saved ({self.train_samples} samples)")
        except Exception as e:
            logger.warning(f"Failed to save models: {e}")

    def train(self) -> bool:
        """Train all models on accumulated data. Returns True if successful."""
        try:
            import xgboost as xgb
            from sklearn.model_selection import train_test_split
            from sklearn.metrics import accuracy_score, mean_absolute_error
        except ImportError:
            logger.warning("xgboost/sklearn not installed — skipping training")
            return False

        raw_data = self.db.get_all_completed_data(limit=5000)
        if len(raw_data) < config.PREDICTOR_MIN_SAMPLES:
            logger.info(
                f"📊 Not enough data for training: {len(raw_data)}/{config.PREDICTOR_MIN_SAMPLES}")
            return False

        # Prepare features and targets
        X, y_continue, y_favorable, y_hunt = self._prepare_training_data(raw_data)
        if X is None or len(X) < 50:
            return False

        logger.info(f"🧠 Training predictor on {len(X)} samples...")

        # ─── Model 1: will_continue (binary classification) ───
        X_train, X_test, y_tr, y_te = train_test_split(
            X, y_continue, test_size=0.2, random_state=42)

        self.model_continue = xgb.XGBClassifier(
            n_estimators=100, max_depth=5, learning_rate=0.1,
            eval_metric="logloss", verbosity=0, random_state=42)
        self.model_continue.fit(X_train, y_tr)
        pred = self.model_continue.predict(X_test)
        acc = accuracy_score(y_te, pred)
        logger.info(f"  📈 will_continue accuracy: {acc:.1%}")

        # ─── Model 2: max_favorable (regression) ──────────────
        X_train2, X_test2, y_tr2, y_te2 = train_test_split(
            X, y_favorable, test_size=0.2, random_state=42)

        self.model_favorable = xgb.XGBRegressor(
            n_estimators=100, max_depth=5, learning_rate=0.1,
            eval_metric="mae", verbosity=0, random_state=42)
        self.model_favorable.fit(X_train2, y_tr2)
        pred2 = self.model_favorable.predict(X_test2)
        mae = mean_absolute_error(y_te2, pred2)
        logger.info(f"  📊 max_favorable MAE: {mae:.2f}%")

        # ─── Model 3: is_stop_hunt (binary classification) ────
        X_train3, X_test3, y_tr3, y_te3 = train_test_split(
            X, y_hunt, test_size=0.2, random_state=42)

        self.model_hunt = xgb.XGBClassifier(
            n_estimators=80, max_depth=4, learning_rate=0.1,
            eval_metric="logloss", verbosity=0, random_state=42)
        self.model_hunt.fit(X_train3, y_tr3)
        pred3 = self.model_hunt.predict(X_test3)
        acc3 = accuracy_score(y_te3, pred3)
        logger.info(f"  🎯 is_stop_hunt accuracy: {acc3:.1%}")

        # Feature importance
        importances = self.model_continue.feature_importances_
        feat_names = FEATURE_COLS[:len(importances)]
        self.feature_importance = {
            name: round(float(imp), 4)
            for name, imp in sorted(zip(feat_names, importances),
                                     key=lambda x: x[1], reverse=True)
        }
        top3 = list(self.feature_importance.items())[:3]
        logger.info(f"  🔑 Top features: {top3}")

        self.last_train_time = time.time()
        self.train_samples = len(X)
        self._save_models()
        return True

    def predict(self, impulse: Impulse,
                coin_profile: Optional[dict] = None) -> Dict[str, float]:
        """
        Predict outcomes for a new impulse.

        Returns dict with:
          - will_continue_prob: 0.0-1.0
          - predicted_favorable_pct: expected max favorable move
          - stop_hunt_prob: 0.0-1.0
          - confidence: model confidence (0-100)
        """
        if self.model_continue is None:
            return {"will_continue_prob": 0.5, "predicted_favorable_pct": 0,
                    "stop_hunt_prob": 0.5, "confidence": 0}

        features = self._impulse_to_features(impulse, coin_profile)
        if features is None:
            return {"will_continue_prob": 0.5, "predicted_favorable_pct": 0,
                    "stop_hunt_prob": 0.5, "confidence": 0}

        X = np.array([features])

        # Predictions
        cont_prob = float(self.model_continue.predict_proba(X)[0][1])
        favorable = float(self.model_favorable.predict(X)[0]) if self.model_favorable else 0
        hunt_prob = float(self.model_hunt.predict_proba(X)[0][1]) if self.model_hunt else 0.5

        # Confidence: based on how far from 50% the predictions are
        confidence = min(100, abs(cont_prob - 0.5) * 200 + self.train_samples / 50)

        return {
            "will_continue_prob": round(cont_prob, 3),
            "predicted_favorable_pct": round(max(0, favorable), 2),
            "stop_hunt_prob": round(hunt_prob, 3),
            "confidence": round(min(100, confidence), 1),
        }

    def _prepare_training_data(self, raw_data: List[dict]):
        """Convert raw DB data to numpy arrays for training."""
        X_list = []
        y_cont = []
        y_fav = []
        y_hunt = []

        for d in raw_data:
            features = self._dict_to_features(d)
            if features is None:
                continue

            max_fav = d.get("max_favorable_pct", 0) or 0
            max_adv = d.get("max_adverse_pct", 0) or 0
            was_hunt = int(d.get("was_stop_hunt", 0) or 0)

            # will_continue: favorable > adverse (impulse paid off)
            will_continue = 1 if max_fav > max_adv else 0

            X_list.append(features)
            y_cont.append(will_continue)
            y_fav.append(max_fav)
            y_hunt.append(was_hunt)

        if not X_list:
            return None, None, None, None

        return (np.array(X_list), np.array(y_cont),
                np.array(y_fav), np.array(y_hunt))

    def _impulse_to_features(self, imp: Impulse,
                              coin_profile: Optional[dict] = None) -> Optional[list]:
        """Convert Impulse dataclass to feature vector."""
        from datetime import datetime, timezone
        try:
            dt = datetime.fromtimestamp(imp.timestamp, tz=timezone.utc)
            hour = dt.hour
        except Exception:
            hour = 12

        return [
            imp.vol_z,
            imp.ret_z,
            imp.combined_score,
            imp.rsi_at_impulse,
            imp.ema_deviation_pct,
            imp.candle_body_pct,
            imp.wick_ratio_top,
            imp.wick_ratio_bottom,
            1.0 if imp.direction == "long" else 0.0,
            1.0 if imp.impulse_location == "at_high" else 0.0,
            1.0 if imp.impulse_location == "at_low" else 0.0,
            1.0 if imp.impulse_location == "mid_range" else 0.0,
            np.sin(2 * np.pi * hour / 24),
            np.cos(2 * np.pi * hour / 24),
            imp.atr_at_impulse,
            # Coin profile features
            (coin_profile or {}).get("impulse_quality_score", 50),
            (coin_profile or {}).get("stop_hunt_frequency", 25),
            (coin_profile or {}).get("momentum_persistence", 50),
            (coin_profile or {}).get("level_respect_score", 50),
        ]

    def _dict_to_features(self, d: dict) -> Optional[list]:
        """Convert raw DB dict to feature vector."""
        from datetime import datetime, timezone
        try:
            ts = d.get("timestamp", 0)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            hour = dt.hour if dt else 12
        except Exception:
            hour = 12

        direction = d.get("direction", "long")
        location = d.get("impulse_location", "mid_range")

        return [
            d.get("vol_z", 0) or 0,
            d.get("ret_z", 0) or 0,
            d.get("combined_score", 0) or 0,
            d.get("rsi_at_impulse", 50) or 50,
            d.get("ema_deviation_pct", 0) or 0,
            d.get("candle_body_pct", 50) or 50,
            d.get("wick_ratio_top", 0) or 0,
            d.get("wick_ratio_bottom", 0) or 0,
            1.0 if direction == "long" else 0.0,
            1.0 if location == "at_high" else 0.0,
            1.0 if location == "at_low" else 0.0,
            1.0 if location == "mid_range" else 0.0,
            np.sin(2 * np.pi * hour / 24),
            np.cos(2 * np.pi * hour / 24),
            d.get("atr_at_impulse", 0) or 0,
            d.get("impulse_quality_score", 50) or 50,
            d.get("stop_hunt_frequency", 25) or 25,
            d.get("momentum_persistence", 50) or 50,
            d.get("level_respect_score", 50) or 50,
        ]

    def get_model_info(self) -> dict:
        """Return model status for reporting."""
        return {
            "trained": self.model_continue is not None,
            "train_samples": self.train_samples,
            "last_train": self.last_train_time,
            "top_features": dict(list(self.feature_importance.items())[:5]),
        }
