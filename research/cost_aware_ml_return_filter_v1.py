"""Research-only, cost-aware BTC return filter.

This module never connects to an exchange or submits orders. It accepts explicit
candle/quote evidence, trains only when called by an operator, and supports a
deterministic, quote-aware virtual replay. It is not an execution adapter.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

SCHEMA_VERSION = 2
MODEL_FORMAT = "xgboost-native-json-v1"
HORIZON_HOURS = 24
MIN_TRAIN_ROWS = 1000
INITIAL_CAPITAL_USDT = Decimal("100")
POSITION_FRACTION = Decimal("0.95")
ENTRY_COST_MULTIPLIER = Decimal("1.5")
MAX_QUOTE_AGE_SECONDS = Decimal("2")
SLIPPAGE_BPS_BASE = Decimal("5")
MAX_QUOTE_AGE = pd.Timedelta(seconds=float(MAX_QUOTE_AGE_SECONDS))
FEATURE_LAGS = (1, 3, 6, 12, 24, 72, 168)
VOLATILITY_WINDOWS = (24, 72, 168)

EXPECTED_VERSIONS = {
    "xgboost": "3.4.1",
    "numpy": "2.3.5",
    "pandas": "2.2.3",
    "scipy": "1.16.3",
}
FEATURE_COLUMNS = tuple(
    [f"log_return_{lag}h" for lag in FEATURE_LAGS]
    + [f"volatility_{window}h" for window in VOLATILITY_WINDOWS]
    + [
        "range_pct",
        "range_mean_24h",
        "candle_body_log",
        "close_location",
        "range_zero",
        "log_volume",
        "volume_vs_median_168h",
    ]
)
FEATURE_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(
        {"schema_version": SCHEMA_VERSION, "feature_columns": FEATURE_COLUMNS},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()

FIXED_MODEL_PARAMETERS: dict[str, Any] = {
    "objective": "reg:squarederror",
    "n_estimators": 300,
    "max_depth": 3,
    "learning_rate": 0.03,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 10,
    "reg_lambda": 10,
    "reg_alpha": 0,
    "gamma": 0,
    "tree_method": "hist",
    "random_state": 20261021,
    "n_jobs": 1,
    "verbosity": 0,
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_CANDLE_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
_QUOTE_COLUMNS = ("received_at", "available_at", "bid", "ask", "symbol")


class ResearchContractError(ValueError):
    """A research input, artifact, or economic contract failed validation."""


@dataclass(frozen=True)
class TrainingSet:
    features: pd.DataFrame
    target: pd.Series
    decision_at: pd.Series
    label_available_at: pd.Series


@dataclass(frozen=True)
class InstrumentFilters:
    min_quantity: Decimal
    quantity_step: Decimal
    min_notional: Decimal

    def __post_init__(self) -> None:
        values = {
            "min_quantity": _decimal(self.min_quantity, "min_quantity"),
            "quantity_step": _decimal(self.quantity_step, "quantity_step"),
            "min_notional": _decimal(self.min_notional, "min_notional"),
        }
        if values["min_quantity"] < 0 or values["min_notional"] < 0:
            raise ResearchContractError("FILTER_LIMITS_MUST_BE_NONNEGATIVE")
        if values["quantity_step"] <= 0:
            raise ResearchContractError("QUANTITY_STEP_MUST_BE_POSITIVE")
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class CostScenario:
    name: str
    fee_rate: Decimal
    slippage_bps_per_side: Decimal

    def __post_init__(self) -> None:
        fee = _decimal(self.fee_rate, "fee_rate")
        slippage = _decimal(self.slippage_bps_per_side, "slippage_bps_per_side")
        if not self.name or not (Decimal("0") <= fee < Decimal("1")):
            raise ResearchContractError("COST_SCENARIO_INVALID")
        if slippage < 0:
            raise ResearchContractError("SLIPPAGE_MUST_BE_NONNEGATIVE")
        object.__setattr__(self, "fee_rate", fee)
        object.__setattr__(self, "slippage_bps_per_side", slippage)


COST_SCENARIOS = {
    "BASE": CostScenario("BASE", Decimal("0.001"), Decimal("5")),
    "STRESS_1": CostScenario("STRESS_1", Decimal("0.001"), Decimal("10")),
    "STRESS_2": CostScenario("STRESS_2", Decimal("0.002"), Decimal("20")),
}


def _decimal(value: Any, label: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise ResearchContractError(f"{label.upper()}_NOT_DECIMAL") from exc
    if not result.is_finite():
        raise ResearchContractError(f"{label.upper()}_NOT_FINITE")
    return result


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ResearchContractError(f"{label.upper()}_SHA256_INVALID")
    return value


def _utc_timestamp(value: Any, label: str) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value)
    except Exception as exc:
        raise ResearchContractError(f"{label.upper()}_TIMESTAMP_INVALID") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ResearchContractError(f"{label.upper()}_TIMESTAMP_MUST_BE_AWARE")
    return stamp.tz_convert("UTC")


def _timestamp_series(values: Sequence[Any], label: str) -> pd.Series:
    return pd.Series(
        [_utc_timestamp(value, label) for value in values],
        dtype="datetime64[ns, UTC]",
    )


def require_pinned_runtime() -> dict[str, str]:
    """Fail closed unless the isolated, documented research runtime is active."""
    if sys.version_info[:2] != (3, 13):
        raise ResearchContractError("PYTHON_313_REQUIRED_FOR_FROZEN_ML_V1")
    actual = {
        package: importlib.metadata.version(package)
        for package in EXPECTED_VERSIONS
    }
    mismatches = {
        package: {"expected": expected, "actual": actual.get(package)}
        for package, expected in EXPECTED_VERSIONS.items()
        if actual.get(package) != expected
    }
    if mismatches:
        raise ResearchContractError(
            "RESEARCH_DEPENDENCY_VERSION_MISMATCH:"
            + json.dumps(mismatches, sort_keys=True)
        )
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        **actual,
    }


def validate_candles(candles: pd.DataFrame) -> pd.DataFrame:
    """Return a defensive normalized copy of complete, gap-free hourly candles.

    timestamp denotes candle-open time. Rows must be UTC-aware, unique,
    contiguous, on whole-hour boundaries, and internally consistent.
    """
    if not isinstance(candles, pd.DataFrame):
        raise ResearchContractError("CANDLES_MUST_BE_DATAFRAME")
    missing = [column for column in _CANDLE_COLUMNS if column not in candles.columns]
    if missing:
        raise ResearchContractError("CANDLE_COLUMNS_MISSING:" + ",".join(missing))
    if len(candles) < 1:
        raise ResearchContractError("CANDLE_DATA_EMPTY")

    result = candles.loc[:, list(_CANDLE_COLUMNS)].copy().reset_index(drop=True)
    timestamps = _timestamp_series(result["timestamp"].tolist(), "candle")
    if any(
        stamp.minute != 0 or stamp.second != 0 or stamp.microsecond != 0
        for stamp in timestamps
    ):
        raise ResearchContractError("CANDLE_TIMESTAMP_NOT_HOUR_ALIGNED")
    timestamp_deltas = timestamps.diff().iloc[1:]
    if any(delta != pd.Timedelta(hours=1) for delta in timestamp_deltas):
        raise ResearchContractError("CANDLE_TIMESTAMPS_NOT_STRICTLY_HOURLY")
    result["timestamp"] = timestamps

    for column in ("open", "high", "low", "close", "volume"):
        try:
            result[column] = pd.to_numeric(result[column], errors="raise").astype("float64")
        except Exception as exc:
            raise ResearchContractError(f"CANDLE_{column.upper()}_NOT_NUMERIC") from exc
        values = result[column].to_numpy(dtype=np.float64)
        if not np.isfinite(values).all():
            raise ResearchContractError(f"CANDLE_{column.upper()}_NOT_FINITE")

    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise ResearchContractError("CANDLE_PRICE_MUST_BE_POSITIVE")
    if (result["volume"] < 0).any():
        raise ResearchContractError("CANDLE_VOLUME_MUST_BE_NONNEGATIVE")
    if (
        (result["high"] < result[["open", "close", "low"]].max(axis=1))
        | (result["low"] > result[["open", "close", "high"]].min(axis=1))
    ).any():
        raise ResearchContractError("CANDLE_OHLC_INCONSISTENT")
    return result


def canonical_dataset_sha256(candles: pd.DataFrame) -> str:
    """Hash the exact normalized hourly candle values consumed by this module.

    This is a logical-data hash, not a digest of source CSV/Parquet bytes.
    The manifest names the contract to distinguish these identities.
    """
    frame = validate_candles(candles)
    rows: list[list[str]] = []
    for row in frame.itertuples(index=False, name=None):
        rows.append([
            row[0].isoformat(),
            *(format(float(value), ".17g") for value in row[1:]),
        ])
    payload = {
        "contract": "canonical-normalized-hourly-candles-v1",
        "columns": list(_CANDLE_COLUMNS),
        "rows": rows,
    }
    return _sha256_bytes(_canonical_json(payload))


def compute_features(candles: pd.DataFrame) -> pd.DataFrame:
    """Build the fixed causal feature vector using only the current/past rows."""
    frame = validate_candles(candles)
    close = frame["close"]
    open_price = frame["open"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]
    features = pd.DataFrame({"timestamp": frame["timestamp"]})

    for lag in FEATURE_LAGS:
        features[f"log_return_{lag}h"] = np.log(close / close.shift(lag))

    one_hour_log_return = np.log(close / close.shift(1))
    for window in VOLATILITY_WINDOWS:
        features[f"volatility_{window}h"] = one_hour_log_return.rolling(
            window=window, min_periods=window
        ).std(ddof=1)

    price_range = high - low
    range_zero = price_range.eq(0.0)
    features["range_pct"] = price_range / close
    features["range_mean_24h"] = features["range_pct"].rolling(
        window=24, min_periods=24
    ).mean()
    features["candle_body_log"] = np.log(close / open_price)
    features["close_location"] = ((close - low) / price_range).where(~range_zero, np.nan)
    features["range_zero"] = range_zero.astype("float64")
    features["log_volume"] = np.log1p(volume)

    prior_median_volume = volume.shift(1).rolling(window=168, min_periods=168).median()
    features["volume_vs_median_168h"] = (volume / prior_median_volume).where(
        prior_median_volume > 0, np.nan
    )
    return features.loc[:, ["timestamp", *FEATURE_COLUMNS]]


def _feature_ready_mask(features: pd.DataFrame) -> np.ndarray:
    other_features = features.loc[
        :, [column for column in FEATURE_COLUMNS if column != "close_location"]
    ].to_numpy(dtype=np.float64)
    finite_others = np.isfinite(other_features).all(axis=1)
    location_ok = (
        features["close_location"].notna()
        | features["range_zero"].eq(1.0)
    ).to_numpy()
    return finite_others & location_ok


def build_training_set(candles: pd.DataFrame, training_cutoff: Any) -> TrainingSet:
    """Build chronologically ordered rows whose 24 h labels matured by cutoff.

    Candle timestamps are open-times. The label for row t uses close[t+24].
    That target candle closes at timestamp[t] + 25 h; such availability must
    be at or before the supplied UTC cutoff. The final 24 h are purged.
    """
    cutoff = _utc_timestamp(training_cutoff, "training_cutoff")
    if cutoff.minute != 0 or cutoff.second != 0 or cutoff.microsecond != 0:
        raise ResearchContractError("TRAINING_CUTOFF_NOT_HOUR_ALIGNED")
    frame = validate_candles(candles)
    features = compute_features(frame)
    close = frame["close"]
    target = close.shift(-HORIZON_HOURS) / close - 1.0
    label_available_at = frame["timestamp"] + pd.Timedelta(hours=HORIZON_HOURS + 1)
    decision_at = frame["timestamp"] + pd.Timedelta(hours=1)

    eligible = (
        _feature_ready_mask(features)
        & target.notna().to_numpy()
        & np.isfinite(target.fillna(0.0).to_numpy(dtype=np.float64))
        & (label_available_at <= cutoff).to_numpy()
    )
    x = features.loc[eligible, list(FEATURE_COLUMNS)].reset_index(drop=True)
    y = target.loc[eligible].astype("float64").reset_index(drop=True)
    decisions = decision_at.loc[eligible].reset_index(drop=True)
    label_times = label_available_at.loc[eligible].reset_index(drop=True)
    if len(x) < MIN_TRAIN_ROWS:
        raise ResearchContractError(
            f"TRAINING_ROWS_BELOW_MINIMUM:{len(x)}<{MIN_TRAIN_ROWS}"
        )
    if not (label_times <= cutoff).all():
        raise ResearchContractError("TRAINING_LABEL_CROSSES_CUTOFF")
    if not np.isfinite(y.to_numpy(dtype=np.float64)).all():
        raise ResearchContractError("TRAINING_TARGET_NOT_FINITE")
    return TrainingSet(
        features=x,
        target=y,
        decision_at=decisions,
        label_available_at=label_times,
    )


def make_regressor() -> XGBRegressor:
    """Return the one frozen CPU-only model configuration; no tuning hooks."""
    return XGBRegressor(**FIXED_MODEL_PARAMETERS)


def _training_rows_sha256(training: TrainingSet) -> str:
    rows: list[Any] = []
    for i in range(len(training.features)):
        feature_values: list[str | None] = []
        for value in training.features.iloc[i].tolist():
            feature_values.append(
                None if pd.isna(value) else format(float(value), ".17g")
            )
        rows.append(
            [
                training.decision_at.iloc[i].isoformat(),
                training.label_available_at.iloc[i].isoformat(),
                feature_values,
                format(float(training.target.iloc[i]), ".17g"),
            ]
        )
    return _sha256_bytes(_canonical_json(rows))


def _manifest_hash(payload: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(dict(payload)))


def train_and_save_model(
    candles: pd.DataFrame,
    *,
    training_cutoff: Any,
    source_dataset_sha256: str,
    git_commit_sha: str,
    model_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Train once from mature historical labels and persist a hash-bound bundle.

    This is an explicit operator-called research function. It must not be
    called with the future evaluation segment. No fit is performed on import.
    """
    source_hash = _require_sha256(source_dataset_sha256, "source_dataset")
    cutoff = _utc_timestamp(training_cutoff, "training_cutoff")
    normalized_candles = validate_candles(candles)
    # Training input may end at the last candle that closes at the cutoff;
    # later candles are forbidden even if their labels would eventually be purged.
    if normalized_candles["timestamp"].iloc[-1] + pd.Timedelta(hours=1) > cutoff:
        raise ResearchContractError("TRAINING_INPUT_EXTENDS_BEYOND_CUTOFF")
    actual_source_hash = canonical_dataset_sha256(normalized_candles)
    if source_hash != actual_source_hash:
        raise ResearchContractError("SOURCE_DATASET_HASH_MISMATCH")
    runtime = require_pinned_runtime()
    if not isinstance(git_commit_sha, str) or not _GIT_SHA_RE.fullmatch(git_commit_sha):
        raise ResearchContractError("GIT_COMMIT_SHA_INVALID")
    model_file = Path(model_path)
    manifest_file = Path(manifest_path)
    if model_file.suffix.lower() not in {".json", ".ubj"}:
        raise ResearchContractError("MODEL_FORMAT_MUST_BE_NATIVE_JSON_OR_UBJ")
    if model_file.resolve() == manifest_file.resolve():
        raise ResearchContractError("MODEL_AND_MANIFEST_PATH_COLLISION")
    if model_file.exists() or manifest_file.exists():
        raise ResearchContractError("MODEL_OUTPUT_ALREADY_EXISTS")

    training = build_training_set(normalized_candles, cutoff)
    model = make_regressor()
    model.fit(training.features, training.target)
    prediction = model.predict(training.features)
    if not np.isfinite(np.asarray(prediction, dtype=np.float64)).all():
        raise ResearchContractError("TRAINED_MODEL_PREDICTION_NOT_FINITE")

    model_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    model_tmp = model_file.with_name(f".{model_file.stem}.tmp{model_file.suffix}")
    manifest_tmp = manifest_file.with_name(f".{manifest_file.name}.tmp")
    published_model = False
    published_manifest = False
    try:
        model.save_model(model_tmp)
        model_hash = _sha256_file(model_tmp)
        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "model_format": MODEL_FORMAT,
            "model_file": model_file.name,
            "model_sha256": model_hash,
            "source_dataset_sha256": source_hash,
            "source_dataset_hash_contract": "canonical-normalized-hourly-candles-v1",
            "training_rows_sha256": _training_rows_sha256(training),
            "source_module_sha256": _sha256_file(Path(__file__)),
            "git_commit_sha": git_commit_sha,
            "feature_schema_sha256": FEATURE_SCHEMA_SHA256,
            "feature_columns": list(FEATURE_COLUMNS),
            "model_parameters": dict(FIXED_MODEL_PARAMETERS),
            "target": "close[t+24h] / close[t] - 1",
            "horizon_hours": HORIZON_HOURS,
            "training_cutoff_utc": _utc_timestamp(
                training_cutoff, "training_cutoff"
            ).isoformat(),
            "training_rows": int(len(training.features)),
            "first_decision_at": training.decision_at.iloc[0].isoformat(),
            "last_decision_at": training.decision_at.iloc[-1].isoformat(),
            "last_label_available_at": training.label_available_at.iloc[-1].isoformat(),
            "runtime": runtime,
            "determinism": {
                "random_state": FIXED_MODEL_PARAMETERS["random_state"],
                "n_jobs": 1,
                "tree_method": "hist",
            },
        }
        manifest["manifest_payload_sha256"] = _manifest_hash(manifest)
        model_tmp.replace(model_file)
        published_model = True
        manifest_tmp.write_bytes(_canonical_json(manifest) + b"\n")
        manifest_tmp.replace(manifest_file)
        published_manifest = True
    except Exception:
        # The manifest is the completeness marker. Remove final artifacts created
        # by this invocation if its publication did not complete.
        if published_manifest:
            manifest_file.unlink(missing_ok=True)
        if published_model:
            model_file.unlink(missing_ok=True)
        for path in (model_tmp, manifest_tmp):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    return manifest


