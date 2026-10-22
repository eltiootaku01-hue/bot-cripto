from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import hashlib

import numpy as np
import pandas as pd
import pytest

from research import cost_aware_ml_return_filter_v1 as candidate


def synthetic_candles(length: int = 520) -> pd.DataFrame:
    index = np.arange(length, dtype=np.float64)
    timestamps = pd.date_range("2024-01-01T00:00:00Z", periods=length, freq="h")
    close = 100.0 * np.exp(0.0001 * index + 0.01 * np.sin(index / 17.0))
    open_price = close * (1.0 + 0.0005 * np.sin(index / 3.0))
    high = np.maximum(open_price, close) * 1.002
    low = np.minimum(open_price, close) * 0.998
    volume = 100.0 + 5.0 * np.sin(index / 5.0) + (index % 7)
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def synthetic_quotes(decision_at: pd.Timestamp, exit_due_at: pd.Timestamp | None = None) -> pd.DataFrame:
    rows = [
        {
            "received_at": decision_at - pd.Timedelta(seconds=2),
            "available_at": decision_at - pd.Timedelta(seconds=1),
            "bid": "99.95",
            "ask": "100.05",
            "symbol": "BTCUSDT",
        },
        {
            "received_at": decision_at + pd.Timedelta(milliseconds=100),
            "available_at": decision_at + pd.Timedelta(milliseconds=200),
            "bid": "99.95",
            "ask": "100.05",
            "symbol": "BTCUSDT",
        },
    ]
    if exit_due_at is not None:
        rows.append(
            {
                "received_at": exit_due_at + pd.Timedelta(milliseconds=100),
                "available_at": exit_due_at + pd.Timedelta(milliseconds=200),
                "bid": "99.95",
                "ask": "100.05",
                "symbol": "BTCUSDT",
            }
        )
    return pd.DataFrame(rows)


def test_features_are_unchanged_when_only_future_suffix_is_perturbed():
    candles = synthetic_candles()
    cutoff = 400
    altered = candles.copy()
    for column in ("open", "high", "low", "close"):
        altered.loc[cutoff + 1 :, column] *= 1.71
    altered.loc[cutoff + 1 :, "volume"] *= 3.0

    original_features = candidate.compute_features(candles)
    changed_features = candidate.compute_features(altered)
    pd.testing.assert_frame_equal(
        original_features.loc[:cutoff, ["timestamp", *candidate.FEATURE_COLUMNS]],
        changed_features.loc[:cutoff, ["timestamp", *candidate.FEATURE_COLUMNS]],
        check_exact=True,
    )


def test_labels_are_mature_before_cutoff_and_use_the_fixed_24h_horizon():
    candles = synthetic_candles(length=1500)
    cutoff = candles["timestamp"].iloc[1350]
    training = candidate.build_training_set(candles, cutoff)

    assert len(training.features) >= candidate.MIN_TRAIN_ROWS
    assert (training.label_available_at <= cutoff).all()
    assert (training.label_available_at > training.decision_at).all()
    assert training.features.columns.tolist() == list(candidate.FEATURE_COLUMNS)

    sample_decision = training.decision_at.iloc[-1]
    candle_start = sample_decision - pd.Timedelta(hours=1)
    source_index = candles.index[candles["timestamp"].eq(candle_start)][0]
    expected = (
        candles["close"].iloc[source_index + 24] / candles["close"].iloc[source_index] - 1.0
    )
    assert training.target.iloc[-1] == pytest.approx(expected, abs=1e-14)


def test_candle_validation_rejects_naive_timestamps_gaps_and_bad_ohlc():
    candles = synthetic_candles(length=200)
    naive = candles.copy()
    naive["timestamp"] = naive["timestamp"].dt.tz_localize(None)
    with pytest.raises(candidate.ResearchContractError, match="MUST_BE_AWARE"):
        candidate.validate_candles(naive)

    gap = candles.drop(index=100).reset_index(drop=True)
    with pytest.raises(candidate.ResearchContractError, match="NOT_STRICTLY_HOURLY"):
        candidate.validate_candles(gap)

    invalid = candles.copy()
    invalid.loc[10, "high"] = invalid.loc[10, "low"] / 2
    with pytest.raises(candidate.ResearchContractError, match="OHLC_INCONSISTENT"):
        candidate.validate_candles(invalid)


