"""Small SSH/rsync-based control plane for the AutoDL + 阿里云 compute pool.

Hosts are plain SSH aliases from ~/.ssh/config (key auth, so every call uses
``-o BatchMode=yes``). This module never reads or prints anything under
``~/.ssh/`` and never ships a model/telemetry secret to a remote host.

Two code roots on one host
--------------------------
``POOL_REMOTE_PROJECT`` selects the remote project directory every command operates on
(default ``/root/gosim/GOSIM26``, the original root, behaviour unchanged). A second root such as
``/root/gosim/GOSIM26-r2`` lets a new source tree run next to batches still running on the old one:
``batch.py`` checks the source hash of the tree it runs from, so a code sync into a root that has
batches in flight would fail them card by card. ``POOL_LOCAL_ROOT`` is the local source tree that
``sync`` pushes and whose hash it compares against (default: the repository this file lives in; point it
at a worktree to deploy that worktree's code). Local results always land in this repository's
``v4/results/remote/``; roots are told apart by tag.

    POOL_REMOTE_PROJECT=/root/gosim/GOSIM26-r2 python -m v4.evaluation.pool init-root all
    POOL_REMOTE_PROJECT=/root/gosim/GOSIM26-r2 POOL_LOCAL_ROOT=/path/to/worktree python -m v4.evaluation.pool sync all
    POOL_REMOTE_PROJECT=/root/gosim/GOSIM26-r2 POOL_LOCAL_ROOT=/path/to/worktree python -m v4.evaluation.pool run ...
    python -m v4.evaluation.pool pull        # pulls the original root and, when present, the second root
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .generate import specifications
from .upstream import ROOT

HOSTS_FILE = Path(__file__).with_name("pool_hosts.json")
DEPLOY_SCRIPT = Path(__file__).with_name("pool_deploy.sh")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
RSYNC_SSH = "ssh -o BatchMode=yes -o ConnectTimeout=10"
SECRET_PATTERN = r"sk-[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}|BEGIN [A-Z ]*PRIVATE KEY"
SYNC_DIRS = ["v4/agent", "v4/evaluation", "v4/experiments", "v4/tests"]
VERIFY_PYTHON_REMOTE = "/root/.uvpy/cpython-3.12.14-linux-x86_64-gnu/bin/python3.12"
REMOTE_HOME = "/root/gosim"  # host-level, shared by every code root: env.sh, runs/, verify/, the engine clone
DEFAULT_REMOTE_PROJECT = f"{REMOTE_HOME}/GOSIM26"
SECOND_REMOTE_PROJECT = f"{REMOTE_HOME}/GOSIM26-r2"  # conventional second root (init-root default, pulled when present)
# Files init-root copies from the local tree into a fresh root; the code directories in SYNC_DIRS come from `sync`.
ROOT_SKELETON = ["pyproject.toml", "uv.lock", ".python-version", "v4/__init__.py", "v4/README.md", "v4/replay"]
_PROJECT_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")  # interpolated into remote shell text: no quotes, spaces or globs


def _validated_project(value: str) -> str:
    value = value.rstrip("/")
    if not _PROJECT_PATH.fullmatch(value) or ".." in value.split("/"):
        raise SystemExit(f"POOL_REMOTE_PROJECT={value!r}: need an absolute path of letters, digits, . _ - /")
    return value


def remote_project(default: str = DEFAULT_REMOTE_PROJECT) -> str:
    """Remote project directory this invocation works on (env POOL_REMOTE_PROJECT, read at call time)."""
    return _validated_project(os.environ.get("POOL_REMOTE_PROJECT") or default)


def log_suffix() -> str:
    """Run logs live in the shared /root/gosim/runs; a non-default root tags its log names (a same-named tag
    in both roots then cannot overwrite or interleave the other root's logs). Default root: names unchanged."""
    project = remote_project()
    return "" if project == DEFAULT_REMOTE_PROJECT else f"@{Path(project).name}"


def local_root() -> Path:
    """Local source tree that sync pushes and hashes (env POOL_LOCAL_ROOT; default this repository)."""
    value = os.environ.get("POOL_LOCAL_ROOT")
    return Path(value).expanduser().resolve() if value else ROOT


def local_python() -> Path | str:
    for root in (local_root(), ROOT):
        candidate = root / ".venv" / "bin" / "python"
        if candidate.exists():
            return candidate
    return sys.executable


def dataset_cards(dataset: str) -> list[str]:
    """Card names of a dataset. With POOL_LOCAL_ROOT set they come from that tree (the code the remote root
    will run), not from this repository's copy."""
    root = local_root()
    if root == ROOT:
        return [spec["name"] for spec in specifications(dataset)]
    code = ("import json, sys; from v4.evaluation.generate import specifications; "
            "print(json.dumps([spec['name'] for spec in specifications(sys.argv[1])]))")
    result = subprocess.run([str(local_python()), "-c", code, dataset], cwd=root, capture_output=True, text=True,
                             check=False)
    if result.returncode != 0:
        raise SystemExit(f"cannot list cards of {dataset!r} from {root}:\n{result.stderr.strip()[-600:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def load_hosts() -> dict:
    return json.loads(HOSTS_FILE.read_text(encoding="utf-8"))


def enabled_hosts(hosts_cfg: dict) -> list[str]:
    return sorted(h for h, c in hosts_cfg["hosts"].items() if c.get("enabled", True))


def resolve_hosts(arg: str | None, hosts_cfg: dict) -> list[str]:
    if arg is None or arg == "all":
        return enabled_hosts(hosts_cfg)
    return arg.split(",")


def is_online(host: str, timeout: int = 8) -> bool:
    try:
        result = subprocess.run(["ssh", *SSH_OPTS, host, "true"], capture_output=True, text=True, timeout=timeout,
                                check=False)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def ssh_run(host: str, remote_cmd: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", *SSH_OPTS, host, remote_cmd], capture_output=True, text=True, timeout=timeout,
                          check=False)


def weighted_assign(items: list, jobs: dict[str, int]) -> dict[str, list]:
    """Smooth weighted round robin: each host gets a share of items proportional to its jobs.

    Generic over the item type: plain card names, or (card, variant) tuples so a host's
    share is computed over actual schedulable units instead of whole cards.
    """
    names = sorted(jobs)
    total = sum(jobs.values())
    credit = dict.fromkeys(names, 0)
    assignment: dict[str, list] = {name: [] for name in names}
    for item in items:
        for name in names:
            credit[name] += jobs[name]
        winner = max(names, key=lambda name: (credit[name], name))
        assignment[winner].append(item)
        credit[winner] -= total
    return assignment


def _split_jobs(sizes: dict[str, int], budget: int) -> dict[str, int]:
    """Per-variant-group ``--jobs`` for one host.

    Each group gets one worker per card (full concurrency) when that already fits in
    budget; otherwise the budget is handed out with the same smooth weighted round robin as
    weighted_assign, proportional to group size and capped at it, so the per-host total
    never exceeds budget. Falls back to 1-per-group (dropping the smallest groups first) if
    there are more non-empty groups than budget allows.
    """
    names = sorted(sizes)
    total = sum(sizes.values())
    if total <= budget:
        return dict(sizes)
    if budget < len(names):
        keep = sorted(names, key=lambda n: (-sizes[n], n))[:budget]
        return {name: 1 for name in keep}
    jobs = dict.fromkeys(names, 1)
    credit = dict.fromkeys(names, 0)
    for _ in range(budget - len(names)):
        for name in names:
            credit[name] += sizes[name]
        candidates = [n for n in names if jobs[n] < sizes[n]]
        if not candidates:
            break
        winner = max(candidates, key=lambda n: (credit[n], n))
        jobs[winner] += 1
        credit[winner] -= total
    return jobs


# --- prepare -----------------------------------------------------------------

def prepare() -> None:
    push = Path("/tmp/gosim-push")
    if push.exists():
        shutil.rmtree(push)
    push.mkdir(parents=True)
    subprocess.run(f"git archive HEAD pyproject.toml uv.lock .python-version v4 | tar -x -C {push}",
                    shell=True, cwd=ROOT, check=True)
    for sub in ("v4/results", "v4/cards", "v4/dist"):
        path = push / sub
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)

    up_git = Path("/tmp/up.git")
    if up_git.exists():
        shutil.rmtree(up_git)
    engine = ROOT.parent / "hackathon-survey26"
    subprocess.run(["git", "clone", "-q", "--bare", "--single-branch", "-b", "main", str(engine), str(up_git)],
                    check=True)

    subprocess.run(["tar", "-chf", "/tmp/parity-cards.tar", "-C", str(ROOT / "v4" / "cards"),
                     "trial-a", "kind-validation-r3-011"], check=True)

    env_sh = Path("/tmp/gosim_env.sh")
    if not env_sh.exists():
        hosts_cfg = load_hosts()
        candidates = ["gosim"] + [h for h in enabled_hosts(hosts_cfg) if h != "gosim"]
        for host in candidates:
            if not is_online(host):
                continue
            result = subprocess.run(["scp", *SSH_OPTS, f"{host}:/root/gosim/env.sh", str(env_sh)],
                                      capture_output=True, text=True, check=False)
            if result.returncode == 0:
                break
        else:
            print("prepare: no online host to fetch env.sh from; /tmp/gosim_env.sh is missing")

    scan = subprocess.run(["grep", "-rlE", SECRET_PATTERN, str(push)], capture_output=True, text=True, check=False)
    if scan.returncode == 0:
        raise SystemExit(f"secret-like pattern found under {push}, aborting:\n{scan.stdout}")
    print("prepare: ok (/tmp/gosim-push, /tmp/up.git, /tmp/parity-cards.tar, /tmp/gosim_env.sh)")


# --- setup ---------------------------------------------------------------------

def cmd_setup(hosts: list[str]) -> None:
    prepare()

    def deploy(host: str) -> tuple[str, list[str]]:
        subprocess.run(["bash", str(DEPLOY_SCRIPT), host], check=False)
        log = Path(f"/tmp/deploy-{host}.log")
        lines = log.read_text(encoding="utf-8").splitlines()[-3:] if log.exists() else ["(no log)"]
        return host, lines

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        for host, lines in pool.map(deploy, hosts):
            print(f"== {host} ==")
            for line in lines:
                print(" ", line)


# --- sync ------------------------------------------------------------------

def source_sha_local() -> str:
    root = local_root()
    result = subprocess.run([str(local_python()), "-c",
                              "from v4.evaluation.run import source_sha256; print(source_sha256()[:16])"],
                             cwd=root, capture_output=True, text=True, check=False)
    return result.stdout.strip()


def project_procs(host: str, project: str) -> int | None:
    """Evaluation processes (generate/batch/run/verify) whose working directory is inside ``project``.

    A process's root is only visible through its cwd (batch command lines are relative), so this resolves
    /proc/<pid>/cwd. ``None`` means the probe itself failed.
    """
    script = (f'proj=$(readlink -f {project}); n=0; for p in $(pgrep -f "[v]4.evaluation"); do '
              f'c=$(readlink -f /proc/$p/cwd 2>/dev/null); case "$c" in "$proj"|"$proj"/*) n=$((n+1));; esac; done; echo $n')
    result = ssh_run(host, script)
    try:
        return int(result.stdout.strip().splitlines()[-1]) if result.returncode == 0 else None
    except (ValueError, IndexError):
        return None


def cmd_sync(hosts: list[str], experiments_only: bool, force: bool = False) -> None:
    dirs = ["v4/experiments"] if experiments_only else SYNC_DIRS
    src = local_root()
    project = remote_project()
    local_sha = source_sha_local()

    def one(host: str) -> None:
        if not is_online(host):
            print(f"{host}: offline, skip")
            return
        if not experiments_only and not force:
            # batch.py re-checks the source hash before every card, so replacing agent code under a running
            # batch fails it card by card. Refuse; the root that has batches in flight must be left alone.
            busy = project_procs(host, project)
            if busy is None or busy > 0:
                reason = "could not check running processes" if busy is None else f"{busy} evaluation process(es) running in it"
                print(f"{host}: SKIP code sync into {project}: {reason}. Wait for them, use another root "
                      f"(POOL_REMOTE_PROJECT), or pass --force")
                return
        for sub in dirs:
            subprocess.run(["rsync", "-a", "--delete", "--exclude=__pycache__", "-e", RSYNC_SSH,
                             f"{src / sub}/", f"{host}:{project}/{sub}/"], check=False)
        remote = ssh_run(host, f'cd {project} && .venv/bin/python -c '
                                '"from v4.evaluation.run import source_sha256; print(source_sha256()[:16])"')
        remote_sha = remote.stdout.strip()
        status = "OK" if remote_sha == local_sha else "MISMATCH"
        print(f"{host}: local={local_sha} remote={remote_sha} {status}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        list(pool.map(one, hosts))


# --- init-root -------------------------------------------------------------

def _init_root_script(project: str) -> str:
    """Remote shell text: skeleton of a second code root that shares the venv and the cards with the original."""
    default = DEFAULT_REMOTE_PROJECT
    return f"""set -e
P={project}; D={default}
[ -x "$D/.venv/bin/python" ] || {{ echo "FAIL: no default root venv at $D"; exit 2; }}
mkdir -p "$P/v4/results" {REMOTE_HOME}/runs
link() {{ # link TARGET LINK: create, accept an identical existing link, refuse to replace anything else
  if [ -L "$2" ]; then [ "$(readlink "$2")" = "$1" ] || {{ echo "FAIL: $2 -> $(readlink "$2"), expected $1"; exit 3; }}
  elif [ -e "$2" ]; then echo "FAIL: $2 exists and is not a symlink, not replacing it"; exit 3
  else ln -s "$1" "$2"; fi; }}
link "$D/.venv" "$P/.venv"
link "$D/v4/cards" "$P/v4/cards"
[ -d "$(dirname "$P")/hackathon-survey26/.git" ] || echo "WARN: engine clone $(dirname "$P")/hackathon-survey26 missing"
cd "$P" && .venv/bin/python -c "import numpy, scipy, pandas, orjson; print('venv ok', numpy.__version__)"
echo "root ok: $P ($(ls "$P/v4/cards" | wc -l) cards shared)"
"""


def cmd_init_root(hosts: list[str]) -> None:
    project = remote_project(default=SECOND_REMOTE_PROJECT)
    if project == DEFAULT_REMOTE_PROJECT:
        raise SystemExit("init-root: refusing to touch the default root; set POOL_REMOTE_PROJECT to a second root "
                         f"(default {SECOND_REMOTE_PROJECT})")
    src = local_root()
    skeleton = [item for item in ROOT_SKELETON if (src / item).exists()]

    def one(host: str) -> str:
        if not is_online(host):
            return f"{host}: offline, skip"
        made = ssh_run(host, _init_root_script(project), timeout=60)
        out = (made.stdout + made.stderr).strip().replace("\n", " | ")
        if made.returncode != 0:
            return f"{host}: init-root FAILED: {out}"
        copied = subprocess.run(["rsync", "-aR", "--exclude=__pycache__", "-e", RSYNC_SSH, *skeleton,
                                  f"{host}:{project}/"], cwd=src, capture_output=True, text=True, check=False)
        if copied.returncode != 0:
            return f"{host}: skeleton copy FAILED: {copied.stderr.strip()[-200:]}"
        return f"{host}: {out}; next: POOL_REMOTE_PROJECT={project} pool sync {host}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        for line in pool.map(one, hosts):
            print(line)


# --- run / requeue ----------------------------------------------------------

def ssh_fire_and_forget(host: str, remote_cmd: str, timeout: int = 30) -> None:
    """Launch a backgrounded remote command without the local ssh hanging on the channel.

    Plain ``ssh host 'cmd &'`` can block indefinitely: without ``-n`` (and a real stdin of
    /dev/null) ssh keeps the channel open waiting on fds the backgrounded remote process
    inherited, even though that process already redirects its own stdout/stderr/stdin. A
    short timeout treats a hang as "probably started anyway" instead of blocking run/requeue.
    """
    try:
        subprocess.run(["ssh", "-n", *SSH_OPTS, host, remote_cmd], capture_output=True, text=True,
                        timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print(f"{host}: ssh did not return within {timeout}s, assuming the job started anyway")


def start_remote_batch(host: str, dataset: str, tag: str, variant_cards: dict[str, list[str]], host_jobs: int) -> None:
    """One generate pass over this host's union of cards, then one batch.py process per
    non-empty variant group - sized by _split_jobs so the host's total concurrency never
    exceeds host_jobs even when cards * variants would otherwise leave it under-filled.
    """
    all_cards = sorted({card for cards in variant_cards.values() for card in cards})
    project, suffix = remote_project(), log_suffix()
    host_log = f"{REMOTE_HOME}/runs/{tag}-{dataset}-{host}{suffix}.log"
    sizes = {variant: len(cards) for variant, cards in variant_cards.items() if cards}
    per_variant_jobs = _split_jobs(sizes, host_jobs)
    batch_cmds = [
        f".venv/bin/python -m v4.evaluation.batch --dataset {dataset} --variants {variant} "
        f"--cards {','.join(cards)} --jobs {per_variant_jobs[variant]} --tag {tag} "
        f">> {REMOTE_HOME}/runs/{tag}-{dataset}-{host}-{variant}{suffix}.log 2>&1 &"
        for variant, cards in variant_cards.items() if cards
    ]
    inner = (
        f". {REMOTE_HOME}/env.sh; cd {project}; "
        f".venv/bin/python -m v4.evaluation.generate --dataset {dataset} --cards {','.join(all_cards)} "
        f"--jobs {host_jobs} > {host_log} 2>&1 && "
        f"{{ {' '.join(batch_cmds)} wait; }}; "
        f'echo "=== done $(date) ===" >> {host_log}'
    )
    remote_cmd = f"mkdir -p {REMOTE_HOME}/runs && setsid nohup bash -c '{inner}' > /dev/null 2>&1 < /dev/null & disown"
    ssh_fire_and_forget(host, remote_cmd)


def _group_by_variant(tasks: list[tuple[str, str]]) -> dict[str, list[str]]:
    by_variant: dict[str, list[str]] = {}
    for card, variant in tasks:
        by_variant.setdefault(variant, []).append(card)
    return by_variant


def cmd_run(dataset: str, variants: list[str], tag: str, hosts_arg: str | None,
            only_cards: list[str] | None = None) -> None:
    hosts_cfg = load_hosts()
    requested = resolve_hosts(hosts_arg, hosts_cfg)
    online = [h for h in requested if is_online(h)]
    for host in requested:
        if host not in online:
            print(f"{host}: offline, skip")
    if not online:
        print("no hosts online, nothing started")
        return
    cards = dataset_cards(dataset)
    if only_cards is not None:
        unknown = sorted(set(only_cards) - set(cards))
        if unknown:
            raise SystemExit(f"--cards: not in dataset {dataset!r}: {', '.join(unknown)}")
        cards = [card for card in cards if card in set(only_cards)]
    tasks = [(card, variant) for card in cards for variant in variants]
    jobs = {host: hosts_cfg["hosts"][host]["jobs"] for host in online}
    assignment = weighted_assign(tasks, jobs)
    table = ROOT / "v4" / "results" / "remote" / "pool" / f"{tag}-{dataset}.json"
    table.parent.mkdir(parents=True, exist_ok=True)
    table.write_text(
        json.dumps({host: [list(task) for task in host_tasks] for host, host_tasks in assignment.items()},
                    indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    def launch(host: str) -> str:
        host_tasks = assignment[host]
        if not host_tasks:
            return f"{host}: nothing assigned"
        start_remote_batch(host, dataset, tag, _group_by_variant(host_tasks), jobs[host])
        n_cards = len({card for card, _ in host_tasks})
        return f"{host}: started {len(host_tasks)} (card,variant) tasks over {n_cards} cards"

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(online))) as pool:
        for line in pool.map(launch, online):
            print(line)


def host_running_tag(host: str, tag: str) -> bool:
    """Is a batch of exactly this tag running in the current project root? Matching is on the whole tag
    (tag "w3" is not "w3ac") and on the process cwd, because a second root may run the same tag name."""
    project = remote_project()
    script = (f'proj=$(readlink -f {project}); for p in $(pgrep -f -- "--tag {tag}( |$)"); do '
              f'c=$(readlink -f /proc/$p/cwd 2>/dev/null); case "$c" in "$proj"|"$proj"/*) echo $p;; esac; done')
    result = ssh_run(host, script)
    return result.returncode == 0 and bool(result.stdout.strip())


def cmd_requeue(dataset: str, variants: list[str], tag: str) -> None:
    hosts_cfg = load_hosts()
    result_root = ROOT / "v4" / "results" / "remote" / f"{dataset}-r2" / tag
    cards = dataset_cards(dataset)
    missing = [(card, variant) for card in cards for variant in variants
               if not (result_root / variant / card / "summary.json").exists()]
    if not missing:
        print("requeue: nothing missing")
        return
    online = [h for h in enabled_hosts(hosts_cfg) if is_online(h)]
    free = []
    for host in online:
        if host_running_tag(host, tag):
            print(f"{host}: online but already running tag {tag}, skip")
        else:
            free.append(host)
    if not free:
        print("requeue: no free host to take the missing (card,variant) tasks")
        return
    jobs = {host: hosts_cfg["hosts"][host]["jobs"] for host in free}
    assignment = weighted_assign(missing, jobs)
    table = ROOT / "v4" / "results" / "remote" / "pool" / f"{tag}-{dataset}.json"
    existing = json.loads(table.read_text(encoding="utf-8")) if table.exists() else {}

    def launch(host: str) -> str | None:
        host_tasks = assignment[host]
        if not host_tasks:
            return None
        existing.setdefault(host, []).extend(list(task) for task in host_tasks)
        start_remote_batch(host, dataset, tag, _group_by_variant(host_tasks), jobs[host])
        return f"{host}: requeued {len(host_tasks)} (card,variant) tasks ({len(missing)} were missing)"

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(free))) as pool:
        for line in pool.map(launch, free):
            if line:
                print(line)
    table.parent.mkdir(parents=True, exist_ok=True)
    table.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --- verify ------------------------------------------------------------------

def sync_verify_inputs(host: str, package: Path, cards: list[str]) -> None:
    project = remote_project()
    ssh_run(host, f"mkdir -p {REMOTE_HOME}/verify {project}/v4/cards", timeout=20)
    for card in cards:
        subprocess.run(["rsync", "-a", "-L", "-e", RSYNC_SSH, f"{ROOT / 'v4' / 'cards' / card}/",
                         f"{host}:{project}/v4/cards/{card}/"], check=False)
    subprocess.run(["rsync", "-a", "-e", RSYNC_SSH, str(package),
                     f"{host}:{REMOTE_HOME}/verify/{package.name}"], check=False)


def start_remote_verify(host: str, package_name: str, cards: list[str]) -> None:
    """One verify_package.py process per card: the script scores its ``--cards`` serially,
    so parallelism across cards comes from running one process per card instead. Each
    process's ``--out`` is the same package-verify directory, but every card writes to its
    own ``<out>/<package-stem>/<card>/`` subpath, so the processes never touch the same file.
    """
    project = remote_project()
    package_remote = f"{REMOTE_HOME}/verify/{package_name}"
    out_dir = f"{project}/v4/results/package-verify"
    log_stem = f"{REMOTE_HOME}/runs/verify-{Path(package_name).stem}"
    verify_cmds = [
        f".venv/bin/python -m v4.evaluation.verify_package --package {package_remote} "
        f"--cards v4/cards/{card} --python {VERIFY_PYTHON_REMOTE} --out {out_dir} "
        f">> {log_stem}-{card}{log_suffix()}.log 2>&1 &"
        for card in cards
    ]
    inner = (
        f". {REMOTE_HOME}/env.sh; cd {project}; "
        f"{' '.join(verify_cmds)} wait"
    )
    remote_cmd = (f"mkdir -p {REMOTE_HOME}/runs && "
                   f"setsid nohup bash -c '{inner}' > /dev/null 2>&1 < /dev/null & disown")
    ssh_fire_and_forget(host, remote_cmd)


def cmd_verify(package: Path, cards: list[str], host: str) -> None:
    if not is_online(host):
        print(f"{host}: offline")
        return
    sync_verify_inputs(host, package, cards)
    start_remote_verify(host, package.name, cards)
    print(f"{host}: verifying {package.name} over {len(cards)} cards in parallel (one process per "
          f"card); pull, then check v4/results/package-verify/{package.stem}/<card>/score_report.json")


# --- pull --------------------------------------------------------------------

def pull_roots(host: str) -> list[str]:
    """Remote project roots to pull from: the original root, the conventional second root and
    POOL_REMOTE_PROJECT, each only if it already has a results directory on that host."""
    candidates = [DEFAULT_REMOTE_PROJECT, SECOND_REMOTE_PROJECT]
    extra = os.environ.get("POOL_REMOTE_PROJECT")
    if extra and remote_project() not in candidates:
        candidates.append(remote_project())
    probe = ssh_run(host, "for d in " + " ".join(candidates) + '; do [ -d "$d/v4/results" ] && echo "$d"; done')
    found = [line.strip() for line in probe.stdout.splitlines() if line.strip() in candidates]
    # a failed probe must not lose results: fall back to the original root, as before this existed
    return found if probe.returncode == 0 and found else [DEFAULT_REMOTE_PROJECT]


def pull_once(hosts_cfg: dict) -> None:
    """Pulled layout: v4/results/remote/<dataset>-r2/<tag>/<variant>/<card>/ holds that card's
    summary.json, score_report.json and agent_events.jsonl ("-r2" is batch.py's own
    results-schema tag, independent of a dataset's card-generation revision). Run logs land
    under v4/results/remote/runs/<host>/. verify's output mirrors the remote path verbatim,
    landing at v4/results/package-verify/<package-stem>/<card>/ instead. Every code root a host has
    is pulled into the same local directories; the roots are told apart by tag, not by path.
    """
    dest = ROOT / "v4" / "results" / "remote"
    dest.mkdir(parents=True, exist_ok=True)
    for host in enabled_hosts(hosts_cfg):
        if not is_online(host):
            print(f"{host}: offline, skip")
            continue
        verify_dest = ROOT / "v4" / "results" / "package-verify"
        verify_dest.mkdir(parents=True, exist_ok=True)
        for project in pull_roots(host):
            subprocess.run(["rsync", "-a", "--prune-empty-dirs", "--include=*/", "--include=summary.json",
                             "--include=score_report.json", "--include=agent_events.jsonl", "--exclude=*",
                             "-e", RSYNC_SSH, f"{host}:{project}/v4/results/", f"{dest}/"], check=False)
            subprocess.run(["rsync", "-a", "-e", RSYNC_SSH, f"{host}:{project}/v4/results/package-verify/",
                             f"{verify_dest}/"], check=False)
        log_dest = dest / "runs" / host
        log_dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(["rsync", "-a", "-e", RSYNC_SSH, f"{host}:{REMOTE_HOME}/runs/*.log", f"{log_dest}/"], check=False)
        print(f"{host}: pulled")


def cmd_pull(loop: int | None) -> None:
    while True:
        pull_once(load_hosts())
        if not loop:
            return
        time.sleep(loop)


# --- status ------------------------------------------------------------------

def _procs_by_root(host: str) -> str:
    """` by_root=GOSIM26:36,GOSIM26-r2:5` - evaluation processes grouped by the directory they run in
    (a code root's name; "other" for the short-lived launcher shells whose cwd is the login directory)."""
    result = ssh_run(host, 'for p in $(pgrep -f "[v]4.evaluation"); do readlink -f /proc/$p/cwd 2>/dev/null; done | sort | uniq -c')
    parts = []
    for line in result.stdout.splitlines():
        count, _, path = line.strip().partition(" ")
        name = Path(path.strip()).name if "gosim" in path else "other"
        parts.append(f"{name}:{count}")
    return f" by_root={','.join(parts)}" if parts else ""


def cmd_status() -> None:
    hosts_cfg = load_hosts()
    for host in enabled_hosts(hosts_cfg):
        if not is_online(host):
            print(f"{host}: offline")
            continue
        load_result = ssh_run(host, "uptime")
        load = load_result.stdout.rsplit("load average:", 1)[-1].strip() if "load average:" in load_result.stdout else "?"
        count = ssh_run(host, 'pgrep -fc "[v]4.evaluation"').stdout.strip() or "0"
        print(f"{host}: online load={load} procs={count}{_procs_by_root(host)}")
        logs = ssh_run(host, f'for f in {REMOTE_HOME}/runs/*.log; do [ -f "$f" ] && echo "$f|$(tail -1 "$f")"; done')
        for line in logs.stdout.splitlines():
            name, _, last = line.partition("|")
            print(f"    {Path(name).name}: {last[:80]}")


# --- CLI -----------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("prepare", help="build /tmp/gosim-push, /tmp/up.git, parity cards tar, env.sh")

    setup = sub.add_parser("setup", help="prepare, then deploy pool_deploy.sh to each host")
    setup.add_argument("hosts", nargs="+")

    init_root = sub.add_parser("init-root", help="create a second code root on hosts (venv and cards shared with the "
                                                 "original root); then `sync` pushes code into it")
    init_root.add_argument("hosts", nargs="+", help="host names, or 'all'")

    sync = sub.add_parser("sync", help="rsync code only (never v4/results or v4/cards) and verify source hash")
    sync.add_argument("hosts", nargs="+", help="host names, or 'all'")
    sync.add_argument("--experiments-only", action="store_true")
    sync.add_argument("--force", action="store_true",
                      help="push code even if evaluation processes are running in that root (they will fail their "
                           "per-card source-hash check)")

    run = sub.add_parser("run", help="split a dataset's cards across online hosts and launch batch.py remotely")
    run.add_argument("--dataset", required=True)
    run.add_argument("--variants", nargs="+", required=True)
    run.add_argument("--tag", required=True)
    run.add_argument("--hosts")
    run.add_argument("--cards", nargs="+", help="only these cards of the dataset (probes and reruns)")
    run.add_argument("--weights", default="auto", choices=["auto"], help="only 'auto' (proportional to jobs) is implemented")

    requeue = sub.add_parser("requeue", help="assign still-missing cards of a tag to free online hosts")
    requeue.add_argument("--dataset", required=True)
    requeue.add_argument("--variants", nargs="+", required=True)
    requeue.add_argument("--tag", required=True)

    verify = sub.add_parser("verify", help="rsync a package + cards to a host and score each card in parallel")
    verify.add_argument("--package", type=Path, required=True)
    verify.add_argument("--cards", nargs="+", required=True)
    verify.add_argument("--host", default="gosim")

    pull = sub.add_parser("pull", help="rsync summary/score/event files and run logs back to this machine")
    pull.add_argument("--loop", type=int, help="repeat every SEC seconds instead of pulling once")

    sub.add_parser("status", help="online/load/process-count and tail of each host's run logs")

    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "setup":
        cmd_setup(args.hosts)
    elif args.command == "sync":
        hosts_cfg = load_hosts()
        hosts = enabled_hosts(hosts_cfg) if args.hosts == ["all"] else args.hosts
        cmd_sync(hosts, args.experiments_only, args.force)
    elif args.command == "init-root":
        hosts_cfg = load_hosts()
        cmd_init_root(enabled_hosts(hosts_cfg) if args.hosts == ["all"] else args.hosts)
    elif args.command == "run":
        cmd_run(args.dataset, args.variants, args.tag, args.hosts, args.cards)
    elif args.command == "requeue":
        cmd_requeue(args.dataset, args.variants, args.tag)
    elif args.command == "verify":
        cmd_verify(args.package, args.cards, args.host)
    elif args.command == "pull":
        cmd_pull(args.loop)
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
