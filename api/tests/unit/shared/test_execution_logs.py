from datetime import datetime, timezone

from shared.execution_logs import project_execution_logs
from src.models.orm.executions import ExecutionLog


def test_projection_hides_global_ids_and_preserves_authorized_order():
    timestamp = datetime(2026, 10, 13, tzinfo=timezone.utc)
    entries = [
        ExecutionLog(id=9001, sequence=7, timestamp=timestamp, level="warning",
                     message="visible", log_metadata={"safe": True}),
        ExecutionLog(id=40002, sequence=9, timestamp=None, level=None,
                     message=None, log_metadata=None),
    ]
    projected = project_execution_logs(iter(entries))
    assert [log.id for log in projected] == [0, 1]
    assert [log.sequence for log in projected] == [7, 9]
    assert projected[0].timestamp == timestamp.isoformat()
    assert projected[0].data == {"safe": True}
    assert projected[1].timestamp == ""
    assert projected[1].level == "info"
    assert projected[1].message == ""
    assert projected[1].data is None
    assert project_execution_logs([]) == []
