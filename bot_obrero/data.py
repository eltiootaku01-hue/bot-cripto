from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class Candle:
    start: datetime
    end: datetime
    close: float
    closed: bool
    availability_timestamp: datetime | None = None

    def require_closed(self):
        if not self.closed:
            raise ValueError("INCOMPLETE_CANDLE")
        return self

    def available_at(self, decision_timestamp: datetime):
        if self.availability_timestamp is None:
            return False
        return self.availability_timestamp <= decision_timestamp

@dataclass(frozen=True)
class LiquidityModel:
    available_quantity: float
    complete: bool
    depth_source: str | None = None

    def validate(self, order_quantity):
        if not self.complete:
            raise ValueError("EXECUTION_MODEL_LIMITED")
        if order_quantity > self.available_quantity:
            raise ValueError("INSUFFICIENT_LIQUIDITY")

@dataclass(frozen=True)
class BacktestAssumptions:
    slippage_model: str | None = None
    spread_model: str | None = None
    fee_model: str | None = None
    funding_model: str | None = None
    latency_model: str | None = None
    partial_fill_model: str | None = None
    liquidity_model: str | None = None
    survivorship_controlled: bool = False
    leakage_controlled: bool = False
    lookahead_controlled: bool = False

    def validate(self):
        required = (self.slippage_model, self.spread_model, self.fee_model, self.latency_model, self.partial_fill_model, self.liquidity_model)
        if not all(required) or not (self.survivorship_controlled and self.leakage_controlled and self.lookahead_controlled):
            raise ValueError("BACKTEST_MODEL_INCOMPLETE")
        return True
