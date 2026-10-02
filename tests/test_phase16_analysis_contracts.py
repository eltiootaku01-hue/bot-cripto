from datetime import datetime, timedelta, timezone

import pytest

from bot_obrero.analysis_contracts import (
    AnalysisResult,
    ArtifactNature,
    ContractError,
    Hypothesis,
    MarketObservation,
    Provenance,
    Signal,
    SignalValidity,
)

T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
OBSERVED = Provenance("market-feed", ArtifactNature.OBSERVED)
DERIVED = Provenance("analysis-engine", ArtifactNature.DERIVED)


def observation(*, available=T):
    return MarketObservation(
        symbol="BTCUSDT",
        observation_timestamp=T,
        available_timestamp=available,
        observation_type="price",
        values={"close": "100"},
        provenance=OBSERVED,
        venue="venue-a",
    )


def analysis(obs, *, decision=T + timedelta(seconds=1)):
    return AnalysisResult.from_observations(
        symbol="BTCUSDT",
        observations=(obs,),
        analysis_type="generic-analysis",
        values={"value": "derived"},
        calculated_at=decision,
        decision_timestamp=decision,
        provenance=DERIVED,
    )


def hypothesis(result, *, decision=T + timedelta(seconds=2)):
    return Hypothesis(
        symbol="BTCUSDT",
        supporting_analysis_ids=(result.analysis_id,),
        strategic_artifact_id="artifact-test-001",
        expected_direction="UNSPECIFIED",
        expected_horizon="UNSPECIFIED",
        invalidation_conditions=("manual-review",),
        created_at=decision,
        decision_timestamp=decision,
        provenance=DERIVED,
    )


def test_market_observation_is_observed_and_immutable():
    obs = observation()
    assert obs.provenance.nature is ArtifactNature.OBSERVED
    with pytest.raises((AttributeError, TypeError)):
        obs.values["close"] = "200"


def test_analysis_traces_observation_and_reuses_temporal_guard():
    obs = observation()
    result = analysis(obs)
    assert result.observation_ids == (obs.observation_id,)
    assert result.provenance.nature is ArtifactNature.DERIVED


def test_analysis_rejects_observation_unavailable_at_decision():
    obs = observation(available=T + timedelta(seconds=5))
    with pytest.raises(ValueError, match="look-ahead: evidence unavailable"):
        analysis(obs, decision=T + timedelta(seconds=1))


def test_analysis_cannot_claim_observed_provenance():
    obs = observation()
    with pytest.raises(ContractError, match="AnalysisResult must be DERIVED"):
        AnalysisResult.from_observations(
            symbol=obs.symbol,
            observations=(obs,),
            analysis_type="generic-analysis",
            values={},
            calculated_at=T,
            decision_timestamp=T,
            provenance=OBSERVED,
        )


def test_hypothesis_traces_analysis_and_supports_expiry():
    result = analysis(observation())
    expires = T + timedelta(minutes=5)
    h = Hypothesis(
        symbol="BTCUSDT",
        supporting_analysis_ids=(result.analysis_id,),
        strategic_artifact_id="artifact-test-001",
        expected_direction="UNSPECIFIED",
        expected_horizon="UNSPECIFIED",
        invalidation_conditions=("manual-review",),
        created_at=T + timedelta(seconds=2),
        decision_timestamp=T + timedelta(seconds=2),
        expires_at=expires,
        provenance=DERIVED,
    )
    assert h.supporting_analysis_ids == (result.analysis_id,)
    assert h.expires_at == expires


def test_signal_traces_hypothesis_and_has_temporal_validity():
    h = hypothesis(analysis(observation()))
    signal = Signal(
        symbol="BTCUSDT",
        hypothesis_id=h.hypothesis_id,
        direction="UNSPECIFIED",
        generated_at=T + timedelta(seconds=3),
        decision_timestamp=T + timedelta(seconds=3),
        expires_at=T + timedelta(minutes=5),
        validity=SignalValidity.VALID,
        provenance=DERIVED,
    )
    assert signal.hypothesis_id == h.hypothesis_id
    assert signal.is_valid_at(T + timedelta(seconds=4))
    assert not signal.is_valid_at(T + timedelta(minutes=6))


def test_signal_unknown_is_not_silently_treated_as_valid():
    h = hypothesis(analysis(observation()))
    signal = Signal(
        symbol="BTCUSDT",
        hypothesis_id=h.hypothesis_id,
        direction="UNSPECIFIED",
        generated_at=T + timedelta(seconds=3),
        decision_timestamp=T + timedelta(seconds=3),
        provenance=DERIVED,
    )
    assert signal.validity is SignalValidity.UNKNOWN
    assert not signal.is_valid_at(T + timedelta(seconds=4))


def test_signal_has_no_order_intent_dependency():
    import bot_obrero.analysis_contracts as module

    assert not hasattr(module, "OrderIntent")
    assert "execution" not in module.__dict__
