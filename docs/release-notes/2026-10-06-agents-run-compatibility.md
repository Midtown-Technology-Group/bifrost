# Restore the agents.run compatibility contract

`agents.run()` again defaults to a bounded 1,800-second wait and returns the
persisted raw output, including `{"text": "answer"}` for unstructured runs.
It continues to enqueue once and poll short status requests. Worker execution
limits, authorization, routes, and caller context are unchanged.

The compatibility baseline is `e2cbbb3702c37bae0d3f360c019ccc52d06bdd2c`.
This is a small SDK compatibility change, rather than a 4.0 contract migration.

| Case | Historical run | Compatibility run | Explicit wait |
|---|---|---|---|
| Unstructured text envelope | Raw dict | Raw dict | Unwrapped text |
| Structured dict | Raw dict | Raw dict | Raw dict |
| Schema with JSON string | Decode JSON | Decode JSON | Decode JSON |
| Invalid schema JSON string | Raw string | Raw string | Raw string |
| Default wait | 1,800 seconds | 1,800 seconds | Unbounded unless supplied or workflow-limited |
| Nonterminal wait expiry | HTTP error | HTTP 504-compatible AgentRunWaitTimeout | AgentRunPending |
| Failed/error-bearing budget result | RuntimeError | RuntimeError | Existing terminal/pending contract |
| Cancelled result | Could return ambiguous output | RuntimeError | RuntimeError |
| Workflow deadline | Server-blocking legacy request | Return margin, same accepted run ID | Pending with same accepted run ID |

`AgentRunWaitTimeout` derives from `BifrostAPIError` / `httpx.HTTPStatusError`.
Its `response.status_code` is 504, and its `run_id`, `reason`, and
`last_known_status` attributes (also in `response.json()`) identify the accepted
run for safe resumption. It is a local wait expiry, not an HTTP server receipt
and not an agent execution timeout. The agent may still be running.

Use `agents.get_run(error.run_id)` or `agents.wait(error.run_id)` to resume;
never call `run()` again as an automatic timeout retry. `enqueue()`, `get_run()`
and `wait()` retain their explicit pending/resume interfaces. Workflow waits
still end before the execution deadline, preserving the return margin.

The canonical route currently persists dictionary outputs. SDK decoding also
accepts legacy string output shapes; this change does not widen the server
response contract. No long Redis-blocking execute request is restored.
