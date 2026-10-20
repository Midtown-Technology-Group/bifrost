# Files

- [Agents, Chat, and Model Runtime](agents-and-chat.md) - Traces agent configuration, chat runs, model and tool execution, durable run state, budgets, cancellation, and model failover.
- [Events, Webhooks, and Scheduling](events-and-schedules.md) - Explains event sources and subscriptions, public webhook validation, topic emission, delivery tracking, and cron-triggered execution.
- [Execution and Workflow Runtime](execution-and-workflows.md) - Explains workflow admission, RabbitMQ dispatch, Redis acceleration, process isolation, durable logical executions and attempt fencing.
- [Durable Jobs, Queues, and Schedulers](jobs-and-schedulers.md) - Explains shared PlatformJob durability, RabbitMQ and PostgreSQL delivery mechanisms, scheduler replica roles, leases, retry, and operator status.
- [Knowledge Sources, Retrieval, and Memory](knowledge-and-memory.md) - Distinguishes namespace knowledge indexing and agent retrieval from private user memory, including chunking, ranking, scope, and storage reporting.
- [Model Inference, Decisions, and App Code Compilation](model-inference-and-code-apps.md) - How Bifrost resolves configured model profiles, validates bounded decision outputs, generates embeddings and compiles app code, with the separate identity and policy boundary of the Codex Gateway.
- [Provider-Managed Webhook Subscriptions](provider-webhooks.md) - How webhook adapters register, renew, replace, and remove external subscriptions, resolve organization-scoped OAuth, and persist provider state independently of inbound event verification.
