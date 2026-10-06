"""Small SSH/rsync-based control plane for the AutoDL + 阿里云 compute pool.

Hosts are plain SSH aliases from ~/.ssh/config (key auth, so every call uses
``-o BatchMode=yes``). This module never reads or prints anything under
``~/.ssh/`` and never ships a model/telemetry secret to a remote host.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import subprocess
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
    result = subprocess.run([".venv/bin/python", "-c",
                              "from v4.evaluation.run import source_sha256; print(source_sha256()[:16])"],
                             cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip()


def cmd_sync(hosts: list[str], experiments_only: bool) -> None:
    dirs = ["v4/experiments"] if experiments_only else SYNC_DIRS
    local_sha = source_sha_local()

    def one(host: str) -> None:
        if not is_online(host):
            print(f"{host}: offline, skip")
            return
        for sub in dirs:
            subprocess.run(["rsync", "-a", "--delete", "--exclude=__pycache__", "-e", RSYNC_SSH,
                             f"{ROOT / sub}/", f"{host}:/root/gosim/GOSIM26/{sub}/"], check=False)
        remote = ssh_run(host, 'cd /root/gosim/GOSIM26 && .venv/bin/python -c '
                                '"from v4.evaluation.run import source_sha256; print(source_sha256()[:16])"')
        remote_sha = remote.stdout.strip()
        status = "OK" if remote_sha == local_sha else "MISMATCH"
        print(f"{host}: local={local_sha} remote={remote_sha} {status}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(hosts))) as pool:
        list(pool.map(one, hosts))


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
    host_log = f"/root/gosim/runs/{tag}-{dataset}-{host}.log"
    sizes = {variant: len(cards) for variant, cards in variant_cards.items() if cards}
    per_variant_jobs = _split_jobs(sizes, host_jobs)
    batch_cmds = [
        f".venv/bin/python -m v4.evaluation.batch --dataset {dataset} --variants {variant} "
        f"--cards {','.join(cards)} --jobs {per_variant_jobs[variant]} --tag {tag} "
        f">> /root/gosim/runs/{tag}-{dataset}-{host}-{variant}.log 2>&1 &"
        for variant, cards in variant_cards.items() if cards
    ]
    inner = (
        f". /root/gosim/env.sh; cd /root/gosim/GOSIM26; "
        f".venv/bin/python -m v4.evaluation.generate --dataset {dataset} --cards {','.join(all_cards)} "
        f"--jobs {host_jobs} > {host_log} 2>&1 && "
        f"{{ {' '.join(batch_cmds)} wait; }}; "
        f'echo "=== done $(date) ===" >> {host_log}'
    )
    remote_cmd = f"mkdir -p /root/gosim/runs && setsid nohup bash -c '{inner}' > /dev/null 2>&1 < /dev/null & disown"
    ssh_fire_and_forget(host, remote_cmd)


def _group_by_variant(tasks: list[tuple[str, str]]) -> dict[str, list[str]]:
    by_variant: dict[str, list[str]] = {}
    for card, variant in tasks:
        by_variant.setdefault(variant, []).append(card)
    return by_variant


def cmd_run(dataset: str, variants: list[str], tag: str, hosts_arg: str | None) -> None:
    hosts_cfg = load_hosts()
    requested = resolve_hosts(hosts_arg, hosts_cfg)
    online = [h for h in requested if is_online(h)]
    for host in requested:
        if host not in online:
            print(f"{host}: offline, skip")
    if not online:
        print("no hosts online, nothing started")
        return
    cards = [spec["name"] for spec in specifications(dataset)]
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
    result = ssh_run(host, f'pgrep -f "tag {tag}"')
    return result.returncode == 0 and bool(result.stdout.strip())


def cmd_requeue(dataset: str, variants: list[str], tag: str) -> None:
    hosts_cfg = load_hosts()
    result_root = ROOT / "v4" / "results" / "remote" / f"{dataset}-r2" / tag
    cards = [spec["name"] for spec in specifications(dataset)]
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


# --- pull --------------------------------------------------------------------

def pull_once(hosts_cfg: dict) -> None:
    """Pulled layout: v4/results/remote/<dataset>-r2/<tag>/<variant>/<card>/ holds that card's
    summary.json, score_report.json and agent_events.jsonl ("-r2" is batch.py's own
    results-schema tag, independent of a dataset's card-generation revision). Run logs land
    under v4/results/remote/runs/<host>/.
    """
    dest = ROOT / "v4" / "results" / "remote"
    dest.mkdir(parents=True, exist_ok=True)
    for host in enabled_hosts(hosts_cfg):
        if not is_online(host):
            print(f"{host}: offline, skip")
            continue
        subprocess.run(["rsync", "-a", "--prune-empty-dirs", "--include=*/", "--include=summary.json",
                         "--include=score_report.json", "--include=agent_events.jsonl", "--exclude=*",
                         "-e", RSYNC_SSH, f"{host}:/root/gosim/GOSIM26/v4/results/", f"{dest}/"], check=False)
        log_dest = dest / "runs" / host
        log_dest.mkdir(parents=True, exist_ok=True)
        subprocess.run(["rsync", "-a", "-e", RSYNC_SSH, f"{host}:/root/gosim/runs/*.log", f"{log_dest}/"], check=False)
        print(f"{host}: pulled")


def cmd_pull(loop: int | None) -> None:
    while True:
        pull_once(load_hosts())
        if not loop:
            return
        time.sleep(loop)


# --- status ------------------------------------------------------------------

def cmd_status() -> None:
    hosts_cfg = load_hosts()
    for host in enabled_hosts(hosts_cfg):
        if not is_online(host):
            print(f"{host}: offline")
            continue
        load_result = ssh_run(host, "uptime")
        load = load_result.stdout.rsplit("load average:", 1)[-1].strip() if "load average:" in load_result.stdout else "?"
        count = ssh_run(host, 'pgrep -fc "[v]4.evaluation"').stdout.strip() or "0"
        print(f"{host}: online load={load} procs={count}")
        logs = ssh_run(host, 'for f in /root/gosim/runs/*.log; do [ -f "$f" ] && echo "$f|$(tail -1 "$f")"; done')
        for line in logs.stdout.splitlines():
            name, _, last = line.partition("|")
            print(f"    {Path(name).name}: {last[:80]}")


# --- CLI -----------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("prepare", help="build /tmp/gosim-push, /tmp/up.git, parity cards tar, env.sh")

    setup = sub.add_parser("setup", help="prepare, then deploy pool_deploy.sh to each host")
    setup.add_argument("hosts", nargs="+")

    sync = sub.add_parser("sync", help="rsync code only (never v4/results or v4/cards) and verify source hash")
    sync.add_argument("hosts", nargs="+", help="host names, or 'all'")
    sync.add_argument("--experiments-only", action="store_true")

    run = sub.add_parser("run", help="split a dataset's cards across online hosts and launch batch.py remotely")
    run.add_argument("--dataset", required=True)
    run.add_argument("--variants", nargs="+", required=True)
    run.add_argument("--tag", required=True)
    run.add_argument("--hosts")
    run.add_argument("--weights", default="auto", choices=["auto"], help="only 'auto' (proportional to jobs) is implemented")

    requeue = sub.add_parser("requeue", help="assign still-missing cards of a tag to free online hosts")
    requeue.add_argument("--dataset", required=True)
    requeue.add_argument("--variants", nargs="+", required=True)
    requeue.add_argument("--tag", required=True)

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
        cmd_sync(hosts, args.experiments_only)
    elif args.command == "run":
        cmd_run(args.dataset, args.variants, args.tag, args.hosts)
    elif args.command == "requeue":
        cmd_requeue(args.dataset, args.variants, args.tag)
    elif args.command == "pull":
        cmd_pull(args.loop)
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
