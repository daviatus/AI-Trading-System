"""Bounded, per-asset research models and disjoint probability calibration.

Class order is always 0=no trade, 1=long, 2=short.  The fixed trend baseline
retains Phase 5's scores and is explicitly not an empirical probability model.
Training, early stopping, calibration and subsequent evaluation are separate.
"""

import hashlib
import importlib.metadata
import json
import os
from dataclasses import dataclass
from pathlib import Path

# The command-line bootstrap also sets these before loading scientific packages.
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_key] = "1"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.optimize import minimize  # noqa: E402
from scipy.special import logsumexp, softmax  # noqa: E402

SCHEMA_VERSION = "phase6a-model-v1"
MODEL_KINDS = ("trend", "logistic", "xgboost", "tree")
CLASS_NAMES = ["no_trade", "long", "short"]


def _json(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _probabilities(probability):
    p = np.asarray(probability, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all():
        raise ValueError("Probabilities must be a finite n-by-3 matrix")
    if (p < 0).any() or not np.allclose(p.sum(axis=1), 1, atol=1e-5):
        raise ValueError("Probabilities must be nonnegative and sum to one")
    return p


def probability_metrics(probability, labels, bins=10):
    """Proper scores and one-versus-rest reliability, including empty bins."""
    p = _probabilities(probability)
    y = np.asarray(labels)
    if y.shape != (len(p),) or not np.isin(y, [0, 1, 2]).all():
        raise ValueError("Metrics require aligned labels in {0,1,2}")
    if not 2 <= bins <= 100:
        raise ValueError("Reliability bins must be between 2 and 100")
    if not len(y):
        return {"rows": 0, "class_counts": [0, 0, 0], "reliability": []}
    y = y.astype(int)
    reliability = []
    for label, name in enumerate(CLASS_NAMES):
        buckets = np.minimum((p[:, label] * bins).astype(int), bins - 1)
        for bucket in range(bins):
            rows = buckets == bucket
            reliability.append(
                {
                    "class": name,
                    "lower": bucket / bins,
                    "upper": (bucket + 1) / bins,
                    "count": int(rows.sum()),
                    "mean_probability": float(p[rows, label].mean()) if rows.any() else None,
                    "observed_frequency": float((y[rows] == label).mean()) if rows.any() else None,
                }
            )
    return {
        "rows": len(y),
        "class_counts": np.bincount(y, minlength=3).tolist(),
        "log_loss": float(-np.log(np.maximum(p[np.arange(len(y)), y], 1e-12)).mean()),
        "multiclass_brier": float(np.square(p - np.eye(3)[y]).sum(axis=1).mean()),
        "accuracy_descriptive_only": float((p.argmax(axis=1) == y).mean()),
        "reliability": reliability,
    }


def apply_calibration(probability, theta):
    p = _probabilities(probability)
    values = np.asarray(theta, dtype=float)
    if values.shape != (3,) or not np.isfinite(values).all():
        raise ValueError("Calibration requires log-temperature and two class biases")
    return softmax(np.log(np.clip(p, 1e-8, 1)) / np.exp(values[0]) + np.r_[0, values[1:]], axis=1)


def calibrate(probability, labels):
    """Regularized temperature and class biases fitted on calibration data only."""
    p = _probabilities(probability)
    y = np.asarray(labels)
    if y.shape != (len(p),) or not len(y) or not np.isin(y, [0, 1, 2]).all():
        raise ValueError("Calibration labels must be nonempty, aligned and in {0,1,2}")
    y = y.astype(int)
    logits = np.log(np.clip(p, 1e-8, 1))

    def objective(theta):
        scaled = logits / np.exp(theta[0]) + np.r_[0, theta[1:]]
        loss = (logsumexp(scaled, axis=1) - scaled[np.arange(len(y)), y]).mean()
        return loss + 0.0001 * np.square(theta).sum()

    fit = minimize(
        objective,
        np.zeros(3),
        method="L-BFGS-B",
        bounds=[(np.log(0.5), np.log(5)), (-5, 5), (-5, 5)],
        options={"maxiter": 150, "ftol": 1e-10},
    )
    if not fit.success or not np.isfinite(fit.x).all():
        raise ValueError("Probability calibration did not converge")
    return fit.x.tolist()


def bounded_indices(mask, maximum):
    if not isinstance(maximum, int) or maximum < 1:
        raise ValueError("Row bound must be a positive integer")
    rows = np.flatnonzero(mask)
    if len(rows) > maximum:
        rows = rows[np.linspace(0, len(rows) - 1, maximum, dtype=int)]
    return rows


def _trend(features):
    probability = np.tile([0.45, 0.275, 0.275], (len(features), 1))
    long = (features.ema_trend_spread > 0) & (features.rsi_14 > 55)
    short = (features.ema_trend_spread < 0) & (features.rsi_14 < 45)
    probability[long, 1] = 0.55
    probability[short, 2] = 0.55
    probability[long | short, 0] = 0.175
    return probability


def _logistic(features, labels, regularization, max_iterations):
    mean = features.mean(axis=0)
    scale = features.std(axis=0)
    scale[scale < 1e-8] = 1
    x = np.column_stack([np.ones(len(features)), np.clip((features - mean) / scale, -20, 20)])
    onehot = np.eye(3)[labels]

    def objective(flat):
        coefficients = flat.reshape(x.shape[1], 3)
        logits = x @ coefficients
        probability = softmax(logits, axis=1)
        loss = (logsumexp(logits, axis=1) - logits[np.arange(len(labels)), labels]).mean()
        penalty = 0.5 * regularization * np.square(coefficients[1:]).sum()
        gradient = x.T @ (probability - onehot) / len(labels)
        gradient[1:] += regularization * coefficients[1:]
        return loss + penalty, gradient.ravel()

    fit = minimize(
        objective,
        np.zeros(x.shape[1] * 3),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": max_iterations, "ftol": 1e-10, "gtol": 1e-6},
    )
    if not fit.success or not np.isfinite(fit.x).all():
        raise ValueError("Regularized logistic fit did not converge")
    return {
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "coefficients": fit.x.reshape(x.shape[1], 3).tolist(),
        "iterations": int(fit.nit),
        "objective": float(fit.fun),
        "standardized_feature_clip": 20,
    }


@dataclass
class ModelBundle:
    """A single asset's predictor, calibrator and deterministic audit metadata."""

    kind: str
    feature_columns: list[str]
    predictor: object
    calibrator: list[float] | None
    metadata: dict

    def _raw(self, frame):
        if self.kind == "trend":
            return _trend(frame)
        if self.kind == "logistic":
            state = self.predictor
            x = np.clip((frame.to_numpy(dtype=float) - state["mean"]) / state["scale"], -20, 20)
            return softmax(np.column_stack([np.ones(len(x)), x]) @ state["coefficients"], axis=1)
        import xgboost as xgb

        matrix = xgb.DMatrix(frame, nthread=1)
        iteration = self.metadata.get("best_iteration")
        kwargs = {"iteration_range": (0, iteration + 1)} if iteration is not None else {}
        return self.predictor.predict(matrix, **kwargs)

    def predict(self, features, mask=None, *, calibrated=True):
        missing = set(self.feature_columns) - set(features.columns)
        if missing:
            raise ValueError(f"Missing model features: {sorted(missing)}")
        frame = features.loc[:, self.feature_columns]
        valid = np.isfinite(frame.to_numpy(copy=False)).all(axis=1)
        if mask is not None:
            chosen = np.asarray(mask)
            if chosen.shape != (len(frame),) or chosen.dtype != bool:
                raise ValueError("Prediction mask must be an aligned boolean vector")
            valid &= chosen
        output = np.full((len(frame), 3), np.nan, dtype="float32")
        rows = np.flatnonzero(valid)
        batch = self.metadata["prediction_batch_rows"]
        for start in range(0, len(rows), batch):
            subset = rows[start : start + batch]
            p = self._raw(frame.iloc[subset])
            if calibrated and self.calibrator is not None:
                p = apply_calibration(p, self.calibrator)
            output[subset] = p
        return output

    def save(self, directory):
        """Write a new immutable artifact directory; JSON and UBJ are deterministic."""
        directory = Path(directory)
        if directory.exists() and any(directory.iterdir()):
            raise FileExistsError("Model artifact directory must be new or empty")
        directory.mkdir(parents=True, exist_ok=True)
        payload = None
        if self.kind in ("xgboost", "tree"):
            self.predictor.save_model(directory / "model.ubj")
            digest = hashlib.sha256((directory / "model.ubj").read_bytes()).hexdigest()
        else:
            payload = self.predictor
            digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "kind": self.kind,
            "features": self.feature_columns,
            "calibrator": self.calibrator,
            "predictor": payload,
            "predictor_sha256": digest,
            "metadata": self.metadata,
        }
        (directory / "model.json").write_text(_json(manifest), encoding="utf-8")
        return manifest


def load_model(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "model.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest["kind"] not in MODEL_KINDS:
        raise ValueError("Unsupported Phase 6 model artifact")
    if manifest["kind"] in ("xgboost", "tree"):
        import xgboost as xgb

        content = (directory / "model.ubj").read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        predictor = xgb.Booster(params={"nthread": 1})
        predictor.load_model(bytearray(content))
    else:
        predictor = manifest["predictor"]
        digest = hashlib.sha256(_json(predictor).encode()).hexdigest()
    if digest != manifest["predictor_sha256"]:
        raise ValueError("Model artifact checksum mismatch")
    return ModelBundle(
        manifest["kind"],
        manifest["features"],
        predictor,
        manifest["calibrator"],
        manifest["metadata"],
    )


def _training_rows(features, labels, masks, columns, config, label_end):
    if not isinstance(features.index, pd.DatetimeIndex) or features.index.tz is None:
        raise ValueError("Models require a timezone-aware chronological DatetimeIndex")
    if not features.index.is_monotonic_increasing or not features.index.is_unique:
        raise ValueError("Model feature timestamps must be unique and increasing")
    labels = np.asarray(labels)
    if labels.shape != (len(features),) or not np.isin(labels, [-1, 0, 1, 2]).all():
        raise ValueError("Labels must align to features and use -1,0,1,2")
    selected = {}
    occupied = np.zeros(len(features), dtype=bool)
    for name in ("train", "calibration", "tuning", "test"):
        if name not in masks:
            if name in ("train", "calibration"):
                raise ValueError(f"Missing {name} mask")
            continue
        mask = np.asarray(masks[name])
        if mask.shape != (len(features),) or mask.dtype != bool:
            raise ValueError("Split masks must be aligned boolean vectors")
        if (occupied & mask).any():
            raise ValueError("Chronological split masks overlap")
        occupied |= mask
        selected[name] = mask
    previous = None
    for name in ("train", "calibration", "tuning", "test"):
        rows = np.flatnonzero(selected.get(name, np.zeros(len(features), dtype=bool)))
        if len(rows):
            if previous is not None and previous >= rows[0]:
                raise ValueError("Split masks must follow train/calibration/tuning/test chronology")
            previous = rows[-1]
    valid = np.isfinite(features.loc[:, columns].to_numpy(copy=False)).all(axis=1) & (labels >= 0)
    maximum = int(config.get("max_training_rows", 40000))
    calibration_maximum = int(config.get("max_calibration_rows", 10000))
    if not 1 <= maximum <= 80000 or not 1 <= calibration_maximum <= 30000:
        raise ValueError("Training/calibration limits exceed conservative Phase 5 resource bounds")
    train = bounded_indices(selected["train"] & valid, maximum)
    calibration = bounded_indices(selected["calibration"] & valid, calibration_maximum)
    if len(train) < config.get("min_training_rows", 100):
        raise ValueError("Insufficient training samples")
    if len(calibration) < config.get("min_calibration_rows", 50):
        raise ValueError("Insufficient disjoint calibration samples")
    if features.index[train[-1]] >= features.index[calibration[0]]:
        raise ValueError("Training must chronologically precede calibration")
    ends = None
    if label_end is not None:
        ends = pd.DatetimeIndex(label_end)
        if len(ends) != len(features) or ends.tz is None:
            raise ValueError("Label end timestamps must be aligned and timezone-aware")
        for name, rows in (("train", train), ("calibration", calibration)):
            if ends[rows].isna().any() or (ends[rows] < features.index[rows]).any():
                raise ValueError(f"Invalid {name} label end timestamps")
        if (ends[train] >= features.index[calibration[0]]).any():
            raise ValueError("Training label outcomes overlap calibration")
        for name in ("tuning", "test"):
            later = np.flatnonzero(selected.get(name, np.zeros(len(features), dtype=bool)))
            if len(later) and (ends[calibration] >= features.index[later[0]]).any():
                raise ValueError("Calibration label outcomes overlap a later evaluation period")
    return labels.astype(int), train, calibration, ends


def fit_model(features, labels, masks, spec, config, *, asset, label_end=None):
    """Fit one asset; neither tuning nor test labels enter any fitting operation.

    ``label_end`` is mandatory for early stopping so overlapping outcomes cannot
    cross its training-only chronological split. Caller-created outer masks must
    also purge labels and apply the configured embargo.
    """
    if config.get("threads", 1) != 1:
        raise ValueError("Phase 6 research models require exactly one worker")
    if not asset or asset not in ("EURUSD", "XAUUSD", "WTI"):
        raise ValueError("A supported per-asset model identifier is required")
    kind = spec.get("kind", "xgboost")
    if kind not in MODEL_KINDS or not spec.get("version"):
        raise ValueError("Model kind and a versioned specification are required")
    columns = list(spec.get("features", features.columns))
    if kind == "trend":
        columns = ["ema_trend_spread", "rsi_14"]
    if not columns or len(set(columns)) != len(columns) or set(columns) - set(features.columns):
        raise ValueError("Feature selection must contain unique existing feature columns")
    y, train, calibration, ends = _training_rows(
        features, labels, masks, columns, config, label_end
    )
    seed = int(config.get("seed", 20261023))
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "model_specification": spec,
        "class_order": CLASS_NAMES,
        "features": columns,
        "seed": seed,
        "threads": 1,
        "train_rows": len(train),
        "calibration_rows": len(calibration),
        "train_first": features.index[train[0]].isoformat(),
        "train_last": features.index[train[-1]].isoformat(),
        "calibration_first": features.index[calibration[0]].isoformat(),
        "calibration_last": features.index[calibration[-1]].isoformat(),
        "class_counts": np.bincount(y[train], minlength=3).tolist(),
        "label_end_validation": "checked" if ends is not None else "caller_purged_masks_only",
        "prediction_batch_rows": int(config.get("prediction_batch_rows", 8192)),
        "packages": {
            name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scipy")
        },
        "best_iteration": None,
        "early_stopping": {"enabled": False},
    }
    if not 1 <= metadata["prediction_batch_rows"] <= 50000:
        raise ValueError("Prediction batches must contain 1 to 50000 rows")
    if kind == "trend":
        predictor = {"rule_version": "phase5-ema-rsi-fixed-v1"}
    elif kind == "logistic":
        penalty = float(spec.get("regularization", spec.get("l2", 0.1)))
        iterations = int(spec.get("max_iterations", 200))
        if not 0 < penalty <= 100 or not 1 <= iterations <= 500:
            raise ValueError("Logistic regularization or iteration bounds are invalid")
        predictor = _logistic(
            features.iloc[train].loc[:, columns].to_numpy(dtype=float),
            y[train],
            penalty,
            iterations,
        )
        metadata["logistic_iterations"] = predictor["iterations"]
        metadata["logistic_l2"] = penalty
    else:
        import xgboost as xgb

        metadata["packages"]["xgboost"] = importlib.metadata.version("xgboost")
        rows = train
        validation = np.array([], dtype=int)
        fraction = float(spec.get("early_stopping_fraction", 0.15)) if kind == "xgboost" else 0
        if not 0 <= fraction <= 0.3:
            raise ValueError("Early stopping fraction must be between 0 and 0.3")
        if fraction:
            if ends is None:
                raise ValueError("label_end is required to purge the early-stopping split")
            count = max(int(np.ceil(len(train) * fraction)), 20)
            validation = train[-count:]
            rows = train[:-count]
            embargo = float(
                config.get("early_stopping_embargo_minutes", config.get("embargo_minutes", 0))
            )
            if not np.isfinite(embargo) or embargo < 0:
                raise ValueError("Early stopping embargo must be nonnegative")
            cutoff = features.index[validation[0]] - pd.Timedelta(minutes=embargo)
            rows = rows[ends[rows] < cutoff]
            if len(rows) < config.get("min_training_rows", 100):
                raise ValueError("Insufficient purged fitting rows before early stopping")
            metadata["early_stopping"] = {
                "enabled": True,
                "fit_rows": len(rows),
                "validation_rows": len(validation),
                "purged_rows": len(train) - len(rows) - len(validation),
                "fit_last": features.index[rows[-1]].isoformat(),
                "fit_label_end_last": ends[rows].max().isoformat(),
                "validation_first": features.index[validation[0]].isoformat(),
                "validation_last": features.index[validation[-1]].isoformat(),
                "embargo_minutes": embargo,
                "source": "chronological_tail_of_training_only",
            }
        counts = np.bincount(y[rows], minlength=3)
        metadata["fit_class_counts"] = counts.tolist()
        weights = np.clip(len(rows) / (3 * np.maximum(counts, 1)), 0.25, 3)[y[rows]]
        matrix = xgb.DMatrix(
            features.iloc[rows].loc[:, columns], label=y[rows], weight=weights, nthread=1
        )
        depth = int(spec.get("max_depth", 2))
        rounds = 1 if kind == "tree" else int(spec.get("n_estimators", 80))
        rate = 1.0 if kind == "tree" else float(spec.get("learning_rate", 0.06))
        patience = int(spec.get("early_stopping_rounds", 10))
        if not 1 <= depth <= (3 if kind == "tree" else 6) or not 1 <= rounds <= 300:
            raise ValueError("Tree depth/rounds exceed conservative experiment bounds")
        if not 0 < rate <= 1 or not 1 <= patience <= 50:
            raise ValueError("Learning rate or early stopping patience is invalid")
        params = {
            "objective": "multi:softprob",
            "num_class": 3,
            "eval_metric": "mlogloss",
            "tree_method": "hist",
            "nthread": 1,
            "seed": seed,
            "max_bin": 128,
            "max_depth": depth,
            "eta": rate,
            "min_child_weight": 50 if kind == "tree" else 30,
            "lambda": 20 if kind == "tree" else 10,
            "subsample": 1 if kind == "tree" else 0.9,
            "colsample_bytree": 1 if kind == "tree" else 0.9,
            "verbosity": 0,
        }
        arguments = {}
        if len(validation):
            stopping = xgb.DMatrix(
                features.iloc[validation].loc[:, columns], label=y[validation], nthread=1
            )
            arguments = {"evals": [(stopping, "training_tail")], "early_stopping_rounds": patience}
        predictor = xgb.train(
            params, matrix, num_boost_round=rounds, verbose_eval=False, **arguments
        )
        metadata["xgboost_parameters"] = params
        metadata["best_iteration"] = int(predictor.best_iteration) if len(validation) else None
        metadata["trained_rounds"] = int(predictor.num_boosted_rounds())
        metadata["feature_importance_gain"] = predictor.get_score(importance_type="gain")
    bundle = ModelBundle(kind, columns, predictor, None, metadata)
    raw = bundle._raw(features.iloc[calibration].loc[:, columns])
    method = spec.get("calibration", "none" if kind == "trend" else "temperature_bias")
    if method not in ("none", "temperature_bias"):
        raise ValueError("Unsupported calibration method")
    if method == "temperature_bias":
        if len(np.unique(y[calibration])) < 2:
            raise ValueError("Calibration requires at least two observed classes")
        bundle.calibrator = calibrate(raw, y[calibration])
    metadata["calibration"] = {
        "method": method,
        "parameters": bundle.calibrator,
        "fitted_on": "calibration_only",
        "evaluation_note": "Fitted calibration scores are descriptive, not independent validation",
        "raw": probability_metrics(raw, y[calibration]),
        "fitted": probability_metrics(
            apply_calibration(raw, bundle.calibrator) if bundle.calibrator is not None else raw,
            y[calibration],
        ),
    }
    if kind == "trend" and method == "none":
        metadata["score_interpretation"] = "fixed baseline scores; not empirical probabilities"
    return bundle
