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
source evidence, not proof of the active compiled App runtime.

Do not directly edit database evidence, bypass Solution ownership protection, or
redeploy a working application merely to retry bookkeeping. Review any remaining
source difference and handle its actual release requirement separately.