def test_fixed_runtime_configuration_is_not_a_tuning_surface():
    model = candidate.make_regressor()
    params = model.get_params()
    assert all(params[name] == value for name, value in candidate.FIXED_MODEL_PARAMETERS.items())
    assert params["random_state"] == 20261021
    assert params["n_jobs"] == 1
    assert params["tree_method"] == "hist"
    assert candidate.HORIZON_HOURS == 24


def test_training_and_native_model_roundtrip_are_reproducible_on_synthetic_data(tmp_path):
    candles = synthetic_candles(length=1500)
    cutoff = candles["timestamp"].iloc[1350]
    model_path = tmp_path / "frozen-model.json"
    manifest_path = tmp_path / "frozen-model.manifest.json"

    training_candles = candles.iloc[:1350].copy()
    manifest = candidate.train_and_save_model(
        training_candles,
        training_cutoff=cutoff,
        source_dataset_sha256=candidate.canonical_dataset_sha256(training_candles),
        git_commit_sha="0" * 40,
        model_path=model_path,
        manifest_path=manifest_path,
    )
    frozen, loaded_manifest = candidate.load_frozen_model(model_path, manifest_path)
    training = candidate.build_training_set(candles, cutoff)
    peer = candidate.make_regressor()
    peer.fit(training.features, training.target)

    sample = training.features.iloc[:32]
    first_predictions = frozen.predict(sample)
    second_predictions = peer.predict(sample)
    np.testing.assert_array_equal(first_predictions, second_predictions)
    assert loaded_manifest["manifest_payload_sha256"] == manifest["manifest_payload_sha256"]
    assert loaded_manifest["model_sha256"] == hashlib.sha256(model_path.read_bytes()).hexdigest()
    assert loaded_manifest["source_dataset_hash_contract"] == "canonical-normalized-hourly-candles-v1"
    assert model_path.suffix == ".json"
    assert not manifest_path.read_text(encoding="utf-8").startswith("pickle")


def test_cost_gate_accounts_for_observed_spread_fees_and_slippage():
    base = candidate.COST_SCENARIOS["BASE"]
    cost = candidate.estimated_round_trip_cost("99.95", "100.05", base)
    assert cost > Decimal("0.003")
    assert not candidate.should_enter("0.001", "99.95", "100.05", base)
    assert candidate.should_enter("0.02", "99.95", "100.05", base)
    stress = candidate.estimated_round_trip_cost(
        "99.95", "100.05", candidate.COST_SCENARIOS["STRESS_2"]
    )
    assert stress > cost


def test_quote_validation_rejects_backdated_and_stale_entry_quotes():
    decision = pd.Timestamp("2026-01-01T12:00:00Z")
    quotes = synthetic_quotes(decision)
    normalized = candidate._normalize_quotes(quotes)
    assert candidate._fresh_quote_after(normalized, decision) == 1

    backdated = quotes.copy()
    backdated.loc[1, "received_at"] = decision - pd.Timedelta(milliseconds=1)
    normalized_backdated = candidate._normalize_quotes(backdated)
    with pytest.raises(candidate.ResearchContractError, match="BACKDATED"):
        candidate._fresh_quote_after(normalized_backdated, decision)

    stale = quotes.copy()
    stale.loc[1, "available_at"] = decision + pd.Timedelta(seconds=3)
    stale.loc[1, "received_at"] = decision + pd.Timedelta(seconds=2)
    normalized_stale = candidate._normalize_quotes(stale)
    with pytest.raises(candidate.ResearchContractError, match="STALE_OR_BACKDATED"):
        candidate._fresh_quote_after(normalized_stale, decision)


