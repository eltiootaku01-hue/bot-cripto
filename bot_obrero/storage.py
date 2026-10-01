from dataclasses import dataclass

@dataclass(frozen=True)
class StoragePolicy:
    transactional:bool
    atomic_writes:bool
    integrity_checks:bool
    backup_enabled:bool
    crash_recovery:bool

def storage_safe(policy):
    return all((policy.transactional,policy.atomic_writes,policy.integrity_checks,policy.crash_recovery))

def recovery_ready(policy):
    return storage_safe(policy) and policy.backup_enabled
