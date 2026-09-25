"""协议边界测试：用真实场景的 initialize / decision_request 驱动 agents/obs14/main.py，检查
(1) 正常请求；(2) 同一序号重复请求（平台“相同决策可安全重试”）是否给出逐字节相同的回复；
(3) 未知 message_type；(4) decision_request 带未知 schema_version；(5) 未知 protocol_version。
只读仓库，输出到 stdout。用法：python proto_test.py <python解释器> <agent main.py> <scenario>
"""
import json, subprocess, sys, time
from pathlib import Path

ROOT = Path("/home/steven/code/GOSIM26")
sys.path.insert(0, str(ROOT / "kit"))
from challenge.challenge_workflow import ChallengeWorkflow  # noqa: E402

python, entry, scenario = sys.argv[1], Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
wf = ChallengeWorkflow(root=scenario)
initial = wf.initial_publication()
V = "participant-agent-protocol-v2"


def start():
    return subprocess.Popen([python, "-B", str(entry)], cwd=str(entry.parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)


def send(proc, msg, expect_reply=True, timeout=120):
    line = json.dumps(msg, separators=(",", ":")) + "\n"
    t0 = time.monotonic()
    proc.stdin.write(line)
    proc.stdin.flush()
    if not expect_reply:
        return None, 0.0
    reply = proc.stdout.readline()
    return reply, time.monotonic() - t0


def case(name, fn):
    proc = start()
    try:
        send(proc, {"protocol_version": V, "message_type": "initialize", "payload": initial}, expect_reply=False)
        result = fn(proc)
        print(f"[{name}] {result}")
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        err = proc.stderr.read()
        tail = [l for l in err.strip().splitlines() if "observer: plan" not in l and "params=" not in l][-4:]
        print(f"    exit={proc.returncode} stderr_tail={tail}")


snap1 = wf.decision_snapshot(1)

def normal_and_repeat(proc):
    r1, t1 = send(proc, {"protocol_version": V, "message_type": "decision_request", "decision_sequence": 1, "payload": snap1})
    r2, t2 = send(proc, {"protocol_version": V, "message_type": "decision_request", "decision_sequence": 1, "payload": snap1})
    d1, d2 = json.loads(r1), json.loads(r2)
    same = r1 == r2
    return (f"first reply in {t1:.1f}s: action={d1.get('action')} tile={d1.get('tile_id')} program={d1.get('program')} reports={d1.get('reports')}; "
            f"repeat of seq 1 in {t2:.2f}s: identical={same}" + ("" if same else f" -> {d2.get('action')} {d2.get('tile_id')} {d2.get('program')} reports={d2.get('reports')}"))

def unknown_type(proc):
    r, _ = send(proc, {"protocol_version": V, "message_type": "heartbeat", "payload": {}}, expect_reply=False)
    time.sleep(1.0)
    alive = proc.poll() is None
    if alive:
        r, t = send(proc, {"protocol_version": V, "message_type": "decision_request", "decision_sequence": 1, "payload": snap1})
        return f"process survived unknown message_type; next reply ok={bool(r)}"
    return f"process EXITED after unknown message_type (code {proc.returncode})"

def unknown_schema(proc):
    bad = dict(snap1, schema_version="decision-snapshot-v4")
    r, t = send(proc, {"protocol_version": V, "message_type": "decision_request", "decision_sequence": 1, "payload": bad}, timeout=60)
    return f"reply to unknown snapshot schema: {r.strip()[:120] or 'NONE (process exited)'}"

def unknown_protocol(proc):
    r, t = send(proc, {"protocol_version": "participant-agent-protocol-v3", "message_type": "decision_request", "decision_sequence": 1, "payload": snap1})
    return f"reply to unknown protocol_version: {r.strip()[:120] or 'NONE (process exited)'}"

def extra_fields(proc):
    msg = {"protocol_version": V, "message_type": "decision_request", "decision_sequence": 1, "payload": dict(snap1, extra_field={"x": 1}),
           "trace_id": "abc", "server_time": "2026-10-05T00:00:00Z"}
    r, t = send(proc, msg)
    return f"reply with extra envelope/payload fields: ok={bool(r)} action={json.loads(r).get('action') if r else None}"

case("normal + repeated sequence", normal_and_repeat)
case("unknown message_type", unknown_type)
case("unknown snapshot schema_version", unknown_schema)
case("unknown protocol_version", unknown_protocol)
case("extra fields", extra_fields)