def test_synthetic_replay_reconciles_fees_and_never_uses_live_execution():
    decision = pd.Timestamp("2026-01-01T12:00:00Z")
    entry_available = decision + pd.Timedelta(milliseconds=200)
    exit_due = entry_available + pd.Timedelta(hours=24)
    quotes = synthetic_quotes(decision, exit_due)
    predictions = pd.DataFrame(
        {"decision_at": [decision], "predicted_return": [0.02]}
    )
    end_at = exit_due + pd.Timedelta(hours=1)
    filters = candidate.InstrumentFilters(
        min_quantity=Decimal("0.0001"),
        quantity_step=Decimal("0.0001"),
        min_notional=Decimal("5"),
    )
    result = candidate.replay_predictions(
        predictions,
        quotes,
        evaluation_end_at=end_at,
        filters=filters,
        scenario=candidate.COST_SCENARIOS["BASE"],
    )

    assert result["operational_authorization"] == "NO_TRADE"
    assert result["initial_capital_usdt"] == "100"
    assert result["round_trips"] == 1
    assert Decimal(result["total_fees_usdt"]) > 0
    assert Decimal(result["final_equity_usdt"]) < Decimal("100")
    assert Decimal(result["net_pnl_usdt"]) == (
        Decimal(result["final_equity_usdt"]) - Decimal(result["initial_capital_usdt"])
    )
    assert Decimal(result["trades"][0]["total_fees_usdt"]) > 0
    assert result["trades"][0]["exit_reason"] == "HORIZON_24H"


def test_negative_prediction_keeps_cash_and_replay_reports_no_fake_profit():
    decision = pd.Timestamp("2026-01-01T12:00:00Z")
    quotes = synthetic_quotes(decision)
    predictions = pd.DataFrame(
        {"decision_at": [decision], "predicted_return": [-0.01]}
    )
    end_at = decision + pd.Timedelta(hours=1)
    filters = candidate.InstrumentFilters(
        min_quantity=Decimal("0.0001"),
        quantity_step=Decimal("0.0001"),
        min_notional=Decimal("5"),
    )
    result = candidate.replay_predictions(
        predictions,
        quotes,
        evaluation_end_at=end_at,
        filters=filters,
        scenario=candidate.COST_SCENARIOS["BASE"],
    )
    assert result["round_trips"] == 0
    assert result["final_equity_usdt"] == "100"
    assert result["net_pnl_usdt"] == "0"
    assert result["max_drawdown_pct_sampled_hourly"] == "0"
    assert "not_intrahour_absolute_drawdown" in result["drawdown_measurement"]


def test_source_hash_validation_fails_before_any_model_training(tmp_path):
    candles = synthetic_candles(length=1500)
    cutoff = candles["timestamp"].iloc[1350]
    with pytest.raises(candidate.ResearchContractError, match="SHA256_INVALID"):
        candidate.train_and_save_model(
            candles,
            training_cutoff=cutoff,
            source_dataset_sha256="not-a-hash",
            git_commit_sha="0" * 40,
            model_path=tmp_path / "model.json",
            manifest_path=tmp_path / "model.manifest.json",
        )
    assert not (tmp_path / "model.json").exists()


def test_prediction_validation_rejects_nan_and_non_hourly_decisions():
    decision = pd.Timestamp("2026-01-01T12:00:00Z")
    bad_values = pd.DataFrame(
        {"decision_at": [decision], "predicted_return": [float("nan")]}
    )
    with pytest.raises(candidate.ResearchContractError, match="NOT_FINITE"):
        candidate._normalize_predictions(bad_values)

    gap = pd.DataFrame(
        {
            "decision_at": [decision, decision + pd.Timedelta(hours=2)],
            "predicted_return": [0.01, 0.02],
        }
    )
    with pytest.raises(candidate.ResearchContractError, match="NOT_STRICTLY_HOURLY"):
        candidate._normalize_predictions(gap)



def test_quote_validation_requires_explicit_btcusdt_identity():
    decision = pd.Timestamp("2026-01-01T12:00:00Z")
    quotes = synthetic_quotes(decision)

    missing_symbol = quotes.drop(columns=["symbol"])
    with pytest.raises(candidate.ResearchContractError, match="COLUMNS_MISSING:symbol"):
        candidate._normalize_quotes(missing_symbol)

    wrong_symbol = quotes.copy()
    wrong_symbol["symbol"] = "ETHUSDT"
    with pytest.raises(candidate.ResearchContractError, match="BTCUSDT_SPOT"):
        candidate._normalize_quotes(wrong_symbol)


