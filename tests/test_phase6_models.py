"""Small deterministic synthetic checks; no historical dataset or database reads."""

import json

import numpy as np
import pandas as pd
import pytest

from trading.research.phase6.models import (
    apply_calibration,
    bounded_indices,
    calibrate,
    fit_model,
    load_model,
    probability_metrics,
)


def fixture():
    rng = np.random.default_rng(15)
    index = pd.date_range("2025-01-06T10:00Z", periods=1800, freq="min")
    features = pd.DataFrame(rng.normal(size=(1800, 4)), index=index, columns=list("abcd"))
    features["ema_trend_spread"] = features.a * 0.001
    features["rsi_14"] = 50 + features.a * 10
    labels = np.where(features.a > 0.5, 1, np.where(features.a < -0.5, 2, 0)).astype("int8")
    positions = np.arange(len(features))
    masks = {
        "train": positions < 880,
        "calibration": (positions >= 900) & (positions < 1180),
        "tuning": (positions >= 1200) & (positions < 1480),
        "test": positions >= 1500,
    }
    config = {
        "seed": 20261023,
        "threads": 1,
        "max_training_rows": 800,
        "max_calibration_rows": 250,
        "prediction_batch_rows": 57,
        "early_stopping_embargo_minutes": 3,
    }
    return features, labels, masks, config, index + pd.Timedelta(minutes=10)


def fit(kind="logistic", **overrides):
    features, labels, masks, config, ends = fixture()
    spec = {"kind": kind, "version": "test-v1", "features": ["a", "b"], **overrides}
    bundle = fit_model(features, labels, masks, spec, config, asset="EURUSD", label_end=ends)
    return bundle, features, labels, masks, config, ends


@pytest.mark.parametrize("kind", ["trend", "logistic", "xgboost", "tree"])
def test_modular_models_predict_save_and_load_deterministically(kind, tmp_path):
    bundle, features, _, masks, _, _ = fit(kind, n_estimators=8, early_stopping_rounds=3)
    prediction = bundle.predict(features, masks["test"])
    assert np.isnan(prediction[~masks["test"]]).all()
    assert np.isfinite(prediction[masks["test"]]).all()
    np.testing.assert_allclose(prediction[masks["test"]].sum(axis=1), 1, atol=1e-6)
    bundle.save(tmp_path / "one")
    bundle.save(tmp_path / "two")
    assert (tmp_path / "one/model.json").read_bytes() == (tmp_path / "two/model.json").read_bytes()
    if kind in ("tree", "xgboost"):
        assert (tmp_path / "one/model.ubj").read_bytes() == (
            tmp_path / "two/model.ubj"
        ).read_bytes()
    restored = load_model(tmp_path / "one")
    np.testing.assert_array_equal(prediction, restored.predict(features, masks["test"]))
    with pytest.raises(FileExistsError, match="new or empty"):
        bundle.save(tmp_path / "one")


def test_logistic_fit_does_not_use_calibration_for_scaling_or_later_labels():
    bundle, features, labels, masks, config, ends = fit()
    altered = labels.copy()
    altered[masks["tuning"] | masks["test"]] = (altered[masks["tuning"] | masks["test"]] + 1) % 3
    other = fit_model(
        features,
        altered,
        masks,
        bundle.metadata["model_specification"],
        config,
        asset="EURUSD",
        label_end=ends,
    )
    assert bundle.metadata == other.metadata
    assert bundle.predictor == other.predictor
    np.testing.assert_array_equal(bundle.predict(features), other.predict(features))
    train = bounded_indices(masks["train"], config["max_training_rows"])
    np.testing.assert_allclose(bundle.predictor["mean"], features.iloc[train][["a", "b"]].mean())
    labels[masks["calibration"]] = 0
    labels[np.flatnonzero(masks["calibration"])[::2]] = 1
    refit = fit_model(
        features,
        labels,
        masks,
        bundle.metadata["model_specification"],
        config,
        asset="EURUSD",
        label_end=ends,
    )
    assert refit.predictor == bundle.predictor
    assert refit.calibrator != bundle.calibrator


def test_xgboost_early_stopping_purges_only_training_tail():
    bundle, _, _, _, _, _ = fit("xgboost", n_estimators=10)
    metadata = bundle.metadata
    stopping = metadata["early_stopping"]
    assert stopping["enabled"]
    assert stopping["purged_rows"] > 0
    assert pd.Timestamp(stopping["fit_label_end_last"]) < (
        pd.Timestamp(stopping["validation_first"]) - pd.Timedelta(minutes=3)
    )
    assert pd.Timestamp(stopping["validation_last"]) < pd.Timestamp(metadata["calibration_first"])
    assert metadata["calibration_rows"] == 250
    assert metadata["best_iteration"] < metadata["trained_rounds"] <= 10


