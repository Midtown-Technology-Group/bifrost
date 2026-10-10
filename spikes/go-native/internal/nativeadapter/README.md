# Trusted native adapter components

This package implements the private material reader released in the isolated
owner proposal. It imports the language-neutral Go codec and standard library;
it does not import tenant authoring code or Rust implementation types.

The inherited descriptor must be a read-only pipe, is marked close-on-exec before
reading, has a caller-supplied finite preparation deadline, and is closed on every
return. A reader consumes at most one delivery, including failed deliveries. The
guardian must close its writer after one four-byte big-endian length-prefixed
UTF-8 JSON envelope of at most 65,536 bytes. Unknown keys, duplicate decoded keys,
invalid Unicode, excessive nesting, trailing bytes and another delivery reject.

The envelope has exactly these fields:

* `version`: `runtime-private-delivery/v1`.
* `provision`: the exact structurally valid common Provision frame. Every decoded
  field must match the Provision already observed on the public protocol stream.
* `sdk_configuration`: exactly `endpoint`, `bearer`, `organization_id`,
  `solution_id`, `test_ca_pem`, all strings. HTTPS origin and certificate parsing
  remain mandatory. This isolated fixture requires a supplied trusted CA.

This finite readiness slice requires a workflow binding, organization scope,
matching organization/Solution, an unexpired grant and exactly `integration-get`.
The only configuration returned to the later tenant FD3 excludes the public
control frame and delivery references. Error text is static and never embeds
private material. This is a closed private delivery format, not an additional
public lifecycle message or a production profile freeze.

Descriptor shape and matching wire fields do **not** authenticate the issuer,
prove sole-writer custody, observe a committed admission, or authorize spawn.
The verified guardian, actual issuer/ingress, release transaction, native adapter
entrypoint and accepted bundle remain required. No process is launched by this
component; no runtime acceptance is claimed. The existing Go workflow and SDK
remain unchanged. Tests run through the existing isolated Go CI lane.

On the initial Linux/amd64 target, `SealExecutable` copies the supplied accepted
child into a memfd, verifies the exact binary digest and applies irreversible
write/grow/shrink/seal locks. The later authorized launch can use that retained
handle rather than re-opening the source path. Empty, oversized, modified or
unsealable bytes fail closed. This addresses byte replacement between validation
and exec; it does not verify deployment acceptance, grant permission, guarantee
descendant cleanup or execute the binary. No Rust type or tenant import is used.