def load_frozen_model(
    model_path: str | Path, manifest_path: str | Path
) -> tuple[XGBRegressor, dict[str, Any]]:
    """Load only a native model whose manifest, schema, versions and digest match."""
    runtime = require_pinned_runtime()
    model_file, manifest_file = Path(model_path), Path(manifest_path)
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ResearchContractError("MODEL_MANIFEST_UNREADABLE") from exc
    payload = dict(manifest)
    claimed_manifest_hash = payload.pop("manifest_payload_sha256", None)
    if not isinstance(claimed_manifest_hash, str) or _manifest_hash(payload) != claimed_manifest_hash:
        raise ResearchContractError("MODEL_MANIFEST_HASH_MISMATCH")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ResearchContractError("MODEL_MANIFEST_SCHEMA_UNSUPPORTED")
    if manifest.get("model_format") != MODEL_FORMAT:
        raise ResearchContractError("MODEL_FORMAT_UNSUPPORTED")
    if manifest.get("feature_columns") != list(FEATURE_COLUMNS):
        raise ResearchContractError("MODEL_FEATURE_SCHEMA_MISMATCH")
    if manifest.get("feature_schema_sha256") != FEATURE_SCHEMA_SHA256:
        raise ResearchContractError("MODEL_FEATURE_SCHEMA_HASH_MISMATCH")
    if manifest.get("source_dataset_hash_contract") != "canonical-normalized-hourly-candles-v1":
        raise ResearchContractError("MODEL_SOURCE_DATASET_HASH_CONTRACT_MISMATCH")
    if manifest.get("model_parameters") != FIXED_MODEL_PARAMETERS:
        raise ResearchContractError("MODEL_PARAMETERS_MISMATCH")
    if manifest.get("runtime") != runtime:
        raise ResearchContractError("MODEL_RUNTIME_MISMATCH")
    if manifest.get("model_file") != model_file.name:
        raise ResearchContractError("MODEL_FILENAME_MISMATCH")
    if _sha256_file(model_file) != manifest.get("model_sha256"):
        raise ResearchContractError("MODEL_FILE_HASH_MISMATCH")
    if _sha256_file(Path(__file__)) != manifest.get("source_module_sha256"):
        raise ResearchContractError("MODEL_SOURCE_MODULE_HASH_MISMATCH")
    _require_sha256(manifest.get("source_dataset_sha256", ""), "source_dataset")
    _require_sha256(manifest.get("training_rows_sha256", ""), "training_rows")
    commit_sha = manifest.get("git_commit_sha", "")
    if not isinstance(commit_sha, str) or not _GIT_SHA_RE.fullmatch(commit_sha):
        raise ResearchContractError("MODEL_GIT_COMMIT_SHA_INVALID")

    model = make_regressor()
    try:
        model.load_model(model_file)
    except Exception as exc:
        raise ResearchContractError("MODEL_LOAD_FAILED") from exc
    names = model.get_booster().feature_names
    if names is not None and names != list(FEATURE_COLUMNS):
        raise ResearchContractError("MODEL_LOADED_FEATURES_MISMATCH")
    return model, manifest


