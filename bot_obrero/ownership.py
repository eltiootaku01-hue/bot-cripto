from dataclasses import dataclass
from enum import Enum

class OwnershipState(str, Enum):
    OWNED="OWNED"; UNKNOWN="UNKNOWN"; CONFLICT="CONFLICT"

@dataclass(frozen=True)
class AccountPosition:
    symbol: str
    quantity: float
    account_id: str | None = None

@dataclass
class OwnershipBook:
    exposures: dict[tuple[str,str],float]
    account_id: str | None = None
    instance_id: str | None = None
    position_owner: dict[str,str] | None = None

    def __post_init__(self):
        if self.position_owner is None:
            self.position_owner = {}

    def exposure(self,strategy_id,symbol):
        return self.exposures.get((strategy_id,symbol),0.0)

    def set_exposure(self,strategy_id,symbol,quantity):
        self.exposures[(strategy_id,symbol)]=quantity

    def resolve(self, symbol):
        owner = self.position_owner.get(symbol)
        return (OwnershipState.OWNED, owner) if owner else (OwnershipState.UNKNOWN, None)

    def register_owner(self, symbol, owner_id):
        current = self.position_owner.get(symbol)
        if current is not None and current != owner_id:
            return OwnershipState.CONFLICT
        self.position_owner[symbol] = owner_id
        return OwnershipState.OWNED
