"""
Agent indexer for parsing and indexing .agent.yaml files.

Handles agent metadata extraction, tool/delegation synchronization, and ID alignment.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

import yaml
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow
from src.models.orm import Agent, AgentTool, AgentDelegation, AIModelProfile

logger = logging.getLogger(__name__)


class AgentIndexer:
    """
    Indexes .agent.yaml files and synchronizes with the database.

    Handles ID alignment, tool/delegation association, and agent definition updates.
    """

    def __init__(self, db: AsyncSession):
        """
        Initialize the agent indexer.

        Args:
            db: Database session for querying and updating agent records
        """
        self.db = db

    async def index_agent(
        self,
        path: str,
        content: bytes,
        *,
        organization_id: UUID | None = None,
    ) -> bool:
        """
        Parse and index agent from .agent.yaml file.

        If the YAML contains an 'id' field, uses that ID (for API-created agents).
        Otherwise generates a new ID (for files synced from git/editor).

        Updates agent definition (name, description, system_prompt, tools, etc.)
        but preserves environment-specific fields (organization_id, access_level).

        Uses ON CONFLICT to update existing agents.

        Args:
            path: File path
            content: File content bytes
            organization_id: Trusted organization scope for validating workflow
                tool references. ``None`` permits global tools only.

        Returns:
            True if content was modified (ID alignment), False otherwise
        """
        content_modified = False

        try:
            agent_data = yaml.safe_load(content.decode("utf-8"))
        except yaml.YAMLError:
            logger.warning(f"Invalid YAML in agent file: {path}")
            return False

        name = agent_data.get("name")
        if not name:
            raise ValueError(f"agent name is required (file: {path})")

        system_prompt = agent_data.get("system_prompt")
        if not system_prompt:
            raise ValueError(f"system_prompt is required (file: {path})")

        # Use ID from JSON if present (for API-created agents), otherwise generate new
        agent_id_str = agent_data.get("id")
        if agent_id_str:
            try:
                agent_id = UUID(agent_id_str)
            except ValueError:
                logger.warning(f"Invalid agent ID in {path}: {agent_id_str}")
                agent_id = uuid4()
                agent_data["id"] = str(agent_id)
                content_modified = True
        else:
            agent_id = uuid4()
            agent_data["id"] = str(agent_id)
            content_modified = True
            logger.info(f"Injecting ID {agent_id} into agent file: {path}")

        # Agents are now "fully virtual" - their path is computed from their ID
        # (e.g., agents/{uuid}.agent.yaml), so we don't need a separate file_path column.
        # We just use the ID from the YAML content directly.

        # Parse channels — validate against AgentChannel enum
        from src.models.enums import AgentChannel

        valid_channel_values = {c.value for c in AgentChannel}
        raw_channels = agent_data.get("channels", ["chat"])
        if not isinstance(raw_channels, list):
            raw_channels = ["chat"]
        channels = [c for c in raw_channels if c in valid_channel_values]
        if not channels:
            channels = ["chat"]

        # Get knowledge_sources (JSONB field)
        knowledge_sources = agent_data.get("knowledge_sources", [])
        if not isinstance(knowledge_sources, list):
            knowledge_sources = []

        llm_profile_id: UUID | None = None
        llm_profile = agent_data.get("llm_profile")
        if llm_profile:
            if not isinstance(llm_profile, str):
                raise ValueError(f"llm_profile must be a profile name (file: {path})")
            llm_profile_id = await self.db.scalar(
                select(AIModelProfile.id).where(
                    func.lower(AIModelProfile.name) == llm_profile.lower()
                )
            )
            if llm_profile_id is None:
                raise ValueError(
                    f"llm_profile '{llm_profile}' does not reference an existing "
                    f"model profile (file: {path})"
                )

        # Agent manifests may carry portable, non-privileged built-in tools,
        # but file sync is not an authorization surface. Drop admin-only tools
        # before the upsert so a repository change cannot grant them.
        from src.core.system_agents import PLATFORM_ADMIN_SYSTEM_TOOLS

        raw_system_tools = agent_data.get("system_tools", [])
        if not isinstance(raw_system_tools, list):
            raw_system_tools = []
        system_tools = [
            tool
            for tool in raw_system_tools
            if isinstance(tool, str) and tool not in PLATFORM_ADMIN_SYSTEM_TOOLS
        ]

        now = datetime.now(timezone.utc)

        # Upsert agent - updates definition but NOT organization_id or access_level
        # These env-specific fields are only set via the API, not from file sync
        stmt = insert(Agent).values(
            id=agent_id,
            name=name,
            description=agent_data.get("description"),
            system_prompt=system_prompt,
            channels=channels,
            knowledge_sources=knowledge_sources,
            is_active=agent_data.get("is_active", True),
            llm_profile_id=llm_profile_id,
            llm_max_tokens=agent_data.get("llm_max_tokens"),
            # Autonomous-run limits are portable agent CONTENT (ManifestAgent
            # fields). Persist configured values, but omit absent values so a
            # new agent remains unbounded and an existing agent keeps its
            # environment's current limit.
            **(
                {"max_iterations": agent_data["max_iterations"]}
                if agent_data.get("max_iterations") is not None
                else {}
            ),
            **(
                {"max_token_budget": agent_data["max_token_budget"]}
                if agent_data.get("max_token_budget") is not None
                else {}
            ),
            system_tools=system_tools,
            # Scope is supplied by the trusted import/deploy boundary for new
            # rows. The conflict-update branch deliberately omits it so repo
            # content cannot move an existing agent between organizations.
            organization_id=organization_id,
            created_by="file_sync",
        ).on_conflict_do_update(
            index_elements=[Agent.id],
            set_={
                # Update definition fields from file
                "name": name,
                "description": agent_data.get("description"),
                "system_prompt": system_prompt,
                "channels": channels,
                "knowledge_sources": knowledge_sources,
                "is_active": agent_data.get("is_active", True),
                "llm_profile_id": llm_profile_id,
                "llm_max_tokens": agent_data.get("llm_max_tokens"),
                # Limits are portable content; persist them on update too, but
                # ONLY when the manifest carries them (same omit-when-absent rule
                # as the insert branch). Omitting the key leaves the existing
                # column value untouched.
                **(
                    {"max_iterations": agent_data["max_iterations"]}
                    if agent_data.get("max_iterations") is not None
                    else {}
                ),
                **(
                    {"max_token_budget": agent_data["max_token_budget"]}
                    if agent_data.get("max_token_budget") is not None
                    else {}
                ),
                "system_tools": system_tools,
                "updated_at": now,
                # NOTE: organization_id and access_level are NOT updated
                # These are preserved from the database (env-specific)
            },
        ).returning(Agent.organization_id)
        upsert_result = await self.db.execute(stmt)
        persisted_organization_id = upsert_result.scalar_one()

        # Sync tool associations (tool_ids are workflow UUIDs)
        # Accept 'tools' as friendlier alias for 'tool_ids'
        tool_ids = agent_data.get("tool_ids") or agent_data.get("tools", [])
        if isinstance(tool_ids, list):
            # Delete existing tool associations
            await self.db.execute(
                delete(AgentTool).where(AgentTool.agent_id == agent_id)
            )
            # Repository content is not an authorization boundary. Resolve the
            # complete requested set through the agent's trusted org scope and
            # accept only active workflow tools. This prevents a manifest from
            # creating durable cross-tenant or non-tool grants by UUID.
            requested_tool_ids: list[UUID] = []
            for tool_id_str in tool_ids:
                try:
                    workflow_id = UUID(str(tool_id_str))
                    if workflow_id in requested_tool_ids:
                        continue
                    requested_tool_ids.append(workflow_id)
                except (TypeError, ValueError):
                    logger.warning(f"Invalid tool_id in agent {name}: {tool_id_str}")

            if requested_tool_ids:
                scope_clause = Workflow.organization_id.is_(None)
                if persisted_organization_id is not None:
                    scope_clause = or_(
                        Workflow.organization_id == persisted_organization_id,
                        Workflow.organization_id.is_(None),
                    )
                allowed_tool_ids = set(
                    (
                        await self.db.execute(
                            select(Workflow.id).where(
                                Workflow.id.in_(requested_tool_ids),
                                Workflow.is_active.is_(True),
                                Workflow.type == "tool",
                                scope_clause,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for workflow_id in requested_tool_ids:
                    if workflow_id in allowed_tool_ids:
                        self.db.add(
                            AgentTool(
                                agent_id=agent_id,
                                workflow_id=workflow_id,
                            )
                        )
                    else:
                        logger.warning(
                            "Agent %s references an unavailable workflow tool %s",
                            name,
                            workflow_id,
                        )

        # Sync delegated agent associations
        delegated_agent_ids = agent_data.get("delegated_agent_ids", [])
        if isinstance(delegated_agent_ids, list):
            # Delete existing delegations
            await self.db.execute(
                delete(AgentDelegation).where(AgentDelegation.parent_agent_id == agent_id)
            )
            # Create new delegations (with existence check to prevent FK violations)
            for child_id_str in delegated_agent_ids:
                try:
                    child_agent_id = UUID(child_id_str)
                    # Check if child agent exists before creating FK relationship
                    agent_exists = await self.db.execute(
                        select(Agent.id).where(Agent.id == child_agent_id)
                    )
                    if agent_exists.scalar_one_or_none():
                        self.db.add(AgentDelegation(parent_agent_id=agent_id, child_agent_id=child_agent_id))
                    else:
                        logger.warning(f"Agent {name} references non-existent agent {child_agent_id}")
                except ValueError:
                    logger.warning(f"Invalid delegated_agent_id in agent {name}: {child_id_str}")

        logger.debug(f"Indexed agent: {name} from {path}")
        return content_modified

    async def delete_agent_for_file(self, path: str) -> int:
        """
        Delete the agent associated with a file.

        Called when a file is deleted to clean up agent records from the database.
        For virtual agents, the ID is extracted from the path (agents/{uuid}.agent.yaml).

        Args:
            path: File path that was deleted (e.g., "agents/{uuid}.agent.yaml")

        Returns:
            Number of agents deleted
        """
        # Extract agent ID from path: {uuid}.agent.yaml -> uuid (any directory)
        import re
        match = re.search(r"([a-f0-9-]+)\.agent\.yaml$", path, re.IGNORECASE)
        if not match:
            logger.warning(f"Cannot extract agent ID from path: {path}")
            return 0

        try:
            agent_id = UUID(match.group(1))
        except ValueError:
            logger.warning(f"Invalid UUID in agent path: {path}")
            return 0

        # Delete the agent by ID (cascade will delete agent_tools and agent_delegations)
        stmt = delete(Agent).where(Agent.id == agent_id)
        result = await self.db.execute(stmt)
        count = result.rowcount if result.rowcount else 0

        if count > 0:
            logger.info(f"Deleted agent {agent_id} from database for deleted file: {path}")

        return count