def predict_returns(model: XGBRegressor, candles: pd.DataFrame) -> pd.DataFrame:
    """Predict causal 24 h gross returns for rows with a complete feature vector."""
    frame = validate_candles(candles)
    features = compute_features(frame)
    ready = _feature_ready_mask(features)
    x = features.loc[ready, list(FEATURE_COLUMNS)].reset_index(drop=True)
    timestamps = features.loc[ready, "timestamp"].reset_index(drop=True)
    if not len(x):
        raise ResearchContractError("NO_FEATURE_READY_ROWS")
    names = model.get_booster().feature_names
    if names is not None and names != list(FEATURE_COLUMNS):
        raise ResearchContractError("PREDICT_MODEL_FEATURE_SCHEMA_MISMATCH")
    values = np.asarray(model.predict(x), dtype=np.float64)
    if values.shape != (len(x),) or not np.isfinite(values).all():
        raise ResearchContractError("PREDICTION_OUTPUT_INVALID")
    return pd.DataFrame(
        {
            "decision_at": timestamps + pd.Timedelta(hours=1),
            "predicted_return": values,
        }
    )


def estimated_round_trip_cost(
    bid: Any, ask: Any, scenario: CostScenario
) -> Decimal:
    """Estimate round-trip friction from observed spread plus fixed fee/slippage."""
    best_bid, best_ask = _decimal(bid, "bid"), _decimal(ask, "ask")
    if best_bid <= 0 or best_ask < best_bid:
        raise ResearchContractError("QUOTE_BID_ASK_INVALID")
    midpoint = (best_bid + best_ask) / Decimal("2")
    spread_fraction = (best_ask - best_bid) / midpoint
    slippage_fraction = scenario.slippage_bps_per_side / Decimal("10000")
    return spread_fraction + (Decimal("2") * scenario.fee_rate) + (
        Decimal("2") * slippage_fraction
    )


