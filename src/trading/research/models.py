"""Small CPU XGBoost models, disjoint chronological calibration and signal ranking."""

import numpy as np
import pandas as pd
import xgboost as xgb
from scipy.optimize import minimize
from scipy.special import softmax

from trading.research.data import MINUTE, regimes
from trading.research.execution import permitted_entries


def purged_masks(index, label_end, fold):
    bounds = [
        pd.Timestamp(fold[k], tz="UTC")
        for k in ("train_end", "calibration_end", "tuning_end", "test_end")
    ]
    if not all(a < b for a, b in zip(bounds[:-1], bounds[1:], strict=True)):
        raise ValueError("Chronological boundaries must increase")
    a, b, c, d = bounds
    # Purge by actual label end time; this also handles irregular minute observations.
    return {
        "train": (index < a) & (label_end < a),
        "calibration": (index >= a) & (index < b) & (label_end < b),
        "tuning": (index >= b) & (index < c) & (label_end < c),
        "test": (index >= c) & (index < d),
    }


def bounded_indices(mask, maximum):
    rows = np.flatnonzero(mask)
    if len(rows) > maximum:
        rows = rows[np.linspace(0, len(rows) - 1, maximum, dtype=int)]
    return rows


def calibrate(probabilities, labels):
    logits = np.log(np.clip(probabilities, 1e-8, 1))

    def objective(theta):
        probability = softmax(logits / np.exp(theta[0]) + np.r_[0, theta[1:]], axis=1)
        return (
            -np.log(np.maximum(probability[np.arange(len(labels)), labels], 1e-12)).mean()
            + 0.0001 * np.square(theta).sum()
        )

    fit = minimize(
        objective,
        np.zeros(3),
        method="L-BFGS-B",
        bounds=[(np.log(0.5), np.log(5)), (-5, 5), (-5, 5)],
        options={"maxiter": 100},
    )
    if not fit.success:
        raise RuntimeError("Probability calibration did not converge")
    return fit.x.tolist()


def apply_calibration(probability, theta):
    return softmax(
        np.log(np.clip(probability, 1e-8, 1)) / np.exp(theta[0]) + np.r_[0, theta[1:]], axis=1
    )


def probability_metrics(probability, labels):
    if not len(labels):
        return {"rows": 0}
    onehot = np.eye(3)[labels]
    return {
        "rows": len(labels),
        "log_loss": float(
            -np.log(np.maximum(probability[np.arange(len(labels)), labels], 1e-12)).mean()
        ),
        "multiclass_brier": float(np.square(probability - onehot).sum(axis=1).mean()),
        "accuracy_descriptive_only": float((probability.argmax(axis=1) == labels).mean()),
        "class_counts": np.bincount(labels, minlength=3).tolist(),
    }


def fit_model(f, labels, masks, model_config, config):
    valid = f.notna().all(axis=1).to_numpy() & (labels >= 0)
    train = bounded_indices(valid & masks["train"], config["max_training_rows"])
    calibration = bounded_indices(valid & masks["calibration"], 30000)
    if min(len(train), len(calibration)) < 1000:
        raise ValueError("Insufficient disjoint training/calibration samples")
    counts = np.bincount(labels[train], minlength=3)
    weights = np.clip(len(train) / (3 * np.maximum(counts, 1)), 0.25, 3)[labels[train]]
    matrix = xgb.DMatrix(
        f.iloc[train], label=labels[train], weight=weights, nthread=config["threads"]
    )
    params = {
        "objective": "multi:softprob",
        "num_class": 3,
        "tree_method": "hist",
        "nthread": config["threads"],
        "seed": config["seed"],
        "max_bin": 128,
        "max_depth": model_config["max_depth"],
        "eta": model_config["learning_rate"],
        "min_child_weight": 30,
        "lambda": 10,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "verbosity": 0,
    }
    model = xgb.train(params, matrix, num_boost_round=model_config["n_estimators"])
    raw = model.predict(xgb.DMatrix(f.iloc[calibration], nthread=config["threads"]))
    theta = calibrate(raw, labels[calibration])
    return (
        model,
        theta,
        {
            "train_rows": len(train),
            "calibration_rows": len(calibration),
            "train_last": str(f.index[train[-1]]),
            "calibration_first": str(f.index[calibration[0]]),
            "calibration_last": str(f.index[calibration[-1]]),
            "class_counts": counts.tolist(),
            "calibration_raw": probability_metrics(raw, labels[calibration]),
            "calibration_fitted": probability_metrics(
                apply_calibration(raw, theta), labels[calibration]
            ),
            "feature_importance_gain": model.get_score(importance_type="gain"),
        },
    )


