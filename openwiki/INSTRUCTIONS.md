# Repository architecture knowledge brief

Document every maintained subsystem and meaningful workflow in this repository.
Discover its actual taxonomy from tracked source, tests and authoritative docs.
Explain responsibilities, entrypoints, control/data flow, public contracts,
constraints, failure behavior, extension seams and focused verification where
those help a developer decide where and how to make a change.

Use a compact hierarchy with conceptual and end-to-end links. A directory or
symbol inventory alone does not satisfy architectural coverage. Mark omissions,
uncertainty and implementation status explicitly. Distinguish source facts,
historical decisions, inferred relationships and unverified runtime observations.
Preserve accepted decisions in existing docs and ADRs and cite them; generated
OpenWiki remains a derived representation. Verify dated claims against source.

Other BiFrost ecosystem members are bifrost, bifrost-workspace, bifrost-infra and
bifrost-ops. Explain this repository's contracts with other members when local
source establishes them. Local claims do not prove another repository's current
behavior. Keep cross-repository source references explicit and qualify inference.

Do not expose credentials, customer/runtime payloads, local memories, agent
sessions or generated indexes. Honor .openwikiignore. Root .bifrost metadata is
exported state; authored nested Solution manifests remain eligible evidence.
Do not claim deployment or live health from source, commits or CI results.
