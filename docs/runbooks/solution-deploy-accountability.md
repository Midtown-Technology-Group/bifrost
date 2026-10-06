# Recover Solution deployment accountability

A successful Solution deployment can leave its source-release obligation in
`attention_required` when post-deploy evidence reconciliation fails. Check the
successful deployment and the installed app or workflows before recovering the
evidence. A failed deployment needs its deployment error resolved first.

Platform administrators can enqueue recovery for the exact install and successful
deploy job:

```bash
bifrost api POST /api/solutions/INSTALL_ID/deploy-jobs/DEPLOY_JOB_ID/reconcile
bifrost api GET /api/platform-jobs/RECOVERY_JOB_ID
```

Use the recovery job ID returned by the POST. Recovery uses the existing Solution
write lock and validates the original successful job, its organization scope,
immutable candidate, stored source artifact, and installed runtime. It writes
accountability evidence only. It does not deploy source, rebuild applications,
execute workflows, or change the original deployment result.

Read `source_release_accountability` in the recovery result and confirm the
obligation through `/api/workspace-promotions/solution-deploy-obligations` in the
same organization context. A completed recovery job means verification finished;
only `released` means the reviewed source obligation was satisfied. A real source
or runtime mismatch remains `attention_required` with its evidence. In particular,
a later README change can differ from an earlier deployed artifact even when the
running application is healthy.

For a locally prebuilt App, the CLI retains the original `.bifrost/apps.yaml`
as `authored_manifest` inside the uploaded build overlay. Accounting checks its
exact bytes against the declared Git file, then allows only `dist_files` and
`bin_dist_files` to differ. App identity, source path, scope and access metadata
must remain unchanged. An older overlay without this retained manifest cannot
prove those facts and stays `attention_required`; do not clear it manually or
replay a deployment just to obtain accounting credit. This manifest check is
source evidence, not proof that locally prebuilt output was compiled from Git.
An altered build overlay with locally supplied output stays `attention_required`
until protected build provenance exists; current automatic delivery must build
from the protected source or carry output checked into that exact source.

Full Solution deployments now retain an App runtime pin in their existing
publication snapshot. Accounting locks the App row, reads every object under
its active deployment ID, and verifies the full output inventory, byte hashes,
package archive digest and build mode. Missing artifacts, unexpected output,
changed ownership or a changed pointer cannot earn credit. Recovery reads these
facts without rebuilding the App or switching its pointer.

Do not directly edit database evidence, bypass Solution ownership protection, or
redeploy a working application merely to retry bookkeeping. Review any remaining
source difference and handle its actual release requirement separately.

## Complete authored packages

The protected complete-package publisher also accepts Solutions without Apps.
It publishes the full authored inventory through the existing `solution.deploy`
PlatformJob and verifies immutable source, registrations and runtime pins before
settling delivery accounting. Packages with Apps still compile and verify each
App's separate output and publication pin; workflow-only packages publish no App.

Reviewed table schema changes update document metadata in place. They preserve
table IDs, names, ownership, scope, access policies and existing documents. The
complete post-publication snapshot includes the exact new schema, so stale
admission and later metadata drift still fail readback. A schema revert restores
metadata without deleting stored document fields. Existing caller compatibility,
workflow control and resource ownership checks remain required.

Complete-package successors retain the active immutable deployment's reviewed
shared-table bindings, Root-file bindings and dependency pins. They do not
introduce new Root grants from package source. Shared-table scope/metadata and
required Root asset bytes are revalidated during preparation, before activation
and during independent readback; drift requires reconciliation without replay.
An owned table's declared name cannot overlap an inherited shared-table alias,
including when the manifest stores owned tables under UUID keys. Ambiguous
table resolution rejects the candidate before staging or switching pointers.

Successors also retain the exact names of already-reviewed immutable resources.
Their bytes come from protected authored source and receive new deployment-local
hash and size pins. The publisher verifies the parent archive and resource
objects, rejects missing names or undeclared SDK reads, and checks the new
archive and runtime objects before activation and during independent readback.
An arbitrary authored asset does not create a new resource permission. Resource
updates and reverts preserve grants and never substitute mutable Root storage.
Authored runtime objects cannot alias relocated `_resources/` objects, even
when both byte sets are equal. Compilation rejects that ambiguity before
publication intent or staging, preserving the existing active runtime.

Complete-package export uses the same independent runtime verifier and carries
the retained authored archive, including manifests, App sources, binary assets
and empty files. It does not capture mutable App/Root source to reconstruct that
archive. Full exports retain the existing encrypted runtime-content overlay;
config values, table rows and operational files remain opt-in. The authored
README is preserved in both modes, and pointer drift still rejects the export.
