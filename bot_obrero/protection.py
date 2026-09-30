from dataclasses import dataclass
from .murphy import ProtectionState

@dataclass
class PositionProtection:
    state: ProtectionState=ProtectionState.POSITION_OPEN
    def submit(self): self.state=ProtectionState.PROTECTION_PENDING
    def confirm(self): self.state=ProtectionState.PROTECTION_CONFIRMED
    def unknown(self): self.state=ProtectionState.PROTECTION_UNKNOWN
    def unprotected(self): self.state=ProtectionState.UNPROTECTED
    def safe(self): return self.state is ProtectionState.PROTECTION_CONFIRMED
