from dataclasses import dataclass
import json

@dataclass(frozen=True)
class LLMDecision:
    accepted: bool
    payload: dict | None
    reason: str

def parse_non_authoritative(payload):
    if not isinstance(payload, str):
        return LLMDecision(False, None, "LLM_INVALID")
    try:
        value = json.loads(payload)
    except (TypeError, ValueError):
        return LLMDecision(False, None, "LLM_INVALID")
    if not isinstance(value, dict) or not value:
        return LLMDecision(False, None, "LLM_AMBIGUOUS")
    return LLMDecision(True, value, "ADVISORY_ONLY")
