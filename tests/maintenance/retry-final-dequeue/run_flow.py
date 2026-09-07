#!/usr/bin/env python3
"""Orchestrator for the #148 flow-level reproduction (PR #149).

Usage: run_flow.py [base|fix] [case ...]

Each case runs real `sr3 start/stop` cycles against a disposable local
RabbitMQ container, with per-case broker queues, configs and state dirs:

  download-main  seed 2 failed downloads -> SIGKILL after final dequeue
                 -> restart. Want 0/2 on baseline, 2/2 on fix.
  post-main      seed 2 failed posts (real files, dead post broker) ->
                 SIGKILL after final dequeue -> restart. Same wants.
  C1             seed -> restart with no kill. Want 2/2 on both.
  C2             batch-2 config: seed -> SIGKILL after 1-of-2 dequeue ->
                 restart. Want file intact (2 lines) on both.
  C3             seed while files missing -> create files -> process to
                 success -> restart. Want 0 recovered on both.

A case aborts (FAIL, evidence preserved) if any step misses its
deadline or assertion. Broker credentials are the container's disposable
defaults, passed via environment, never written to any file.
"""

import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SR3 = shutil.which("sr3")
if not SR3:
    raise RuntimeError("sr3 must be on PATH to run this reproduction")
# Source repository the trees are built from; override with REPRO_REPO.
REPO = os.environ.get(
    "REPRO_REPO",
    os.path.normpath(
        os.path.join(
            HERE, "..", "..", "..", "..", "..", "..", "sarracenia")))
TREES_WANT = {
    "base": "24de015ccbdd20419ff4cc58e560478009d6ed20",
    "fix": "19dbb236564dbd39c9024b3f7c0fae791d3b4334",
}
TREES = {}
EVIDENCE = os.path.join(HERE, "evidence")
EXCHANGE = "xs_retryfinal"

# Allocated at runtime in main(); never fixed. The broker mapping comes
# from `docker port`, the dead post port is verified refused first.
BROKER_PORT = None
DEAD_PORT = None

PHASE_DEADLINE = 300
RUN_DEADLINE = 5400


def sh(args, env, log_path, timeout=PHASE_DEADLINE):
    with open(log_path, "ab") as log:
        log.write(("+ %s\n" % " ".join(args)).encode())
        log.flush()
        try:
            proc = subprocess.run(args,
                                  env=env,
                                  stdout=log,
                                  stderr=subprocess.STDOUT,
                                  timeout=timeout)
            return proc.returncode
        except subprocess.TimeoutExpired:
            log.write(b"TIMEOUT\n")
            log.flush()
            return "timeout"


def sr3_env(casedir, tree, extra=None):
    # Only SR3's own directories are isolated, via the XDG variables the
    # application itself honors. HOME is intentionally left alone so the
    # worker runs with the exact operator interpreter and installed
    # dependencies; the worktree under test takes import precedence
    # through PYTHONPATH. A startup guard below proves the real home
    # gained no repro artifacts.
    home = os.path.join(casedir, "home")
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    env["XDG_CACHE_HOME"] = os.path.join(home, ".cache")
    env["PYTHONPATH"] = casedir + os.pathsep + tree
    env["PATH"] = os.path.dirname(SR3) + os.pathsep + env.get("PATH", "")
    env["REPRO_RUNDIR"] = casedir
    if extra:
        env.update(extra)
    return env


def sr3(casedir, tree, args, tag, extra=None, timeout=PHASE_DEADLINE):
    env = sr3_env(casedir, tree, extra)
    return sh([SR3] + args, env,
              os.path.join(casedir, "logs", "sr3-%s.log" % tag),
              timeout=timeout)


def wait_until(desc, deadline, fn):
    end = time.time() + deadline
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(2)
    raise RuntimeError("deadline waiting for: %s" % desc)


def state_dir(casedir, cfgname):
    return os.path.join(casedir, "home", ".cache", "sr3", "subscribe",
                        cfgname)


