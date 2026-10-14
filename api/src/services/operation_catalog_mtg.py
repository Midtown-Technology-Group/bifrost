"""REST-only identities for routes present in MTG but absent from upstream R1a.

These entries classify existing bindings; they introduce no capabilities or
permission policy. Empty action_scopes deliberately defer scope decisions to
R2a. The existing handler/dependencies remain the authorization authority.
"""

from src.models.contracts.operation_catalog import (
    OperationAsyncPolicy,
    OperationDefinition,
    OperationTargetKind,
    RestOperationBinding,
)


def _rest_only(
    operation_id: str,
    method: str,
    path: str,
    target_kind: OperationTargetKind,
    handler: str,
    *,
    async_policy: OperationAsyncPolicy = OperationAsyncPolicy.SYNCHRONOUS,
) -> OperationDefinition:
    return OperationDefinition(
        operation_id=operation_id,
        summary=f"Existing REST binding: {method} {path}",
        target_kind=target_kind,
        rest=RestOperationBinding(
            method=method, path=path,
            response_model=(
                "PlatformJobAccepted" if async_policy == OperationAsyncPolicy.PLATFORM_JOB else None
            ),
        ),
        action_scopes=(),
        authorization_resolver=f"{handler}: existing dependencies and handler checks",
        async_policy=async_policy,
        exclusions={
            surface: "No canonical binding assigned in this metadata-only slice."
            for surface in ("cli", "mcp", "native_builder", "manifest", "sdk")
        },
    )


