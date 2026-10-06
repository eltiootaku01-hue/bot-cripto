"""Immutable provider-neutral Risk Evaluation Context v1.0.

HUESO 02-I4 defines only the logical evidence snapshot consumed by a future
RiskEngine. It does not evaluate risk, resolve limits, query live state, or
authorize execution.

The identity is deterministic and content-derived:
risk-evaluation-context-v1:<sha256>.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping

from .analysis_contracts import AnalysisResult, MarketObservation, Provenance, Signal
from .effective_capacity import EffectiveCapacity
from .evidence_binding import (
    AvailabilityBinding,
    AvailabilitySubjectKind,
    RiskLimitResolution,
    RiskLimitResolutionStatus,
    derive_reservation_read_set_id,
)
from .market_data import InstrumentIdentity, MarketData
from .reservation import ReservationReadSet
from .risk_contracts import (
    CanonicalAccountState,
    CanonicalExposure,
    CanonicalPosition,
    Completeness,
    RiskLimitSet,
)
from .trade_proposal import TradeProposal


class RiskEvaluationContextError(ValueError):
    """Raised when the immutable evaluation context is structurally invalid."""


class RiskEvaluationContextStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RiskEvaluationContextError(f"{field_name} must be timezone-aware")


def _require_type(value: Any, expected: type, field_name: str) -> None:
    if not isinstance(value, expected):
        raise RiskEvaluationContextError(f"{field_name} must be {expected.__name__}")


def _canonical_datetime(value: datetime) -> str:
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _canonical_decimal(value: Decimal) -> dict[str, str]:
    return {"__decimal__": str(value)}


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, datetime):
        return {"__datetime__": _canonical_datetime(value)}
    if isinstance(value, Enum):
        return {
            "__enum__": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": value.value,
        }
    if isinstance(value, Mapping):
        items = [
            [_canonical_value(key), _canonical_value(item)]
            for key, item in value.items()
        ]
        items.sort(
            key=lambda item: json.dumps(
                item[0], sort_keys=True, separators=(",", ":")
            )
        )
        return {"__mapping__": items}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, frozenset):
        return sorted(
            (_canonical_value(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    raise TypeError(
        "unsupported value for deterministic RiskEvaluationContext identity: "
        f"{type(value).__name__}"
    )


def _canonical_provenance(value: Provenance) -> dict[str, Any]:
    return {
        "source": value.source,
        "nature": value.nature.value,
        "reference": value.reference,
        "metadata": _canonical_value(value.metadata),
    }


def _canonical_trade_proposal(value: TradeProposal) -> dict[str, Any]:
    return {
        "proposal_id": value.proposal_id,
        "signal_id": value.signal_id,
        "symbol": value.symbol,
        "side": value.side.value,
        "requested_quantity": _canonical_decimal(value.requested_quantity),
        "requested_price": None if value.requested_price is None else _canonical_decimal(value.requested_price),
        "max_quote_spend": None if value.max_quote_spend is None else _canonical_decimal(value.max_quote_spend),
        "price_policy": value.price_policy.value,
        "order_type": value.order_type.value,
        "strategy_identity": value.strategy_identity,
        "strategy_version": value.strategy_version,
        "decision_timestamp": _canonical_datetime(value.decision_timestamp),
        "correlation_id": value.correlation_id,
    }


def _canonical_instrument(value: InstrumentIdentity) -> dict[str, Any]:
    return {
        "instrument_id": value.instrument_id,
        "symbol": value.symbol,
        "market": value.market,
        "instrument_type": value.instrument_type.value,
        "base_asset": value.base_asset,
        "quote_asset": value.quote_asset,
    }


def _canonical_account_state(value: CanonicalAccountState) -> dict[str, Any]:
    balances = sorted(
        (
            {
                "asset": balance.asset,
                "total": _canonical_decimal(balance.total),
                "available": _canonical_decimal(balance.available),
                "locked": _canonical_decimal(balance.locked),
            }
            for balance in value.balances
        ),
        key=lambda item: item["asset"],
    )
    return {
        "account_state_id": value.account_state_id,
        "account_id": value.account_id,
        "as_of": _canonical_datetime(value.as_of),
        "balances": balances,
        "completeness": value.completeness.value,
        "provenance": _canonical_provenance(value.provenance),
    }


def _canonical_position(value: CanonicalPosition) -> dict[str, Any]:
    return {
        "position_id": value.position_id,
        "account_id": value.account_id,
        "symbol": value.symbol,
        "quantity": _canonical_decimal(value.quantity),
        "as_of": _canonical_datetime(value.as_of),
        "completeness": value.completeness.value,
        "provenance": _canonical_provenance(value.provenance),
    }


def _canonical_exposure(value: CanonicalExposure) -> dict[str, Any]:
    return {
        "exposure_id": value.exposure_id,
        "account_id": value.account_id,
        "symbol": value.symbol,
        "quantity": _canonical_decimal(value.quantity),
        "valuation_price": None if value.valuation_price is None else _canonical_decimal(value.valuation_price),
        "notional": None if value.notional is None else _canonical_decimal(value.notional),
        "as_of": _canonical_datetime(value.as_of),
        "valuation_as_of": None if value.valuation_as_of is None else _canonical_datetime(value.valuation_as_of),
        "completeness": value.completeness.value,
        "provenance": _canonical_provenance(value.provenance),
    }


def _canonical_risk_limit_set(value: RiskLimitSet) -> dict[str, Any]:
    limits = sorted(
        (
            {
                "risk_limit_id": limit.risk_limit_id,
                "scope": limit.scope,
                "metric": limit.metric,
                "threshold": _canonical_decimal(limit.threshold),
                "unit": limit.unit,
                "effective_from": _canonical_datetime(limit.effective_from),
                "effective_until": None if limit.effective_until is None else _canonical_datetime(limit.effective_until),
                "provenance": _canonical_provenance(limit.provenance),
            }
            for limit in value.limits
        ),
        key=lambda item: item["risk_limit_id"],
    )
    return {
        "risk_limit_set_id": value.risk_limit_set_id,
        "limits": limits,
        "as_of": _canonical_datetime(value.as_of),
        "provenance": _canonical_provenance(value.provenance),
    }


def _canonical_risk_limit_resolution(value: RiskLimitResolution | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "status": value.status.value,
        "risk_limit_set_id": value.risk_limit_set_id,
        "applicable_limit_ids": sorted(value.applicable_limit_ids),
    }


def _canonical_reservation_read_set(value: ReservationReadSet) -> dict[str, Any]:
    return {"reservation_read_set_id": derive_reservation_read_set_id(value)}


def _canonical_effective_capacity(value: EffectiveCapacity) -> dict[str, Any]:
    return {
        "account_id": value.account_id,
        "resource_kind": value.resource_kind.value,
        "asset": value.asset,
        "canonical_available": _canonical_decimal(value.canonical_available),
        "protected_active_reserved": _canonical_decimal(value.protected_active_reserved),
        "effective_available": _canonical_decimal(value.effective_available),
        "status": value.status.value,
        "completeness": value.completeness.value,
    }


def _canonical_market_data(value: MarketData) -> dict[str, Any]:
    return {
        "market_data_id": value.market_data_id,
        "instrument": _canonical_instrument(value.instrument),
        "source": {
            "source_id": value.source.source_id,
            "provider": value.source.provider,
            "venue": value.source.venue,
        },
        "data_type": value.data_type,
        "observed_at": _canonical_datetime(value.observed_at),
        "received_at": _canonical_datetime(value.received_at),
        "available_at": None if value.available_at is None else _canonical_datetime(value.available_at),
        "payload": value.payload.to_dict(),
        "quality": value.quality.value,
        "completeness": value.completeness.value,
        "source_sequence": value.source_sequence,
    }


def _canonical_market_observation(value: MarketObservation) -> dict[str, Any]:
    return {
        "observation_id": value.observation_id,
        "symbol": value.symbol,
        "observation_timestamp": _canonical_datetime(value.observation_timestamp),
        "available_timestamp": _canonical_datetime(value.available_timestamp),
        "observation_type": value.observation_type,
        "values": _canonical_value(value.values),
        "provenance": _canonical_provenance(value.provenance),
        "venue": value.venue,
    }


def _canonical_analysis_result(value: AnalysisResult) -> dict[str, Any]:
    return {
        "analysis_id": value.analysis_id,
        "symbol": value.symbol,
        "observation_ids": list(value.observation_ids),
        "analysis_type": value.analysis_type,
        "values": _canonical_value(value.values),
        "calculated_at": _canonical_datetime(value.calculated_at),
        "decision_timestamp": _canonical_datetime(value.decision_timestamp),
        "provenance": _canonical_provenance(value.provenance),
    }


def _canonical_signal(value: Signal) -> dict[str, Any]:
    return {
        "signal_id": value.signal_id,
        "symbol": value.symbol,
        "hypothesis_id": value.hypothesis_id,
        "direction": value.direction,
        "generated_at": _canonical_datetime(value.generated_at),
        "decision_timestamp": _canonical_datetime(value.decision_timestamp),
        "provenance": _canonical_provenance(value.provenance),
        "evidence": _canonical_value(value.evidence),
        "expires_at": None if value.expires_at is None else _canonical_datetime(value.expires_at),
        "validity": value.validity.value,
    }


def _canonical_binding(value: AvailabilityBinding) -> dict[str, Any]:
    return {
        "subject_kind": value.subject_kind.value,
        "subject_id": value.subject_id,
        "available_at": _canonical_datetime(value.available_at),
        "source": value.source,
        "evidence_reference": value.evidence_reference,
    }


def _sha256(payload: Any) -> str:
    encoded = json.dumps(
        _canonical_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                _snapshot(key): _snapshot(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, list):
        return tuple(_snapshot(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_snapshot(item) for item in value)
    if isinstance(value, set):
        return frozenset(_snapshot(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_snapshot(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        snapshot = copy(value)
        for descriptor in fields(value):
            object.__setattr__(
                snapshot,
                descriptor.name,
                _snapshot(getattr(value, descriptor.name)),
            )
        return snapshot
    return value


@dataclass(frozen=True)
class RiskEvaluationContext:
    """Immutable logical snapshot for a future risk evaluation."""

    trade_proposal: TradeProposal
    instrument: InstrumentIdentity
    canonical_account_state: CanonicalAccountState
    risk_limit_set: RiskLimitSet
    evaluation_timestamp: datetime
    availability_bindings: tuple[AvailabilityBinding, ...] = ()
    risk_limit_resolution: RiskLimitResolution | None = None
    canonical_position: CanonicalPosition | None = None
    canonical_exposure: CanonicalExposure | None = None
    reservation_read_set: ReservationReadSet | None = None
    effective_capacity: EffectiveCapacity | None = None
    market_data: MarketData | None = None
    market_observation: MarketObservation | None = None
    analysis_result: AnalysisResult | None = None
    signal: Signal | None = None
    evaluation_context_id: str = field(init=False)
    completeness: RiskEvaluationContextStatus = field(init=False)
    incompleteness_reasons: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        snapshot_fields = (
            "trade_proposal",
            "instrument",
            "canonical_account_state",
            "risk_limit_set",
            "evaluation_timestamp",
            "availability_bindings",
            "risk_limit_resolution",
            "canonical_position",
            "canonical_exposure",
            "reservation_read_set",
            "effective_capacity",
            "market_data",
            "market_observation",
            "analysis_result",
            "signal",
        )
        for field_name in snapshot_fields:
            object.__setattr__(
                self,
                field_name,
                _snapshot(getattr(self, field_name)),
            )

        _require_type(self.trade_proposal, TradeProposal, "trade_proposal")
        _require_type(self.instrument, InstrumentIdentity, "instrument")
        _require_type(self.canonical_account_state, CanonicalAccountState, "canonical_account_state")
        _require_type(self.risk_limit_set, RiskLimitSet, "risk_limit_set")
        _require_aware(self.evaluation_timestamp, "evaluation_timestamp")

        if self.trade_proposal.decision_timestamp > self.evaluation_timestamp:
            raise RiskEvaluationContextError(
                "trade proposal decision_timestamp cannot follow evaluation_timestamp"
            )
        if self.trade_proposal.symbol != self.instrument.symbol:
            raise RiskEvaluationContextError(
                "trade proposal symbol must match instrument symbol"
            )

        if self.canonical_position is not None:
            _require_type(self.canonical_position, CanonicalPosition, "canonical_position")
            if self.canonical_position.account_id != self.canonical_account_state.account_id:
                raise RiskEvaluationContextError(
                    "canonical_position.account_id must match canonical_account_state.account_id"
                )

        if self.canonical_exposure is not None:
            _require_type(self.canonical_exposure, CanonicalExposure, "canonical_exposure")
            if self.canonical_exposure.account_id != self.canonical_account_state.account_id:
                raise RiskEvaluationContextError(
                    "canonical_exposure.account_id must match canonical_account_state.account_id"
                )

        if self.reservation_read_set is not None:
            _require_type(self.reservation_read_set, ReservationReadSet, "reservation_read_set")
            if self.reservation_read_set.account_id != self.canonical_account_state.account_id:
                raise RiskEvaluationContextError(
                    "reservation_read_set.account_id must match canonical_account_state.account_id"
                )

        if self.effective_capacity is not None:
            _require_type(self.effective_capacity, EffectiveCapacity, "effective_capacity")
            if self.effective_capacity.account_id != self.canonical_account_state.account_id:
                raise RiskEvaluationContextError(
                    "effective_capacity.account_id must match canonical_account_state.account_id"
                )

        if self.market_data is not None:
            _require_type(self.market_data, MarketData, "market_data")
            if self.market_data.instrument != self.instrument:
                raise RiskEvaluationContextError("market_data.instrument must match instrument")

        if self.market_observation is not None:
            _require_type(self.market_observation, MarketObservation, "market_observation")
            if self.market_observation.symbol != self.instrument.symbol:
                raise RiskEvaluationContextError(
                    "market_observation.symbol must match instrument symbol"
                )

        if self.analysis_result is not None:
            _require_type(self.analysis_result, AnalysisResult, "analysis_result")
            if self.analysis_result.symbol != self.instrument.symbol:
                raise RiskEvaluationContextError(
                    "analysis_result.symbol must match instrument symbol"
                )

        if self.signal is not None:
            _require_type(self.signal, Signal, "signal")
            if self.signal.signal_id != self.trade_proposal.signal_id:
                raise RiskEvaluationContextError(
                    "signal.signal_id must match trade_proposal.signal_id"
                )
            if self.signal.symbol != self.instrument.symbol:
                raise RiskEvaluationContextError(
                    "signal.symbol must match instrument symbol"
                )

        if self.risk_limit_resolution is not None:
            _require_type(self.risk_limit_resolution, RiskLimitResolution, "risk_limit_resolution")
            if self.risk_limit_resolution.status in {
                RiskLimitResolutionStatus.AVAILABLE,
                RiskLimitResolutionStatus.NO_APPLICABLE_LIMIT,
                RiskLimitResolutionStatus.LIMITS_INCOMPLETE,
            }:
                if self.risk_limit_resolution.risk_limit_set_id != self.risk_limit_set.risk_limit_set_id:
                    raise RiskEvaluationContextError(
                        "risk_limit_resolution.risk_limit_set_id must match risk_limit_set"
                    )
                known_limit_ids = {limit.risk_limit_id for limit in self.risk_limit_set.limits}
                unknown_ids = set(self.risk_limit_resolution.applicable_limit_ids) - known_limit_ids
                if unknown_ids:
                    raise RiskEvaluationContextError(
                        "risk_limit_resolution contains unknown applicable limit ids"
                    )

        bindings = tuple(self.availability_bindings)
        if not all(isinstance(item, AvailabilityBinding) for item in bindings):
            raise RiskEvaluationContextError(
                "availability_bindings must contain only AvailabilityBinding values"
            )
        seen_binding_keys: set[tuple[AvailabilitySubjectKind, str]] = set()
        for binding in bindings:
            key = (binding.subject_kind, binding.subject_id)
            if key in seen_binding_keys:
                raise RiskEvaluationContextError(
                    "availability_bindings must not contain duplicate subject bindings"
                )
            seen_binding_keys.add(key)
            binding.validate_for(self.evaluation_timestamp)
        object.__setattr__(self, "availability_bindings", bindings)

        expected_subjects: dict[AvailabilitySubjectKind, str | None] = {
            AvailabilitySubjectKind.ACCOUNT_STATE: self.canonical_account_state.account_state_id,
            AvailabilitySubjectKind.RISK_LIMIT_SET: self.risk_limit_set.risk_limit_set_id,
            AvailabilitySubjectKind.RESERVATION_READ_SET: (
                None
                if self.reservation_read_set is None
                else derive_reservation_read_set_id(self.reservation_read_set)
            ),
            AvailabilitySubjectKind.EXPOSURE: (
                None if self.canonical_exposure is None else self.canonical_exposure.exposure_id
            ),
            AvailabilitySubjectKind.MARKET_DATA: (
                None if self.market_data is None else self.market_data.market_data_id
            ),
            AvailabilitySubjectKind.MARKET_OBSERVATION: (
                None
                if self.market_observation is None
                else self.market_observation.observation_id
            ),
        }

        for kind, expected_id in expected_subjects.items():
            matching = [item for item in bindings if item.subject_kind is kind]
            if expected_id is None:
                if matching:
                    raise RiskEvaluationContextError(
                        f"availability binding {kind.value} has no matching snapshot"
                    )
                continue
            if matching and (len(matching) != 1 or matching[0].subject_id != expected_id):
                raise RiskEvaluationContextError(
                    f"availability binding {kind.value} does not match its snapshot"
                )

        binding_by_kind = {item.subject_kind: item for item in bindings}
        market_data_binding = binding_by_kind.get(AvailabilitySubjectKind.MARKET_DATA)
        if market_data_binding is not None and self.market_data is not None:
            if (
                self.market_data.available_at is not None
                and self.market_data.available_at != market_data_binding.available_at
            ):
                raise RiskEvaluationContextError(
                    "MARKET_DATA availability binding disagrees with MarketData.available_at"
                )

        observation_binding = binding_by_kind.get(AvailabilitySubjectKind.MARKET_OBSERVATION)
        if observation_binding is not None and self.market_observation is not None:
            if observation_binding.available_at != self.market_observation.available_timestamp:
                raise RiskEvaluationContextError(
                    "MARKET_OBSERVATION availability binding disagrees with available_timestamp"
                )

        reasons: list[str] = []
        required_subjects = {
            AvailabilitySubjectKind.ACCOUNT_STATE,
            AvailabilitySubjectKind.RISK_LIMIT_SET,
        }
        if self.reservation_read_set is not None:
            required_subjects.add(AvailabilitySubjectKind.RESERVATION_READ_SET)
        if self.canonical_exposure is not None:
            required_subjects.add(AvailabilitySubjectKind.EXPOSURE)
        if self.market_data is not None:
            required_subjects.add(AvailabilitySubjectKind.MARKET_DATA)
        if self.market_observation is not None:
            required_subjects.add(AvailabilitySubjectKind.MARKET_OBSERVATION)

        for kind in sorted(required_subjects, key=lambda item: item.value):
            expected_id = expected_subjects[kind]
            if not any(item.subject_kind is kind and item.subject_id == expected_id for item in bindings):
                reasons.append(f"{kind.value}_AVAILABILITY_BINDING_MISSING")

        if self.risk_limit_resolution is None:
            reasons.append("RISK_LIMIT_RESOLUTION_MISSING")
        elif self.risk_limit_resolution.status in {
            RiskLimitResolutionStatus.LIMITS_UNAVAILABLE,
            RiskLimitResolutionStatus.LIMITS_INCOMPLETE,
        }:
            reasons.append(
                f"RISK_LIMIT_RESOLUTION_{self.risk_limit_resolution.status.value}"
            )

        if self.canonical_account_state.completeness is not Completeness.COMPLETE:
            reasons.append("ACCOUNT_STATE_INCOMPLETE")
        if self.canonical_position is not None and self.canonical_position.completeness is not Completeness.COMPLETE:
            reasons.append("POSITION_INCOMPLETE")
        if self.canonical_exposure is not None and self.canonical_exposure.completeness is not Completeness.COMPLETE:
            reasons.append("EXPOSURE_INCOMPLETE")
        if self.reservation_read_set is not None and self.reservation_read_set.completeness is not Completeness.COMPLETE:
            reasons.append("RESERVATION_READ_SET_INCOMPLETE")
        if self.effective_capacity is not None and self.effective_capacity.completeness is not Completeness.COMPLETE:
            reasons.append("EFFECTIVE_CAPACITY_INCOMPLETE")
        if self.market_data is not None:
            if self.market_data.available_at is None:
                reasons.append("MARKET_DATA_AVAILABILITY_UNKNOWN")
            if self.market_data.quality.value != "VALID":
                reasons.append("MARKET_DATA_QUALITY_NOT_VALID")
            if self.market_data.completeness.value != "COMPLETE":
                reasons.append("MARKET_DATA_INCOMPLETE")

        reasons_tuple = tuple(sorted(set(reasons)))
        object.__setattr__(self, "incompleteness_reasons", reasons_tuple)
        object.__setattr__(
            self,
            "completeness",
            RiskEvaluationContextStatus.COMPLETE
            if not reasons_tuple
            else RiskEvaluationContextStatus.INCOMPLETE,
        )

        identity_payload = {
            "version": "1.0",
            "trade_proposal": _canonical_trade_proposal(self.trade_proposal),
            "instrument": _canonical_instrument(self.instrument),
            "canonical_account_state": _canonical_account_state(self.canonical_account_state),
            "risk_limit_set": _canonical_risk_limit_set(self.risk_limit_set),
            "risk_limit_resolution": _canonical_risk_limit_resolution(self.risk_limit_resolution),
            "evaluation_timestamp": _canonical_datetime(self.evaluation_timestamp),
            "availability_bindings": [
                _canonical_binding(item)
                for item in sorted(bindings, key=lambda item: (item.subject_kind.value, item.subject_id))
            ],
            "canonical_position": None if self.canonical_position is None else _canonical_position(self.canonical_position),
            "canonical_exposure": None if self.canonical_exposure is None else _canonical_exposure(self.canonical_exposure),
            "reservation_read_set": None if self.reservation_read_set is None else _canonical_reservation_read_set(self.reservation_read_set),
            "effective_capacity": None if self.effective_capacity is None else _canonical_effective_capacity(self.effective_capacity),
            "market_data": None if self.market_data is None else _canonical_market_data(self.market_data),
            "market_observation": None if self.market_observation is None else _canonical_market_observation(self.market_observation),
            "analysis_result": None if self.analysis_result is None else _canonical_analysis_result(self.analysis_result),
            "signal": None if self.signal is None else _canonical_signal(self.signal),
        }
        object.__setattr__(
            self,
            "evaluation_context_id",
            f"risk-evaluation-context-v1:{_sha256(identity_payload)}",
        )

    @property
    def proposal_id(self) -> str:
        return self.trade_proposal.proposal_id

    @property
    def signal_id(self) -> str:
        return self.trade_proposal.signal_id

    @property
    def correlation_id(self) -> str:
        return self.trade_proposal.correlation_id

    @property
    def decision_timestamp(self) -> datetime:
        return self.trade_proposal.decision_timestamp

    @property
    def is_complete(self) -> bool:
        return self.completeness is RiskEvaluationContextStatus.COMPLETE

    def require_complete(self) -> None:
        if not self.is_complete:
            reason = ",".join(self.incompleteness_reasons) or "UNKNOWN"
            raise RiskEvaluationContextError(
                f"RiskEvaluationContext is incomplete: {reason}"
            )


__all__ = [
    "RiskEvaluationContext",
    "RiskEvaluationContextError",
    "RiskEvaluationContextStatus",
]
