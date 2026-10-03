#!/usr/bin/env bash
# Hosted verification only; never execute on the physical Proxmox host.
set -euo pipefail
exec python3 - "${1:-verify}" <<'PY'
import ast
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

MODE = sys.argv[1]
if MODE not in {'format', 'verify', 'cleanup'}:
    sys.exit(2)
ROOT = Path.cwd()
OUT = Path(os.environ['RC_EVIDENCE'])
RAW = Path(os.environ['RC_PRIVATE_LOGS'])
STATE = OUT / 'state.json'
s = json.loads(STATE.read_text())
PREFIX = 'bifrost-rc-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
LABEL = 'bifrost.rc.owner'
HELPER = 'scripts/ci/workflow-sql-source.sh'
HELPER_HASH = 'fb3f4cd114da421d6fb2b4d007e522e413382f12b614985028c3689129241c4c'
CAPS = {'prepr':480, 'checks':480, 'units':120, 'binary':120,
        'stack':180, 'manifest':60, 'target':900, 'cleanup':120}
COUNT = {'scenarios':39, 'paired_scenarios':38, 'rust_only_scenarios':1,
         'python_actor_invocations':44, 'rust_actor_invocations':45, 'total_actor_invocations':89}
PATHS = [
 'core-rs/crates/bifrost-db/examples/workflow_running_cancel_sql.rs',
 'core-rs/crates/bifrost-db/Cargo.toml', 'core-rs/Cargo.lock',
 'api/tests/parity/workflow_running_cancel_sql_harness.py',
 'api/tests/parity/test_workflow_running_cancel_sql.py',
 'scripts/ci/workflow-running-cancel-sql.sh', '.github/workflows/workflow-running-cancel-sql.yml',
 'core-rs/crates/bifrost-db/src/workflow_parity.rs', 'core-rs/crates/bifrost-db/src/lib.rs',
 'core-rs/crates/bifrost-domain/src/lib.rs', 'core-rs/crates/bifrost-domain/src/workflow/mod.rs',
 'core-rs/crates/bifrost-domain/src/workflow/tests.rs', 'api/src/services/execution/attempts.py',
 'api/src/repositories/executions.py', 'api/src/models/orm/executions.py',
 'api/src/core/database.py', 'api/src/core/pubsub.py',
 'api/tests/parity/workflow_domain_harness.py', 'core-rs/Dockerfile']
stage = None
start = None

def interrupted(signum, _frame):
    raise SystemExit(128+signum)

signal.signal(signal.SIGTERM, interrupted)
signal.signal(signal.SIGINT, interrupted)

def check(value):
    if not value:
        raise RuntimeError('closed RC custody failure')

def save():
    temp = STATE.with_suffix('.new')
    temp.write_text(json.dumps(s, sort_keys=True))
    temp.replace(STATE)

def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def begin(name):
    global stage, start
    now = time.monotonic()
    if start is not None:
        s['stages'][-1].update(end=now,duration=now-start)
    stage, start = name, now
    if name=='cleanup':
        start = s.setdefault('cleanup_start', start)
    s.setdefault('stages', []).append({'stage':name, 'start':start, 'cap':CAPS[name]})
    save()

def left():
    check(start is not None)
    reserve = 120 if stage == 'cleanup' else 240
    seconds = min(CAPS[stage] - (time.monotonic()-start), 2700-(time.monotonic()-s['start'])-reserve)
    check(seconds > 0)
    return seconds

def clean_env():
    env = os.environ.copy()
    for key in ('GITHUB_TOKEN','GH_TOKEN','GHCR_TOKEN','GHCR_USERNAME','DOCKER_CONFIG',
                'BIFROST_ACTION_PIN_TOKEN_FILE','SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY'):
        env.pop(key, None)
    return env

def command(argv, name, env=None, cap=8*1024*1024, accept=0):
    # All raw logs and acquisition failures remain outside the uploaded tree.
    path = RAW / (str(len(s.get('commands', []))) + '-' + name + '.log')
    fd = proc = poll = pipe = None
    total = 0
    code = None
    original = cleanup_error = None
    cleanup_labels = []
    reaped = False
    def note(label, error):
        nonlocal cleanup_error
        cleanup_labels.append({'operation':label, 'class':'control' if not isinstance(error,Exception) else 'ordinary'})
        if cleanup_error is None:
            cleanup_error = error
    try:
        deadline = time.monotonic()+left()
        fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=env, start_new_session=True)
        pipe = proc.stdout
        poll = selectors.DefaultSelector()
        poll.register(pipe, selectors.EVENT_READ)
        while poll.get_map():
            remaining = deadline-time.monotonic()
            check(remaining > 0)
            for key, _ in poll.select(min(remaining, 1)):
                block = os.read(key.fileobj.fileno(), 65536)
                if not block:
                    poll.unregister(key.fileobj)
                    continue
                total += len(block)
                check(total <= cap)
                view = memoryview(block)
                while view:
                    n = os.write(fd, view)
                    check(n > 0)
                    view = view[n:]
        code = proc.wait(timeout=max(0.001, deadline-time.monotonic()))
        reaped = True
        if accept is not None:
            check(code == accept)
    except BaseException as error:
        original = error
    finally:
        if poll is not None:
            try:
                poll.close()
            except BaseException as error:
                note('selector-close',error)
        if proc is not None:
            needs_kill = original is not None
            try:
                running = proc.poll() is None
                needs_kill = needs_kill or running
            except BaseException as error:
                needs_kill = True
                note('poll',error)
            if needs_kill:
                try:
                    os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    note('group-kill',error)
            try:
                proc.wait(timeout=max(0.001,min(2,2700-(time.monotonic()-s['start'])-120)))
                reaped = True
            except BaseException as error:
                note('reap',error)
        if pipe is not None:
            try:
                pipe.close()
            except BaseException as error:
                note('stdout-close',error)
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                note('log-close',error)
        try:
            s.setdefault('commands', []).append({'name':name,'exit':code,'bytes':total,'stage':stage,
                'process_started':proc is not None,'process_reaped':reaped,'cleanup_failed':bool(cleanup_labels),
                'cleanup_classes':cleanup_labels[:]})
            save()
        except BaseException as error:
            note('receipt',error)
    if original is not None:
        raise original
    if cleanup_error is not None:
        if not isinstance(cleanup_error,Exception):
            raise cleanup_error
        raise RuntimeError('closed RC child cleanup failure') from None
    left()
    return path

