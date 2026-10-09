# Bifrost operation reference

Generated from the canonical operation catalog. Use the stable intent
ID when reasoning; select the CLI or MCP binding available in the current
harness.

| Intent | CLI | MCP | Scope |
|---|---|---|---|
| `agents.list` | `bifrost agents list` | `bifrost_list_agents` | `agents.read` |
| `agents.get` | `bifrost agents get` | `bifrost_get_agent` | `agents.read` |
| `agents.create` | `bifrost agents create` | `bifrost_create_agent` | `agents.readwrite` |
| `agents.update` | `bifrost agents update` | `bifrost_update_agent` | `agents.readwrite` |
| `agents.delete` | `bifrost agents delete` | `bifrost_delete_agent` | `agents.readwrite` |
| `forms.list` | `bifrost forms list` | `bifrost_list_forms` | `forms.read` |
| `forms.get` | `bifrost forms get` | `bifrost_get_form` | `forms.read` |
| `forms.create` | `bifrost forms create` | `bifrost_create_form` | `forms.readwrite` |
| `forms.update` | `bifrost forms update` | `bifrost_update_form` | `forms.readwrite` |
| `forms.delete` | `bifrost forms delete` | `bifrost_delete_form` | `forms.readwrite` |
| `tables.list` | `bifrost tables list` | `bifrost_list_tables` | `tables.read` |
| `tables.get` | `bifrost tables get` | `bifrost_get_table` | `tables.read` |
| `tables.create` | `bifrost tables create` | `bifrost_create_table` | `tables.readwrite` |
| `tables.update` | `bifrost tables update` | `bifrost_update_table` | `tables.readwrite` |
| `tables.delete` | `bifrost tables delete` | `bifrost_delete_table` | `tables.readwrite` |
| `apps.list` | `bifrost apps list` | `bifrost_list_apps` | `apps.read` |
| `apps.get` | `bifrost apps get` | `bifrost_get_app` | `apps.read` |
| `apps.create` | `bifrost apps create` | `bifrost_create_app` | `apps.readwrite` |
| `apps.update` | `bifrost apps update` | `bifrost_update_app` | `apps.readwrite` |
| `apps.delete` | `bifrost apps delete` | `bifrost_delete_app` | `apps.readwrite` |
| `apps.dependencies.get` | `bifrost apps get-dependencies` | `bifrost_get_app_dependencies` | `apps.read` |
| `apps.dependencies.update` | `bifrost apps update-dependencies` | `bifrost_update_app_dependencies` | `apps.readwrite` |
| `apps.validate` | `bifrost apps validate` | `bifrost_validate_app` | `apps.read` |
| `apps.publish` | `bifrost apps publish` | `bifrost_publish_app` | `apps.deploy.execute` |
| `platform.jobs.get` | `bifrost platform-jobs get` | `bifrost_get_platform_job` | — |
| `apps.replace` | `bifrost apps replace` | `bifrost_replace_app` | `apps.readwrite` |
| `solutions.list` | — | `bifrost_list_solutions` | `solutions.read` |
| `solutions.get` | — | `bifrost_get_solution` | `solutions.read` |
| `solutions.create` | `bifrost solution create` | `bifrost_create_solution` | `solutions.readwrite` |
| `solutions.update` | — | `bifrost_update_solution` | `solutions.readwrite` |
| `solutions.delete` | — | `bifrost_delete_solution` | `solutions.readwrite`, `solutions.deploy.execute` |
| `solutions.sync` | — | `bifrost_sync_solution` | `solutions.readwrite`, `solutions.deploy.execute` |
| `solutions.export` | `bifrost solution export` | — | `solutions.read`, `solutions.build.execute` |
| `solutions.deploy` | `bifrost solution deploy` | — | `solutions.readwrite`, `solutions.deploy.execute` |
| `solutions.install` | `bifrost solution install` | — | `solutions.deploy.execute` |
| `solutions.capture` | `bifrost solution capture` | — | `solutions.readwrite`, `solutions.build.execute` |
| `workflows.list` | `bifrost workflows list` | `bifrost_list_workflows` | `workflows.read` |
| `workflows.validate` | `bifrost workflows validate` | `bifrost_validate_workflow` | `workflows.read` |
| `workflows.register` | `bifrost workflows register` | `bifrost_register_workflow` | `workflows.readwrite`, `repository.read` |
| `workflows.execute` | `bifrost workflows execute` | `bifrost_execute_workflow` | `workflows.execute` |
| `workflows.update` | `bifrost workflows update` | `bifrost_update_workflow` | `workflows.readwrite` |
| `workflows.delete` | `bifrost workflows delete` | `bifrost_delete_workflow` | `workflows.readwrite`, `repository.readwrite` |
| `workflows.roles.grant` | `bifrost workflows grant-role` | `bifrost_grant_workflow_role` | `workflows.readwrite`, `roles.readwrite` |
| `workflows.roles.revoke` | `bifrost workflows revoke-role` | `bifrost_revoke_workflow_role` | `workflows.readwrite`, `roles.readwrite` |
| `integrations.list` | `bifrost integrations list` | `bifrost_list_integrations` | `integrations.read` |
| `integrations.get` | `bifrost integrations get` | `bifrost_get_integration` | `integrations.read` |
| `integrations.create` | `bifrost integrations create` | `bifrost_create_integration` | `integrations.readwrite` |
| `integrations.update` | `bifrost integrations update` | `bifrost_update_integration` | `integrations.readwrite` |
| `integrations.delete` | — | — | `integrations.readwrite` |
| `integrations.mappings.list` | — | — | `integrations.read` |
| `integrations.mappings.get` | — | — | `integrations.read` |
| `integrations.mappings.get_by_org` | — | — | `integrations.read` |
| `integrations.mappings.create` | `bifrost integrations create-mapping` | `bifrost_create_integration_mapping` | `integrations.readwrite` |
| `integrations.mappings.update` | `bifrost integrations update-mapping` | `bifrost_update_integration_mapping` | `integrations.readwrite` |
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
| `executions.list` | `bifrost workflows list-executions` | `bifrost_list_workflow_executions` | `executions.read` |
| `executions.get` | `bifrost workflows get-execution` | `bifrost_get_workflow_execution` | `executions.read` |
| `knowledge.namespaces.list` | `bifrost knowledge list-namespaces` | `bifrost_list_knowledge_namespaces` | `knowledge.read` |
| `knowledge.documents.list` | `bifrost knowledge list-documents` | `bifrost_list_knowledge_documents` | `knowledge.read` |
| `knowledge.documents.get` | `bifrost knowledge get-document` | `bifrost_get_knowledge_document` | `knowledge.read` |
| `knowledge.documents.create` | `bifrost knowledge create-document` | `bifrost_create_knowledge_document` | `knowledge.readwrite` |
| `knowledge.documents.update` | `bifrost knowledge update-document` | `bifrost_update_knowledge_document` | `knowledge.readwrite` |
| `knowledge.documents.delete` | `bifrost knowledge delete-document` | `bifrost_delete_knowledge_document` | `knowledge.readwrite` |
| `roles.list` | `bifrost roles list` | `bifrost_list_roles` | `roles.read` |
| `roles.get` | `bifrost roles get` | `bifrost_get_role` | `roles.read` |
| `roles.create` | `bifrost roles create` | `bifrost_create_role` | `roles.readwrite` |
| `roles.update` | `bifrost roles update` | `bifrost_update_role` | `roles.readwrite` |
| `roles.delete` | `bifrost roles delete` | `bifrost_delete_role` | `roles.readwrite` |
| `roles.users.list` | — | — | `roles.read` |
| `roles.users.assign` | — | — | `roles.readwrite` |
| `roles.users.remove` | — | — | `roles.readwrite` |
| `roles.users.bulk_remove` | — | — | `roles.readwrite` |
| `roles.forms.list` | — | — | `roles.read` |
| `roles.forms.assign` | — | — | `roles.readwrite` |
| `roles.forms.remove` | — | — | `roles.readwrite` |
| `roles.forms.bulk_remove` | — | — | `roles.readwrite` |
| `roles.agents.list` | — | — | `roles.read` |
| `roles.agents.assign` | — | — | `roles.readwrite` |
| `roles.agents.remove` | — | — | `roles.readwrite` |
| `roles.agents.bulk_remove` | — | — | `roles.readwrite` |
| `roles.apps.list` | — | — | `roles.read` |
| `roles.apps.assign` | — | — | `roles.readwrite` |
| `roles.apps.bulk_remove` | — | — | `roles.readwrite` |
| `roles.workflows.list` | — | — | `roles.read` |
| `roles.workflows.assign` | — | — | `roles.readwrite` |
| `roles.workflows.bulk_remove` | — | — | `roles.readwrite` |
| `roles.knowledge.list` | — | — | `roles.read` |
| `roles.knowledge.assign` | — | — | `roles.readwrite` |
| `roles.knowledge.bulk_remove` | — | — | `roles.readwrite` |
| `users.list` | — | — | `organizations.read` |
| `users.get` | — | — | `organizations.read` |
| `users.create` | — | — | `organizations.readwrite` |
| `users.update` | — | — | `organizations.readwrite` |
| `users.delete` | — | — | `organizations.readwrite` |
| `users.bulk_update` | — | — | `organizations.readwrite` |
| `users.invites.resend` | — | — | `organizations.readwrite` |
| `users.invites.send` | — | — | `organizations.readwrite` |
| `users.invites.regenerate` | — | — | `organizations.readwrite` |
| `users.invites.revoke` | — | — | `organizations.readwrite` |
| `users.roles.list` | — | — | `roles.read` |
| `users.forms.list` | — | — | `roles.read` |
| `claims.list` | `bifrost claims list` | `bifrost_list_claims` | `claims.read` |
| `claims.get` | `bifrost claims get` | `bifrost_get_claim` | `claims.read` |
| `claims.create` | `bifrost claims create` | `bifrost_create_claim` | `claims.readwrite` |
| `claims.update` | `bifrost claims update` | `bifrost_update_claim` | `claims.readwrite` |
| `claims.delete` | `bifrost claims delete` | `bifrost_delete_claim` | `claims.readwrite` |
| `files.policies.list` | `bifrost files policies list` | `bifrost_list_file_policies` | `filepolicies.read` |
| `files.policies.get` | `bifrost files policies get` | `bifrost_get_file_policy` | `filepolicies.read` |
| `files.policies.set` | `bifrost files policies set` | `bifrost_set_file_policy` | `filepolicies.readwrite` |
| `files.policies.delete` | `bifrost files policies delete` | `bifrost_delete_file_policy` | `filepolicies.readwrite` |
| `files.policies.test` | — | — | `filepolicies.read` |
| `files.structure.list` | — | — | `filepolicies.read` |
| `configs.list` | `bifrost configs list` | `bifrost_list_configs` | `configs.read` |
| `configs.create` | `bifrost configs create` | `bifrost_create_config` | `configs.readwrite` |
| `configs.update` | `bifrost configs update` | `bifrost_update_config` | `configs.readwrite` |
| `configs.delete` | `bifrost configs delete` | `bifrost_delete_config` | `configs.readwrite` |
| `policy.rules.list` | `bifrost policy-rules list` | `bifrost_list_policy_rules` | `policyrules.read` |
| `policy.rules.create` | `bifrost policy-rules create` | `bifrost_create_policy_rule` | `policyrules.readwrite` |
| `policy.rules.update` | `bifrost policy-rules update` | `bifrost_update_policy_rule` | `policyrules.readwrite` |
| `policy.rules.delete` | `bifrost policy-rules delete` | `bifrost_delete_policy_rule` | `policyrules.readwrite` |
| `policy.rules.list_usages` | `bifrost policy-rules list-usages` | `bifrost_list_policy_rule_usages` | `policyrules.read` |
| `organizations.list` | `bifrost organizations list` | `bifrost_list_organizations` | `organizations.read` |
| `organizations.get` | `bifrost organizations get` | `bifrost_get_organization` | `organizations.read` |
| `organizations.create` | `bifrost organizations create` | `bifrost_create_organization` | `organizations.readwrite` |
| `organizations.update` | `bifrost organizations update` | `bifrost_update_organization` | `organizations.readwrite` |
| `organizations.delete` | `bifrost organizations delete` | `bifrost_delete_organization` | `organizations.readwrite` |
| `events.sources.list` | `bifrost events list-sources` | `bifrost_list_event_sources` | `events.read` |
| `events.sources.get` | `bifrost events get-source` | `bifrost_get_event_source` | `events.read` |
| `events.sources.create` | `bifrost events create-source` | `bifrost_create_event_source` | `events.readwrite` |
| `events.sources.update` | `bifrost events update-source` | `bifrost_update_event_source` | `events.readwrite` |
| `events.sources.delete` | `bifrost events delete-source` | `bifrost_delete_event_source` | `events.readwrite` |
| `events.subscriptions.list` | `bifrost events list-subscriptions` | `bifrost_list_event_subscriptions` | `events.read` |
| `events.subscriptions.create` | `bifrost events create-subscription` | `bifrost_create_event_subscription` | `events.readwrite` |
| `events.subscriptions.update` | `bifrost events update-subscription` | `bifrost_update_event_subscription` | `events.readwrite` |
| `events.subscriptions.delete` | `bifrost events delete-subscription` | `bifrost_delete_event_subscription` | `events.readwrite` |
| `events.webhook_adapters.list` | `bifrost events list-webhook-adapters` | `bifrost_list_event_webhook_adapters` | `events.read` |
| `workspace.files.list` | `bifrost files list` | `bifrost_list_files` | `repository.read` |
| `workspace.files.search` | `bifrost files search` | `bifrost_search_files` | `repository.read` |
| `workspace.files.read` | `bifrost files read` | `bifrost_read_file` | `repository.read` |
| `workspace.files.stat` | `bifrost files stat` | `bifrost_stat_file` | `repository.read` |
| `workspace.files.exists` | `bifrost files exists` | `bifrost_exists_file` | `repository.read` |
| `workspace.files.write` | `bifrost files write` | `bifrost_write_file` | `repository.readwrite` |
| `workspace.files.delete` | `bifrost files delete` | `bifrost_delete_file` | `repository.readwrite` |
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
| `agentactionapprovals.approve_approval` | — | — | — |
| `agentactionapprovals.deny_approval` | — | — | — |
| `agentactionapprovals.list_approvals` | — | — | — |
| `applications.inspect_github_app_publication` | — | — | — |
| `applications.publish_github_app_source` | — | — | — |
| `appservice.app_service_metrics` | — | — | — |
| `auth.authorize_cli_native_auth` | — | — | — |
| `auth.exchange_cli_native_auth_token` | — | — | — |
| `auth.start_cli_native_auth` | — | — | — |
| `chat.submit_teams_event` | — | — | — |
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
| `devicecontrolkeys.create_control_key_route` | — | — | — |
| `devicecontrolkeys.get_control_key_route` | — | — | — |
| `devicecontrolkeys.list_control_keys_route` | — | — | — |
| `devicecontrolkeys.revoke_control_key_endpoint` | — | — | — |
| `devicecontrolkeys.rotate_control_key_endpoint` | — | — | — |
| `deviceprotocol.claim_route` | — | — | — |
| `deviceprotocol.heartbeat_route` | — | — | — |
| `deviceprotocol.logs_route` | — | — | — |
| `deviceprotocol.result_route` | — | — | — |
| `deviceprotocol.running_route` | — | — | — |
| `devices.cancel_job_route` | — | — | — |
| `devices.create_device_route` | — | — | — |
| `devices.create_job_route` | — | — | — |
| `devices.disable_device_route` | — | — | — |
| `devices.enable_device_route` | — | — | — |
| `devices.enroll_device_route` | — | — | — |
| `devices.get_device_route` | — | — | — |
| `devices.get_job_logs_route` | — | — | — |
| `devices.get_job_route` | — | — | — |
| `devices.list_devices_route` | — | — | — |
| `devices.list_jobs_route` | — | — | — |
| `devices.rotate_device_key_endpoint` | — | — | — |
| `externalworkers.enroll` | — | — | — |
| `externalworkers.status` | — | — | — |
| `files.preview_workspace_file_impact` | — | — | — |
| `mcp.resolve_gateway_operation_receipt` | — | — | — |
| `packages.get_package_installation_progress` | — | — | — |
| `runtimemaintenance.enter_maintenance` | — | — | — |
| `runtimemaintenance.exit_maintenance` | — | — | — |
| `runtimemaintenance.runtime_maintenance_status` | — | — | — |
| `runtimemaintenance.seal_maintenance` | — | — | — |
| `sdkmodules.read_deployment_resource` | — | — | — |
| `solutiondeployments.activate_deployment` | — | — | — |
| `solutiondeployments.activate_initial_workflow_install` | — | — | — |
| `solutiondeployments.activate_live_handoff` | — | — | — |
| `solutiondeployments.activate_repo_workflow_adoption` | — | — | — |
| `solutiondeployments.activate_source_revision` | — | — | — |
| `solutiondeployments.activate_workflow_revision` | — | — | — |
| `solutiondeployments.build_live_handoff_candidate` | — | — | — |
| `solutiondeployments.create_deployment` | — | — | — |
| `solutiondeployments.deliver_github_package` | — | — | — |
| `solutiondeployments.deliver_github_source` | — | — | — |
| `solutiondeployments.deployment_capabilities` | — | — | — |
| `solutiondeployments.inspect_active_deployment` | — | — | — |
| `solutiondeployments.inspect_deployment` | — | — | — |
| `solutiondeployments.inspect_github_package` | — | — | — |
| `solutiondeployments.inspect_initial_workflow_install` | — | — | — |
| `solutiondeployments.inspect_repo_workflow_adoption` | — | — | — |
| `solutiondeployments.inspect_source_revision` | — | — | — |
| `solutiondeployments.inspect_workflow_revision` | — | — | — |
| `solutiondeployments.preflight_live_handoff` | — | — | — |
| `solutiondeployments.preview_shared_table_bindings` | — | — | — |
| `solutiondeployments.recover_github_package` | — | — | — |
| `solutiondeployments.rollback_deployment` | — | — | — |
| `solutiondeployments.rollback_live_handoff` | — | — | — |
| `solutiondeployments.stage_initial_workflow_install` | — | — | — |
| `solutiondeployments.stage_repo_workflow_adoption` | — | — | — |
| `solutiondeployments.stage_source_revision` | — | — | — |
| `solutiondeployments.stage_workflow_revision` | — | — | — |
| `solutions.reconcile_solution_deployment` | — | — | — |
| `tables.update_document_conditional` | — | — | — |
| `users.create_external_identity` | — | — | — |
| `users.delete_external_identity` | — | — | — |
| `users.list_external_identities` | — | — | — |
| `workers.list_worker_control_commands` | — | — | — |
| `workspaceadmin.inspect_oauth` | — | — | — |
| `workspaceadmin.reconcile_oauth` | — | — | — |
| `workspaceadmin.recover_oauth` | — | — | — |
| `workspaceadmin.redact_execution` | — | — | — |
| `workspacepromotions.activate_workspace_release` | — | — | — |
| `workspacepromotions.declare_workspace_source_release` | — | — | — |
| `workspacepromotions.declare_workspace_source_release_from_github` | — | — | — |
| `workspacepromotions.enqueue_workspace_promotion_preview` | — | — | — |
| `workspacepromotions.execute_workspace_promotion_canary` | — | — | — |
| `workspacepromotions.get_live_workspace_release` | — | — | — |
| `workspacepromotions.get_solution_deploy_obligation` | — | — | — |
| `workspacepromotions.get_workspace_promotion_artifact` | — | — | — |
| `workspacepromotions.get_workspace_release_status` | — | — | — |
| `workspacepromotions.get_workspace_source_release` | — | — | — |
| `workspacepromotions.inspect_workspace_release_retirement` | — | — | — |
| `workspacepromotions.list_solution_deploy_obligations` | — | — | — |
| `workspacepromotions.list_workspace_source_releases` | — | — | — |
| `workspacepromotions.prepare_workspace_release` | — | — | — |
| `workspacepromotions.preview_workspace_promotion` | — | — | — |
| `workspacepromotions.retire_workspace_release` | — | — | — |
| `workspacepromotions.retry_workspace_release_history_lock` | — | — | — |
| `workspacepromotions.set_workspace_source_release_disposition` | — | — | — |
| `workspacepromotions.upload_workspace_promotion_draft` | — | — | — |
| `workspacerepochangesets.abort_workspace_repo_changeset` | — | — | — |
| `workspacerepochangesets.activate_workspace_repo_changeset` | — | — | — |
| `workspacerepochangesets.apply_workspace_repo_git_convergence` | — | — | — |
| `workspacerepochangesets.begin_workspace_repo_changeset` | — | — | — |
| `workspacerepochangesets.list_recoverable_workspace_repo_git_closures` | — | — | — |
| `workspacerepochangesets.preview_workspace_repo_git_convergence` | — | — | — |
| `workspacerepochangesets.retry_workspace_repo_git_closure` | — | — | — |
| `workspacerepochangesets.show_workspace_repo_changeset` | — | — | — |
| `workspacerepochangesets.stage_workspace_repo_file` | — | — | — |
| `workspacerepochangesets.validate_workspace_repo_changeset` | — | — | — |
| `workspacerepochangesets.workspace_repo_changeset_diff` | — | — | — |
| `workspacerepochangesets.workspace_repo_state` | — | — | — |