def predict(model, theta, f, mask, threads=1):
    rows = np.flatnonzero(mask & f.notna().all(axis=1).to_numpy())
    output = np.full((len(f), 3), np.nan, dtype="float32")
    if len(rows):
        raw = model.predict(xgb.DMatrix(f.iloc[rows], nthread=threads))
        output[rows] = apply_calibration(raw, theta)
    return output


def payoff_profile(path, labels, train_mask, costs):
    """Past-only conditional economic payoffs; do not assume every winner reaches TP."""
    valid = train_mask & (labels >= 0)
    result = {}
    for name, label in (("long", 1), ("short", 2)):
        net_r = path[name]["gross_per_unit"] / np.maximum(path["distance"], 1e-12)
        good, bad = net_r[valid & (labels == label)], net_r[valid & (labels != label)]
        result[name] = {
            "win_r": float(np.mean(good)) if len(good) else 0,
            "loss_r": float(np.mean(bad)) if len(bad) else -1,
        }
    return result


def opportunities(
    asset,
    f,
    bars,
    probability,
    horizon,
    profile,
    costs,
    metadata,
    mask,
    threshold=0.55,
    regime_filter="all",
    baseline=False,
    risk_fraction=0.0025,
    max_leverage=5,
    stop_atr=2,
    economic_filter=True,
):
    valid = mask & f.notna().all(axis=1).to_numpy()
    reg = regimes(f)
    if baseline:
        probability = np.tile([0.45, 0.275, 0.275], (len(f), 1))
        long = (f.ema_trend_spread > 0) & (f.rsi_14 > 55)
        short = (f.ema_trend_spread < 0) & (f.rsi_14 < 45)
        probability[long, 1], probability[short, 2] = 0.55, 0.55
        probability[long | short, 0] = 0.175
    side = np.where(probability[:, 1] >= probability[:, 2], 1, 2)
    p = probability[np.arange(len(f)), side]
    direction = np.where(side == 1, 1, -1)
    expected = np.zeros(len(f))
    for label, name in ((1, "long"), (2, "short")):
        chosen = side == label
        expected[chosen] = (
            p[chosen] * profile[name]["win_r"] + (1 - p[chosen]) * profile[name]["loss_r"]
        )
    stop_fraction = f.atr_fraction_14.to_numpy() * stop_atr
    expected -= costs.fraction / np.maximum(stop_fraction, 1e-12)
    entropy = -(np.clip(probability, 1e-10, 1) * np.log(np.clip(probability, 1e-10, 1))).sum(axis=1)
    confidence = 1 - entropy / np.log(3)
    valid &= p >= threshold
    if economic_filter and not baseline:
        valid &= expected > 0
    if regime_filter == "exclude_abnormal":
        valid &= reg != "abnormal"
    positions = np.flatnonzero(valid)
    positions = positions[permitted_entries(f.index[positions], horizon, metadata)]
    return pd.DataFrame(
        {
            "time": f.index[positions] + pd.Timedelta(minutes=1),
            "asset": asset,
            "position": positions,
            "direction": direction[positions],
            "probability": p[positions],
            "confidence": confidence[positions],
            "score": expected[positions]
            * np.minimum(risk_fraction, max_leverage * (stop_fraction[positions] + costs.fraction))
            * (0.5 + 0.5 * confidence[positions]),
            "regime": reg[positions],
            "horizon": horizon,
            "estimated_net_return": expected[positions] * stop_fraction[positions],
            "estimated_risk_fraction": np.minimum(
                risk_fraction, max_leverage * (stop_fraction[positions] + costs.fraction)
            ),
            "roundtrip_cost_fraction": costs.fraction,
        }
    )


def label_end_times(index, horizon):
    return pd.DatetimeIndex(index.asi8 + horizon * MINUTE, tz="UTC")
