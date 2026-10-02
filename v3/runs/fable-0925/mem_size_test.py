"""测量：initialize / decision_request 的 JSON 字节数（前 80 次决策，全等待），以及 agent 进程收到 initialize 与首次决策后的峰值内存。
用法：python mem_size_test.py <agent 解释器> <agent main.py> <scenario>"""
import json, subprocess, sys, time
from pathlib import Path

ROOT = Path("/home/steven/code/GOSIM26")
sys.path.insert(0, str(ROOT / "kit"))
from challenge.challenge_workflow import ChallengeWorkflow  # noqa: E402

python, entry, scenario = sys.argv[1], Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
wf = ChallengeWorkflow(root=scenario)
initial = wf.initial_publication()
init_line = json.dumps({"protocol_version": "participant-agent-protocol-v2", "message_type": "initialize", "payload": initial}, separators=(",", ":"))
print(f"initialize 行：{len(init_line) / 1e6:.1f} MB（target_catalog {len(initial['target_catalog'])} 行）")

sizes = []
class Rec:
    def __init__(self): self.n = 0
    def __call__(self, snapshot, deadline):
        self.n += 1
        line = json.dumps({"protocol_version": "participant-agent-protocol-v2", "message_type": "decision_request",
                           "decision_sequence": snapshot["decision_sequence"], "payload": snapshot}, separators=(",", ":"))
        sizes.append((len(line), snapshot["night_start"] is not None, snapshot["weekly"] is not None, len(snapshot["candidate_tiles"])))
        if self.n >= 80:
            raise RuntimeError("stop")
        return {"action": "wait"}
wf2 = ChallengeWorkflow(root=scenario)
wf2.run(Rec(), wallclock_seconds=600)
plain = [s for s, ns, wk, _ in sizes if not ns]
ns = [s for s, n, wk, _ in sizes if n and not wk]
wk = [s for s, n, w, _ in sizes if w]
print(f"decision_request 行：普通 {min(plain) / 1e3:.0f}–{max(plain) / 1e3:.0f} KB（中位 {sorted(plain)[len(plain) // 2] / 1e3:.0f} KB，候选 {min(c for *_, c in sizes)}–{max(c for *_, c in sizes)} 块）；"
      f"夜首 {[round(s / 1e6, 2) for s in ns]} MB；含每周发布 {[round(s / 1e6, 2) for s in wk]} MB")
est = sorted(plain)[len(plain) // 2] * 1400 + 30 * (ns[0] if ns else 0) + 5 * (wk[0] if wk else 0)
print(f"整场约 1,400 次决策的下行 JSON 总量估计：约 {est / 1e6:.0f} MB")

def hwm(pid):
    for line in open(f"/proc/{pid}/status"):
        if line.startswith("VmHWM"):
            return int(line.split()[1]) / 1024
proc = subprocess.Popen([python, "-B", str(entry)], cwd=str(entry.parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
t0 = time.monotonic()
proc.stdin.write(init_line + "\n"); proc.stdin.flush()
time.sleep(3.0)
m1 = hwm(proc.pid)
snap = wf.decision_snapshot(1)
proc.stdin.write(json.dumps({"protocol_version": "participant-agent-protocol-v2", "message_type": "decision_request", "decision_sequence": 1, "payload": snap}) + "\n"); proc.stdin.flush()
reply = proc.stdout.readline()
t1 = time.monotonic() - t0
m2 = hwm(proc.pid)
proc.stdin.close(); proc.wait(timeout=10)
print(f"agent（{python}）峰值内存：initialize 后 {m1:.0f} MB，首次决策后 {m2:.0f} MB；initialize+首次决策共 {t1:.1f} s")
