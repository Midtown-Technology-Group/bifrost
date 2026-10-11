"""Scope-aware manifest generation for Solutions and Git organizations.

generate_manifest historically dumps ALL orgs' entities. A per-scope Solution
export must NOT cross-contaminate tenants: generate_manifest(db, solution_id=X)
returns only that install's solution-managed entities (workflows/forms/agents/
apps/tables), never _repo/ rows or other installs. Organization-scoped Git
generation likewise includes only that tenant and global fallback rows.
"""
from __future__ import annotations

import uuid

import pytest

from src.models.enums import EventSourceType
from src.models.orm.config import Config
from src.models.orm.events import EventSource
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.users import Role
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from src.services.manifest_generator import generate_manifest


@pytest.mark.e2e
class TestScopeAwareManifest:
    async def test_organization_filter_excludes_other_tenant_metadata(
        self, db_session
    ) -> None:
        """A Git manifest contains the selected org and global fallback only."""
        db = db_session
        suffix = uuid.uuid4().hex[:8]
        org = Organization(
            id=uuid.uuid4(),
            name=f"Manifest Org {suffix}",
            created_by="test@example.com",
        )
        foreign_org = Organization(
            id=uuid.uuid4(),
            name=f"Foreign Manifest Org {suffix}",
            created_by="test@example.com",
        )
        local_role = Role(
            id=uuid.uuid4(),
            name=f"manifest-local-{suffix}",
            created_by="test@example.com",
        )
        foreign_role = Role(
            id=uuid.uuid4(),
            name=f"manifest-foreign-{suffix}",
            created_by="test@example.com",
        )
        db.add_all([org, foreign_org, local_role, foreign_role])
        await db.flush()

        def workflow(label: str, organization_id) -> Workflow:
            return Workflow(
                id=uuid.uuid4(),
                name=f"manifest_{label}_{suffix}",
                function_name="run",
                path=f"workflows/manifest_{label}_{suffix}.py",
                type="workflow",
                organization_id=organization_id,
                solution_id=None,
            )

        local_workflow = workflow("local", org.id)
        global_workflow = workflow("global", None)
        foreign_workflow = workflow("foreign", foreign_org.id)
        integration = Integration(
            id=uuid.uuid4(),
            name=f"Manifest Integration {suffix}",
            is_deleted=False,
        )
        db.add_all([
            local_workflow,
            global_workflow,
            foreign_workflow,
            integration,
        ])
        await db.flush()

        local_config = Config(
            id=uuid.uuid4(),
            key=f"local_{suffix}",
            value={"tenant": "local"},
            organization_id=org.id,
            integration_id=integration.id,
            updated_by="test@example.com",
        )
        global_config = Config(
            id=uuid.uuid4(),
            key=f"global_{suffix}",
            value={"tenant": "global"},
            organization_id=None,
            integration_id=integration.id,
            updated_by="test@example.com",
        )
        foreign_config = Config(
            id=uuid.uuid4(),
            key=f"foreign_{suffix}",
            value={"tenant": "foreign"},
            organization_id=foreign_org.id,
            integration_id=integration.id,
            updated_by="test@example.com",
        )
        local_table = Table(
            id=uuid.uuid4(), name=f"local_table_{suffix}", organization_id=org.id
        )
        global_table = Table(
            id=uuid.uuid4(), name=f"global_table_{suffix}", organization_id=None
        )
        foreign_table = Table(
            id=uuid.uuid4(),
            name=f"foreign_table_{suffix}",
            organization_id=foreign_org.id,
        )
        local_event = EventSource(
            id=uuid.uuid4(),
            name=f"local_event_{suffix}",
            source_type=EventSourceType.TOPIC,
            organization_id=org.id,
            created_by="test@example.com",
        )
        global_event = EventSource(
            id=uuid.uuid4(),
            name=f"global_event_{suffix}",
            source_type=EventSourceType.TOPIC,
            organization_id=None,
            created_by="test@example.com",
        )
        foreign_event = EventSource(
            id=uuid.uuid4(),
            name=f"foreign_event_{suffix}",
            source_type=EventSourceType.TOPIC,
            organization_id=foreign_org.id,
            created_by="test@example.com",
        )
        db.add_all([
            WorkflowRole(
                workflow_id=local_workflow.id,
                role_id=local_role.id,
                assigned_by="test@example.com",
            ),
            WorkflowRole(
                workflow_id=foreign_workflow.id,
                role_id=foreign_role.id,
                assigned_by="test@example.com",
            ),
            IntegrationMapping(
                integration_id=integration.id,
                organization_id=org.id,
                entity_id="local-tenant",
            ),
            IntegrationMapping(
                integration_id=integration.id,
                organization_id=foreign_org.id,
                entity_id="foreign-tenant",
            ),
            local_config,
            global_config,
            foreign_config,
            local_table,
            global_table,
            foreign_table,
            local_event,
            global_event,
            foreign_event,
        ])
        await db.flush()

        manifest = await generate_manifest(db, organization_id=org.id)

        assert {entry.id for entry in manifest.organizations} == {str(org.id)}
        assert str(local_workflow.id) in manifest.workflows
        assert str(global_workflow.id) in manifest.workflows
        assert str(foreign_workflow.id) not in manifest.workflows
        role_ids = {entry.id for entry in manifest.roles}
        assert str(local_role.id) in role_ids
        assert str(foreign_role.id) not in role_ids
        assert str(local_config.id) in manifest.configs
        assert str(global_config.id) in manifest.configs
        assert str(foreign_config.id) not in manifest.configs
        assert str(local_table.id) in manifest.tables
        assert str(global_table.id) in manifest.tables
        assert str(foreign_table.id) not in manifest.tables
        assert str(local_event.id) in manifest.events
        assert str(global_event.id) in manifest.events
        assert str(foreign_event.id) not in manifest.events
        mappings = manifest.integrations[str(integration.id)].mappings
        assert {mapping.entity_id for mapping in mappings} == {"local-tenant"}

    async def test_solution_filter_excludes_repo_and_other_installs(self, db_session) -> None:
        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"man-{uuid.uuid4().hex[:8]}", name="M", organization_id=None)
        other = Solution(id=uuid.uuid4(), slug=f"oth-{uuid.uuid4().hex[:8]}", name="O", organization_id=None)
        db.add_all([sol, other])
        await db.flush()

        mine = uuid.uuid4()
        db.add_all([
            Workflow(id=mine, name=f"mine_{mine.hex[:6]}", function_name="run",
                     path=f"workflows/mine_{mine.hex[:6]}.py", type="workflow",
                     organization_id=None, solution_id=sol.id),
            Workflow(id=uuid.uuid4(), name=f"repo_{uuid.uuid4().hex[:6]}", function_name="run",
                     path=f"workflows/repo_{uuid.uuid4().hex[:6]}.py", type="workflow",
                     organization_id=None, solution_id=None),
            Workflow(id=uuid.uuid4(), name=f"other_{uuid.uuid4().hex[:6]}", function_name="run",
                     path=f"workflows/other_{uuid.uuid4().hex[:6]}.py", type="workflow",
                     organization_id=None, solution_id=other.id),
        ])
        await db.flush()

        manifest = await generate_manifest(db, solution_id=sol.id)
        wf_ids = {w.id for w in manifest.workflows.values()}
        assert wf_ids == {str(mine)}

    async def test_no_solution_id_is_repo_scoped(self, db_session) -> None:
        """generate_manifest(db) with no solution_id is a WORKSPACE (_repo/)
        regen: it includes _repo/ workflows but EXCLUDES solution-managed rows,
        so a normal .bifrost/ regen never serializes deploy-owned entities into
        the workspace git/import flow (Codex #16)."""
        db = db_session
        sol = Solution(id=uuid.uuid4(), slug=f"man-{uuid.uuid4().hex[:8]}", name="M", organization_id=None)
        db.add(sol)
        await db.flush()

        repo_id, sol_wf_id = uuid.uuid4(), uuid.uuid4()
        db.add_all([
            Workflow(
                id=repo_id, name=f"legacy_{repo_id.hex[:6]}", function_name="run",
                path=f"workflows/legacy_{repo_id.hex[:6]}.py", type="workflow",
                organization_id=None, solution_id=None,
            ),
            Workflow(
                id=sol_wf_id, name=f"sol_{sol_wf_id.hex[:6]}", function_name="run",
                path=f"workflows/sol_{sol_wf_id.hex[:6]}.py", type="workflow",
                organization_id=None, solution_id=sol.id,
            ),
        ])
        await db.flush()
        manifest = await generate_manifest(db)
        assert str(repo_id) in manifest.workflows          # _repo/ included
        assert str(sol_wf_id) not in manifest.workflows    # solution-managed excluded
