"""Explicit deterministic online baseline; existing batch models are not substituted."""

import math

import numpy as np

SUPPORT = {
    "online_softmax_v1": "incremental fitting, frozen inference and scalar online calibration",
    "trend": "unsupported in STRAT-2D v1; existing stateless Phase 6 rule unchanged",
    "logistic": "unsupported; existing batch L-BFGS definition is not online SGD",
    "tree": "unsupported; no validated incremental implementation",
    "xgboost": "unsupported; no validated bounded continuation/calibration implementation",
}


class OnlineModel:
    def __init__(self, spec, state=None):
        if spec.get("kind") != "online_softmax_v1":
            raise ValueError("Unsupported streaming model kind; no substitution is permitted")
        self.spec = spec
        n = len(spec["features"])
        if not 1 <= n <= 32 or len(set(spec["features"])) != n:
            raise ValueError("Bounded unique model features required")
        if len(spec["scales"]) != n or not all(math.isfinite(x) and x > 0 for x in spec["scales"]):
            raise ValueError("Positive fixed feature scales required")
        if not 0 < spec["learning_rate"] <= 0.1 or not 0 <= spec["l2"] <= 1:
            raise ValueError("Invalid online learning parameters")
        self.state = state or {
            "weights": np.zeros((n + 1, 3)).tolist(),
            "train_rows": 0,
            "calibration_rows": 0,
            "log_temperature": 0.0,
            "seed": spec["seed"],
            "last_train_decision": None,
            "maximum_training_label_end": None,
            "last_calibration_decision": None,
            "maximum_calibration_label_end": None,
            "class_counts": [0, 0, 0],
            "calibration_class_counts": [0, 0, 0],
        }
        self.weights = np.asarray(self.state["weights"], dtype=float)
        # Fixed arrays are per-model constants, not retained training history.
        self.scales = np.asarray(spec["scales"], dtype=float)
        self.targets = np.eye(3)
        if self.state["seed"] != spec["seed"]:
            raise ValueError("Model seed mismatch")
        if self.weights.shape != (n + 1, 3) or not np.isfinite(self.weights).all():
            raise ValueError("Corrupt model coefficients")

    def vector(self, values):
        x = np.asarray(values, dtype=float)
        if x.shape != (len(self.spec["features"]),) or not np.isfinite(x).all():
            raise ValueError("Incomplete or misaligned predictor row")
        return np.r_[1.0, np.clip(x / self.scales, -20, 20)]

    @staticmethod
    def softmax(logits):
        values = np.exp(logits - np.max(logits))
        return values / values.sum()

    def predict(self, values):
        return self.softmax(
            (self.vector(values) @ self.weights) / math.exp(self.state["log_temperature"])
        )

    def fit_one(self, values, label, decision, label_end, train_end):
        if label not in (0, 1, 2) or label_end >= train_end or decision >= train_end:
            raise ValueError("Training label is unavailable before the training boundary")
        if (
            self.state["last_train_decision"] is not None
            and decision <= self.state["last_train_decision"]
        ):
            raise ValueError("Training updates must be strictly chronological")
        x = self.vector(values)
        p = self.softmax(x @ self.weights)
        target = self.targets[label]
        rate = self.spec["learning_rate"] / math.sqrt(1 + self.state["train_rows"] * 0.0001)
        gradient = x[:, None] * (p - target)
        gradient[1:] += self.spec["l2"] * self.weights[1:]
        self.weights -= rate * gradient
        self.state["train_rows"] += 1
        self.state["class_counts"][label] += 1
        self.state["last_train_decision"] = decision
        self.state["maximum_training_label_end"] = max(
            label_end, self.state["maximum_training_label_end"] or label_end
        )

    def calibrate_one(self, values, label, decision=None, label_end=None, calibration_end=None):
        if label not in (0, 1, 2):
            raise ValueError("Invalid calibration target")
        if decision is not None:
            if label_end is None or calibration_end is None or label_end >= calibration_end:
                raise ValueError("Calibration label crosses its boundary")
            if (
                self.state["last_calibration_decision"]
                and decision <= self.state["last_calibration_decision"]
            ):
                raise ValueError("Calibration updates must be chronological")
            self.state["last_calibration_decision"] = decision
            self.state["maximum_calibration_label_end"] = max(
                label_end, self.state["maximum_calibration_label_end"] or label_end
            )
        logits = self.vector(values) @ self.weights / math.exp(self.state["log_temperature"])
        p = self.softmax(logits)
        gradient = float(logits[label] - p @ logits)
        self.state["log_temperature"] = float(
            np.clip(self.state["log_temperature"] - 0.005 * gradient, math.log(0.5), math.log(5))
        )
        self.state["calibration_rows"] += 1
        self.state["calibration_class_counts"][label] += 1

    def export(self):
        self.state["weights"] = self.weights.tolist()
        if not np.isfinite(self.weights).all():
            raise ValueError("Nonfinite model update")
        return self.state