def capture(argv, name):
    return command(argv, name, env=clean_env(), cap=65536).read_text().strip()

def facts(kind, value, name):
    return json.loads(capture(['docker',kind,'inspect',value], name))[0]

def owned_run(image, argv, name, mounts=(), network='none'):
    cname = PREFIX+'-'+name
    check(not capture(['docker','ps','-aq','--filter','name=^/'+cname+'$'], name+'-absence'))
    rec = {'name':cname, 'cid':None, 'image':image}
    s.setdefault('containers', []).append(rec)
    save()
    args = ['docker','create','--name',cname,'--label',LABEL+'='+PREFIX,'--network',network]
    for source, target, ro in mounts:
        args += ['--mount','type='+('bind' if source.startswith('/') else 'volume')+',source='+source+',target='+target+(',readonly' if ro else '')]
    command(args+[image]+argv, name+'-create', clean_env(), cap=65536)
    rec['cid'] = capture(['docker','inspect',cname,'--format','{{.Id}}'], name+'-cid')
    save()
    f = facts('container',rec['cid'],name+'-facts')
    check(f['Name']=='/'+cname and f['Image']==image and f['Config']['Labels'][LABEL]==PREFIX and f['HostConfig']['NetworkMode']==network)
    check(len(f['Mounts'])==len(mounts))
    for source, target, ro in mounts:
        m = next(m for m in f['Mounts'] if m['Destination']==target)
        check(m['RW']==(not ro) and (m['Source']==source if source.startswith('/') else m['Name']==source))
    check(not {x.split('=',1)[0] for x in f['Config']['Env']}.intersection({'GITHUB_TOKEN','GH_TOKEN','GHCR_TOKEN','GHCR_USERNAME','DOCKER_CONFIG','BIFROST_ACTION_PIN_TOKEN_FILE'}))
    log = command(['docker','start','-a',rec['cid']], name+'-run', clean_env())
    f = facts('container',rec['cid'],name+'-exit')
    check(not f['State']['Running'] and f['State']['ExitCode']==0)
    return rec, log

def image(target):
    tag = PREFIX+':'+target
    check(not capture(['docker','image','ls','-q',tag],target+'-image-absence'))
    rec = {'tag':tag,'id':None}
    s.setdefault('images',[]).append(rec)
    save()
    command(['docker','build','--target',target,'-t',tag,'-f','core-rs/Dockerfile','core-rs'],target+'-build',clean_env())
    rec['id'] = capture(['docker','image','inspect',tag,'--format','{{.Id}}'],target+'-image-id')
    save()
    return rec['id']

def volume(name):
    v = PREFIX+'-'+name
    check(not capture(['docker','volume','ls','-q','--filter','name=^'+v+'$'],name+'-volume-absence'))
    rec = {'name':v,'created':None,'mountpoint':None}
    s.setdefault('volumes',[]).append(rec)
    save()
    command(['docker','volume','create','--label',LABEL+'='+PREFIX,v],name+'-volume-create',clean_env())
    f = facts('volume',v,name+'-volume-facts')
    check(f['Labels'][LABEL]==PREFIX)
    rec.update(created=f['CreatedAt'],mountpoint=f['Mountpoint'])
    save()
    return v

def source_head():
    graph = {}
    for path in sorted((ROOT/'api/alembic/versions').glob('*.py')):
        tree = ast.parse(path.read_text())
        fields = {}
        for node in tree.body:
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                targets = node.targets if isinstance(node,ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t,ast.Name) and t.id in {'revision','down_revision','depends_on'}:
                        check(t.id not in fields)
                        fields[t.id] = ast.literal_eval(node.value)
        if not fields:
            continue
        rev = fields.get('revision')
        check('revision' in fields and 'down_revision' in fields and isinstance(rev,str) and rev not in graph and fields.get('depends_on') is None)
        parent = fields.get('down_revision')
        parents = [] if parent is None else [parent] if isinstance(parent,str) else list(parent) if isinstance(parent,tuple) else None
        check(parents is not None and all(isinstance(p,str) for p in parents))
        graph[rev] = parents
    check(graph and all(p in graph for parents in graph.values() for p in parents))
    seen, visiting = set(), set()
    def visit(rev):
        check(rev not in visiting)
        if rev in seen:
            return
        visiting.add(rev)
        for p in graph[rev]:
            visit(p)
        visiting.remove(rev)
        seen.add(rev)
    for rev in graph:
        visit(rev)
    heads = sorted(set(graph)-{p for parents in graph.values() for p in parents})
    check(len(heads)==1)
    (OUT/'source-heads.json').write_bytes(encoded({'provenance':'source_expected_only','revisions':len(graph),'heads':heads}))
    return heads

def prepare_source():
    check(not capture(['git','status','--porcelain','--untracked-files=all'],'clean-source'))
    s['source'] = capture(['git','rev-parse','HEAD','HEAD^{tree}'],'source-id').splitlines()
    check(s['source'][0]==os.environ['GITHUB_SHA'])
    command(['git','-c','credential.helper=','-c','http.extraheader=','fetch','origin','main'],'fetch-main',clean_env())
    command(['git','merge-base','--is-ancestor','origin/main','HEAD'],'main-ancestor',clean_env())
    check(sha(ROOT/HELPER)==HELPER_HASH)
    s['project'] = capture(['bash','-c','source scripts/lib/test_helpers.sh; compute_project_name .'],'project-name')
    s['logdir'] = '/tmp/bifrost-'+s['project']
    for kind, args in [('containers',['docker','ps','-aq']),('volumes',['docker','volume','ls','-q']),('networks',['docker','network','ls','-q'])]:
        check(not capture(args+['--filter','label=com.docker.compose.project='+s['project']],'initial-'+kind))
    s['source_hashes'] = {p:sha(ROOT/p) for p in PATHS} if MODE=='verify' else {}
    s['stack_owned'] = True
    save()

