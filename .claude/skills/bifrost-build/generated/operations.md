# Bifrost operation reference

Generated from the canonical operation catalog. Use the stable intent
ID when reasoning; select the CLI or MCP binding available in the current
harness. A scope with a note in parentheses is needed only beyond the
caller's own items.

| Intent | CLI | MCP | Scope |
|---|---|---|---|
| `productupdates.get` | — | — | `settings.read` |
| `productupdates.receipts.create` | — | — | `settings.readwrite` |
| `agents.list` | `bifrost agents list` | `bifrost_agent_list` | `agents.readbasic` |
| `agents.get` | `bifrost agents get` | `bifrost_agent_get` | `agents.readbasic` |
| `agents.create` | `bifrost agents create` | `bifrost_agent_create` | `agents.readwrite` (not for your own private agents) |
| `agents.update` | `bifrost agents update` | `bifrost_agent_update` | `agents.readwrite` (not for your own private agents) |
| `agents.delete` | `bifrost agents delete` | `bifrost_agent_delete` | `agents.readwrite` (not for your own private agents) |
| `forms.list` | `bifrost forms list` | `bifrost_form_list` | `forms.readbasic` |
| `forms.get` | `bifrost forms get` | `bifrost_form_get` | `forms.readbasic` |
| `forms.create` | `bifrost forms create` | `bifrost_form_create` | `forms.readwrite` |
| `forms.update` | `bifrost forms update` | `bifrost_form_update` | `forms.readwrite` |
| `forms.delete` | `bifrost forms delete` | `bifrost_form_delete` | `forms.readwrite` |
| `tables.list` | `bifrost tables list` | `bifrost_table_list` | `tables.read` |
| `tables.get` | `bifrost tables get` | `bifrost_table_get` | `tables.read` |
| `tables.create` | `bifrost tables create` | `bifrost_table_create` | `tables.readwrite` |
| `tables.update` | `bifrost tables update` | `bifrost_table_update` | `tables.readwrite` |
| `tables.delete` | `bifrost tables delete` | `bifrost_table_delete` | `tables.readwrite` |
| `apps.list` | `bifrost apps list` | `bifrost_app_list` | `apps.readbasic` |
| `apps.get` | `bifrost apps get` | `bifrost_app_get` | `apps.readbasic` |
| `apps.create` | `bifrost apps create` | `bifrost_app_create` | `apps.readwrite` |
| `apps.update` | `bifrost apps update` | `bifrost_app_update` | `apps.readwrite` |
| `apps.delete` | `bifrost apps delete` | `bifrost_app_delete` | `apps.readwrite` |
| `apps.dependencies.get` | `bifrost apps get-dependencies` | `bifrost_app_dependencies_get` | `apps.readbasic` |
| `apps.dependencies.update` | `bifrost apps update-dependencies` | `bifrost_app_dependencies_update` | `apps.readwrite` |
| `apps.validate` | `bifrost apps validate` | `bifrost_app_validate` | `apps.readbasic` |
| `apps.publish` | `bifrost apps publish` | `bifrost_app_publish` | `apps.publish` |
| `platform.jobs.get` | `bifrost platform-jobs get` | `bifrost_platform_job_get` | `platformjobs.read.all` (only for other people's items) |
| `apps.replace` | `bifrost apps replace` | `bifrost_app_replace` | `apps.readwrite` |
| `solutions.list` | — | `bifrost_solution_list` | `solutions.read` |
| `solutions.get` | — | `bifrost_solution_get` | `solutions.read` |
| `solutions.create` | `bifrost solution create` | `bifrost_solution_create` | `solutions.readwrite` |
| `solutions.update` | — | `bifrost_solution_update` | `solutions.readwrite` |
| `solutions.delete` | — | `bifrost_solution_delete` | `solutions.readwrite`, `solutions.deploy` |
| `solutions.sync` | — | `bifrost_solution_sync` | `solutions.readwrite`, `solutions.deploy` |
| `solutions.export` | `bifrost solution export` | — | `solutions.read`, `solutions.build` |
| `solutions.deploy` | `bifrost solution deploy` | — | `solutions.readwrite`, `solutions.deploy` |
| `solutions.install` | `bifrost solution install` | — | `solutions.deploy` |
| `solutions.capture` | `bifrost solution capture` | — | `solutions.readwrite`, `solutions.build` |
| `workflows.list` | `bifrost workflows list` | `bifrost_workflow_list` | `workflows.read` |
| `workflows.validate` | `bifrost workflows validate` | `bifrost_workflow_validate` | `workflows.read` |
| `workflows.register` | `bifrost workflows register` | `bifrost_workflow_register` | `workflows.readwrite`, `repository.read` |
| `workflows.execute` | `bifrost workflows execute` | `bifrost_workflow_execute` | `workflows.execute` |
| `workflows.get` | `bifrost workflows get` | `bifrost_workflow_get` | `workflows.read` |
| `workflows.update` | `bifrost workflows update` | `bifrost_workflow_update` | `workflows.readwrite` |
| `workflows.delete` | `bifrost workflows delete` | `bifrost_workflow_delete` | `workflows.readwrite`, `repository.readwrite` |
| `workflows.roles.grant` | `bifrost workflows grant-role` | `bifrost_workflow_role_grant` | `workflows.readwrite` |
| `workflows.roles.revoke` | `bifrost workflows revoke-role` | `bifrost_workflow_role_revoke` | `workflows.readwrite` |
| `integrations.list` | `bifrost integrations list` | `bifrost_integration_list` | `integrations.read` |
| `integrations.get` | `bifrost integrations get` | `bifrost_integration_get` | `integrations.read` |
| `integrations.create` | `bifrost integrations create` | `bifrost_integration_create` | `integrations.readwrite` |
| `integrations.update` | `bifrost integrations update` | `bifrost_integration_update` | `integrations.readwrite` |
| `integrations.delete` | — | — | `integrations.readwrite` |
| `integrations.mappings.list` | — | — | `integrations.read` |
| `integrations.mappings.get` | — | — | `integrations.read` |
| `integrations.mappings.get_by_org` | — | — | `integrations.read` |
| `integrations.mappings.create` | `bifrost integrations create-mapping` | `bifrost_integration_mapping_create` | `integrations.readwrite` |
| `integrations.mappings.update` | `bifrost integrations update-mapping` | `bifrost_integration_mapping_update` | `integrations.readwrite` |
| `integrations.config.get` | — | — | `integrations.read` |
| `integrations.config.update` | — | — | `integrations.readwrite` |
| `integrations.mappings.batch` | — | — | `integrations.readwrite` |
| `integrations.mappings.delete` | — | — | `integrations.readwrite` |
| `integrations.mappings.authorize` | — | — | `integrations.readwrite` |
| `integrations.mappings.disconnect` | — | — | `integrations.readwrite` |
| `integrations.mappings.refresh` | — | — | `integrations.readwrite` |
| `integrations.oauth.get` | — | — | `integrations.read` |
| `integrations.oauth.authorize` | — | — | `integrations.read` |
| `integrations.oauth.entity_id_source.update` | — | — | `integrations.readwrite` |
| `integrations.oauth.entity_id_source.delete` | — | — | `integrations.readwrite` |
| `integrations.test` | — | — | `integrations.read` |
| `integrations.generate_sdk` | — | — | `integrations.readwrite` |
| `executions.list` | `bifrost workflows list-executions` | `bifrost_execution_list` | `executions.readbasic` |
| `executions.get` | `bifrost workflows get-execution` | `bifrost_execution_get` | `executions.readbasic` |
| `knowledge.namespaces.list` | `bifrost knowledge list-namespaces` | `bifrost_knowledge_namespace_list` | `knowledge.read` |
| `knowledge.documents.list` | `bifrost knowledge list-documents` | `bifrost_knowledge_document_list` | `knowledge.read` |
| `knowledge.documents.get` | `bifrost knowledge get-document` | `bifrost_knowledge_document_get` | `knowledge.read` |
| `knowledge.documents.create` | `bifrost knowledge create-document` | `bifrost_knowledge_document_create` | `knowledge.readwrite` |
| `knowledge.documents.update` | `bifrost knowledge update-document` | `bifrost_knowledge_document_update` | `knowledge.readwrite` |
| `knowledge.documents.delete` | `bifrost knowledge delete-document` | `bifrost_knowledge_document_delete` | `knowledge.readwrite` |
| `roles.list` | `bifrost roles list` | `bifrost_role_list` | `roles.read` |
| `roles.get` | `bifrost roles get` | `bifrost_role_get` | `roles.read` |
| `roles.create` | `bifrost roles create` | `bifrost_role_create` | `roles.readwrite` |
| `roles.update` | `bifrost roles update` | `bifrost_role_update` | `roles.readwrite` |
| `roles.delete` | `bifrost roles delete` | `bifrost_role_delete` | `roles.readwrite` |
| `roles.bridge_grants.get` | `bifrost roles bridge-grants` | `bifrost_role_bridge_grants_get` | `roles.read` |
| `roles.bridge_grants.update` | `bifrost roles set-bridge-grants` | `bifrost_role_bridge_grants_update` | `roles.readwrite` |
| `roles.users.list` | — | — | `roleassignments.read` |
| `roles.users.assign` | — | — | `roleassignments.readwrite` |
| `roles.users.remove` | — | — | `roleassignments.readwrite` |
| `roles.users.bulk_remove` | — | — | `roleassignments.readwrite` |
| `roles.forms.list` | — | — | `roles.read` |
| `roles.forms.assign` | — | — | `forms.readwrite` |
| `roles.forms.remove` | — | — | `forms.readwrite` |
| `roles.forms.bulk_remove` | — | — | `forms.readwrite` |
| `roles.agents.list` | — | — | `roles.read` |
| `roles.agents.assign` | — | — | `agents.readwrite` |
| `roles.agents.remove` | — | — | `agents.readwrite` |
| `roles.agents.bulk_remove` | — | — | `agents.readwrite` |
| `roles.apps.list` | — | — | `roles.read` |
| `roles.apps.assign` | — | — | `apps.readwrite` |
| `roles.apps.bulk_remove` | — | — | `apps.readwrite` |
| `roles.workflows.list` | — | — | `roles.read` |
| `roles.workflows.assign` | — | — | `workflows.readwrite` |
| `roles.workflows.bulk_remove` | — | — | `workflows.readwrite` |
| `roles.knowledge.list` | — | — | `roles.read` |
| `roles.knowledge.assign` | — | — | `roles.readwrite` |
| `roles.knowledge.bulk_remove` | — | — | `roles.readwrite` |
| `users.list` | — | — | `users.read` |
| `users.get` | — | — | `users.read` |
| `users.create` | — | — | `users.readwrite`, `userlifecycle.readwrite` |
| `users.update` | — | — | `userlifecycle.readwrite` |
| `users.delete` | — | — | `userlifecycle.readwrite` |
| `users.bulk_update` | — | — | `userlifecycle.readwrite` |
| `users.invites.resend` | — | — | `users.readwrite` |
| `users.invites.send` | — | — | `users.readwrite` |
| `users.invites.regenerate` | — | — | `users.readwrite` |
| `users.invites.revoke` | — | — | `users.readwrite` |
| `users.mfa.reset` | — | — | `users.readwrite` |
| `users.roles.list` | — | — | `roleassignments.read` |
| `users.forms.list` | — | — | `roleassignments.read` |
| `claims.list` | `bifrost claims list` | `bifrost_claim_list` | `claims.read` |
| `claims.get` | `bifrost claims get` | `bifrost_claim_get` | `claims.read` |
| `claims.create` | `bifrost claims create` | `bifrost_claim_create` | `claims.readwrite` |
| `claims.update` | `bifrost claims update` | `bifrost_claim_update` | `claims.readwrite` |
| `claims.delete` | `bifrost claims delete` | `bifrost_claim_delete` | `claims.readwrite` |
| `files.policies.list` | `bifrost files policies list` | `bifrost_file_policy_list` | `filepolicies.read` |
| `files.policies.get` | `bifrost files policies get` | `bifrost_file_policy_get` | `filepolicies.read` |
| `files.policies.set` | `bifrost files policies set` | `bifrost_file_policy_set` | `filepolicies.readwrite` |
| `files.policies.delete` | `bifrost files policies delete` | `bifrost_file_policy_delete` | `filepolicies.readwrite` |
| `files.policies.test` | — | — | `filepolicies.read` |
| `files.structure.list` | — | — | `filepolicies.read` |
| `configs.list` | `bifrost configs list` | `bifrost_config_list` | `configs.read` |
| `configs.get` | `bifrost configs get` | `bifrost_config_get` | `configs.read` |
| `configs.create` | `bifrost configs create` | `bifrost_config_create` | `configs.readwrite` |
| `configs.update` | `bifrost configs update` | `bifrost_config_update` | `configs.readwrite` |
| `configs.delete` | `bifrost configs delete` | `bifrost_config_delete` | `configs.readwrite` |
| `policy.rules.list` | `bifrost policy-rules list` | `bifrost_policy_rule_list` | `policyrules.read` |
| `policy.rules.create` | `bifrost policy-rules create` | `bifrost_policy_rule_create` | `policyrules.readwrite` |
| `policy.rules.get` | `bifrost policy-rules get` | `bifrost_policy_rule_get` | `policyrules.read` |
| `policy.rules.update` | `bifrost policy-rules update` | `bifrost_policy_rule_update` | `policyrules.readwrite` |
| `policy.rules.delete` | `bifrost policy-rules delete` | `bifrost_policy_rule_delete` | `policyrules.readwrite` |
| `policy.rules.list_usages` | `bifrost policy-rules list-usages` | `bifrost_policy_rule_usage_list` | `policyrules.read` |
| `organizations.list` | `bifrost organizations list` | `bifrost_organization_list` | `organizations.read` |
| `organizations.get` | `bifrost organizations get` | `bifrost_organization_get` | `organizations.read` |
| `organizations.create` | `bifrost organizations create` | `bifrost_organization_create` | `organizations.readwrite` |
| `organizations.update` | `bifrost organizations update` | `bifrost_organization_update` | `organizations.readwrite` |
| `organizations.delete` | `bifrost organizations delete` | `bifrost_organization_delete` | `organizations.readwrite` |
| `events.sources.list` | `bifrost events list-sources` | `bifrost_event_source_list` | `events.read` |
| `events.sources.get` | `bifrost events get-source` | `bifrost_event_source_get` | `events.read` |
| `events.sources.create` | `bifrost events create-source` | `bifrost_event_source_create` | `events.readwrite` |
| `events.sources.update` | `bifrost events update-source` | `bifrost_event_source_update` | `events.readwrite` |
| `events.sources.delete` | `bifrost events delete-source` | `bifrost_event_source_delete` | `events.readwrite` |
| `events.subscriptions.list` | `bifrost events list-subscriptions` | `bifrost_event_subscription_list` | `events.read` |
| `events.subscriptions.get` | `bifrost events get-subscription` | `bifrost_event_subscription_get` | `events.read` |
| `events.subscriptions.create` | `bifrost events create-subscription` | `bifrost_event_subscription_create` | `events.readwrite` |
| `events.subscriptions.update` | `bifrost events update-subscription` | `bifrost_event_subscription_update` | `events.readwrite` |
| `events.subscriptions.delete` | `bifrost events delete-subscription` | `bifrost_event_subscription_delete` | `events.readwrite` |
| `events.webhook_adapters.list` | `bifrost events list-webhook-adapters` | `bifrost_event_webhook_adapter_list` | `events.read` |
| `workspace.files.list` | `bifrost files list` | `bifrost_file_list` | `repository.read` |
| `workspace.files.search` | `bifrost files search` | `bifrost_file_search` | `repository.read` |
| `workspace.files.read` | `bifrost files read` | `bifrost_file_read` | `repository.read` |
| `workspace.files.stat` | `bifrost files stat` | `bifrost_file_stat` | `repository.read` |
| `workspace.files.exists` | `bifrost files exists` | `bifrost_file_exists` | `repository.read` |
| `workspace.files.write` | `bifrost files write` | `bifrost_file_write` | `repository.readwrite` |
| `workspace.files.delete` | `bifrost files delete` | `bifrost_file_delete` | `repository.readwrite` |
| `workspace.files.pull` | — | — | `repository.read` |
| `workspace.files.manifest` | — | — | `repository.read` |
| `workspace.files.watch` | — | — | `repository.read` |
| `workspace.files.watchers` | — | — | `repository.read` |
| `workspace.files.editor.list` | — | — | `repository.read` |
| `workspace.files.editor.read` | — | — | `repository.read` |
| `workspace.files.editor.write` | — | — | `repository.readwrite` |
| `workspace.files.editor.folder.create` | — | — | `repository.readwrite` |
| `workspace.files.editor.delete` | — | — | `repository.readwrite` |
| `workspace.files.editor.rename` | — | — | `repository.readwrite` |
| `agentactionapprovals.approve_approval` | — | — | `agents.readwrite.all` |
| `agentactionapprovals.deny_approval` | — | — | `agents.readwrite.all` |
| `agentactionapprovals.list_approvals` | — | — | `agents.readwrite.all` |
| `applications.inspect_github_app_publication` | — | — | — |
| `applications.publish_github_app_source` | — | — | — |
| `appservice.app_service_metrics` | — | — | `platform.read` |
| `auth.authorize_cli_native_auth` | — | — | — |
| `auth.exchange_cli_native_auth_token` | — | — | — |
| `auth.start_cli_native_auth` | — | — | — |
| `chat.submit_teams_event` | — | — | `agents.execute` |
| `cli.sdk_integration_request_slot_acquire` | — | — | — |
| `cli.sdk_integration_request_slot_release` | — | — | — |
| `codexgateway.create_gateway_key` | — | — | — |
| `codexgateway.create_response` | — | — | — |
| `codexgateway.create_response.post` | — | — | — |
| `codexgateway.disconnect_oauth_account` | — | — | — |
| `codexgateway.get_oauth_status` | — | — | — |
| `codexgateway.import_oauth_auth_cache` | — | — | — |
| `codexgateway.list_gateway_keys` | — | — | — |
| `codexgateway.revoke_gateway_key` | — | — | — |
| `codexgateway.start_oauth_connect` | — | — | — |
| `devicecontrolkeys.create_control_key_route` | — | — | `devices.readwrite`, `devices.execute` |
| `devicecontrolkeys.get_control_key_route` | — | — | `devices.readwrite` |
| `devicecontrolkeys.list_control_keys_route` | — | — | `devices.readwrite` |
| `devicecontrolkeys.revoke_control_key_endpoint` | — | — | `devices.readwrite` |
| `devicecontrolkeys.rotate_control_key_endpoint` | — | — | `devices.readwrite`, `devices.execute` |
| `deviceprotocol.claim_route` | — | — | — |
| `deviceprotocol.heartbeat_route` | — | — | — |
| `deviceprotocol.logs_route` | — | — | — |
| `deviceprotocol.result_route` | — | — | — |
| `deviceprotocol.running_route` | — | — | — |
| `devices.cancel_job_route` | — | — | `devices.execute` |
| `devices.create_device_route` | — | — | `devices.readwrite` |
| `devices.create_job_route` | — | — | `devices.execute` |
| `devices.disable_device_route` | — | — | `devices.readwrite` |
| `devices.enable_device_route` | — | — | `devices.readwrite` |
| `devices.enroll_device_route` | — | — | — |
| `devices.get_device_route` | — | — | `devices.readwrite` |
| `devices.get_job_logs_route` | — | — | `devices.execute` |
| `devices.get_job_route` | — | — | `devices.execute` |
| `devices.list_devices_route` | — | — | `devices.readwrite` |
| `devices.list_jobs_route` | — | — | `devices.execute` |
| `devices.rotate_device_key_endpoint` | — | — | `devices.readwrite` |
| `externalworkers.enroll` | — | — | — |
| `externalworkers.status` | — | — | `platform.read` |
| `externalworkers.identity` | — | — | — |
| `files.preview_workspace_file_impact` | — | — | `repository.read` |
| `mcp.resolve_gateway_operation_receipt` | — | — | `mcp.readwrite` |
| `packages.get_package_installation_progress` | — | — | `platform.read` |
| `runtimemaintenance.enter_maintenance` | — | — | `platform.readwrite` |
| `runtimemaintenance.exit_maintenance` | — | — | `platform.readwrite` |
| `runtimemaintenance.runtime_maintenance_status` | — | — | `platform.read` |
| `runtimemaintenance.seal_maintenance` | — | — | `platform.readwrite` |
| `sdkmodules.read_deployment_resource` | — | — | `repository.read` |
| `solutiondeployments.activate_deployment` | — | — | `solutions.deploy` |
| `solutiondeployments.activate_initial_workflow_install` | — | — | `solutions.deploy` |
| `solutiondeployments.activate_live_handoff` | — | — | `solutions.deploy` |
| `solutiondeployments.activate_repo_workflow_adoption` | — | — | `solutions.deploy` |
| `solutiondeployments.activate_source_revision` | — | — | `solutions.deploy` |
| `solutiondeployments.activate_workflow_revision` | — | — | `solutions.deploy` |
| `solutiondeployments.build_live_handoff_candidate` | — | — | `solutions.deploy` |
| `solutiondeployments.create_deployment` | — | — | `solutions.deploy` |
| `solutiondeployments.deliver_github_package` | — | — | — |
| `solutiondeployments.deliver_github_source` | — | — | — |
| `solutiondeployments.deployment_capabilities` | — | — | `solutions.read` |
| `solutiondeployments.inspect_active_deployment` | — | — | `solutions.read` |
| `solutiondeployments.inspect_deployment` | — | — | `solutions.read` |
| `solutiondeployments.inspect_github_package` | — | — | — |
| `solutiondeployments.inspect_initial_workflow_install` | — | — | `solutions.deploy` |
| `solutiondeployments.inspect_repo_workflow_adoption` | — | — | `solutions.deploy` |
| `solutiondeployments.inspect_source_revision` | — | — | `solutions.deploy` |
| `solutiondeployments.inspect_workflow_revision` | — | — | `solutions.deploy` |
| `solutiondeployments.preflight_live_handoff` | — | — | `solutions.deploy` |
| `solutiondeployments.preview_shared_table_bindings` | — | — | `solutions.read` |
| `solutiondeployments.recover_github_package` | — | — | — |
| `solutiondeployments.rollback_deployment` | — | — | `solutions.deploy` |
| `solutiondeployments.rollback_live_handoff` | — | — | `solutions.deploy` |
| `solutiondeployments.stage_initial_workflow_install` | — | — | `solutions.deploy` |
| `solutiondeployments.stage_repo_workflow_adoption` | — | — | `solutions.deploy` |
| `solutiondeployments.stage_source_revision` | — | — | `solutions.deploy` |
| `solutiondeployments.stage_workflow_revision` | — | — | `solutions.deploy` |
| `solutions.reconcile_solution_deployment` | — | — | `solutions.deploy` |
| `tables.update_document_conditional` | — | — | — |
| `users.create_external_identity` | — | — | `userlifecycle.readwrite` |
| `users.delete_external_identity` | — | — | `userlifecycle.readwrite` |
| `users.list_external_identities` | — | — | `userlifecycle.read` |
| `workers.list_worker_control_commands` | — | — | `platform.read` |
| `workspaceadmin.inspect_oauth` | — | — | `integrations.read` |
| `workspaceadmin.reconcile_oauth` | — | — | `integrations.readwrite` |
| `workspaceadmin.recover_oauth` | — | — | `integrations.readwrite` |
| `workspaceadmin.redact_execution` | — | — | `executions.readwrite.all` |
| `workspacepromotions.activate_workspace_release` | — | — | `repository.readwrite` |
| `workspacepromotions.declare_workspace_source_release` | — | — | `repository.readwrite` |
| `workspacepromotions.declare_workspace_source_release_from_github` | — | — | — |
| `workspacepromotions.enqueue_workspace_promotion_preview` | — | — | `repository.readwrite` |
| `workspacepromotions.execute_workspace_promotion_canary` | — | — | `repository.readwrite` |
| `workspacepromotions.get_live_workspace_release` | — | — | `repository.read` |
| `workspacepromotions.get_solution_deploy_obligation` | — | — | `repository.read` |
| `workspacepromotions.get_workspace_promotion_artifact` | — | — | `repository.read` |
| `workspacepromotions.get_workspace_release_status` | — | — | `repository.read` |
| `workspacepromotions.get_workspace_source_release` | — | — | `repository.read` |
| `workspacepromotions.inspect_workspace_release_retirement` | — | — | `repository.read` |
| `workspacepromotions.list_solution_deploy_obligations` | — | — | `repository.read` |
| `workspacepromotions.list_workspace_source_releases` | — | — | `repository.read` |
| `workspacepromotions.prepare_workspace_release` | — | — | `repository.readwrite` |
| `workspacepromotions.preview_workspace_promotion` | — | — | `repository.readwrite` |
| `workspacepromotions.retire_workspace_release` | — | — | `repository.readwrite` |
| `workspacepromotions.retry_workspace_release_history_lock` | — | — | `repository.readwrite` |
| `workspacepromotions.set_workspace_source_release_disposition` | — | — | `repository.readwrite` |
| `workspacepromotions.upload_workspace_promotion_draft` | — | — | `repository.readwrite` |
| `workspacerepochangesets.abort_workspace_repo_changeset` | — | — | `repository.readwrite` |
| `workspacerepochangesets.activate_workspace_repo_changeset` | — | — | `repository.readwrite` |
| `workspacerepochangesets.apply_workspace_repo_git_convergence` | — | — | `repository.readwrite` |
| `workspacerepochangesets.begin_workspace_repo_changeset` | — | — | `repository.readwrite` |
| `workspacerepochangesets.list_recoverable_workspace_repo_git_closures` | — | — | `repository.read` |
| `workspacerepochangesets.preview_workspace_repo_git_convergence` | — | — | `repository.read` |
| `workspacerepochangesets.retry_workspace_repo_git_closure` | — | — | `repository.readwrite` |
| `workspacerepochangesets.show_workspace_repo_changeset` | — | — | `repository.read` |
| `workspacerepochangesets.stage_workspace_repo_file` | — | — | `repository.readwrite` |
| `workspacerepochangesets.validate_workspace_repo_changeset` | — | — | `repository.readwrite` |
| `workspacerepochangesets.workspace_repo_changeset_diff` | — | — | `repository.read` |
| `workspacerepochangesets.workspace_repo_state` | — | — | `repository.read` |
