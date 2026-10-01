from datetime import datetime

from .execution import EvidenceRecord
from .murphy import ProtectionState


class PositionProtection:
    def __init__(self, state: ProtectionState = ProtectionState.POSITION_OPEN):
        self._state = state

    @property
    def state(self) -> ProtectionState:
        return self._state

    def submit(self):
        self._state = ProtectionState.PROTECTION_PENDING

    def confirm(self, evidence: EvidenceRecord, *, now: datetime):
        if evidence.kind != "protection_safe" or not evidence.valid_for(evidence.intent_id, now):
            self._state = ProtectionState.PROTECTION_UNKNOWN
            raise ValueError("PROTECTION_CONFIRMATION_REQUIRES_VALID_EVIDENCE")
        self._state = ProtectionState.PROTECTION_CONFIRMED

    def unknown(self):
        self._state = ProtectionState.PROTECTION_UNKNOWN

    def unprotected(self):
        self._state = ProtectionState.UNPROTECTED

    def safe(self):
        return self._state is ProtectionState.PROTECTION_CONFIRMED
