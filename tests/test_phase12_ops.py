from bot_obrero.circuit import CircuitReason,should_freeze
from bot_obrero.shutdown import ShutdownReason,ShutdownRecord
from bot_obrero.logging_policy import LogRotationPolicy
from bot_obrero.storage import StoragePolicy,storage_safe
def test_circuit_reasons_freeze():
    for reason in (CircuitReason.STATE_UNKNOWN,CircuitReason.WEBSOCKET_STALE,CircuitReason.CONFIGURATION_MISMATCH,CircuitReason.ORDER_RESULT_UNKNOWN):
        assert should_freeze(reason)
def test_log_rotation_policy():
    assert LogRotationPolicy(10_000_000,5,30).valid()
def test_storage_policy_requires_safety_properties():
    assert storage_safe(StoragePolicy(True,True,True,True,True))
    assert not storage_safe(StoragePolicy(False,True,True,True,True))
def test_shutdown_recovery_is_explicit():
    x=ShutdownRecord(ShutdownReason.CRASH,False,False)
    assert x.reason is ShutdownReason.CRASH
