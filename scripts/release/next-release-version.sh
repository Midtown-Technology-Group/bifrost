#!/usr/bin/env bash
# Complete main Git interval + paginated PR metadata; exits 3 for no release.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/automatic-release.py" version "$@"