MTG_REST_OPERATIONS: tuple[OperationDefinition, ...] = (
    _rest_only(
        "agentactionapprovals.approve_approval", "POST", "/api/agent-action-approvals/{approval_id}/approve",
        OperationTargetKind.RESOURCE, "src.routers.agent_action_approvals.approve_approval",
    ),
    _rest_only(
        "agentactionapprovals.deny_approval", "POST", "/api/agent-action-approvals/{approval_id}/deny",
        OperationTargetKind.RESOURCE, "src.routers.agent_action_approvals.deny_approval",
    ),
    _rest_only(
        "agentactionapprovals.list_approvals", "GET", "/api/agent-action-approvals",
        OperationTargetKind.COLLECTION, "src.routers.agent_action_approvals.list_approvals",
    ),
    _rest_only(
        "applications.inspect_github_app_publication", "POST", "/api/applications/{app_id}/github-source/{job_id}/inspect",
        OperationTargetKind.RESOURCE, "src.routers.applications.inspect_github_app_publication",
    ),
    _rest_only(
        "applications.publish_github_app_source", "POST", "/api/applications/{app_id}/github-source",
        OperationTargetKind.RESOURCE, "src.routers.applications.publish_github_app_source",
        async_policy=OperationAsyncPolicy.PLATFORM_JOB,
    ),
    _rest_only(
        "appservice.app_service_metrics", "GET", "/api/platform/app-service/metrics",
        OperationTargetKind.PLATFORM, "src.routers.platform.app_service.app_service_metrics",
    ),
    _rest_only(
        "auth.authorize_cli_native_auth", "GET", "/auth/cli/authorize",
        OperationTargetKind.COLLECTION, "src.routers.auth.authorize_cli_native_auth",
    ),
    _rest_only(
        "auth.exchange_cli_native_auth_token", "POST", "/auth/cli/token",
        OperationTargetKind.COLLECTION, "src.routers.auth.exchange_cli_native_auth_token",
    ),
    _rest_only(
        "auth.start_cli_native_auth", "POST", "/auth/cli/start",
        OperationTargetKind.COLLECTION, "src.routers.auth.start_cli_native_auth",
    ),
    _rest_only(
        "chat.submit_teams_event", "POST", "/api/chat/teams/events",
        OperationTargetKind.COLLECTION, "src.routers.chat.submit_teams_event",
    ),
    _rest_only(
        "cli.sdk_integration_request_slot_acquire", "POST", "/api/sdk/integrations/request-slot/acquire",
        OperationTargetKind.COLLECTION, "src.routers.cli.sdk_integration_request_slot_acquire",
    ),
    _rest_only(
        "cli.sdk_integration_request_slot_release", "POST", "/api/sdk/integrations/request-slot/release",
        OperationTargetKind.COLLECTION, "src.routers.cli.sdk_integration_request_slot_release",
    ),
    _rest_only(
        "codexgateway.create_gateway_key", "POST", "/api/codex-gateway/keys",
        OperationTargetKind.COLLECTION, "src.routers.codex_gateway.create_gateway_key",
    ),
    _rest_only(
        "codexgateway.create_response", "POST", "/v1/responses",
        OperationTargetKind.COLLECTION, "src.routers.codex_gateway.create_response",
    ),
    _rest_only(
        "codexgateway.create_response.post", "POST", "/api/v1/responses",
        OperationTargetKind.COLLECTION, "src.routers.codex_gateway.create_response",
    ),
    _rest_only(
        "codexgateway.disconnect_oauth_account", "DELETE", "/api/codex-gateway/oauth",
        OperationTargetKind.COLLECTION, "src.routers.codex_gateway.disconnect_oauth_account",
    ),
    _rest_only(
        "codexgateway.get_oauth_status", "GET", "/api/codex-gateway/oauth/status",
        OperationTargetKind.COLLECTION, "src.routers.codex_gateway.get_oauth_status",
    ),
    _rest_only(
        "codexgateway.import_oauth_auth_cache", "POST", "/api/codex-gateway/oauth/import-auth-cache",
        OperationTargetKind.COLLECTION, "src.routers.codex_gateway.import_oauth_auth_cache",
    ),
    _rest_only(
        "codexgateway.list_gateway_keys", "GET", "/api/codex-gateway/keys",
        OperationTargetKind.COLLECTION, "src.routers.codex_gateway.list_gateway_keys",
    ),
    _rest_only(
        "codexgateway.revoke_gateway_key", "DELETE", "/api/codex-gateway/keys/{key_id}",
        OperationTargetKind.RESOURCE, "src.routers.codex_gateway.revoke_gateway_key",
    ),
    _rest_only(
        "codexgateway.start_oauth_connect", "POST", "/api/codex-gateway/oauth/connect",
        OperationTargetKind.COLLECTION, "src.routers.codex_gateway.start_oauth_connect",
    ),
    _rest_only(
        "devicecontrolkeys.create_control_key_route", "POST", "/api/device-control-keys",
        OperationTargetKind.COLLECTION, "src.routers.device_control_keys.create_control_key_route",
    ),
    _rest_only(
        "devicecontrolkeys.get_control_key_route", "GET", "/api/device-control-keys/{key_id}",
        OperationTargetKind.RESOURCE, "src.routers.device_control_keys.get_control_key_route",
    ),
    _rest_only(
        "devicecontrolkeys.list_control_keys_route", "GET", "/api/device-control-keys",
        OperationTargetKind.COLLECTION, "src.routers.device_control_keys.list_control_keys_route",
    ),
    _rest_only(
        "devicecontrolkeys.revoke_control_key_endpoint", "POST", "/api/device-control-keys/{key_id}/revoke",
        OperationTargetKind.RESOURCE, "src.routers.device_control_keys.revoke_control_key_endpoint",
    ),
    _rest_only(
        "devicecontrolkeys.rotate_control_key_endpoint", "POST", "/api/device-control-keys/{key_id}/rotate",
        OperationTargetKind.RESOURCE, "src.routers.device_control_keys.rotate_control_key_endpoint",
    ),
    _rest_only(
        "deviceprotocol.claim_route", "POST", "/api/device/jobs/claim",
        OperationTargetKind.COLLECTION, "src.routers.device_protocol.claim_route",
    ),
    _rest_only(
        "deviceprotocol.heartbeat_route", "POST", "/api/device/heartbeat",
        OperationTargetKind.COLLECTION, "src.routers.device_protocol.heartbeat_route",
    ),
    _rest_only(
        "deviceprotocol.logs_route", "POST", "/api/device/jobs/{job_id}/logs",
        OperationTargetKind.RESOURCE, "src.routers.device_protocol.logs_route",
    ),
    _rest_only(
        "deviceprotocol.result_route", "POST", "/api/device/jobs/{job_id}/result",
        OperationTargetKind.RESOURCE, "src.routers.device_protocol.result_route",
    ),
    _rest_only(
        "deviceprotocol.running_route", "POST", "/api/device/jobs/{job_id}/running",
        OperationTargetKind.RESOURCE, "src.routers.device_protocol.running_route",
    ),
    _rest_only(
        "devices.cancel_job_route", "POST", "/api/devices/{device_id}/jobs/{job_id}/cancel",
        OperationTargetKind.RESOURCE, "src.routers.devices.cancel_job_route",
    ),
    _rest_only(
        "devices.create_device_route", "POST", "/api/devices",
        OperationTargetKind.COLLECTION, "src.routers.devices.create_device_route",
    ),
    _rest_only(
        "devices.create_job_route", "POST", "/api/devices/{device_id}/jobs",
        OperationTargetKind.RESOURCE, "src.routers.devices.create_job_route",
    ),
    _rest_only(
        "devices.disable_device_route", "POST", "/api/devices/{device_id}/disable",
        OperationTargetKind.RESOURCE, "src.routers.devices.disable_device_route",
    ),
    _rest_only(
        "devices.enable_device_route", "POST", "/api/devices/{device_id}/enable",
        OperationTargetKind.RESOURCE, "src.routers.devices.enable_device_route",
    ),
    _rest_only(
        "devices.enroll_device_route", "POST", "/api/devices/enroll",
        OperationTargetKind.COLLECTION, "src.routers.devices.enroll_device_route",
    ),
    _rest_only(
        "devices.get_device_route", "GET", "/api/devices/{device_id}",
        OperationTargetKind.RESOURCE, "src.routers.devices.get_device_route",
    ),
    _rest_only(
        "devices.get_job_logs_route", "GET", "/api/devices/{device_id}/jobs/{job_id}/logs",
        OperationTargetKind.RESOURCE, "src.routers.devices.get_job_logs_route",
    ),
    _rest_only(
        "devices.get_job_route", "GET", "/api/devices/{device_id}/jobs/{job_id}",
        OperationTargetKind.RESOURCE, "src.routers.devices.get_job_route",
    ),
    _rest_only(
        "devices.list_devices_route", "GET", "/api/devices",
        OperationTargetKind.COLLECTION, "src.routers.devices.list_devices_route",
    ),
    _rest_only(
        "devices.list_jobs_route", "GET", "/api/devices/{device_id}/jobs",
        OperationTargetKind.RESOURCE, "src.routers.devices.list_jobs_route",
    ),
    _rest_only(
        "devices.rotate_device_key_endpoint", "POST", "/api/devices/{device_id}/rotate-key",
        OperationTargetKind.RESOURCE, "src.routers.devices.rotate_device_key_endpoint",
    ),
    _rest_only(
        "externalworkers.enroll", "POST", "/api/platform/external-workers/enroll",
        OperationTargetKind.PLATFORM, "src.routers.platform.external_workers.enroll",
    ),
    _rest_only(
        "externalworkers.status", "GET", "/api/platform/external-workers",
        OperationTargetKind.PLATFORM, "src.routers.platform.external_workers.status",
    ),
    _rest_only(
        "files.preview_workspace_file_impact", "POST", "/api/files/impact",
        OperationTargetKind.COLLECTION, "src.routers.files.preview_workspace_file_impact",
    ),
    _rest_only(
        "mcp.resolve_gateway_operation_receipt", "POST", "/api/mcp/operation-receipts/{receipt_id}/resolve",
        OperationTargetKind.RESOURCE, "src.routers.mcp.resolve_gateway_operation_receipt",
    ),
    _rest_only(
        "packages.get_package_installation_progress", "GET", "/api/packages/installations/{run_id}",
        OperationTargetKind.RESOURCE, "src.routers.packages.get_package_installation_progress",
    ),
    _rest_only(
        "runtimemaintenance.enter_maintenance", "POST", "/api/platform/runtime-maintenance/enter",
        OperationTargetKind.PLATFORM, "src.routers.platform.runtime_maintenance.enter_maintenance",
    ),
    _rest_only(
        "runtimemaintenance.exit_maintenance", "POST", "/api/platform/runtime-maintenance/{generation}/exit",
        OperationTargetKind.PLATFORM, "src.routers.platform.runtime_maintenance.exit_maintenance",
    ),
    _rest_only(
        "runtimemaintenance.runtime_maintenance_status", "GET", "/api/platform/runtime-maintenance",
        OperationTargetKind.PLATFORM, "src.routers.platform.runtime_maintenance.runtime_maintenance_status",
    ),
    _rest_only(
        "runtimemaintenance.seal_maintenance", "POST", "/api/platform/runtime-maintenance/{generation}/seal",
        OperationTargetKind.PLATFORM, "src.routers.platform.runtime_maintenance.seal_maintenance",
    ),
    _rest_only(
        "sdkmodules.read_deployment_resource", "GET", "/api/sdk/resources/{path}",
        OperationTargetKind.RESOURCE, "src.routers.sdk_modules.read_deployment_resource",
    ),
    _rest_only(
        "solutiondeployments.activate_deployment", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/activate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.activate_deployment",
    ),
    _rest_only(
        "solutiondeployments.activate_initial_workflow_install", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/initial-workflow/activate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.activate_initial_workflow_install",
    ),
    _rest_only(
        "solutiondeployments.activate_live_handoff", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/activate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.activate_live_handoff",
    ),
    _rest_only(
        "solutiondeployments.activate_repo_workflow_adoption", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/repo-workflow-adoption/activate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.activate_repo_workflow_adoption",
    ),
    _rest_only(
        "solutiondeployments.activate_source_revision", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/source-revision/activate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.activate_source_revision",
    ),
    _rest_only(
        "solutiondeployments.activate_workflow_revision", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/workflow-revision/activate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.activate_workflow_revision",
    ),
    _rest_only(
        "solutiondeployments.build_live_handoff_candidate", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/candidate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.build_live_handoff_candidate",
    ),
    _rest_only(
        "solutiondeployments.create_deployment", "POST", "/api/solutions/{solution_id}/deployments",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.create_deployment",
    ),
    _rest_only(
        "solutiondeployments.deliver_github_package", "POST", "/api/solutions/{solution_id}/deployments/github-package",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.deliver_github_package",
    ),
    _rest_only(
        "solutiondeployments.deliver_github_source", "POST", "/api/solutions/{solution_id}/deployments/github-source",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.deliver_github_source",
    ),
    _rest_only(
        "solutiondeployments.deployment_capabilities", "GET", "/api/solutions/{solution_id}/deployments/capabilities",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.deployment_capabilities",
    ),
    _rest_only(
        "solutiondeployments.inspect_active_deployment", "GET", "/api/solutions/{solution_id}/deployments/active",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.inspect_active_deployment",
    ),
    _rest_only(
        "solutiondeployments.inspect_deployment", "GET", "/api/solutions/{solution_id}/deployments/{deployment_id}",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.inspect_deployment",
    ),
    _rest_only(
        "solutiondeployments.inspect_github_package", "POST", "/api/solutions/{solution_id}/deployments/github-package/status",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.inspect_github_package",
    ),
    _rest_only(
        "solutiondeployments.inspect_initial_workflow_install", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/initial-workflow/preflight",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.inspect_initial_workflow_install",
    ),
    _rest_only(
        "solutiondeployments.inspect_repo_workflow_adoption", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/repo-workflow-adoption/preflight",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.inspect_repo_workflow_adoption",
    ),
    _rest_only(
        "solutiondeployments.inspect_source_revision", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/source-revision/preflight",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.inspect_source_revision",
    ),
    _rest_only(
        "solutiondeployments.inspect_workflow_revision", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/workflow-revision/preflight",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.inspect_workflow_revision",
    ),
    _rest_only(
        "solutiondeployments.preflight_live_handoff", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/preflight",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.preflight_live_handoff",
    ),
    _rest_only(
        "solutiondeployments.preview_shared_table_bindings", "POST", "/api/solutions/{solution_id}/deployments/shared-tables/preview",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.preview_shared_table_bindings",
    ),
    _rest_only(
        "solutiondeployments.recover_github_package", "POST", "/api/solutions/{solution_id}/deployments/github-package/recover",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.recover_github_package",
    ),
    _rest_only(
        "solutiondeployments.rollback_deployment", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/rollback",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.rollback_deployment",
    ),
    _rest_only(
        "solutiondeployments.rollback_live_handoff", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/live-handoff/rollback",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.rollback_live_handoff",
    ),
    _rest_only(
        "solutiondeployments.stage_initial_workflow_install", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/initial-workflow/candidate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.stage_initial_workflow_install",
    ),
    _rest_only(
        "solutiondeployments.stage_repo_workflow_adoption", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/repo-workflow-adoption/candidate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.stage_repo_workflow_adoption",
    ),
    _rest_only(
        "solutiondeployments.stage_source_revision", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/source-revision/candidate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.stage_source_revision",
    ),
    _rest_only(
        "solutiondeployments.stage_workflow_revision", "POST", "/api/solutions/{solution_id}/deployments/{deployment_id}/workflow-revision/candidate",
        OperationTargetKind.RESOURCE, "src.routers.solution_deployments.stage_workflow_revision",
    ),
    _rest_only(
        "solutions.reconcile_solution_deployment", "POST", "/api/solutions/{solution_id}/deploy-jobs/{deploy_job_id}/reconcile",
        OperationTargetKind.RESOURCE, "src.routers.solutions.reconcile_solution_deployment",
        async_policy=OperationAsyncPolicy.PLATFORM_JOB,
    ),
    _rest_only(
        "tables.update_document_conditional", "PATCH", "/api/tables/{table_id}/documents/{doc_id}/conditional",
        OperationTargetKind.RESOURCE, "src.routers.tables.update_document_conditional",
    ),
    _rest_only(
        "users.create_external_identity", "POST", "/api/users/{user_id}/external-identities",
        OperationTargetKind.RESOURCE, "src.routers.users.create_external_identity",
    ),
    _rest_only(
        "users.delete_external_identity", "DELETE", "/api/users/{user_id}/external-identities/{identity_id}",
        OperationTargetKind.RESOURCE, "src.routers.users.delete_external_identity",
    ),
    _rest_only(
        "users.list_external_identities", "GET", "/api/users/{user_id}/external-identities",
        OperationTargetKind.RESOURCE, "src.routers.users.list_external_identities",
    ),
    _rest_only(
        "workers.list_worker_control_commands", "GET", "/api/platform/workers/commands/history",
        OperationTargetKind.PLATFORM, "src.routers.platform.workers.list_worker_control_commands",
    ),
    _rest_only(
        "workspaceadmin.inspect_oauth", "GET", "/api/oauth/connections/{connection_name}/diagnostics",
        OperationTargetKind.RESOURCE, "src.routers.workspace_admin.inspect_oauth",
    ),
    _rest_only(
        "workspaceadmin.reconcile_oauth", "POST", "/api/oauth/connections/{connection_name}/reconcile",
        OperationTargetKind.RESOURCE, "src.routers.workspace_admin.reconcile_oauth",
    ),
    _rest_only(
        "workspaceadmin.recover_oauth", "POST", "/api/oauth/connections/{connection_name}/recover",
        OperationTargetKind.RESOURCE, "src.routers.workspace_admin.recover_oauth",
    ),
    _rest_only(
        "workspaceadmin.redact_execution", "POST", "/api/executions/{execution_id}/redact-sensitive-fields",
        OperationTargetKind.RESOURCE, "src.routers.workspace_admin.redact_execution",
    ),
    _rest_only(
        "workspacepromotions.activate_workspace_release", "POST", "/api/workspace-promotions/releases/{release_id}/activate",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.activate_workspace_release",
    ),
    _rest_only(
        "workspacepromotions.declare_workspace_source_release", "POST", "/api/workspace-promotions/source-releases",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.declare_workspace_source_release",
    ),
    _rest_only(
        "workspacepromotions.declare_workspace_source_release_from_github", "POST", "/api/workspace-promotions/source-releases/github",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.declare_workspace_source_release_from_github",
    ),
    _rest_only(
        "workspacepromotions.enqueue_workspace_promotion_preview", "POST", "/api/workspace-promotions/preview-jobs",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.enqueue_workspace_promotion_preview",
        async_policy=OperationAsyncPolicy.PLATFORM_JOB,
    ),
    _rest_only(
        "workspacepromotions.execute_workspace_promotion_canary", "POST", "/api/workspace-promotions/artifacts/{artifact_id}/canary",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.execute_workspace_promotion_canary",
    ),
    _rest_only(
        "workspacepromotions.get_live_workspace_release", "GET", "/api/workspace-promotions/live",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.get_live_workspace_release",
    ),
    _rest_only(
        "workspacepromotions.get_solution_deploy_obligation", "GET", "/api/workspace-promotions/solution-deploy-obligations/{obligation_id}",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.get_solution_deploy_obligation",
    ),
    _rest_only(
        "workspacepromotions.get_workspace_promotion_artifact", "GET", "/api/workspace-promotions/artifacts/{artifact_id}",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.get_workspace_promotion_artifact",
    ),
    _rest_only(
        "workspacepromotions.get_workspace_release_status", "GET", "/api/workspace-promotions/releases/{release_id}",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.get_workspace_release_status",
    ),
    _rest_only(
        "workspacepromotions.get_workspace_source_release", "GET", "/api/workspace-promotions/source-releases/{source_release_id}",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.get_workspace_source_release",
    ),
    _rest_only(
        "workspacepromotions.inspect_workspace_release_retirement", "GET", "/api/workspace-promotions/live/retirement-inventory",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.inspect_workspace_release_retirement",
    ),
    _rest_only(
        "workspacepromotions.list_solution_deploy_obligations", "GET", "/api/workspace-promotions/solution-deploy-obligations",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.list_solution_deploy_obligations",
    ),
    _rest_only(
        "workspacepromotions.list_workspace_source_releases", "GET", "/api/workspace-promotions/source-releases",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.list_workspace_source_releases",
    ),
    _rest_only(
        "workspacepromotions.prepare_workspace_release", "POST", "/api/workspace-promotions/artifacts/{artifact_id}/prepare",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.prepare_workspace_release",
        async_policy=OperationAsyncPolicy.PLATFORM_JOB,
    ),
    _rest_only(
        "workspacepromotions.preview_workspace_promotion", "POST", "/api/workspace-promotions/preview",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.preview_workspace_promotion",
    ),
    _rest_only(
        "workspacepromotions.retire_workspace_release", "POST", "/api/workspace-promotions/live/retire",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.retire_workspace_release",
    ),
    _rest_only(
        "workspacepromotions.retry_workspace_release_history_lock", "POST", "/api/workspace-promotions/releases/{release_id}/retry-history-lock",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.retry_workspace_release_history_lock",
        async_policy=OperationAsyncPolicy.PLATFORM_JOB,
    ),
    _rest_only(
        "workspacepromotions.set_workspace_source_release_disposition", "POST", "/api/workspace-promotions/source-releases/{source_release_id}/disposition",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.set_workspace_source_release_disposition",
    ),
    _rest_only(
        "workspacepromotions.upload_workspace_promotion_draft", "POST", "/api/workspace-promotions/drafts",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_promotions.upload_workspace_promotion_draft",
    ),
    _rest_only(
        "workspacerepochangesets.abort_workspace_repo_changeset", "POST", "/api/workspace-repo-changesets/{changeset_id}/abort",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.abort_workspace_repo_changeset",
    ),
    _rest_only(
        "workspacerepochangesets.activate_workspace_repo_changeset", "POST", "/api/workspace-repo-changesets/{changeset_id}/activate",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.activate_workspace_repo_changeset",
    ),
    _rest_only(
        "workspacerepochangesets.apply_workspace_repo_git_convergence", "POST", "/api/workspace-repo-changesets/git-convergence/apply",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.apply_workspace_repo_git_convergence",
    ),
    _rest_only(
        "workspacerepochangesets.begin_workspace_repo_changeset", "POST", "/api/workspace-repo-changesets",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.begin_workspace_repo_changeset",
    ),
    _rest_only(
        "workspacerepochangesets.list_recoverable_workspace_repo_git_closures", "GET", "/api/workspace-repo-changesets/recoverable-git-closures",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.list_recoverable_workspace_repo_git_closures",
    ),
    _rest_only(
        "workspacerepochangesets.preview_workspace_repo_git_convergence", "POST", "/api/workspace-repo-changesets/git-convergence/preview",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.preview_workspace_repo_git_convergence",
    ),
    _rest_only(
        "workspacerepochangesets.retry_workspace_repo_git_closure", "POST", "/api/workspace-repo-changesets/{changeset_id}/retry-git-closure",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.retry_workspace_repo_git_closure",
    ),
    _rest_only(
        "workspacerepochangesets.show_workspace_repo_changeset", "GET", "/api/workspace-repo-changesets/{changeset_id}",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.show_workspace_repo_changeset",
    ),
    _rest_only(
        "workspacerepochangesets.stage_workspace_repo_file", "POST", "/api/workspace-repo-changesets/{changeset_id}/files",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.stage_workspace_repo_file",
    ),
    _rest_only(
        "workspacerepochangesets.validate_workspace_repo_changeset", "POST", "/api/workspace-repo-changesets/{changeset_id}/validate",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.validate_workspace_repo_changeset",
    ),
    _rest_only(
        "workspacerepochangesets.workspace_repo_changeset_diff", "GET", "/api/workspace-repo-changesets/{changeset_id}/diff",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.workspace_repo_changeset_diff",
    ),
    _rest_only(
        "workspacerepochangesets.workspace_repo_state", "GET", "/api/workspace-repo-changesets/state",
        OperationTargetKind.WORKSPACE, "src.routers.workspace_repo_changesets.workspace_repo_state",
    ),
)
