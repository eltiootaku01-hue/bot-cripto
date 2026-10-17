"""Fail-closed composition of the canonical market signal with local PAPER runtime.

This is the only orchestration bridge from the 4h signal producer into financial
risk/admission/PAPER. It cannot create its own quote, risk policy, account balance,
or evidence bundle. Those are supplied by explicit collaborators and checked
against the exact signal/proposal before the existing runtime is invoked.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Callable, Sequence

from .analysis_contracts import AnalysisResult, MarketObservation, Signal, SignalValidity
from .evidence_binding import AvailabilitySubjectKind
from .financial_execution_runtime import (
    EvidenceProvider,
    FinancialExecutionRuntime,
    FinancialExecutionRuntimeResult,
)
from .market_data import MarketData
from .market_signal_pipeline import (
    DEFAULT_MAX_CANDLE_AGE,
    EmaRsiCrossoverConfig,
    MarketSignalPipelineResult,
    MarketSignalPipelineStatus,
    generate_market_signal,
)
from .paper_adapter import (
    PaperExecutionAdapter,
    canonical_account_state_from_paper_snapshot,
)
from .risk_contracts import CanonicalAccountState
from .risk_evaluation_context import RiskEvaluationContext, RiskEvaluationContextStatus
from .risk_evaluation_policy import RiskEvaluationPolicy
from .signal_trade_proposal_bridge import PaperProposalPolicy
from .trade_proposal import TradeProposal


class MarketSignalPaperStatus(str, Enum):
    BLOCKED = "BLOCKED"
    NO_TRADE = "NO_TRADE"
    RUNTIME_RETURNED = "RUNTIME_RETURNED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MarketSignalPaperResult:
    status: MarketSignalPaperStatus
    reason: str
    pipeline_result: MarketSignalPipelineResult
    runtime_result: FinancialExecutionRuntimeResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, MarketSignalPaperStatus):
            raise TypeError("status must be MarketSignalPaperStatus")
        if type(self.reason) is not str or not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if not isinstance(self.pipeline_result, MarketSignalPipelineResult):
            raise TypeError("pipeline_result must be MarketSignalPipelineResult")
        if self.runtime_result is not None and not isinstance(
            self.runtime_result, FinancialExecutionRuntimeResult
        ):
            raise TypeError("runtime_result must be FinancialExecutionRuntimeResult")
        if self.status is MarketSignalPaperStatus.RUNTIME_RETURNED and self.runtime_result is None:
            raise ValueError("RUNTIME_RETURNED requires an explicit runtime result")
        if self.status is not MarketSignalPaperStatus.RUNTIME_RETURNED and self.runtime_result is not None:
            raise ValueError("runtime result is forbidden when the runtime was not invoked")


PaperRiskContextFactory = Callable[
    [Signal, TradeProposal, MarketSignalPipelineResult, CanonicalAccountState],
    RiskEvaluationContext,
]


def _aware(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field.upper()}_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc)


def _blocked(
    result: MarketSignalPipelineResult,
    reason: str,
    *,
    status: MarketSignalPaperStatus = MarketSignalPaperStatus.BLOCKED,
) -> MarketSignalPaperResult:
    return MarketSignalPaperResult(status=status, reason=reason, pipeline_result=result)


def _validate_risk_context(
    *,
    context: RiskEvaluationContext,
    signal: Signal,
    proposal: TradeProposal,
    pipeline: MarketSignalPipelineResult,
    account_state: CanonicalAccountState,
) -> str | None:
    if not isinstance(context, RiskEvaluationContext):
        return "RISK_CONTEXT_CONTRACT_INVALID"
    if context.completeness is not RiskEvaluationContextStatus.COMPLETE or not context.is_complete:
        return "RISK_CONTEXT_INCOMPLETE"
    if context.trade_proposal != proposal:
        return "RISK_CONTEXT_PROPOSAL_MISMATCH"
    if context.signal != signal or context.signal.signal_id != proposal.signal_id:
        return "RISK_CONTEXT_SIGNAL_BINDING_MISMATCH"
    if context.canonical_account_state != account_state:
        return "RISK_CONTEXT_ACCOUNT_SNAPSHOT_MISMATCH"
    if context.instrument.symbol != proposal.symbol or context.instrument.market != "SPOT":
        return "RISK_CONTEXT_INSTRUMENT_NOT_BINANCE_SPOT"
    if pipeline.analysis_snapshot is None or len(pipeline.analysis_snapshot.results) != 1:
        return "CANONICAL_ANALYSIS_EVIDENCE_MISSING"
    analysis = pipeline.analysis_snapshot.results[0]
    if not isinstance(analysis, AnalysisResult) or context.analysis_result != analysis:
        return "RISK_CONTEXT_ANALYSIS_BINDING_MISMATCH"
    if context.market_observation is None:
        return "PAPER_BOOK_TICKER_OBSERVATION_REQUIRED"
    observation = context.market_observation
    if not isinstance(observation, MarketObservation):
        return "PAPER_BOOK_TICKER_OBSERVATION_INVALID"
    if (
        observation.observation_type != "BINANCE_SPOT_BOOK_TICKER"
        or observation.symbol != proposal.symbol
        or observation.venue != "BINANCE"
        or not observation.provenance.source.startswith("binance-public:")
    ):
        return "PAPER_BOOK_TICKER_PROVENANCE_INVALID"
    decision = pipeline.decision_timestamp
    try:
        observed_at = _aware(observation.observation_timestamp, "quote_observed_at")
        available_at = _aware(observation.available_timestamp, "quote_available_at")
        context_time = _aware(context.evaluation_timestamp, "risk_evaluation_timestamp")
        if decision is None:
            return "PIPELINE_DECISION_TIMESTAMP_UNKNOWN"
        decision = _aware(decision, "pipeline_decision_timestamp")
    except ValueError as exc:
        return str(exc)
    if observed_at > available_at or available_at > decision:
        return "PAPER_BOOK_TICKER_NOT_AVAILABLE_AT_SIGNAL_DECISION"
    if context_time < proposal.decision_timestamp:
        return "RISK_CONTEXT_PRECEDES_PROPOSAL_DECISION"
    values = observation.values
    if type(values.get("bid")) is not str or type(values.get("ask")) is not str:
        return "PAPER_BOOK_TICKER_DECIMALS_INVALID"
    try:
        bid, ask = Decimal(values["bid"]), Decimal(values["ask"])
    except (InvalidOperation, TypeError, ValueError):
        return "PAPER_BOOK_TICKER_DECIMALS_INVALID"
    if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask < bid:
        return "PAPER_BOOK_TICKER_PRICE_INVARIANT_FAILED"

    matches = tuple(
        binding
        for binding in context.availability_bindings
        if binding.subject_kind is AvailabilitySubjectKind.MARKET_OBSERVATION
        and binding.subject_id == observation.observation_id
    )
    if (
        len(matches) != 1
        or matches[0].available_at != observation.available_timestamp
        or matches[0].source != observation.provenance.source
        or matches[0].evidence_reference != observation.observation_id
    ):
        return "PAPER_BOOK_TICKER_AVAILABILITY_BINDING_MISMATCH"
    if signal.validity is not SignalValidity.VALID:
        return "SIGNAL_VALIDITY_NOT_CONFIRMED"
    if not signal.is_valid_at(context.evaluation_timestamp):
        return "SIGNAL_EXPIRED_BEFORE_RISK_EVALUATION"
    return None


def run_market_signal_to_paper_runtime(
    items: Sequence[MarketData],
    *,
    decision_timestamp: datetime,
    proposal_policy: PaperProposalPolicy,
    runtime: FinancialExecutionRuntime,
    risk_policy: RiskEvaluationPolicy,
    context_factory: PaperRiskContextFactory,
    created_at: datetime,
    evidence_provider: EvidenceProvider,
    config: EmaRsiCrossoverConfig = EmaRsiCrossoverConfig(),
    expected_symbol: str | None = None,
    max_candle_age: timedelta = DEFAULT_MAX_CANDLE_AGE,
) -> MarketSignalPaperResult:
    """Produce a point-in-time signal and pass only a fully bound proposal to PAPER.

    The runtime is invoked once at most. The caller must explicitly supply the
    PAPER proposal terms, a context builder that attaches a separate public
    Binance book-ticker observation, the approved risk policy, and readiness
    evidence. No retry is attempted when the runtime outcome is uncertain.
    """
    pipeline = generate_market_signal(
        items,
        decision_timestamp=decision_timestamp,
        config=config,
        expected_symbol=expected_symbol,
        max_candle_age=max_candle_age,
        proposal_policy=proposal_policy,
    )
    if pipeline.status is MarketSignalPipelineStatus.NO_TRADE:
        return _blocked(pipeline, "CANONICAL_STRATEGY_RETURNED_NO_TRADE",
                        status=MarketSignalPaperStatus.NO_TRADE)
    if pipeline.status is not MarketSignalPipelineStatus.PROPOSAL_READY:
        return _blocked(pipeline, "MARKET_SIGNAL_PIPELINE_NOT_PROPOSAL_READY:" + pipeline.reason)
    signal, proposal = pipeline.signal, pipeline.trade_proposal
    if not isinstance(signal, Signal) or not isinstance(proposal, TradeProposal):
        return _blocked(pipeline, "CANONICAL_SIGNAL_OR_PROPOSAL_MISSING")
    try:
        decision = _aware(decision_timestamp, "decision_timestamp")
        admission_time = _aware(created_at, "created_at")
    except ValueError as exc:
        return _blocked(pipeline, str(exc))
    if proposal.decision_timestamp != decision:
        return _blocked(pipeline, "PROPOSAL_DECISION_TIMESTAMP_MISMATCH")
    if admission_time < decision:
        return _blocked(pipeline, "CREATED_AT_PRECEDES_SIGNAL_DECISION")
    if signal.validity is not SignalValidity.VALID or not signal.is_valid_at(admission_time):
        return _blocked(pipeline, "SIGNAL_EXPIRED_OR_VALIDITY_UNKNOWN_BEFORE_RUNTIME")
    if not isinstance(proposal_policy, PaperProposalPolicy):
        return _blocked(pipeline, "EXPLICIT_PAPER_PROPOSAL_POLICY_REQUIRED")
    if not isinstance(runtime, FinancialExecutionRuntime):
        return _blocked(pipeline, "CANONICAL_FINANCIAL_EXECUTION_RUNTIME_REQUIRED")
    if not isinstance(risk_policy, RiskEvaluationPolicy) or not callable(context_factory):
        return _blocked(pipeline, "RISK_POLICY_OR_CONTEXT_FACTORY_INVALID")
    if not callable(evidence_provider):
        return _blocked(pipeline, "READINESS_EVIDENCE_PROVIDER_REQUIRED")

    adapter = runtime._paper_adapter()
    if not isinstance(adapter, PaperExecutionAdapter):
        return _blocked(pipeline, "LOCAL_PAPER_ADAPTER_REQUIRED_NO_LIVE_FALLBACK")
    try:
        account_state = canonical_account_state_from_paper_snapshot(adapter.snapshot())
    except Exception as exc:
        return _blocked(pipeline, f"PAPER_ACCOUNT_SNAPSHOT_UNKNOWN:{type(exc).__name__}")
    try:
        context = context_factory(signal, proposal, pipeline, account_state)
    except Exception as exc:
        return _blocked(pipeline, f"RISK_CONTEXT_FACTORY_FAILED:{type(exc).__name__}")
    reason = _validate_risk_context(
        context=context,
        signal=signal,
        proposal=proposal,
        pipeline=pipeline,
        account_state=account_state,
    )
    if reason is not None:
        return _blocked(pipeline, reason)

    try:
        result = runtime.start(
            proposal=proposal,
            context=context,
            policy=risk_policy,
            canonical_account_state=account_state,
            account_id=account_state.account_id,
            created_at=admission_time,
            evidence_provider=evidence_provider,
        )
    except Exception as exc:
        # start() can fail after durable effects; a caller must reconcile, not retry.
        return _blocked(
            pipeline,
            f"FINANCIAL_RUNTIME_OUTCOME_UNKNOWN:{type(exc).__name__}",
            status=MarketSignalPaperStatus.UNKNOWN,
        )
    if not isinstance(result, FinancialExecutionRuntimeResult):
        return _blocked(
            pipeline,
            "FINANCIAL_RUNTIME_RESULT_CONTRACT_INVALID_OUTCOME_UNKNOWN",
            status=MarketSignalPaperStatus.UNKNOWN,
        )
    return MarketSignalPaperResult(
        status=MarketSignalPaperStatus.RUNTIME_RETURNED,
        reason=f"RUNTIME_RESULT:{result.status.value}:{result.reason}",
        pipeline_result=pipeline,
        runtime_result=result,
    )


__all__ = [
    "MarketSignalPaperResult",
    "MarketSignalPaperStatus",
    "PaperRiskContextFactory",
    "run_market_signal_to_paper_runtime",
]