def test_replay_rejects_entry_quote_beyond_evaluation_end():
    decision = pd.Timestamp("2026-01-01T12:00:00Z")
    quotes = synthetic_quotes(decision)
    predictions = pd.DataFrame({"decision_at": [decision], "predicted_return": [0.02]})
    filters = candidate.InstrumentFilters(
        min_quantity=Decimal("0.0001"),
        quantity_step=Decimal("0.0001"),
        min_notional=Decimal("5"),
    )
    with pytest.raises(candidate.ResearchContractError, match="EVALUATION_WINDOW_QUOTE_UNAVAILABLE"):
        candidate.replay_predictions(
            predictions,
            quotes,
            evaluation_end_at=decision + pd.Timedelta(milliseconds=100),
            filters=filters,
            scenario=candidate.COST_SCENARIOS["BASE"],
        )


def test_terminal_liquidation_never_uses_a_quote_after_evaluation_end():
    decision = pd.Timestamp("2026-01-01T12:00:00Z")
    entry_available = decision + pd.Timedelta(milliseconds=200)
    exit_due = entry_available + pd.Timedelta(hours=24)
    quotes = synthetic_quotes(decision, exit_due)
    predictions = pd.DataFrame({"decision_at": [decision], "predicted_return": [0.02]})
    end_at = exit_due - pd.Timedelta(seconds=5)
    filters = candidate.InstrumentFilters(
        min_quantity=Decimal("0.0001"),
        quantity_step=Decimal("0.0001"),
        min_notional=Decimal("5"),
    )
    with pytest.raises(candidate.ResearchContractError, match="EVALUATION_END_QUOTE_UNAVAILABLE"):
        candidate.replay_predictions(
            predictions,
            quotes,
            evaluation_end_at=end_at,
            filters=filters,
            scenario=candidate.COST_SCENARIOS["BASE"],
        )


def test_dataset_digest_must_match_actual_normalized_training_input(tmp_path):
    candles = synthetic_candles(length=1500)
    cutoff = candles["timestamp"].iloc[1350]
    training_candles = candles.iloc[:1350].copy()
    claimed_hash = candidate.canonical_dataset_sha256(training_candles)
    altered = training_candles.copy()
    altered.loc[5, "volume"] *= 1.01

    with pytest.raises(candidate.ResearchContractError, match="SOURCE_DATASET_HASH_MISMATCH"):
        candidate.train_and_save_model(
            altered,
            training_cutoff=cutoff,
            source_dataset_sha256=claimed_hash,
            git_commit_sha="0" * 40,
            model_path=tmp_path / "model.json",
            manifest_path=tmp_path / "model.manifest.json",
        )
    assert not (tmp_path / "model.json").exists()


def test_training_refuses_candles_that_extend_past_cutoff(tmp_path):
    candles = synthetic_candles(length=1500)
    cutoff = candles["timestamp"].iloc[1350]
    input_beyond_cutoff = candles.iloc[:1351].copy()
    with pytest.raises(candidate.ResearchContractError, match="TRAINING_INPUT_EXTENDS_BEYOND_CUTOFF"):
        candidate.train_and_save_model(
            input_beyond_cutoff,
            training_cutoff=cutoff,
            source_dataset_sha256=candidate.canonical_dataset_sha256(input_beyond_cutoff),
            git_commit_sha="0" * 40,
            model_path=tmp_path / "model.json",
            manifest_path=tmp_path / "model.manifest.json",
        )
    assert not (tmp_path / "model.json").exists()


def test_failed_manifest_publication_removes_orphan_model(tmp_path, monkeypatch):
    candles = synthetic_candles(length=1500)
    cutoff = candles["timestamp"].iloc[1350]
    training_candles = candles.iloc[:1350].copy()
    model_path = tmp_path / "frozen-model.json"
    manifest_path = tmp_path / "frozen-model.manifest.json"
    original_replace = Path.replace

    def fail_manifest_replace(self, target):
        if Path(target) == manifest_path:
            raise OSError("synthetic_manifest_publication_failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_manifest_replace)
    with pytest.raises(OSError, match="synthetic_manifest_publication_failure"):
        candidate.train_and_save_model(
            training_candles,
            training_cutoff=cutoff,
            source_dataset_sha256=candidate.canonical_dataset_sha256(training_candles),
            git_commit_sha="0" * 40,
            model_path=model_path,
            manifest_path=manifest_path,
        )
    assert not model_path.exists()
    assert not manifest_path.exists()
    assert not list(tmp_path.glob(".*.tmp*"))