def dispose_credential():
    command(['bash',HELPER,'credential-cleanup'],'credential-disposal',cap=65536)
    os.environ.pop('BIFROST_ACTION_PIN_TOKEN_FILE',None)
    os.environ.pop('SQL_SOURCE_ACTION_CREDENTIAL_IDENTITY',None)
    s['credential_disposed'] = True
    save()

def manifest_and_receipt(toolchain):
    begin('manifest')
    s['api_before'] = capture(['docker','image','inspect','bifrost-test-api-dev:latest','--format','{{.Id}}'],'api-before')
    # Direct Python entrypoint avoids normal application startup for pure manifest.
    name = PREFIX+'-manifest'
    check(not capture(['docker','ps','-aq','--filter','name=^/'+name+'$'],'manifest-absence'))
    rec = {'name':name,'cid':None,'image':s['api_before']}
    s.setdefault('containers',[]).append(rec)
    save()
    args = ['docker','create','--name',name,'--label',LABEL+'='+PREFIX,'--network','none','--entrypoint','python']
    for directory in ('src','tests'):
        args += ['--mount',f'type=bind,source={ROOT}/api/{directory},target=/app/{directory},readonly']
    command(args+[s['api_before'],'-m','tests.parity.workflow_running_cancel_sql_harness','--synthetic-manifest'],'manifest-create',clean_env(),cap=65536)
    rec['cid'] = capture(['docker','inspect',name,'--format','{{.Id}}'],'manifest-cid')
    save()
    f = facts('container',rec['cid'],'manifest-facts')
    check(f['Name']=='/'+name and f['Image']==s['api_before'] and f['Config']['Entrypoint']==['python'] and f['HostConfig']['NetworkMode']=='none' and f['Config']['Labels'][LABEL]==PREFIX)
    check(len(f['Mounts'])==2 and all(not m['RW'] and m['Source']==str(ROOT/'api'/m['Destination'].removeprefix('/app/')) for m in f['Mounts']))
    check(not {x.split('=',1)[0] for x in f['Config']['Env']}.intersection({'GITHUB_TOKEN','GH_TOKEN','GHCR_TOKEN','GHCR_USERNAME','DOCKER_CONFIG','BIFROST_ACTION_PIN_TOKEN_FILE'}))
    raw = command(['docker','start','-a',rec['cid']],'manifest-output',clean_env(),cap=65536).read_bytes()
    final = facts('container',rec['cid'],'manifest-terminal')
    check(final['Id']==rec['cid'] and not final['State']['Running'] and final['State']['ExitCode']==0)
    s['manifest_exit'] = final['State']['ExitCode']
    save()
    m = json.loads(raw)
    check(set(m)=={'schema','roster','seed_matrix','expected_counts'} and m['schema']=='bifrost.test.workflow-running-cancel-manifest/v1' and m['expected_counts']==COUNT and raw.strip()==encoded(m))
    (OUT/'manifest.json').write_bytes(encoded(m))
    s['evidence_root'] = s['logdir']+'/workflow-running-cancel-sql'
    dest = Path(s['evidence_root'])
    check(not dest.exists())
    dest.mkdir(mode=0o1777)
    os.chmod(dest,0o1777)
    (dest/'observations').mkdir(mode=0o1777)
    os.chmod(dest/'observations',0o1777)
    shutil.copyfile(RAW/'driver',dest/'driver')
    (dest/'driver').chmod(0o555)
    s['driver_before'] = sha(dest/'driver')
    receipt = {'schema':'bifrost.test.workflow-running-cancel-receipt/v1',
               'candidate_sha':s['source'][0],'candidate_tree':s['source'][1],
               'driver_sha256':s['driver_before'],'source_sha256':{p:sha(ROOT/p) for p in PATHS},
               'roster_sha256':hashlib.sha256(encoded(m['roster'])).hexdigest(),
               'seed_matrix_sha256':hashlib.sha256(encoded(m['seed_matrix'])).hexdigest(),
               'rust_image_id':toolchain,'api_image_id':s['api_before'],'database_label':'rc-disposable-fixtures',
               'migration_heads':source_head(),'expected_counts':COUNT}
    (dest/'receipt.json').write_bytes(encoded(receipt))
    s['receipt_before'] = sha(dest/'receipt.json')
    s['uid_before'] = {'uid':dest.stat().st_uid,'gid':dest.stat().st_gid,'mode':stat.S_IMODE(dest.stat().st_mode)}
    (OUT/'producer-receipt.json').write_bytes(encoded(receipt))
    save()

