"""Shared fixtures for offline end-to-end bridge tests.

Adapted from Astra's independent acceptance harness (fake-app-server.py /
independent-bridge-check.py). Everything is synthetic: the "node" binary is
sys.executable and the "zcode.cjs" entrypoint is a Python program that
answers the version gate and speaks the app-server NDJSON protocol from a
scripted per-case behavior. No Node install, no account, no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE.parent / "handoff" / "skills" / "astra-glm-orchestrator" / "scripts" / "zcode_worker.py"

SECRET = "SYNTHETIC_ACCEPTANCE_KEY_DO_NOT_USE"
MODEL = "GLM-5.3-Flash"
PROVIDER = "account:zai-individual-coding-plan"
ENDPOINT = "https://api.z.ai/api/anthropic"
RUNTIME_VERSION = "0.16.9"

# A fake app-server child. `sys.argv[-1] == "version"` answers the offline
# version gate; otherwise it serves the NDJSON protocol with per-case
# behavior selected by <workspace>/case.txt (same case semantics as the
# independent acceptance harness).
FAKE_SERVER_SOURCE = f'''
import sys,json
from pathlib import Path
if sys.argv[-1]=='version':
 print('{RUNTIME_VERSION}');raise SystemExit
workspace=Path.cwd();(workspace/'spawned.marker').write_text('spawned');case=(workspace/'case.txt').read_text();sid='sess_fake';turn='turn_fake';model='{MODEL}';provider='{PROVIDER}';effort='high';seq=0

def emit(x): print(json.dumps(x),flush=True)
def event(kind,payload):
 global seq
 seq+=1
 emit({{'method':'session/event','params':{{'sessionId':sid,'turnId':turn,'seq':seq,'type':kind,'payload':payload}}}})
def snapshot(final=False):
 m={{'providerId':provider,'modelId':model}};c={{**m,'options':{{'reasoningLevel':effort}}}}
 s={{'session':{{'sessionId':sid,'workspace':{{'workspacePath':str(workspace),'workspaceKey':str(workspace)}},'model':m,'status':'idle'}},'settings':{{'model':{{'current':c,'available':[{{'ref':m.copy(),'reasoning':{{'levels':[{{'value':v}} for v in ('low','high','max')]}}}}]}}}},'runtime':{{'pendingRequestIds':[]}}}}
 if case=='wrong_create' and not final:s['settings']['model']['current']['modelId']='WRONG_MODEL'
 if case=='missing_workspace' and not final:s['session'].pop('workspace')
 if case=='wrong_final' and final:s['settings']['model']['current']['modelId']='WRONG_MODEL'
 return s
def complete():
 if case=='failed':event('turn.failed',{{'message':'synthetic failure'}})
 else:event('turn.completed',{{'response':('{SECRET}' if case=='secret_echo' else 'FAKE_WORKER_DELIVERY'),'resultType':'success','usage':({{'{SECRET}':6}} if case=='secret_usage' else {{'inputTokens':4,'outputTokens':2,'totalTokens':6,'modelRequestCount':1}})}})
for line in sys.stdin:
 m=json.loads(line);method=m.get('method');rid=m.get('id');params=m.get('params',{{}}) or {{}}
 if method=='provider/updateAccountConfig':out={{'status':'received'}}
 elif method=='session/create':
  effort=params['model']['options']['reasoningLevel'];out=snapshot()
 elif method=='session/subscribe':out={{'sessionId':sid,'events':[],'eventSeq':0}}
 elif method=='session/send':
  event('turn.started',{{}})
  if case=='eof':raise SystemExit
  if case=='early':complete()
  emit({{'id':rid,'result':{{'accepted':True,'sessionId':sid,'stateRevision':3}}}})
  if case in ('early','timeout'):continue
  if case=='denied':
   emit({{'id':'server-perm','method':'interaction/requestPermission','params':{{'sessionId':sid,'turnId':turn,'requestId':'perm_1','toolCallId':'tool_1','toolName':'Bash','riskLevel':'critical','input':{{'command':'synthetic forbidden operation'}}}}}});continue
  if case in ('wrong_auth','secret_metadata'):
   emit({{'id':'server-auth','method':'interaction/requestProviderRuntimeHeaders','params':{{'sessionId':('{SECRET}' if case=='secret_metadata' else 'sess_other'),'providerId':provider,'modelSelection':{{'providerId':provider,'modelId':model}},'workspace':{{'workspacePath':str(workspace),'workspaceKey':str(workspace)}},'reason':'model-request'}}}});continue
  complete();continue
 elif method=='session/read':out=snapshot(final=True)
 elif method in ('session/stop','session/close'):out={{'sessionId':sid}}
 elif rid=='server-perm':complete();continue
 elif rid=='server-auth':
  (workspace/'auth.marker').write_text(json.dumps({{'secret_received':'apiKey' in json.dumps(m)}}));complete();continue
 elif method:
  emit({{'id':rid,'error':{{'code':-32601,'message':'unsupported fake method'}}}});continue
 else:continue
 emit({{'id':rid,'result':out}})
'''


def make_builtin_config(endpoint: str = ENDPOINT, revision: int = 30) -> dict:
    return {
        "schemaVersion": 1,
        "revision": revision,
        "config": {
            "providerConfigRules": {"providerRules": [{
                "providerId": PROVIDER,
                "config": {
                    "builtinModelIds": [MODEL],
                    "access": {"type": "zhipu-account",
                               "mode": "individual-coding-plan",
                               "accountType": "zai"},
                    "api": {"type": "anthropic-messages", "baseUrl": endpoint},
                }}]},
            "modelConfigRules": {
                "builtinProviderModelRules": [{
                    "providerId": PROVIDER, "modelId": MODEL,
                    "config": {"enabled": True}}],
                "modelRules": [{
                    "modelId": MODEL,
                    "config": {"reasoning": {"enabled": True,
                                             "defaultVariant": "max"}}}],
            },
        },
    }


def set_builtin_endpoint(builtin_path: Path, endpoint: str) -> None:
    cfg = json.loads(builtin_path.read_text(encoding="utf-8"))
    cfg["config"]["providerConfigRules"]["providerRules"][0]["config"]["api"]["baseUrl"] = endpoint
    builtin_path.write_text(json.dumps(cfg), encoding="utf-8")


def make_home(base: Path) -> Path:
    home = base / "zcode"
    (home / "v2").mkdir(parents=True)
    (home / "v2" / "provider_config.json").write_text("{}", encoding="utf-8")
    (home / "v2" / "config.json").write_text(json.dumps({
        "provider": {"builtin:zai-coding-plan": {
            "enabled": True, "kind": "anthropic",
            "models": {MODEL: {}},
            "options": {"apiKey": SECRET, "baseURL": ENDPOINT}}}}),
        encoding="utf-8")
    return home


def make_runtime(base: Path) -> dict:
    """Create <base>/resources/{glm/zcode.cjs,config/provider/zcode-builtin.json}
    and the zcode home, mirroring the verified runtime layout."""
    cjs = base / "resources" / "glm" / "zcode.cjs"
    cjs.parent.mkdir(parents=True)
    cjs.write_text(FAKE_SERVER_SOURCE, encoding="utf-8")
    builtin = base / "resources" / "config" / "provider" / "zcode-builtin.json"
    builtin.parent.mkdir(parents=True)
    builtin.write_text(json.dumps(make_builtin_config()), encoding="utf-8")
    return {"cjs": cjs, "builtin": builtin, "home": make_home(base)}


def run_worker(base: Path, case: str, runtime: dict, *, timeout: int = 4,
               run_dir_name: str = "attempt-01", escape: bool = False,
               effort: str = "high") -> tuple[subprocess.CompletedProcess, Path, Path]:
    """Run the real worker CLI against the fake runtime. Returns
    (completed process, workspace, run_dir)."""
    ws = base / case
    ws.mkdir()
    (ws / "case.txt").write_text(case, encoding="utf-8")
    task = ws / "TASK.md"
    task.write_text("Synthetic offline task", encoding="utf-8")
    rd = ws / ".." / "escaped" / "attempt-01" if escape \
        else ws / run_dir_name
    args = [sys.executable, "-B", str(WORKER),
            "--node", sys.executable, "--zcode-path", str(runtime["cjs"]),
            "--zcode-home", str(runtime["home"]),
            "run", "--workspace", str(ws), "--task", str(task),
            "--run-dir", str(rd), "--timeout", str(timeout),
            "--effort", effort]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=40)
    return proc, ws, ws / run_dir_name
