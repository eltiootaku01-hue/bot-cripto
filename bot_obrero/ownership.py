from dataclasses import dataclass

@dataclass(frozen=True)
class AccountPosition:
    symbol: str
    quantity: float

@dataclass
class OwnershipBook:
    exposures: dict[tuple[str,str],float]
    def exposure(self,strategy_id,symbol): return self.exposures.get((strategy_id,symbol),0.0)
    def set_exposure(self,strategy_id,symbol,quantity): self.exposures[(strategy_id,symbol)]=quantity