def verify():
    begin('prepr')
    prepare_source()
    pending = None
    try:
        command(['./test.sh','pre-pr'],'literal-prepr')
    except BaseException as error:
        pending = error
    finally:
        try:
            dispose_credential()
        except BaseException as error:
            if pending is None:
                pending = error
    if pending is not None:
        raise pending
    begin('checks')
    image('checks')
    toolchain = image('toolchain')
    home, target = volume('cargo-home'), volume('cargo-target')
    mounts = [(home,'/usr/local/cargo',False),(target,'/targets',False)]
    owned_run(toolchain,['sh','-c','test ! -e /usr/local/cargo/credentials && test ! -e /usr/local/cargo/credentials.toml && test ! -e /usr/local/cargo/config && test ! -e /usr/local/cargo/config.toml && cargo fetch --locked'],'fetch',mounts,network='bridge')
    begin('units')
    for name, selector in [('feature-lib',['--lib']),('example-units',['--example','workflow_running_cancel_sql'])]:
        _, log = owned_run(toolchain,['cargo','test','--offline','--locked','-p','bifrost-db','--features','workflow-sql-parity','--target-dir','/targets']+selector,name,mounts)
        text = log.read_text(errors='replace')
        expected = 8 if name=='feature-lib' else 5
        check(f'test result: ok. {expected} passed; 0 failed; 0 ignored;' in text)
        s[name] = {'passed':expected}
    begin('binary')
    rec, _ = owned_run(toolchain,['cargo','build','--offline','--locked','-p','bifrost-db','--example','workflow_running_cancel_sql','--features','workflow-sql-parity','--target-dir','/targets'],'binary',mounts)
    command(['docker','cp',rec['cid']+':/targets/debug/examples/workflow_running_cancel_sql',str(RAW/'driver')],'copy-binary',clean_env(),cap=65536)
    begin('stack')
    command(['./test.sh','stack','up'],'stack-up')
    manifest_and_receipt(toolchain)
    begin('target')
    junit = Path(s['logdir'])/'test-results.xml'
    if junit.exists() or junit.is_symlink():
        check(stat.S_ISREG(junit.lstat().st_mode))
        junit.unlink()
    check(not junit.exists() and not junit.is_symlink())
    s['target_started'] = True
    save()
    command(['./test.sh','tests/parity/test_workflow_running_cancel_sql.py','-v'],'targeted')

def format_only():
    begin('checks')
    prepare_source()
    toolchain = image('toolchain')
    dest = RAW/'format-source'
    command(['git','clone','--no-hardlinks','--no-checkout',str(ROOT),str(dest)],'format-clone',clean_env())
    command(['git','-C',str(dest),'checkout','--detach',s['source'][0]],'format-checkout',clean_env())
    owned_run(toolchain,['cargo','fmt','-p','bifrost-db'],'format',[(str(dest/'core-rs'),'/workspace/core-rs',False)])
    changed = capture(['git','-C',str(dest),'diff','--name-only'],'format-changed').splitlines()
    check(set(changed)<={'core-rs/crates/bifrost-db/examples/workflow_running_cancel_sql.rs'})
    patch = command(['git','-C',str(dest),'diff','--binary'],'format-patch',clean_env(),cap=1024*1024).read_bytes()
    (OUT/'format.patch').write_bytes(patch)
    check(not capture(['git','status','--porcelain','--untracked-files=all'],'original-preserved'))

JUNIT_SCHEMA = 'bifrost.test.workflow-running-cancel-junit-projection/v1'
JUNIT_LIMIT = 1024*1024
JUNIT_COUNTS = ('observed_records','known_records','unique_known_scenarios','unknown_records',
                'passed_records','failure_markers','error_markers','skipped_markers')
LIFETIME_TEST = 'test_actual_case_work_expiry_disposes_owned_cohorts'
LIFETIME_ALIAS = 'control-lifetime-expiry'

def manifest_ids():
    manifest = json.loads((OUT/'manifest.json').read_bytes())
    allowed = [row['case_id'] for row in manifest['roster']]
    check(len(allowed)==39 and len(set(allowed))==39 and
          all(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value) for value in allowed) and
          LIFETIME_ALIAS not in allowed)
    check(manifest['expected_counts']==COUNT and manifest['schema']=='bifrost.test.workflow-running-cancel-manifest/v1')
    check(hashlib.sha256(encoded(manifest['roster'])).hexdigest()==json.loads((OUT/'producer-receipt.json').read_bytes())['roster_sha256'])
    return allowed

def project_junit(raw, allowed_ids):
    summary = {'schema':JUNIT_SCHEMA,'admission':'rejected','rejection_label':None,
               **{key:None for key in JUNIT_COUNTS}}
    rows = []
    names = {f'test_actual_running_cancel_sql_differential[{identity}]':identity for identity in allowed_ids}
    names[LIFETIME_TEST] = LIFETIME_ALIAS
    expected = set(allowed_ids) | {LIFETIME_ALIAS}
    def reject(label):
        if summary['rejection_label'] is None:
            summary['rejection_label'] = label
    try:
        text = raw.decode('utf-8')
        check(len(raw)<=JUNIT_LIMIT and not text.startswith('\ufeff') and '<!DOCTYPE' not in text and '<!ENTITY' not in text)
        declaration = re.match(r'\A\s*<\?xml\s+(.*?)\?>',text,re.DOTALL)
        if declaration:
            encoding = re.search(r'\bencoding\s*=\s*([\"\'])(.*?)\1',declaration.group(1))
            check(encoding is None or encoding.group(2).lower() in {'utf-8','utf8'})
        root = ET.fromstring(text)
    except Exception:
        reject('invalid_xml')
        root = None
    if root is not None:
        nodes = [(root,0)]
        elements = 0
        valid = root.tag=='testsuites'
        while nodes:
            node,depth = nodes.pop()
            elements += 1
            if elements>4096 or depth>8:
                valid = False
                break
            nodes.extend((child,depth+1) for child in node)
        cases = []
        if valid:
            for suite in root:
                if suite.tag!='testsuite':
                    valid = False
                    break
                for node in suite:
                    if node.tag=='testcase':
                        cases.append(node)
                        if len(cases)>128:
                            valid = False
                            break
                    elif node.tag not in {'properties','system-out','system-err'}:
                        valid = False
                if not valid:
                    break
        if not valid:
            reject('invalid_shape')
        else:
            summary.update({key:0 for key in JUNIT_COUNTS})
            summary['observed_records'] = len(cases)
            occurrences = {}
            for case in cases:
                markers = {'failure':0,'error':0,'skipped':0}
                structural = True
                for child in case:
                    if child.tag in markers:
                        markers[child.tag] += 1
                    elif child.tag not in {'properties','system-out','system-err'}:
                        structural = False
                for kind,count in markers.items():
                    summary[kind+'_markers'] += count
                if not structural:
                    reject('invalid_shape')
                count = sum(markers.values())
                if count>1:
                    reject('invalid_status')
                if count==0:
                    summary['passed_records'] += 1
                identity = names.get(case.get('name')) if case.get('classname')=='tests.parity.test_workflow_running_cancel_sql' else None
                if identity is None:
                    summary['unknown_records'] += 1
                    reject('unknown_identity')
                    continue
                summary['known_records'] += 1
                occurrences[identity] = occurrences.get(identity,0)+1
                occurrence = occurrences[identity]
                if occurrence>1:
                    reject('duplicate_record')
                value = case.get('time')
                if value is None or len(value)>32 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',value):
                    reject('invalid_time')
                    continue
                decimal = Decimal(value)
                if not decimal.is_finite() or not 0<=decimal<=2700:
                    reject('invalid_time')
                    continue
                if structural and count<=1 and occurrence<=2:
                    status = next((kind for kind,n in markers.items() if n),'passed')
                    rows.append({'scenario_id':identity,'occurrence':str(occurrence),'status':status,
                                 'time_seconds':format(decimal,'f')})
            summary['unique_known_scenarios'] = len(occurrences)
            if set(occurrences)!=expected or summary['observed_records']!=40:
                reject('incomplete_roster')
            if summary['failure_markers'] or summary['error_markers'] or summary['skipped_markers']:
                reject('invalid_status')
            if summary['rejection_label'] is None:
                summary['admission'] = 'accepted'
    return summary,rows

