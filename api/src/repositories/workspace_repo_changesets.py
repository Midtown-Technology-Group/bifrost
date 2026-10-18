"""Persistence boundary for workspace _repo changesets."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.workspace_repo_changesets import WorkspaceRepoChangeset

RETRYABLE_GIT_FAILURE_STATES = ("failed", "not_configured")
GIT_CLOSURE_PENDING_STALE_AFTER = timedelta(minutes=15)


class WorkspaceRepoChangesetRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add(self, row: WorkspaceRepoChangeset) -> WorkspaceRepoChangeset:
        self.db.add(row)
        await self.db.flush()
        return row

    async def get(
        self, changeset_id: UUID, organization_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRepoChangeset | None:
        stmt = select(WorkspaceRepoChangeset).where(
            WorkspaceRepoChangeset.id == changeset_id,
            WorkspaceRepoChangeset.organization_id == organization_id,
        )
        if for_update:
            # ``expire_on_commit=False`` keeps this row in the session identity map
            # across the external Git operation. Re-populate it while acquiring the
            # lock so a later transaction cannot replace the durable operation owner
            # and then be overwritten by this stale instance.
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def count_open(self, scope: str, organization_id: UUID) -> int:
        statuses = ("open", "staged", "validated", "activating")
        stmt = (
            select(func.count())
            .select_from(WorkspaceRepoChangeset)
            .where(
                WorkspaceRepoChangeset.scope == scope,
                WorkspaceRepoChangeset.organization_id == organization_id,
                WorkspaceRepoChangeset.status.in_(statuses),
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def list_retryable_git_failures(
        self, organization_id: UUID, *, scope: str | None = None
    ) -> list[WorkspaceRepoChangeset]:
        failure_phase = WorkspaceRepoChangeset.failure_detail["phase"].astext
        failure_state = WorkspaceRepoChangeset.failure_detail["state"].astext
        stmt = (
            select(WorkspaceRepoChangeset)
            .where(
                WorkspaceRepoChangeset.organization_id == organization_id,
                or_(
                    failure_state.in_(RETRYABLE_GIT_FAILURE_STATES),
                    and_(
                        failure_state == "pending",
                        WorkspaceRepoChangeset.updated_at
                        <= func.now() - GIT_CLOSURE_PENDING_STALE_AFTER,
                    ),
                ),
                or_(
                    and_(
                        WorkspaceRepoChangeset.status == "activated",
                        failure_phase == "git_closure",
                    ),
                    and_(
                        WorkspaceRepoChangeset.status == "committed_unpushed",
                        failure_phase == "git_push",
                    ),
                    and_(
                        WorkspaceRepoChangeset.status == "activated",
                        failure_phase == "git_convergence",
                    ),
                ),
            )
            .order_by(WorkspaceRepoChangeset.updated_at.desc())
        )
        if scope is not None:
            stmt = stmt.where(WorkspaceRepoChangeset.scope == scope)
        return list((await self.db.execute(stmt)).scalars().all())
