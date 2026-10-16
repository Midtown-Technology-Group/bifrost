# Inspecting a Go build artifact

The local inspector checks an existing `isolated-native-bundle/v1` archive
against an expected SHA-256. It reads the archive in memory and reports JSON
with its platform, entry sizes and hashes. It never extracts, compiles or runs
the workflow. Python 3 on Linux is sufficient; Go and the Rust server are not
needed for inspection.

```sh
python3 -B spikes/go-native/scripts/inspect_bundle.py /path/to/native-bundle.tar \
  --sha256 sha256:<expected-archive-digest>
```

Get the expected digest from the build evidence or accepted deployment you
intend to inspect. A binary digest is different from the archive digest.
The inspector requires an explicit pin and rejects changed bytes, noncanonical
archives, unexpected entries and inconsistent embedded hashes. Symlinks, FIFOs
and other nonregular inputs are rejected; the archive size is bounded.

Success prints one JSON document and exits zero. Failure prints a fixed error
to stderr, emits no partial success document and exits nonzero. The original
file is left unchanged. Reports include byte identities rather than arbitrary
producer-supplied metadata or embedded content.

Inspection checks archive bytes and internal consistency. It does not
authenticate the producer or admit a deployment: those decisions still belong
to BiFrost's build trust and control-plane contracts. Claimed test/scan results
inside an archive cannot authenticate themselves.

This command is a build/developer tool for the existing bounded Go bundle
recipe. It adds no runtime messages, SDK capabilities, credentials, renewal,
database access or lifecycle behavior. The accepted spike remains pinned at
`56d1fb006d220b9dbb0747f1cca4def18677f918` with evidence HEAD
`a47586732ef1b2523b9119b5056d83574ccdd779`; changes here do not qualify a new
runtime deployment or modify that historical evidence.
