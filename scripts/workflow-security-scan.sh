#!/usr/bin/env bash
# Scan every tracked definition, regardless of repository ignore settings.
set -euo pipefail

scanner="$1"
output="$2"
mkdir -p "$output"
git ls-files -z -- '.github/workflows/*.yml' '.github/workflows/*.yaml' \
  ':(glob)**/action.yml' ':(glob)**/action.yaml' > "$output/scanned-files.nul"
mapfile -d '' -t definitions < "$output/scanned-files.nul"
test "${#definitions[@]}" -gt 0
"$scanner" --offline --strict-collection --no-config --no-ignores \
  --format=sarif -- "${definitions[@]}" \
  > "$output/results.sarif" 2> "$output/zizmor.stderr"
