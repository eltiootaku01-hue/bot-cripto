from __future__ import annotations

from decimal import Decimal

import pytest

from frankestain.candidates.backtest_metrics import (
    EquityPoint,
    summarize_equity_curve,
)
from frankestain.candidates.portfolio_risk import (
    DrawdownPolicy,
    GrossExposurePolicy,
    RiskTrigger,
    evaluate_drawdown,
    evaluate_gross_exposure,
)
from frankestain.candidates.replay_contract import ReplayEvent, ReplayTimeline


def test_drawdown_candidate_trailing_peak_is_fail_closed_at_limit():
    policy = DrawdownPolicy(
        maximum_drawdown=Decimal("0.10"),
        trailing=True,
    )

    observation = evaluate_drawdown(
        current_equity=Decimal("90"),
        reference_equity=Decimal("100"),
        policy=policy,
    )

    assert observation.drawdown == Decimal("-0.10")
    assert observation.trigger is RiskTrigger.HALT_NEW_ENTRIES


def test_drawdown_candidate_updates_trailing_reference_without_side_effects():
    policy = DrawdownPolicy(
        maximum_drawdown=Decimal("0.10"),
        trailing=True,
    )

    observation = evaluate_drawdown(
        current_equity=Decimal("110"),
        reference_equity=Decimal("100"),
        policy=policy,
    )

    assert observation.reference_equity == Decimal("110")
    assert observation.drawdown == Decimal("0")


def test_gross_exposure_candidate_triggers_only_above_limit():
    policy = GrossExposurePolicy(maximum_fraction=Decimal("1.50"))

    safe = evaluate_gross_exposure(
        portfolio_value=Decimal("100"),
        gross_exposure=Decimal("150"),
        policy=policy,
    )
    high = evaluate_gross_exposure(
        portfolio_value=Decimal("100"),
        gross_exposure=Decimal("151"),
        policy=policy,
    )

    assert safe.trigger is RiskTrigger.NONE
    assert high.trigger is RiskTrigger.REDUCE_EXPOSURE


def test_replay_timeline_freezes_payload_and_filters_by_availability():
    event = ReplayEvent(
        event_id="e1",
        event_type="MARKET_DATA",
        event_timestamp=__import__("datetime").datetime(
            2026, 10, 6, 10, 0, tzinfo=__import__("datetime").timezone.utc
        ),
        available_at=__import__("datetime").datetime(
            2026, 10, 6, 10, 1, tzinfo=__import__("datetime").timezone.utc
        ),
        payload={"nested": {"value": 1}},
    )

    timeline = ReplayTimeline(events=(event,))

    assert timeline.available_by(event.available_at) == (event,)
    assert timeline.available_by(
        __import__("datetime").datetime(
            2026, 10, 6, 10, 0, tzinfo=__import__("datetime").timezone.utc
        )
    ) == ()

    with pytest.raises(TypeError):
        event.payload["nested"]["value"] = 2


def test_backtest_metrics_are_deterministic():
    points = (
        EquityPoint("t0", Decimal("100")),
        EquityPoint("t1", Decimal("120")),
        EquityPoint("t2", Decimal("90")),
        EquityPoint("t3", Decimal("110")),
    )

    metrics = summarize_equity_curve(
        points,
        trade_count=4,
        winning_trades=3,
        losing_trades=1,
    )

    assert metrics.initial_equity == Decimal("100")
    assert metrics.final_equity == Decimal("110")
    assert metrics.total_return == Decimal("0.1")
    assert metrics.max_drawdown == Decimal("-0.25")
    assert metrics.trade_count == 4
    assert metrics.win_rate == Decimal("0.75")