def publish_junit(summary,rows):
    root = ET.Element('rc-junit-projection',schema=JUNIT_SCHEMA,admission=summary['admission'])
    names = {'observed_records':'records','known_records':'known_records','unique_known_scenarios':'unique_known_scenarios',
             'unknown_records':'unknown_records','passed_records':'passed_records','failure_markers':'failure_markers',
             'error_markers':'error_markers','skipped_markers':'skipped_markers'}
    ET.SubElement(root,'observed',**{names[key]:str(summary[key]) for key in JUNIT_COUNTS if summary[key] is not None})
    for row in rows:
        case = ET.SubElement(root,'testcase',**row)
        if row['status']!='passed':
            ET.SubElement(case,row['status'],label='pytest_'+row['status'])
    if summary['rejection_label'] is not None:
        ET.SubElement(root,'rejection',label=summary['rejection_label'])
    projection = ET.tostring(root,encoding='utf-8',xml_declaration=True)
    check(len(projection)<=65536)
    (OUT/'test-results.safe.xml').write_bytes(projection)
    s['junit_projection'] = summary
    s['junit_projection_admission'] = summary['admission']
    s['junit_projection_sha256'] = hashlib.sha256(projection).hexdigest()
    save()

def capture_junit():
    summary = {'schema':JUNIT_SCHEMA,'admission':'rejected','rejection_label':'capture_uncertain',
               **{key:None for key in JUNIT_COUNTS}}
    rows = []
    source = Path(s['logdir'])/'test-results.xml'
    identity = None
    fd = private_fd = None
    after = None
    original = cleanup_error = None
    s['junit_projection_fresh'] = False
    try:
        try:
            before = source.lstat()
        except FileNotFoundError:
            summary['rejection_label'] = 'missing_xml'
            raise RuntimeError('closed missing JUnit') from None
        check(stat.S_ISREG(before.st_mode))
        identity = (before.st_dev,before.st_ino,before.st_uid)
        check(before.st_size<=JUNIT_LIMIT)
        fd = os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        opened = os.fstat(fd)
        check(stat.S_ISREG(opened.st_mode) and (opened.st_dev,opened.st_ino,opened.st_uid)==identity)
        chunks = []
        size = 0
        while True:
            left()
            block = os.read(fd,min(65536,JUNIT_LIMIT+1-size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
            check(size<=JUNIT_LIMIT)
        after = os.fstat(fd)
        check((after.st_dev,after.st_ino,after.st_uid)==identity and
              (after.st_size,after.st_mtime_ns)==(opened.st_size,opened.st_mtime_ns) and size==after.st_size)
        raw = b''.join(chunks)
        private_fd = os.open(RAW/'junit.raw.xml',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        view = memoryview(raw)
        while view:
            left()
            count = os.write(private_fd,view)
            check(count>0)
            view = view[count:]
        allowed_ids = manifest_ids()
        summary,rows = project_junit(raw,allowed_ids)
        s['junit_projection_fresh'] = True
        left()
    except BaseException as error:
        original = error
    finally:
        for owned in (private_fd,fd):
            if owned is not None:
                try:
                    os.close(owned)
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
        try:
            if identity is not None:
                actual = source.lstat()
                check(stat.S_ISREG(actual.st_mode) and (actual.st_dev,actual.st_ino,actual.st_uid)==identity)
                if after is not None:
                    check((actual.st_size,actual.st_mtime_ns)==(after.st_size,after.st_mtime_ns))
                source.unlink()
            else:
                # No captured regular inode: absence is safe; a present path is uncertainty.
                check(not source.exists() and not source.is_symlink())
            s['junit_raw_disposed'] = True
        except BaseException as error:
            s['junit_raw_disposed'] = False
            if cleanup_error is None:
                cleanup_error = error
        if original is not None or cleanup_error is not None:
            summary['admission'] = 'rejected'
            if summary['rejection_label'] is None:
                summary['rejection_label'] = 'capture_uncertain'
        try:
            publish_junit(summary,rows)
        except BaseException as error:
            if cleanup_error is None:
                cleanup_error = error
    if original is not None:
        raise original
    if cleanup_error is not None:
        if not isinstance(cleanup_error,Exception):
            raise cleanup_error
        raise RuntimeError('closed JUnit cleanup failure') from None
    check(summary['admission']=='accepted')

def finite_clock(value):
    return type(value) in (int,float) and math.isfinite(value) and value>=0

def canonical_uuid(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value) is not None

def closed_record(value,keys):
    check(type(value) is dict and set(value)==set(keys))

def read_safe_record(path):
    # Capture one bounded regular safe receipt; never upload an unvalidated tree.
    before = path.lstat()
    check(stat.S_ISREG(before.st_mode) and before.st_size<=JUNIT_LIMIT)
    identity = (before.st_dev,before.st_ino,before.st_uid)
    fd = None
    original = cleanup_error = None
    raw = None
    try:
        fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        opened = os.fstat(fd)
        check(stat.S_ISREG(opened.st_mode) and (opened.st_dev,opened.st_ino,opened.st_uid)==identity)
        pieces = []
        size = 0
        while True:
            left()
            block = os.read(fd,min(65536,JUNIT_LIMIT+1-size))
            if not block:
                break
            pieces.append(block)
            size += len(block)
            check(size<=JUNIT_LIMIT)
        after = os.fstat(fd)
        actual = path.lstat()
        check((after.st_dev,after.st_ino,after.st_uid)==identity and
              (actual.st_dev,actual.st_ino,actual.st_uid)==identity and stat.S_ISREG(actual.st_mode) and
              (after.st_size,after.st_mtime_ns)==(opened.st_size,opened.st_mtime_ns)==(actual.st_size,actual.st_mtime_ns) and
              size==after.st_size)
        raw = b''.join(pieces)
    except BaseException as error:
        original = error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                cleanup_error = error
    if original is not None:
        raise original
    if cleanup_error is not None:
        raise cleanup_error
    def unique(pairs):
        value = {}
        for key,item in pairs:
            check(key not in value)
            value[key] = item
        return value
    def invalid_constant(_value):
        raise RuntimeError('closed safe receipt numeric failure')
    def finite_number(text):
        value = float(text)
        check(math.isfinite(value))
        return value
    data = json.loads(raw.decode('utf-8'),object_pairs_hook=unique,
                      parse_constant=invalid_constant,parse_float=finite_number)
    return data,raw

def collect_business():
    allowed = set(manifest_ids())
    dest = Path(s['evidence_root'])/'observations'
    records = []
    seen = set()
    original = summary_error = None
    try:
        for path in sorted(dest.glob('*.json')):
            if path.name.startswith('control-lifetime-'):
                continue
            check(path.name.startswith(('r-','c-','x-','t-','w-')))
            data,raw = read_safe_record(path)
            closed_record(data,{'schema','scenario_id','run_uuid','paired','passed','cleanup_ok',
                                'gate_scope','rust_publisher','event_parity','counts','lanes'})
            check(data['schema']=='bifrost.test.workflow-running-cancel-observation/v1' and
                  data['scenario_id'] in allowed and data['scenario_id'] not in seen and canonical_uuid(data['run_uuid']) and
                  path.name==data['scenario_id']+'-'+data['run_uuid']+'.json')
            check(all(type(data[key]) is bool for key in ('paired','passed','cleanup_ok')) and
                  data['gate_scope']=='sql_projection' and data['rust_publisher']=='absent' and data['event_parity']=='held')
            count_keys = {'python_selected','rust_selected','python_invoked','rust_invoked','python_completed','rust_completed'}
            closed_record(data['counts'],count_keys)
            check(all(type(n) is int and 0<=n<=89 for n in data['counts'].values()))
            check(type(data['lanes']) is list and [row['lane'] for row in data['lanes']]==(['p','r'] if data['paired'] else ['r']))
            for lane in data['lanes']:
                closed_record(lane,{'lane','cohort_id','preservation_verified','actors','graphs','python_reference_events','database_admission'})
                check(canonical_uuid(lane['cohort_id']) and type(lane['preservation_verified']) is bool and
                      all(type(lane[key]) is list for key in ('actors','graphs','python_reference_events','database_admission')))
            seen.add(data['scenario_id'])
            records.append(data)
            (OUT/path.name).write_bytes(raw)
    except BaseException as error:
        original = error
    finally:
        try:
            s['observations'] = [{'name':p.name,'sha256':sha(p)} for p in sorted(OUT.glob('*.json')) if p.name.startswith(('r-','c-','x-','t-','w-'))]
            s['scenario_counts'] = {'selected':39,'completed':len(records),'passed':sum(d['passed'] for d in records),
                'failed':sum(not d['passed'] for d in records),'paired':sum(d['paired'] for d in records),'rust_only':sum(not d['paired'] for d in records)}
            counts = {key:sum(d['counts'][key] for d in records) for key in ('python_selected','rust_selected','python_completed','rust_completed')}
            counts['total_completed'] = counts['python_completed']+counts['rust_completed']
            s['actor_counts'] = counts
            save()
        except BaseException as error:
            summary_error = error
    if original is not None:
        raise original
    if summary_error is not None:
        raise summary_error
    check(seen==allowed and len(records)==39 and
          counts=={'python_selected':44,'rust_selected':45,'python_completed':44,'rust_completed':45,'total_completed':89} and
          sum(d['counts']['python_invoked'] for d in records)==44 and sum(d['counts']['rust_invoked'] for d in records)==45 and
          all(d['passed'] and d['cleanup_ok'] for d in records))

def collect_lifetime():
    s['lifetime_control'] = {'selected':1,'completed':0,'passed':0,'receipt_sha256':None}
    save()
    paths = sorted((Path(s['evidence_root'])/'observations').glob('control-lifetime-*.json'))
    check(len(paths)==1)
    path = paths[0]
    data,raw = read_safe_record(path)
    closed_record(data,{'schema','control_id','run_uuid','case_seconds','work_seconds','cleanup_reserve_seconds',
                        'started','work_end','case_end','timeout_observed','original_error_preserved','passed',
                        'business_invocations','lanes'})
    check(data['schema']=='bifrost.test.workflow-running-cancel-lifetime-control/v1' and
          data['control_id']=='case-work-expiry' and canonical_uuid(data['run_uuid']) and
          path.name=='control-lifetime-'+data['run_uuid']+'.json')
    check(all(type(data[key]) is int and data[key]==n for key,n in
              (('case_seconds',90),('work_seconds',75),('cleanup_reserve_seconds',15),('business_invocations',0))))
    check(all(type(data[key]) is bool for key in ('timeout_observed','original_error_preserved','passed')) and
          all(finite_clock(data[key]) for key in ('started','work_end','case_end')) and
          data['work_end']==data['started']+75 and data['case_end']==data['started']+90)
    check(type(data['lanes']) is list and len(data['lanes'])==2)
    success = data['timeout_observed'] and data['original_error_preserved']
    for lane,name in zip(data['lanes'],('p','r')):
        closed_record(lane,{'lane','cleanup_started','cleanup_finished','cleanup_ok','rows_absent','redis_disposed'})
        check(lane['lane']==name and all(type(lane[key]) is bool for key in ('cleanup_ok','rows_absent','redis_disposed')) and
              finite_clock(lane['cleanup_started']) and finite_clock(lane['cleanup_finished']) and
              data['started']<=lane['cleanup_started']<=lane['cleanup_finished']<=data['case_end'])
        success = success and data['work_end']<=lane['cleanup_started'] and all(lane[key] for key in ('cleanup_ok','rows_absent','redis_disposed'))
    check(not data['passed'] or success)
    (OUT/path.name).write_bytes(raw)
    s['lifetime_control'] = {'selected':1,'completed':1,'passed':int(data['passed']),
                            'receipt_sha256':hashlib.sha256(raw).hexdigest()}
    save()
    check(data['passed'] and success)


def cleanup():
    begin('cleanup')
    failures = []
    evidence_failures = []
    evidence_control = None

    def retain_failure(labels, label, error):
        nonlocal evidence_control
        labels.append(label)
        if evidence_control is None and not isinstance(error,Exception):
            evidence_control = error

    if s.get('target_started'):
        try:
            dest = Path(s['evidence_root'])
            s['driver_after'] = sha(dest/'driver')
            s['receipt_after'] = sha(dest/'receipt.json')
            s['api_after'] = capture(['docker','image','inspect','bifrost-test-api-dev:latest','--format','{{.Id}}'],'api-after')
            check(s['driver_before']==s['driver_after'] and s['receipt_before']==s['receipt_after'] and s['api_before']==s['api_after'])
            s['uid_after'] = {'uid':dest.stat().st_uid,'gid':dest.stat().st_gid,'mode':stat.S_IMODE(dest.stat().st_mode)}
        except BaseException as error:
            evidence_failures.append('target-custody')
            if not isinstance(error,Exception):
                evidence_control = error
        for operation,label in ((collect_business,'business-observations'),(collect_lifetime,'lifetime-control')):
            try:
                operation()
            except BaseException as error:
                evidence_failures.append(label)
                if evidence_control is None and not isinstance(error,Exception):
                    evidence_control = error
        # Raw XML capture/disposal is independent of all other target-custody checks.
        try:
            capture_junit()
        except BaseException as error:
            evidence_failures.append('junit-projection')
            if evidence_control is None and not isinstance(error,Exception):
                evidence_control = error
    if os.environ.get('BIFROST_ACTION_PIN_TOKEN_FILE'):
        try:
            dispose_credential()
        except BaseException as error:
            retain_failure(failures,'credential',error)
    if s.get('stack_owned'):
        try:
            command(['./test.sh','stack','down'],'own-stack-down',clean_env())
        except BaseException as error:
            retain_failure(failures,'stack-down',error)
    for rec in s.get('containers',[]):
        if rec.get('removed'):
            continue
        try:
            current = capture(['docker','ps','-aq','--no-trunc','--filter','name=^/'+rec['name']+'$'],rec['name']+'-cleanup-find')
            if current:
                check(rec['cid'] is None or rec['cid']==current)
                f = facts('container',current,rec['name']+'-cleanup-facts')
                check(f['Name']=='/'+rec['name'] and f['Image']==rec['image'] and f['Config']['Labels'][LABEL]==PREFIX)
                command(['docker','rm','-f',current],rec['name']+'-remove',clean_env(),cap=65536)
            else:
                check(rec['cid'] is None)
            rec['removed'] = True
            save()
        except BaseException as error:
            retain_failure(failures,'container',error)
    for rec in s.get('volumes',[]):
        if rec.get('removed'):
            continue
        try:
            current = capture(['docker','volume','ls','-q','--filter','name=^'+rec['name']+'$'],rec['name']+'-cleanup-find')
            if current:
                f = facts('volume',current,rec['name']+'-cleanup-facts')
                check(f['Labels'][LABEL]==PREFIX and (rec['created'] is None or f['CreatedAt']==rec['created']) and (rec['mountpoint'] is None or f['Mountpoint']==rec['mountpoint']))
                command(['docker','volume','rm',rec['name']],rec['name']+'-remove',clean_env(),cap=65536)
            else:
                check(rec['created'] is None)
            rec['removed'] = True
            save()
        except BaseException as error:
            retain_failure(failures,'volume',error)
    for rec in s.get('images',[]):
        if rec.get('removed'):
            continue
        try:
            current = capture(['docker','image','ls','-q','--no-trunc',rec['tag']],rec['tag'].split(':')[-1]+'-cleanup-image')
            check(current and rec['id'] and current==rec['id'])
            command(['docker','image','rm',rec['tag']],rec['tag'].split(':')[-1]+'-remove-image',clean_env(),cap=65536)
            rec['removed'] = True
            save()
        except BaseException as error:
            retain_failure(failures,'image',error)
    inventory = {}
    for key, args in [('containers',['docker','ps','-aq']),('volumes',['docker','volume','ls','-q']),('networks',['docker','network','ls','-q'])]:
        try:
            custom = capture(args+['--filter','label='+LABEL+'='+PREFIX],'final-custom-'+key)
            project = capture(args+['--filter','label=com.docker.compose.project='+s['project']],'final-project-'+key) if s.get('stack_owned') else ''
            inventory[key] = sorted(set((custom+'\n'+project).split()))
            check(not inventory[key])
        except BaseException as error:
            retain_failure(failures,'inspection-'+key,error)
    source_matches = False
    try:
        if s.get('main_mode')=='verify':
            source_matches = set(s.get('source_hashes',{}))==set(PATHS) and all(sha(ROOT/p)==h for p,h in s['source_hashes'].items())
        elif s.get('main_mode')=='format':
            source_matches = not capture(['git','status','--porcelain','--untracked-files=all'],'format-final-source') and capture(['git','rev-parse','HEAD','HEAD^{tree}'],'format-final-identity').splitlines()==s['source']
        check(source_matches)
    except BaseException as error:
        retain_failure(evidence_failures,'source-custody',error)
    if not failures:
        try:
            info = RAW.lstat()
            check(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o700 and {'dev':info.st_dev,'ino':info.st_ino,'uid':info.st_uid}==s['private_identity'])
            shutil.rmtree(RAW)
            check(not RAW.exists())
            s['private_logs_disposed'] = True
        except BaseException as error:
            retain_failure(failures,'private-disposal',error)
    else:
        s['private_logs_disposed'] = False
    s['cleanup'] = {'project':s.get('project'),'exit':int(bool(failures)),**inventory}
    s['inspection'] = {'exit':int(bool(evidence_failures or failures)), 'driver_matches':s.get('driver_before')==s.get('driver_after') if s.get('target_started') else False,
                      'api_image_matches':s.get('api_before')==s.get('api_after') if s.get('target_started') else False,
                      'source_matches':source_matches,
                      'junit_projection_fresh':bool(s.get('junit_projection_fresh')),
                      'junit_projection_admission':s.get('junit_projection_admission'),'observations_retained':len(s.get('observations',[]))}
    s['evidence_failures'] = evidence_failures
    s['disposed'] = not failures
    try:
        save()
    except BaseException as error:
        # State-save failure cannot replace an earlier recorded control object.
        if evidence_control is None:
            raise
    if evidence_control is not None:
        raise evidence_control
    check(not failures and not evidence_failures)

def failure_code(error):
    return error.code if isinstance(error,SystemExit) and isinstance(error.code,int) and error.code else 130 if isinstance(error,KeyboardInterrupt) else 1

original = None
status = 0
if MODE!='cleanup':
    s['main_mode'] = MODE
    save()
try:
    if MODE=='cleanup':
        if not s.get('disposed'):
            cleanup()
    elif MODE=='format':
        format_only()
    else:
        verify()
except BaseException as error:
    original = error
    status = failure_code(error)
finally:
    if MODE!='cleanup':
        try:
            cleanup()
        except BaseException as error:
            if original is None:
                original = error
            status = status or failure_code(error)
    try:
        if MODE!='cleanup':
            s['gate_exit'] = status
            s['primary_publication'] = 'pending'
            save()
        primary = s.get('gate_exit',1)
        if MODE=='cleanup':
            s['cleanup_invocation_exit'] = status
            (OUT/'cleanup-invocation.json').write_bytes(encoded({'schema':'bifrost.test.workflow-running-cancel-cleanup/v1','exit':status,'primary_publication':s.get('primary_publication','unknown'),'primary_exit':primary if s.get('primary_publication')=='complete' else None,'cleanup':s.get('cleanup'),'inspection':s.get('inspection')}))
            save()
        if MODE!='cleanup' and s.get('source') and s.get('main_mode')=='verify':
            receipt = {'schema':'bifrost.test.workflow-running-cancel-completion/v2',
                'candidate_sha':s['source'][0],'candidate_tree':s['source'][1],
                'producer_receipt_sha256':s.get('receipt_before'),
                'driver_sha256_before':s.get('driver_before'),'driver_sha256_after':s.get('driver_after'),
                'api_image_id_before':s.get('api_before'),'api_image_id_after':s.get('api_after'),
                'scenario_counts':s.get('scenario_counts'),'actor_counts':s.get('actor_counts'),
                'lifetime_control':s.get('lifetime_control',{'selected':1,'completed':0,'passed':0,'receipt_sha256':None}),
                'junit_projection_sha256':s.get('junit_projection_sha256'),'junit_projection_admission':s.get('junit_projection_admission'),'observations':s.get('observations',[]),
                'gate_exit':primary,'cleanup':s.get('cleanup'),'inspection':s.get('inspection')}
            (OUT/'completion.json').write_bytes(encoded(receipt))
        elif MODE!='cleanup' and s.get('source') and s.get('main_mode')=='format':
            (OUT/'format-completion.json').write_bytes(encoded({'schema':'bifrost.test.workflow-running-cancel-format/v1','candidate_sha':s['source'][0],'candidate_tree':s['source'][1],'patch_sha256':sha(OUT/'format.patch') if (OUT/'format.patch').is_file() else None,'gate_exit':primary,'cleanup':s.get('cleanup'),'inspection':s.get('inspection')}))
        if MODE!='cleanup':
            s['exit'] = primary
            save()
            (OUT/'exit-status.txt').write_text(str(primary)+'\n')
            s['primary_publication'] = 'complete'
            save()
    except BaseException as error:
        if original is None:
            original = error
        status = status or failure_code(error)
        if MODE!='cleanup':
            s['gate_exit'] = status
            s['exit'] = status
            s['primary_publication'] = 'failed'
            # Independent best-effort failure markers; absence is uncertainty, never success.
            try:
                save()
            except BaseException:
                pass
            try:
                (OUT/'exit-status.txt').write_text(str(status)+'\n')
            except BaseException:
                pass
if original is not None and not isinstance(original,Exception):
    raise original
if status:
    print('Running/Cancel source gate failed',file=sys.stderr)
sys.exit(status)
PY