def should_enter(
    predicted_return: Any,
    bid: Any,
    ask: Any,
    scenario: CostScenario,
) -> bool:
    """Apply the fixed 1.5x round-trip-cost gate; no parameter search is exposed."""
    predicted = _decimal(predicted_return, "predicted_return")
    cost = estimated_round_trip_cost(bid, ask, scenario)
    return predicted > ENTRY_COST_MULTIPLIER * cost


def _normalize_predictions(predictions: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(predictions, pd.DataFrame):
        raise ResearchContractError("PREDICTIONS_MUST_BE_DATAFRAME")
    if predictions.empty:
        raise ResearchContractError("PREDICTIONS_EMPTY")
    if not {"decision_at", "predicted_return"}.issubset(predictions.columns):
        raise ResearchContractError("PREDICTION_COLUMNS_MISSING")
    result = predictions.loc[:, ["decision_at", "predicted_return"]].copy().reset_index(drop=True)
    times = _timestamp_series(result["decision_at"].tolist(), "decision")
    if any(
        stamp.minute != 0 or stamp.second != 0 or stamp.microsecond != 0
        for stamp in times
    ):
        raise ResearchContractError("DECISION_TIMESTAMP_NOT_HOUR_ALIGNED")
    if any(delta != pd.Timedelta(hours=1) for delta in times.diff().iloc[1:]):
        raise ResearchContractError("DECISION_TIMESTAMPS_NOT_STRICTLY_HOURLY")
    try:
        values = pd.to_numeric(result["predicted_return"], errors="raise").astype("float64")
    except Exception as exc:
        raise ResearchContractError("PREDICTION_NOT_NUMERIC") from exc
    if not np.isfinite(values.to_numpy()).all():
        raise ResearchContractError("PREDICTION_NOT_FINITE")
    result["decision_at"] = times
    result["predicted_return"] = values
    return result


def _normalize_quotes(quotes: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(quotes, pd.DataFrame):
        raise ResearchContractError("QUOTES_MUST_BE_DATAFRAME")
    missing = [column for column in _QUOTE_COLUMNS if column not in quotes.columns]
    if missing:
        raise ResearchContractError("QUOTE_COLUMNS_MISSING:" + ",".join(missing))
    result = quotes.loc[:, list(_QUOTE_COLUMNS)].copy().reset_index(drop=True)
    if not result["symbol"].map(lambda value: isinstance(value, str) and value == "BTCUSDT").all():
        raise ResearchContractError("QUOTES_MUST_BE_BTCUSDT_SPOT")
    result["received_at"] = _timestamp_series(result["received_at"].tolist(), "quote_received")
    result["available_at"] = _timestamp_series(result["available_at"].tolist(), "quote_available")
    if (result["received_at"] > result["available_at"]).any():
        raise ResearchContractError("QUOTE_AVAILABLE_BEFORE_RECEIVE")
    for column in ("bid", "ask"):
        try:
            result[column] = result[column].map(lambda value: _decimal(value, column))
        except Exception as exc:
            raise ResearchContractError(f"QUOTE_{column.upper()}_INVALID") from exc
    for bid, ask in zip(result["bid"], result["ask"]):
        if bid <= 0 or ask < bid:
            raise ResearchContractError("QUOTE_BID_ASK_INVALID")
    times = result["available_at"]
    if any(delta <= pd.Timedelta(0) for delta in times.diff().iloc[1:]):
        raise ResearchContractError("QUOTE_AVAILABLE_TIMESTAMPS_NOT_STRICT")
    if result.empty:
        raise ResearchContractError("QUOTE_DATA_EMPTY")
    return result


def _fresh_quote_after(quotes: pd.DataFrame, instant: pd.Timestamp) -> int:
    nanoseconds = quotes["available_at"].astype("int64").to_numpy()
    index = int(np.searchsorted(nanoseconds, instant.value, side="right"))
    if index >= len(quotes):
        raise ResearchContractError("QUOTE_UNAVAILABLE_AFTER_DECISION")
    row = quotes.iloc[index]
    if (
        row["available_at"] - instant > MAX_QUOTE_AGE
        or row["received_at"] <= instant
    ):
        raise ResearchContractError("QUOTE_AFTER_DECISION_STALE_OR_BACKDATED")
    return index


def _fresh_quote_at_or_after(quotes: pd.DataFrame, instant: pd.Timestamp) -> int:
    nanoseconds = quotes["available_at"].astype("int64").to_numpy()
    index = int(np.searchsorted(nanoseconds, instant.value, side="left"))
    if index >= len(quotes):
        raise ResearchContractError("EXIT_QUOTE_UNAVAILABLE")
    row = quotes.iloc[index]
    if (
        row["available_at"] - instant > MAX_QUOTE_AGE
        or row["received_at"] < instant
    ):
        raise ResearchContractError("EXIT_QUOTE_STALE_OR_BACKDATED")
    return index


def _fresh_quote_before(quotes: pd.DataFrame, instant: pd.Timestamp) -> int:
    nanoseconds = quotes["available_at"].astype("int64").to_numpy()
    index = int(np.searchsorted(nanoseconds, instant.value, side="right")) - 1
    if index < 0:
        raise ResearchContractError("MARK_QUOTE_UNAVAILABLE")
    row = quotes.iloc[index]
    if (
        instant - row["available_at"] > MAX_QUOTE_AGE
        or row["received_at"] > instant
    ):
        raise ResearchContractError("MARK_QUOTE_STALE_OR_FUTURE")
    return index


def _execution_price(reference_price: Decimal, scenario: CostScenario, *, buy: bool) -> Decimal:
    slippage = scenario.slippage_bps_per_side / Decimal("10000")
    return reference_price * (Decimal("1") + slippage if buy else Decimal("1") - slippage)


def _rounded_quantity(raw_quantity: Decimal, filters: InstrumentFilters) -> Decimal:
    units = (raw_quantity / filters.quantity_step).to_integral_value(rounding=ROUND_FLOOR)
    return units * filters.quantity_step


def replay_predictions(
    predictions: pd.DataFrame,
    quotes: pd.DataFrame,
    *,
    evaluation_end_at: Any,
    filters: InstrumentFilters,
    scenario: CostScenario,
) -> dict[str, Any]:
    """Run a research-only, long/cash, one-position virtual replay.

    Quotes must be the ordered public BTCUSDT Spot bookTicker observations
    captured prospectively. Each entry/exit and every open-position hourly mark
    fails closed if no valid quote within two seconds is available.
    """
    if not isinstance(scenario, CostScenario):
        raise ResearchContractError("NON_PREREGISTERED_COST_SCENARIO")
    if scenario.name not in COST_SCENARIOS or scenario != COST_SCENARIOS[scenario.name]:
        raise ResearchContractError("NON_PREREGISTERED_COST_SCENARIO")
    signals = _normalize_predictions(predictions)
    quote_frame = _normalize_quotes(quotes)
    end_at = _utc_timestamp(evaluation_end_at, "evaluation_end")
    if end_at <= signals["decision_at"].iloc[-1]:
        raise ResearchContractError("EVALUATION_END_MUST_FOLLOW_LAST_DECISION")
    # Exclude post-boundary observations up front; no fill/mark selector below
    # can even inspect a quote whose availability lies beyond evaluation_end_at.
    window_quotes = quote_frame.loc[quote_frame["available_at"] <= end_at].reset_index(drop=True)
    if window_quotes.empty:
        raise ResearchContractError("EVALUATION_WINDOW_HAS_NO_QUOTES")

    cash = INITIAL_CAPITAL_USDT
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    total_fees = Decimal("0")
    skipped_filter_count = 0
    peak_equity = INITIAL_CAPITAL_USDT
    worst_drawdown_pct = Decimal("0")

    def close_position(quote_index: int, reason: str) -> None:
        nonlocal cash, position, total_fees
        if position is None:
            raise ResearchContractError("ATTEMPTED_EXIT_WITHOUT_POSITION")
        row = window_quotes.iloc[quote_index]
        exit_at = row["available_at"]
        exit_price = _execution_price(row["bid"], scenario, buy=False)
        exit_notional = position["quantity"] * exit_price
        exit_fee = exit_notional * scenario.fee_rate
        proceeds = exit_notional - exit_fee
        net_pnl = proceeds - position["entry_cash_spend"]
        cash += proceeds
        total_fees += exit_fee
        trades.append(
            {
                "entry_at": position["entry_at"].isoformat(),
                "exit_at": exit_at.isoformat(),
                "entry_price": str(position["entry_price"]),
                "exit_price": str(exit_price),
                "quantity": str(position["quantity"]),
                "entry_fee_usdt": str(position["entry_fee"]),
                "exit_fee_usdt": str(exit_fee),
                "total_fees_usdt": str(position["entry_fee"] + exit_fee),
                "net_pnl_usdt": str(net_pnl),
                "net_return_on_entry_spend_pct": str(
                    (net_pnl / position["entry_cash_spend"]) * Decimal("100")
                ),
                "exit_reason": reason,
            }
        )
        position = None

    def mark_equity(mark_at: pd.Timestamp) -> Decimal:
        if position is None:
            return cash
        quote_index = _fresh_quote_before(window_quotes, mark_at)
        mark_bid = quote_frame.iloc[quote_index]["bid"]
        liquidation_price = _execution_price(mark_bid, scenario, buy=False)
        liquidation_notional = position["quantity"] * liquidation_price
        liquidation_fee = liquidation_notional * scenario.fee_rate
        return cash + liquidation_notional - liquidation_fee

    def record_equity(mark_at: pd.Timestamp) -> None:
        nonlocal peak_equity, worst_drawdown_pct
        equity = mark_equity(mark_at)
        if equity > peak_equity:
            peak_equity = equity
        drawdown = (
            (equity / peak_equity - Decimal("1")) * Decimal("100")
            if peak_equity > 0 else Decimal("0")
        )
        worst_drawdown_pct = min(worst_drawdown_pct, drawdown)
        equity_curve.append(
            {"timestamp": mark_at.isoformat(), "equity_usdt": str(equity)}
        )

    for prediction in signals.itertuples(index=False):
        decision_at = prediction.decision_at
        if position is not None and position["exit_due_at"] <= decision_at:
            exit_index = _fresh_quote_at_or_after(
                window_quotes, position["exit_due_at"]
            )
            exit_available_at = window_quotes.iloc[exit_index]["available_at"]
            if exit_available_at <= decision_at:
                close_position(exit_index, "HORIZON_24H")

        record_equity(decision_at)

        if position is not None:
            continue

        try:
            entry_index = _fresh_quote_after(window_quotes, decision_at)
        except ResearchContractError as exc:
            if str(exc) == "QUOTE_UNAVAILABLE_AFTER_DECISION":
                raise ResearchContractError("EVALUATION_WINDOW_QUOTE_UNAVAILABLE") from exc
            raise
        entry_quote = window_quotes.iloc[entry_index]
        if entry_quote["available_at"] > end_at:
            raise ResearchContractError("EVALUATION_WINDOW_QUOTE_UNAVAILABLE")
        if not should_enter(
            prediction.predicted_return,
            entry_quote["bid"],
            entry_quote["ask"],
            scenario,
        ):
            continue

        entry_price = _execution_price(entry_quote["ask"], scenario, buy=True)
        raw_quantity = (
            cash * POSITION_FRACTION
            / (entry_price * (Decimal("1") + scenario.fee_rate))
        )
        quantity = _rounded_quantity(raw_quantity, filters)
        entry_notional = quantity * entry_price
        if quantity < filters.min_quantity or entry_notional < filters.min_notional:
            skipped_filter_count += 1
            continue
        entry_fee = entry_notional * scenario.fee_rate
        entry_cash_spend = entry_notional + entry_fee
        if quantity <= 0 or entry_cash_spend > cash:
            skipped_filter_count += 1
            continue

        cash -= entry_cash_spend
        total_fees += entry_fee
        position = {
            "entry_at": entry_quote["available_at"],
            "entry_price": entry_price,
            "quantity": quantity,
            "entry_fee": entry_fee,
            "entry_cash_spend": entry_cash_spend,
            "exit_due_at": entry_quote["available_at"] + pd.Timedelta(hours=HORIZON_HOURS),
        }

    if position is not None:
        if position["exit_due_at"] <= end_at:
            exit_index = _fresh_quote_at_or_after(window_quotes, position["exit_due_at"])
            exit_available_at = window_quotes.iloc[exit_index]["available_at"]
            if exit_available_at > end_at:
                raise ResearchContractError("HORIZON_EXIT_AFTER_EVALUATION_END")
            close_position(exit_index, "HORIZON_24H")
        else:
            # A terminal liquidation may only use the latest quote before the
            # boundary, and it must satisfy the same freshness contract.
            try:
                terminal_index = _fresh_quote_before(window_quotes, end_at)
            except ResearchContractError as exc:
                raise ResearchContractError("EVALUATION_END_QUOTE_UNAVAILABLE") from exc
            close_position(terminal_index, "TERMINAL_LIQUIDATION")

    final_equity = cash
    if not equity_curve or equity_curve[-1]["timestamp"] != end_at.isoformat():
        equity_curve.append(
            {"timestamp": end_at.isoformat(), "equity_usdt": str(final_equity)}
        )
    if final_equity > peak_equity:
        peak_equity = final_equity
    final_drawdown = (
        (final_equity / peak_equity - Decimal("1")) * Decimal("100")
        if peak_equity > 0 else Decimal("0")
    )
    worst_drawdown_pct = min(worst_drawdown_pct, final_drawdown)
    net_pnl = final_equity - INITIAL_CAPITAL_USDT
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy": "COST_AWARE_ML_RETURN_FILTER_V1",
        "operational_authorization": "NO_TRADE",
        "scenario": scenario.name,
        "initial_capital_usdt": str(INITIAL_CAPITAL_USDT),
        "final_equity_usdt": str(final_equity),
        "net_pnl_usdt": str(net_pnl),
        "return_pct": str((net_pnl / INITIAL_CAPITAL_USDT) * Decimal("100")),
        "max_drawdown_pct_sampled_hourly": str(worst_drawdown_pct),
        "drawdown_measurement": "hourly_decision_marks_plus_terminal_mark; not_intrahour_absolute_drawdown",
        "total_fees_usdt": str(total_fees),
        "round_trips": len(trades),
        "profitable_round_trips": sum(
            1 for trade in trades if Decimal(trade["net_pnl_usdt"]) > 0
        ),
        "skipped_for_instrument_filters": skipped_filter_count,
        "trades": trades,
        "equity_curve": equity_curve,
    }
