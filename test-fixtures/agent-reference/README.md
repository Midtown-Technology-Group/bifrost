# Agent reference inputs

These are inert, byte-exact public-workspace inputs for the bounded C1-R
characterization. They are not backend modules and must never be imported by
fixture setup or registration discovery. The isolated named lane will mount
this directory read-only and carry its Python files under the runtime paths in
`provenance.json` through the existing reviewed Solution APIs/compiler.

Authored inputs come from workspace commit
`e8605dc8edb6df8a997c171b534b324ac7ebd8ec`; their SHA256 and Git blob identities
are retained in the manifest. The bootstrap is a separate fixture-owned input.
The actual platform candidate commit containing these blobs is the installation
source pin; the workspace origin is not a claim that the mixed fixture tree is
that workspace commit.

Both declared tools remain unchanged. The nominal packet invokes only capacity.
The bootstrap retains the authored comment-only package initializer through an
explicit dependency, but is neither granted, attached nor executed. Expected
compiler closure sets are unexecuted acceptance gates, not compiler evidence.

Keep these `.py` input blobs outside the API implementation package: formatting
or importing them would change the unchanged-consumer experiment. Reviewed
recipes require both runtime and repository source paths ending in `.py`;
`.py.source` mappings are unsupported and must not be introduced. API quality
configuration and CI gates remain unchanged. Registration/archive hashes and
recipe-to-repository mapping must be checked independently before any execution.

No runtime, model, vendor, public credential or Rust ownership acceptance follows
from retaining these files. Never use a sibling checkout, optional live secret,
extra source file, global-source grant or namespace fallback to make them run.
