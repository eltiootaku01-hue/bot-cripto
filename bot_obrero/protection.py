from dataclasses import dataclass
from datetime import datetime
from .execution import EvidenceRecord
from .murphy import ProtectionState


@dataclass
class PositionProtection:
    state: ProtectionState = ProtectionState.POSITION_OPEN

    def submit(self):
        self.state = ProtectionState.PROTECTION_PENDING

    def confirm(self, evidence: EvidenceRecord, *, now: datetime):
        if evidence.kind != "protection_safe" or not evidence.valid_for(evidence.intent_id, now):
            self.state = ProtectionState.PROTECTION_UNKNOWN
            raise ValueError("PROTECTION_CONFIRMATION_REQUIRES_VALID_EVIDENCE")
        self.state = ProtectionState.PROTECTION_CONFIRMED

    def unknown(self):
        self.state = ProtectionState.PROTECTION_UNKNOWN

    def unprotected(self):
        self.state = ProtectionState.UNPROTECTED

    def safe(self):
        return self.state is ProtectionState.PROTECTION_CONFIRMED
