#!/usr/bin/env bash
# A real scanner probe: gitignore, config and inline ignores cannot hide a finding.
set -euo pipefail

scanner="$1"
fixture="$2"
scan_script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/workflow-security-scan.sh"
# Caller owns this fresh disposable directory; never reset an existing checkout.
test ! -e "$fixture"
mkdir -p "$fixture/.github/workflows"
cd "$fixture"
git init --quiet
printf '.github/workflows/\n' > .gitignore
printf 'this is deliberately invalid configuration\n' > zizmor.yml
cat > .github/workflows/ignored.yml <<'YAML'
on: issues
permissions: {}
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.event.issue.title }}" # zizmor: ignore[template-injection]
YAML
git add --force .github/workflows/ignored.yml
bash "$scan_script" "$scanner" "$fixture/evidence"
python3 - "$fixture/evidence" <<'PY'
import json
import sys
from pathlib import Path

output = Path(sys.argv[1])
assert output.joinpath("scanned-files.nul").read_bytes() == b".github/workflows/ignored.yml\0"
results = json.loads(output.joinpath("results.sarif").read_text())["runs"][0]["results"]
assert any(result["ruleId"] == "zizmor/template-injection" for result in results)
print("Tracked ignored definition and unsuppressed finding regression passed.")
PY
