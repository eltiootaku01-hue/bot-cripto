from dataclasses import dataclass
@dataclass(frozen=True)
class LogRotationPolicy:
    max_size_bytes:int
    backup_count:int
    retention_days:int
    def valid(self):
        return self.max_size_bytes>0 and self.backup_count>=0 and self.retention_days>=0