def queue_lines(casedir, cfgname, kind):
    """Line count of the main retry queue file, None if absent.

    Matches only the exact queue file: glob suffixes such as .new and
    .hk are different lifecycle stages and must never be counted here.
    """
    matches = [
        path
        for path in glob.glob(
            os.path.join(state_dir(casedir, cfgname),
                         "diskqueue_%s_retry_*" % kind))
        if re.fullmatch(r"diskqueue_%s_retry_[0-9]+" % kind,
                        os.path.basename(path))
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise RuntimeError("ambiguous queue files, want exactly one: %s"
                           % sorted(matches))
    with open(matches[0], "rb") as handle:
        return handle.read().count(b"\n")


def list_state(casedir, cfgname):
    out = []
    root = state_dir(casedir, cfgname)
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(base, name)
            try:
                st = os.stat(path)
            except FileNotFoundError:
                continue
            out.append({
                "path": os.path.relpath(path, root),
                "size": st.st_size,
            })
    return out


def prepare_trees():
    """Create fresh worktrees for both revisions under evidence/trees.

    The harness never trusts pre-existing checkouts: a vanished or
    wrong tree once made every worker silently import an unrelated
    installed copy, and every result looked plausible. Each tree is
    verified by import before any case runs.
    """
    trees_dir = os.path.join(EVIDENCE, "trees")
    if os.path.exists(trees_dir):
        shutil.rmtree(trees_dir)
    os.makedirs(trees_dir)
    for name, sha in TREES_WANT.items():
        dest = os.path.join(trees_dir, name)
        out = subprocess.run(
            ["git", "-C", REPO, "worktree", "add", "--detach", dest, sha],
            capture_output=True,
            text=True,
            timeout=120)
        assert out.returncode == 0, "worktree add failed: %s" % out.stderr
        probe = subprocess.run(
            [sys.executable, "-c",
             "import sarracenia; print(sarracenia.__file__)"],
            env={**os.environ,
                 "PYTHONPATH": dest},
            capture_output=True,
            text=True,
            timeout=60)
        got = probe.stdout.strip().splitlines()
        assert probe.returncode == 0 and got and got[-1].startswith(dest), \
            "tree %s does not import from itself: %s %s" % (name, got,
                                                            probe.stderr[-500:])
        TREES[name] = dest
    return trees_dir


def drop_trees(trees_dir):
    subprocess.run(["git", "-C", REPO, "worktree", "remove", "--force",
                    os.path.join(trees_dir, "base")],
                   capture_output=True)
    subprocess.run(["git", "-C", REPO, "worktree", "remove", "--force",
                    os.path.join(trees_dir, "fix")],
                   capture_output=True)
    shutil.rmtree(trees_dir, ignore_errors=True)


def assert_tree_identity(record, tree):
    """Every worker-reported sarracenia path must live under the tree
    under test. A mismatch aborts the case: results from the wrong
    interpreter revision are worse than no results."""
    for key in ("kill_marker", "restart_marker"):
        marker = record.get(key)
        if not marker:
            continue
        path = marker.get("sarracenia_file", "")
        assert path.startswith(tree + os.sep), \
            "worker ran %s, want tree %s" % (path, tree)


def broker_env():
    return {
        "REPRO_AMQP_URL":
        "amqp://guest:guest@127.0.0.1:%d//" % BROKER_PORT,
        "REPRO_EXCHANGE": EXCHANGE,
    }


def inject(action, env_extra):
    env = dict(os.environ)
    env.update(broker_env())
    env.update(env_extra)
    out = subprocess.run([sys.executable,
                          os.path.join(HERE, "inject.py"), action],
                         env=env,
                         capture_output=True,
                         text=True,
                         timeout=120)
    return out.returncode, out.stdout.strip()


def broker_depth(queue):
    rc, out = inject("depth", {"REPRO_QUEUE": queue})
    return int(out) if rc == 0 else None


def flow_events(casedir):
    path = os.path.join(casedir, "flow-events.jsonl")
    events = []
    if os.path.exists(path):
        with open(path) as handle:
            for line in handle:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    return events


def is_retry_arrivals(casedir):
    seen = set()
    for event in flow_events(casedir):
        for relpath in event.get("isRetry", []):
            seen.add(relpath)
    return seen


def pid_dead(pid):
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


class Case:

    def __init__(self, tree_name, tree, name, template, batch, mode,
                 post_path):
        self.tree_name = tree_name
        self.tree = tree
        self.name = name
        self.cfgname = "retryfinal" if template == "retryfinal.conf" \
            else os.path.splitext(template)[0]
        self.tag = "%s-%s" % (tree_name, name)
        self.casedir = os.path.join(EVIDENCE, self.tag)
        self.queue = "q_repro.%s" % self.tag.replace("-", ".")
        self.routing = "v03.post.%s.data" % self.tag.replace("-", ".")
        # subtopic is relative: sr3 prepends topicPrefix at bind time.
        self.subtopic = "%s.#" % self.tag.replace("-", ".")
        # Plain filesystem path; the file:// scheme is added at publish
        # time. The download path points at a directory that is never
        # created, so every download fails through ordinary processing.
        self.baseurl = (os.path.join(self.casedir, "missing")
                        if not post_path else
                        os.path.join(self.casedir, "source"))
        if os.path.exists(self.casedir):
            shutil.rmtree(self.casedir)
        os.makedirs(os.path.join(self.casedir, "logs"))
        os.makedirs(os.path.join(self.casedir, "downloads"))
        shutil.copy(os.path.join(HERE, "repro_cb.py"), self.casedir)
        with open(
                os.path.join(HERE, "templates", "subscribe",
                             template)) as handle:
            text = handle.read()
        text = text.replace("__QUEUE__", self.queue)
        text = text.replace("__SUBTOPIC__", self.subtopic)
        text = text.replace("__BATCH__", str(batch))
        text = text.replace("__PORT__", str(BROKER_PORT))
        text = text.replace("__DEADPORT__", str(DEAD_PORT))
        text = text.replace("__DOWNLOADS__",
                            os.path.join(self.casedir, "downloads"))
        confdir = os.path.join(self.casedir, "home", ".config", "sr3",
                               "subscribe")
        os.makedirs(confdir)
        with open(os.path.join(confdir, self.cfgname + ".conf"),
                  "w") as handle:
            handle.write(text)
        self.record = {
            "tree": tree_name,
            "case": name,
            "config": self.cfgname,
            "batch": batch,
            "queue": self.queue,
        }

    def start(self, phase, kill="0", mode="download"):
        return sr3(self.casedir, self.tree,
                   ["start", "subscribe/%s" % self.cfgname],
                   phase,
                   extra={
                       "REPRO_KILL": kill,
                       "REPRO_MODE": mode
                   })

    def stop(self, phase):
        return sr3(self.casedir, self.tree,
                   ["stop", "subscribe/%s" % self.cfgname], phase)

    def status(self, phase):
        return sr3(self.casedir, self.tree,
                   ["status", "subscribe/%s" % self.cfgname], phase)

    def setup_files(self, create):
        """Create matching source files when the scenario needs real
        downloads (post path). Otherwise the base directory stays absent
        so every download fails through ordinary processing."""
        if not create:
            return
        os.makedirs(self.baseurl, exist_ok=True)
        for index in (1, 2):
            with open(
                    os.path.join(
                        self.baseurl,
                        "run-%s-data-%d.bin" % (self.tag, index)),
                    "wb") as handle:
                handle.write(b"retryfinal %s data %d\n" %
                             (self.tag.encode(), index))

    def inject(self):
        rc, out = inject(
            "inject", {
                "REPRO_TAG": self.tag,
                "REPRO_BASEURL": "file://%s" % self.baseurl,
                "REPRO_ROUTING": self.routing,
            })
        assert rc == 0, "inject failed: %s" % out
        self.record.setdefault("injected", []).extend(out.split())

    def purge_broker(self):
        """Empty the case queue so reruns never see another run's
        leftovers. Local retry files are untouched."""
        env = dict(os.environ)
        env.update(broker_env())
        env["REPRO_QUEUE"] = self.queue
        out = subprocess.run([sys.executable,
                              os.path.join(HERE, "inject.py"), "purge"],
                             env=env,
                             capture_output=True,
                             text=True,
                             timeout=120)
        assert out.returncode == 0, "purge failed: %s" % out.stderr

    def seed_until_queued(self, kind, phase="seed"):
        """Start the flow, wait for its queue, inject, wait for 2
        persisted retry records, stop. Injection after queue declaration
        so no message is dropped on a missing binding."""
        rc = self.start(phase)
        assert rc == 0, "sr3 start rc=%s" % rc
        wait_until("broker queue declared", PHASE_DEADLINE,
                   lambda: broker_depth(self.queue) is not None or None)
        self.purge_broker()
        self.inject()
        wait_until(
            "%s queue holds 2 records" % kind, PHASE_DEADLINE,
            lambda: queue_lines(self.casedir, self.cfgname, kind) == 2
            or None)
        self.record["broker_depth_after_seed"] = broker_depth(self.queue)
        rc = self.stop(phase)
        self.record["seed_stop_rc"] = rc
        assert rc == 0, "sr3 stop rc=%s" % rc
        assert queue_lines(self.casedir, self.cfgname, kind) == 2
        self.record["seed_state"] = list_state(self.casedir, self.cfgname)

    def kill_after_dequeue(self, mode, phase="kill"):
        """Start with killing armed; wait for the kill marker; the worker
        must be dead; stop the (dead) config; snapshot pre-restart state."""
        rc = self.start(phase, kill="1", mode=mode)
        assert rc == 0, "sr3 start rc=%s" % rc
        marker_path = os.path.join(self.casedir, "kill.json")
        wait_until("kill marker", PHASE_DEADLINE,
                   lambda: os.path.exists(marker_path) or None)
        with open(marker_path) as handle:
            marker = json.load(handle)
        self.record["kill_marker"] = marker
        wait_until("worker dead", 120,
                   lambda: pid_dead(marker["pid"]) or None)
        rc = self.stop(phase)
        self.record["kill_stop_rc"] = rc
        self.status(phase)
        self.record["pre_restart_state"] = list_state(self.casedir,
                                                      self.cfgname)
        return marker

    def restart_and_count(self, want_arrivals, want_lines, kind,
                          phase="restart", watched_seconds=40,
                          exact_arrivals=True):
        """Restart disarmed; wait; stop; assert arrivals and file lines.

        Arrivals are exact by default (main cases: both records must show
        up). C2 passes exact_arrivals=False because repeated small cycles
        legitimately surface both records over time; there the file line
        count is the recovery proof and arrivals only prove pickup.
        """
        if os.path.exists(os.path.join(self.casedir,
                                       "flow-events.jsonl")):
            os.remove(os.path.join(self.casedir, "flow-events.jsonl"))
        rc = self.start(phase)
        assert rc == 0, "sr3 start rc=%s" % rc
        if want_arrivals:
            wait_until(
                "%d distinct retry arrivals" % want_arrivals,
                PHASE_DEADLINE,
                lambda: len(is_retry_arrivals(self.casedir))
                >= want_arrivals or None)
        else:
            time.sleep(watched_seconds)
        rc = self.stop(phase)
        assert rc == 0, "sr3 stop rc=%s" % rc
        arrivals = sorted(is_retry_arrivals(self.casedir))
        self.record["restart_arrivals"] = arrivals
        if exact_arrivals:
            assert len(arrivals) == want_arrivals, \
                "arrivals %s, want %d" % (arrivals, want_arrivals)
        else:
            assert len(arrivals) >= want_arrivals, \
                "arrivals %s, want at least %d" % (arrivals,
                                                   want_arrivals)
        for relpath in arrivals:
            assert self.tag in relpath, "stale record: %s" % relpath
        lines = queue_lines(self.casedir, self.cfgname, kind)
        self.record["restart_file_lines"] = lines
        assert lines == want_lines, \
            "queue lines %s, want %s" % (lines, want_lines)

    def save(self, verdict):
        self.record["verdict"] = verdict
        with open(os.path.join(self.casedir, "evidence.json"),
                  "w") as handle:
            json.dump(self.record, handle, indent=2)


def run_download_main(tree_name, tree):
    case = Case(tree_name, tree, "download-main", "retryfinal.conf", 4,
                "download", False)
    case.seed_until_queued("work")
    assert broker_depth(case.queue) in (0, None)
    marker = case.kill_after_dequeue("download")
    assert marker["dequeued"] == 2, marker
    if tree_name == "base":
        assert case.record["pre_restart_state"] == [] or all(
            not e["path"].startswith("diskqueue_work_retry")
            for e in case.record["pre_restart_state"]), case.record[
                "pre_restart_state"]
    case.restart_and_count(2 if tree_name == "fix" else 0,
                           2 if tree_name == "fix" else None, "work")
    case.save("PASS")
    return case


def run_post_main(tree_name, tree):
    case = Case(tree_name, tree, "post-main", "retryfinal_post.conf", 4,
                "post", True)
    case.setup_files(create=True)
    case.seed_until_queued("post")
    assert queue_lines(case.casedir, case.cfgname, "work") in (None, 0), \
        "downloads must succeed on the post path"
    assert broker_depth(case.queue) in (0, None)
    marker = case.kill_after_dequeue("post")
    assert marker["dequeued"] == 2, marker
    case.restart_and_count(2 if tree_name == "fix" else 0,
                           2 if tree_name == "fix" else None, "post")
    case.save("PASS")
    return case


def run_c1(tree_name, tree):
    case = Case(tree_name, tree, "C1-pre-dequeue", "retryfinal.conf", 4,
                "download", False)
    case.seed_until_queued("work")
    case.restart_and_count(2, 2, "work", phase="restart")
    case.save("PASS")
    return case


def run_c2(tree_name, tree):
    p1 = os.path.join(HERE, "templates", "subscribe", "retryfinal.conf")
    case = Case(tree_name, tree, "C2-partial", "retryfinal.conf", 2,
                "partial", False)
    case.cfgname = "retryfinal_p1"
    confdir = os.path.join(case.casedir, "home", ".config", "sr3",
                           "subscribe")
    os.rename(
        os.path.join(confdir, "retryfinal.conf"),
        os.path.join(confdir, "retryfinal_p1.conf"))
    case.seed_until_queued("work")
    marker = case.kill_after_dequeue("partial")
    assert marker["dequeued"] == 1, marker
    case.restart_and_count(1, 2, "work", phase="restart",
                           exact_arrivals=False)
    case.save("PASS")
    return case


def run_c3(tree_name, tree):
    case = Case(tree_name, tree, "C3-retire", "retryfinal.conf", 4,
                "download", False)
    case.seed_until_queued("work")
    # Now make the downloads succeed and let the flow retire the batch.
    os.makedirs(case.baseurl, exist_ok=True)
    for index in (1, 2):
        with open(
                os.path.join(case.baseurl,
                             "run-%s-data-%d.bin" % (case.tag, index)),
                "wb") as handle:
            handle.write(b"retryfinal %s data %d\n" %
                         (case.tag.encode(), index))
    rc = case.start("process")
    assert rc == 0
    wait_until(
        "both downloads present", PHASE_DEADLINE, lambda: all(
            os.path.isfile(
                os.path.join(case.casedir, "downloads",
                             "run-%s-data-%d.bin" % (case.tag, index)))
            for index in (1, 2)) or None)
    rc = case.stop("process")
    assert rc == 0
    assert queue_lines(case.casedir, case.cfgname, "work") is None, \
        "retired queue file must be gone"
    # Restart: nothing may arrive and no queue file may exist.
    if os.path.exists(os.path.join(case.casedir, "flow-events.jsonl")):
        os.remove(os.path.join(case.casedir, "flow-events.jsonl"))
    rc = case.start("restart")
    assert rc == 0
    time.sleep(45)
    rc = case.stop("restart")
    assert rc == 0
    arrivals = sorted(is_retry_arrivals(case.casedir))
    assert arrivals == [], arrivals
    assert queue_lines(case.casedir, case.cfgname, "work") is None
    case.record["restart_arrivals"] = arrivals
    case.save("PASS")
    return case


CASES = {
    "download-main": run_download_main,
    "post-main": run_post_main,
    "C1-pre-dequeue": run_c1,
    "C2-partial": run_c2,
    "C3-retire": run_c3,
}


def owned_pids(casedir, cfgname):
    """PIDs read from this case's own pidfiles. The only identities this
    harness may ever signal: exact, run-owned, never name-matched."""
    pids = set()
    root = state_dir(casedir, cfgname)
    if not os.path.isdir(root):
        return pids
    for name in os.listdir(root):
        if not name.endswith(".pid"):
            continue
        try:
            with open(os.path.join(root, name)) as handle:
                pids.add(int(handle.read().strip().split()[0]))
        except (ValueError, OSError):
            pass
    return pids


def terminate_owned(casedir, cfgname):
    """Stop exactly this case's workers: SIGTERM, wait, escalate to
    SIGKILL, then verify every owned PID is dead. Raises if any owned
    PID survives. Never touches any other process."""
    pending = owned_pids(casedir, cfgname)
    for pid in pending:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    end = time.time() + 30
    while time.time() < end:
        pending = {p for p in pending if not pid_dead(p)}
        if not pending:
            break
        time.sleep(1)
    for pid in pending:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    end = time.time() + 15
    while time.time() < end:
        pending = {p for p in pending if not pid_dead(p)}
        if not pending:
            return
        time.sleep(1)
    raise RuntimeError("owned workers survived cleanup: %s" % sorted(pending))


def assert_real_home_clean():
    """Prove the un-isolated real home gained no repro artifacts. Never
    deletes anything there: any hit aborts the run instead."""
    hits = []
    for base in (os.path.expanduser("~/.config/sr3"),
                 os.path.expanduser("~/.cache/sr3")):
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for name in files + root.split(os.sep)[-1:]:
                if "retryfinal" in name or "retryfinal" in root:
                    hits.append(os.path.join(root, name))
                    break
    if hits:
        raise RuntimeError(
            "real home contains repro artifacts, refusing: %s" % hits[:5])


def alloc_dead_port():
    """A currently-unused loopback port for the dead post broker,
    verified refused immediately before use."""
    for _attempt in range(20):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        check = socket.socket()
        check.settimeout(2)
        try:
            check.connect(("127.0.0.1", port))
            check.close()
        except OSError:
            return port
    raise RuntimeError("no verifiably closed loopback port found")


def docker_broker_port(name):
    out = subprocess.run(["docker", "port", name, "5672"],
                         capture_output=True,
                         text=True,
                         timeout=60)
    assert out.returncode == 0, "docker port failed: %s" % out.stderr
    return int(out.stdout.strip().rsplit(":", 1)[1])


def main():
    global BROKER_PORT, DEAD_PORT
    wanted_trees = sys.argv[1:2] or ["base", "fix"]
    wanted_cases = sys.argv[2:] or list(CASES)
    assert_real_home_clean()
    DEAD_PORT = alloc_dead_port()
    if os.path.exists(EVIDENCE):
        shutil.rmtree(EVIDENCE)
    os.makedirs(EVIDENCE)
    trees_dir = prepare_trees()
    print("trees: %s" % TREES, flush=True)
    with open(os.path.join(EVIDENCE, "trees.json"), "w") as handle:
        json.dump({"want": TREES_WANT, "paths": TREES}, handle, indent=2)
    name = "sr-retryfinal-%s" % uuid.uuid4().hex[:8]
    subprocess.run([
        "docker", "run", "-d", "--rm", "--name", name,
        "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
        "--label", "repro=retryfinal-149", "-p", "127.0.0.1::5672",
        "rabbitmq:4-alpine"
    ],
                   check=True,
                   capture_output=True)
    BROKER_PORT = docker_broker_port(name)
    print("broker %s on 127.0.0.1:%d, dead post port %d" %
          (name, BROKER_PORT, DEAD_PORT),
          flush=True)
    run_deadline = time.time() + RUN_DEADLINE
    try:
        env = dict(os.environ)
        env.update(broker_env())
        wait_until(
            "broker healthy", 180, lambda: subprocess.run(
                [sys.executable,
                 os.path.join(HERE, "inject.py"), "setup"],
                env={
                    **env, "REPRO_EXCHANGE": EXCHANGE
                },
                capture_output=True).returncode == 0 or None)
        summary = []
        failed = 0
        for tree_name in wanted_trees:
            for case_name in wanted_cases:
                if time.time() > run_deadline:
                    raise RuntimeError("overall run deadline exceeded")
                case_obj = None
                try:
                    case_obj = CASES[case_name](tree_name,
                                               TREES[tree_name])
                    with open(
                            os.path.join(case_obj.casedir,
                                         "evidence.json")) as handle:
                        assert_tree_identity(json.load(handle),
                                             TREES[tree_name])
                    print("PASS %s-%s" % (tree_name, case_name),
                          flush=True)
                    summary.append([tree_name, case_name, "PASS"])
                except BaseException as primary:
                    print("FAIL %s-%s: %s" % (tree_name, case_name,
                                             primary),
                          flush=True)
                    summary.append(
                        [tree_name, case_name, "FAIL: %s" % primary])
                    failed += 1
                    if case_obj is not None:
                        try:
                            terminate_owned(case_obj.casedir,
                                            case_obj.cfgname)
                        except Exception as cleanup_err:
                            raise RuntimeError(
                                "cleanup failed after case failure: %s"
                                % primary) from cleanup_err
        with open(os.path.join(EVIDENCE, "summary.json"), "w") as handle:
            json.dump(summary, handle, indent=2)
        print("failed: %d" % failed, flush=True)
        return 1 if failed else 0
    finally:
        drop_trees(trees_dir)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
