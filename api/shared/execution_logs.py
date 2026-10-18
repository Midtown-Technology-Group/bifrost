"""Public execution-log projections shared by REST and SDK reads."""

from collections.abc import Iterable

from src.models import ExecutionLogPublic
from src.models.orm.executions import ExecutionLog


def project_execution_logs(entries: Iterable[ExecutionLog]) -> list[ExecutionLogPublic]:
    """Expose request-local ordinals, never database-global log identifiers."""
    return [
        ExecutionLogPublic(
            id=public_id,
            timestamp=log.timestamp.isoformat() if log.timestamp else "",
            level=log.level or "info",
            message=log.message or "",
            data=log.log_metadata,
            sequence=log.sequence,
        )
        for public_id, log in enumerate(entries)
    ]