def test_xgboost_requires_label_end_for_safe_stopping():
    features, labels, masks, config, _ = fixture()
    with pytest.raises(ValueError, match="label_end is required"):
        fit_model(
            features, labels, masks, {"kind": "xgboost", "version": "v1"}, config, asset="WTI"
        )


def test_xgboost_calibration_labels_cannot_change_predictor():
    bundle, features, labels, masks, config, ends = fit("xgboost", n_estimators=8)
    labels[masks["calibration"]] = (labels[masks["calibration"]] + 1) % 3
    changed = fit_model(
        features,
        labels,
        masks,
        bundle.metadata["model_specification"],
        config,
        asset="EURUSD",
        label_end=ends,
    )
    assert bundle.predictor.save_raw(raw_format="ubj") == changed.predictor.save_raw(
        raw_format="ubj"
    )
    np.testing.assert_array_equal(
        bundle.predict(features, calibrated=False), changed.predict(features, calibrated=False)
    )
    assert bundle.calibrator != changed.calibrator


def test_logistic_l2_configuration_is_recorded():
    bundle, _, _, _, _, _ = fit("logistic", l2=1.0)
    assert bundle.metadata["logistic_l2"] == 1


def test_fixed_trend_scores_preserve_existing_rule():
    bundle, features, _, _, _, _ = fit("trend")
    probability = bundle.predict(features)
    long = (features.ema_trend_spread > 0) & (features.rsi_14 > 55)
    short = (features.ema_trend_spread < 0) & (features.rsi_14 < 45)
    np.testing.assert_allclose(probability[long, 1], 0.55)
    np.testing.assert_allclose(probability[short, 2], 0.55)
    np.testing.assert_allclose(probability[~(long | short), 0], 0.45)
    assert bundle.calibrator is None
    assert "not empirical" in bundle.metadata["score_interpretation"]


def test_calibration_is_deterministic_and_reports_complete_reliability_bins():
    raw = np.tile([0.95, 0.025, 0.025], (300, 1))
    labels = np.tile([0, 1, 2], 100)
    theta = calibrate(raw, labels)
    assert theta == calibrate(raw, labels)
    before = probability_metrics(raw, labels)
    after = probability_metrics(apply_calibration(raw, theta), labels)
    assert after["log_loss"] < before["log_loss"]
    assert len(after["reliability"]) == 30
    assert sum(row["count"] for row in after["reliability"]) == 900
    assert any(row["mean_probability"] is None for row in after["reliability"])
    assert probability_metrics(np.empty((0, 3)), np.array([], dtype=int))["rows"] == 0


@pytest.mark.parametrize("defect", ["overlap", "chronology", "label_overlap", "threads", "feature"])
def test_invalid_training_inputs_fail_closed(defect):
    features, labels, masks, config, ends = fixture()
    spec = {"kind": "logistic", "version": "v1", "features": ["a", "b"]}
    if defect == "overlap":
        masks["calibration"][0] = True
    elif defect == "chronology":
        masks["train"], masks["calibration"] = masks["calibration"], masks["train"]
    elif defect == "label_overlap":
        ends = ends.copy()
        ends = ends.where(~masks["train"], features.index[1000])
    elif defect == "threads":
        config["threads"] = 2
    else:
        spec["features"] = ["not_present"]
    with pytest.raises(ValueError):
        fit_model(features, labels, masks, spec, config, asset="WTI", label_end=ends)


def test_unused_feature_nan_does_not_suppress_selected_features():
    bundle, features, _, _, _, _ = fit()
    before = bundle.predict(features)
    features["unused"] = np.nan
    np.testing.assert_array_equal(before, bundle.predict(features))
    features.loc[features.index[0], "a"] = np.nan
    assert np.isnan(bundle.predict(features)[0]).all()


def test_model_rejects_corrupted_linear_artifact(tmp_path):
    bundle, _, _, _, _, _ = fit()
    bundle.save(tmp_path / "model")
    path = tmp_path / "model/model.json"
    manifest = json.loads(path.read_text())
    manifest["predictor"]["coefficients"][0][0] += 1
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="checksum"):
        load_model(tmp_path / "model")


def test_per_asset_identity_and_version_are_required():
    features, labels, masks, config, ends = fixture()
    for asset, spec in (
        ("BTCUSD", {"kind": "logistic", "version": "v1"}),
        ("WTI", {"kind": "logistic"}),
    ):
        with pytest.raises(ValueError):
            fit_model(features, labels, masks, spec, config, asset=asset, label_end=ends)


@pytest.mark.parametrize(
    "probability", [np.array([[1, -0.5, 0.5]]), np.ones((1, 3)), np.array([[np.nan, 0, 1]])]
)
def test_invalid_probability_streams_are_rejected(probability):
    with pytest.raises(ValueError):
        probability_metrics(probability, np.array([0]))
