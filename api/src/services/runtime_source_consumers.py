"""Common retained runtime source obligation; no admission or writer authority."""

from sqlalchemy import exists, select
from sqlalchemy.sql.selectable import Exists

from src.models.orm.executions import Execution
from src.models.orm.runtime_execution import RuntimeResourceSettlement, RuntimeSession


def unsettled_runtime_source_consumer() -> Exists:
    """Terminal result/session closure alone cannot release a source pin.

    Python-owned roots have no common runtime session and remain unchanged.
    Runtime admission retains the session atomically with the existing root;
    only the immutable original-custody settlement discharges this obligation.
    """
    session = RuntimeSession.__table__
    settlement = RuntimeResourceSettlement.__table__
    settled = exists(
        select(settlement.c.session_id)
        .where(settlement.c.session_id == session.c.id)
        .correlate(session)
    )
    return exists(
        select(session.c.id)
        .where(session.c.execution_id == Execution.id, ~settled)
        .correlate(Execution)
    )
