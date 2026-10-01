from dataclasses import dataclass

@dataclass(frozen=True)
class ApiPermissions:
    read:bool
    trade:bool
    withdraw:bool
    def safe_for_trading(self):
        return self.read and self.trade and not self.withdraw
