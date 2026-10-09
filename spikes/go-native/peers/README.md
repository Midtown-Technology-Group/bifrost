# Proposed-profile independent Python stream peer

`execution_codec.py` is first-party candidate codec code. It uses the exact three
shared schema copies already pinned by the Go component and the hashed
jsonschema/referencing wheel closure. It imports no test oracle, platform runtime,
Rust implementation, lifecycle/session machinery or credential issuer. Only
published message schemas and wire semantics define its input/output.

The public component operations decode a raw payload, read one framed message,
write a validated framed message and retain the exact raw payload digest. Reader
EOF is distinct from truncation. Partial/interrupted IO is exercised. Integer
lexical rules retain the incumbent P0 i64/u64 fallback, safe control range and
negative-zero rejection. Schemas are local-only and integer/pattern/UTC checking
is strict; business data remains opaque. This is not a general user-schema loader.

Hosted diagnostic CI executes94 shared raw specimens plus seven transport/receipt
checks and18 Go encodings each direction, with a deliberate shape-valid semantic
drift rejection. Until that exact run passes, these are test expectations, not
proof. The older Python-oracle exchange remains a separate reference check.

No legal session, adapter import/initialization custody, authenticated provision,
Rust authority, durable Result or Execution API proof follows. This codec cannot
execute or admit an application. Ordinary tenant Go code and its SDK are unchanged.
Physical pve-t340 execution is forbidden by the repository's validation lane;
run through the supported hosted diagnostic or approved dedicated environment.
