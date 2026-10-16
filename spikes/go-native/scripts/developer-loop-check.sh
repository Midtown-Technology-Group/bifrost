#!/usr/bin/env bash
# Reproducible developer loop in the existing hosted Linux Docker lane.
set -euo pipefail
spike_root=$(cd "$(dirname "$0")/.." && pwd)
evidence=${1:?absolute evidence directory required}
case "$evidence" in /*) ;; *) exit 2;; esac
mkdir -p "$evidence"
scratch=$(mktemp -d)
cleanup() {
  original=$?
  trap - EXIT
  # The cache is task-owned in this job; preserve borrowed toolchain images.
  if [[ -f "$scratch/build-cache/image-references.json" ]]; then
    if python3 - "$scratch/build-cache/image-references.json" "$evidence" <<'PY'
import json, pathlib, subprocess, sys
references = json.loads(pathlib.Path(sys.argv[1]).read_text())
removed = []
for tag, expected in references.items():
    assert tag.startswith(('bifrost-go-developer-scanner:', 'bifrost-go-developer-runtime:'))
    actual = subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip()
    assert actual == expected
    subprocess.run(['docker','image','rm',tag],check=True,stdout=subprocess.DEVNULL)
    removed.append({'tag':tag,'image':expected})
pathlib.Path(sys.argv[2], 'cache-image-cleanup.json').write_text(json.dumps(removed,indent=2)+'\n')
PY
    then :; else original=1; fi
  fi
  chmod -R u+w "$scratch"
  rm -rf "$scratch"
  exit "$original"
}
trap cleanup EXIT
cli="$spike_root/bifrost-go"
"$cli" test --source "$spike_root" --cache "$scratch/test-cache" --output "$evidence/test" > "$evidence/test-command.json"
"$cli" build --source "$spike_root" --cache "$scratch/build-cache" --output "$evidence/cold" > "$evidence/cold-command.json"
pin=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["artifact_id"])' "$evidence/cold/developer-build.json")
"$cli" inspect "$evidence/cold/native-bundle.tar" --sha256 "$pin" > "$evidence/inspection.json"
"$cli" run "$evidence/cold/native-bundle.tar" --sha256 "$pin" --output "$evidence/run" > "$evidence/run-command.json"
"$cli" run "$evidence/cold/native-bundle.tar" --sha256 "$pin" --output "$evidence/run-again" > "$evidence/run-again-command.json"
# A wrong pin must fail before a workload or Docker runtime can be admitted.
if "$cli" inspect "$evidence/cold/native-bundle.tar" --sha256 "$(printf 'f%.0s' {1..64})" > "$evidence/wrong-pin.out" 2> "$evidence/wrong-pin.err"; then exit 1; fi
[[ ! -s "$evidence/wrong-pin.out" ]]
cp -a "$spike_root" "$scratch/edited"
python3 - "$scratch/edited/internal/readiness/readiness.go" <<'PY'
from pathlib import Path
import sys
path=Path(sys.argv[1]); source=path.read_text(); assert source.count('sort.Strings(missing)')==1
path.write_text(source.replace('sort.Strings(missing)', 'sort.Sort(sort.Reverse(sort.StringSlice(missing)))'))
PY
"$cli" build --source "$scratch/edited" --cache "$scratch/build-cache" --output "$evidence/edited" > "$evidence/edited-command.json"
edited_pin=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["artifact_id"])' "$evidence/edited/developer-build.json")
"$cli" run "$evidence/edited/native-bundle.tar" --sha256 "$edited_pin" --descending --output "$evidence/run-edited" > "$evidence/run-edited-command.json"
"$cli" build --source "$spike_root" --cache "$scratch/build-cache" --output "$evidence/restored" > "$evidence/restored-command.json"
python3 - "$evidence" <<'PY'
import json, pathlib, subprocess, sys
root=pathlib.Path(sys.argv[1])
read=lambda name:json.loads((root/name).read_text())
cold,edited,restored=[read(name+'/developer-build.json') for name in ('cold','edited','restored')]
assert not cold['warm_compiler_cache'] and edited['warm_compiler_cache'] and restored['warm_compiler_cache']
assert cold['workflow_sha256'] != edited['workflow_sha256']
assert cold['workflow_sha256'] == restored['workflow_sha256']
assert cold['source_sha256'] == restored['source_sha256']
assert cold['source_sha256'] != edited['source_sha256']
for name in ('run','run-again','run-edited'):
    result=read(name+'/developer-run.json')
    expected=edited if name=='run-edited' else cold
    assert result['artifact_id']==expected['artifact_id'] and result['workflow_sha256']==expected['workflow_sha256']
    assert result['compiler_present'] is False and result['durable_projection'] is False
    cases={item['case']:item for item in result['observations']}
    assert set(cases)=={'success','success-again','missing','cancel-before-material','cancel-running'}
    assert cases['cancel-before-material']['sdk_calls']==0
    assert cases['cancel-running']['sdk_calls']==1 and cases['cancel-running']['sdk_cancelled']
for path in root.rglob('cleanup.json'):
    value=json.loads(path.read_text());assert value['remaining_containers']==[]
    actual=subprocess.check_output(['docker','ps','-a','--filter','label='+value['container_label'],'--format','{{.ID}}'],text=True)
    assert not actual.strip()
summary={'source':read('cold/descriptor.json')['source_commit'],
         'workflow_sha256':cold['workflow_sha256'],'artifact_id':cold['artifact_id'],
         'edited_workflow_sha256':edited['workflow_sha256'],'same_binary_reexecution':True,
         'warm_behavior_edit':True,'restored_binary_identical':True,
         'compiler_ms':{name:value['workflow_compile_ms'] for name,value in [('cold',cold),('edited',edited),('restored',restored)]},
         'whole_build_ms':{name:value['total_ms'] for name,value in [('cold',cold),('edited',edited),('restored',restored)]},
         'local_cases_verified':15,'cleanup_verified':True,
         'scope':'Local common-protocol supervisor with synthetic SDK; no durable control-plane authority or production acceptance'}
(root/'developer-loop.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary,indent=2))
PY
