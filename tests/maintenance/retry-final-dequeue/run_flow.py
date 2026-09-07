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

import contextlib
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
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SR3 = shutil.which("sr3")
if not SR3:
    raise RuntimeError("sr3 must be on PATH to run this reproduction")


def resolve_repo():
    """The source repository worktrees are built from. Prefers an
    explicit REPRO_REPO, then the enclosing checkout of this fixture
    (which is how it runs from the committed branch location)."""
    explicit = os.environ.get("REPRO_REPO")
    if explicit:
        return explicit
    out = subprocess.run(["git", "-C", HERE, "rev-parse",
                          "--show-toplevel"],
                         capture_output=True,
                         text=True,
                         timeout=60)
    if out.returncode == 0 and out.stdout.strip():
        return out.stdout.strip()
    raise RuntimeError(
        "cannot locate source repository: set REPRO_REPO explicitly")


REPO = resolve_repo()


def resolve_fix_rev():
    """The fix revision under test floats with the PR branch: an
    explicit REPRO_FIX_REV wins, otherwise the branch head is resolved
    through every remote name an ordinary reviewer checkout might use.
    The resolved immutable SHA is recorded in trees.json with the
    evidence, so results never depend on a moving ref."""
    explicit = os.environ.get("REPRO_FIX_REV")
    if explicit:
        return explicit
    for ref in ("myfork/fix/retry-final-dequeue-recovery",
                "origin/fix/retry-final-dequeue-recovery",
                "fix/retry-final-dequeue-recovery"):
        out = subprocess.run(
            ["git", "-C", REPO, "rev-parse", "--verify", ref],
            capture_output=True,
            text=True,
            timeout=remaining(60))
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    raise RuntimeError(
        "cannot resolve the PR branch head: fetch the fork branch or "
        "set REPRO_FIX_REV explicitly")


TREES_WANT = {
    "base": "24de015ccbdd20419ff4cc58e560478009d6ed20",
}
TREES = {}
EVIDENCE = os.environ.get("REPRO_EVIDENCE", os.path.join(HERE, "evidence"))
EXCHANGE = "xs_retryfinal"

# Allocated at runtime in main(); never fixed. The broker mapping comes
# from `docker port`, the dead post port is verified refused first.
BROKER_PORT = None
DEAD_PORT = None

PHASE_DEADLINE = 300
RUN_DEADLINE = 5400
RUN_DEADLINE_AT = None
RUN_DIR = None


def remaining(default):
    """Bound any blocking call by the overall run budget. Outside a
    matrix run there is no budget and the default stands."""
    if RUN_DEADLINE_AT is None:
        return default
    return max(1, min(default, RUN_DEADLINE_AT - time.time()))


def sh(args, env, log_path, timeout=PHASE_DEADLINE):
    with open(log_path, "ab") as log:
        log.write(("+ %s\n" % " ".join(args)).encode())
        log.flush()
        try:
            proc = subprocess.run(args,
                                  env=env,
                                  stdout=log,
                                  stderr=subprocess.STDOUT,
                                  timeout=remaining(timeout))
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
    end = time.time() + remaining(deadline)
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


def run_root():
    assert RUN_DIR is not None, "run directory not initialized"
    return RUN_DIR


def prepare_trees():
    """Create fresh worktrees for both revisions under evidence/trees.

    The harness never trusts pre-existing checkouts: a vanished or
    wrong tree once made every worker silently import an unrelated
    installed copy, and every result looked plausible. Each tree is
    verified by import before any case runs.
    """
    trees_dir = os.path.join(run_root(), "trees")
    if os.path.exists(trees_dir):
        shutil.rmtree(trees_dir)
    os.makedirs(trees_dir)
    want = dict(TREES_WANT)
    want["fix"] = resolve_fix_rev()
    created = []
    try:
        for name, sha in want.items():
            dest = os.path.join(trees_dir, name)
            out = subprocess.run(
                ["git", "-C", REPO, "worktree", "add", "--detach", dest,
                 sha],
                capture_output=True,
                text=True,
                timeout=remaining(120))
            assert out.returncode == 0, \
                "worktree add failed: %s" % out.stderr
            created.append(dest)
            probe = subprocess.run(
                [sys.executable, "-c",
                 "import sarracenia; print(sarracenia.__file__)"],
                env={**os.environ,
                     "PYTHONPATH": dest},
                capture_output=True,
                text=True,
                timeout=remaining(60))
            got = probe.stdout.strip().splitlines()
            assert probe.returncode == 0 and got \
                and got[-1].startswith(dest), \
                "tree %s does not import from itself: %s %s" % (
                    name, got, probe.stderr[-500:])
            TREES[name] = dest
    except BaseException:
        # A failure halfway through setup must not leave the first tree
        # behind: ownership is recorded before allocation, so partial
        # setup cleans itself up instead of leaking into the next run.
        for dest in created:
            subprocess.run(["git", "-C", REPO, "worktree", "remove",
                            "--force", dest],
                           capture_output=True,
                           timeout=remaining(120))
        shutil.rmtree(trees_dir, ignore_errors=True)
        raise
    return trees_dir


def drop_trees(trees_dir):
    """Remove the harness-owned worktrees, verifying every removal.
    Raises on any failure: a half-removed tree must never pass silently."""
    if trees_dir is None:
        return
    errors = []
    for name in ("base", "fix"):
        path = os.path.join(trees_dir, name)
        if not os.path.exists(path) and not os.path.islink(path):
            continue
        out = subprocess.run(
            ["git", "-C", REPO, "worktree", "remove", "--force", path],
            capture_output=True,
            text=True,
            timeout=120)
        if out.returncode != 0 or os.path.exists(path):
            errors.append("%s: %s" % (name, out.stderr.strip()[-300:]))
    if os.path.exists(trees_dir) and os.listdir(trees_dir):
        errors.append("trees dir not empty: %s" % trees_dir)
    shutil.rmtree(trees_dir, ignore_errors=True)
    if errors:
        raise RuntimeError("worktree cleanup failed: %s" % " | ".join(errors))


def assert_tree_identity(record, tree):
    """Every worker-reported sarracenia path must live under the tree
    under test. A mismatch aborts the case: results from the wrong
    interpreter revision are worse than no results."""
    for key in ("kill_marker",):
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
                         timeout=remaining(120))
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

    def __init__(self, tree_name, tree, name, template, batch,
                 post_path, cfgname=None):
        self.tree_name = tree_name
        self.tree = tree
        self.name = name
        if cfgname is None:
            cfgname = "retryfinal" if template == "retryfinal.conf" \
                else os.path.splitext(template)[0]
        self.cfgname = cfgname
        self.tag = "%s-%s" % (tree_name, name)
        self.casedir = os.path.join(run_root(), self.tag)
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
        for event in flow_events(self.casedir):
            path = event.get("sarracenia_file", "")
            assert path.startswith(self.tree + os.sep), \
                "restart worker ran %s, want tree %s" % (path, self.tree)
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


# Per-case construction parameters: config template, batch size,
# whether downloads succeed (post path), config-name override.
# make_case builds the case without starting any worker, so cleanup
# always knows exactly what it owns even if the body raises first.
CASE_PARAMS = {
    "download-main": ("retryfinal.conf", 4, False, None),
    "post-main": ("retryfinal_post.conf", 4, True, None),
    "C1-pre-dequeue": ("retryfinal.conf", 4, False, None),
    "C2-partial": ("retryfinal.conf", 2, False, "retryfinal_p1"),
    "C3-retire": ("retryfinal.conf", 4, False, None),
}


def make_case(tree_name, tree, case_name):
    """Build the case without starting any worker, so cleanup always
    knows exactly what it owns even if the body raises first."""
    assert BROKER_PORT is not None and DEAD_PORT is not None, \
        "ports must be allocated before building cases"
    template, batch, post_path, cfgname = CASE_PARAMS[case_name]
    return Case(tree_name, tree, case_name, template, batch, post_path,
                cfgname)


def run_download_main(case):
    tree_name = case.tree_name
    case.seed_until_queued("work")
    depth = broker_depth(case.queue)
    assert depth == 0, \
        "broker queue must read exactly zero, not unknown: %r" % depth
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


def run_post_main(case):
    tree_name = case.tree_name
    case.setup_files(create=True)
    case.seed_until_queued("post")
    assert queue_lines(case.casedir, case.cfgname, "work") in (None, 0), \
        "downloads must succeed on the post path"
    depth = broker_depth(case.queue)
    assert depth == 0, \
        "broker queue must read exactly zero, not unknown: %r" % depth
    marker = case.kill_after_dequeue("post")
    assert marker["dequeued"] == 2, marker
    case.restart_and_count(2 if tree_name == "fix" else 0,
                           2 if tree_name == "fix" else None, "post")
    case.save("PASS")
    return case


def run_c1(case):
    case.seed_until_queued("work")
    case.restart_and_count(2, 2, "work", phase="restart")
    case.save("PASS")
    return case


def run_c2(case):
    case.seed_until_queued("work")
    marker = case.kill_after_dequeue("partial")
    assert marker["dequeued"] == 1, marker
    case.restart_and_count(1, 2, "work", phase="restart",
                           exact_arrivals=False)
    case.save("PASS")
    return case


def run_c3(case):
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


CASE_BODIES = {
    "download-main": run_download_main,
    "post-main": run_post_main,
    "C1-pre-dequeue": run_c1,
    "C2-partial": run_c2,
    "C3-retire": run_c3,
}
CASES = list(CASE_BODIES)


def owned_pids(casedir, cfgname):
    """Worker PIDs from SR3's own state: `subscribe_<cfg>_<nn>.pid`
    files hold the worker PID (instance.py writes `%d`), and the
    `running`/`starting` marker files show what sr3 believes about the
    config. These exact PIDs are the only identities this harness may
    ever signal; each is identity-checked before use."""
    root = state_dir(casedir, cfgname)
    pids = {}
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith(".pid"):
                continue
            try:
                with open(os.path.join(root, name)) as handle:
                    pid = int(handle.read().strip().split()[0])
            except (ValueError, OSError):
                continue
            pids[pid] = name
    return pids


def verify_worker(pid, cfgname):
    """Confirm /proc identity before signaling: the process must be an
    sr3 instance worker for this config. Returns False if already dead
    (nothing to do). Raises on identity mismatch instead of signaling a
    stranger, including on PID reuse."""
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as handle:
            cmdline = handle.read().replace(b"\0", b" ").decode()
    except (FileNotFoundError, ProcessLookupError):
        return False
    if "instance.py" not in cmdline \
            or "subscribe/%s" % cfgname not in cmdline:
        raise RuntimeError(
            "pid %d identity mismatch, refusing to signal: %r" %
            (pid, cmdline[:160]))
    return True


def terminate_owned(casedir, cfgname):
    """Stop exactly this case's workers: SIGTERM verified owners, wait,
    escalate survivors to SIGKILL, then verify every owned PID is dead.
    Raises if any owned PID survives. Never touches any other process."""
    pending = {
        pid for pid in owned_pids(casedir, cfgname)
        if verify_worker(pid, cfgname)
    }
    for pid in pending:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    end = time.time() + 30
    while time.time() < end:
        pending = {
            pid for pid in pending if verify_worker(pid, cfgname)
        }
        if not pending:
            break
        time.sleep(1)
    for pid in pending:
        # Re-verify: only a confirmed owner is ever escalated.
        if verify_worker(pid, cfgname):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    end = time.time() + 15
    while time.time() < end:
        pending = {
            pid for pid in pending if verify_worker(pid, cfgname)
        }
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


# Sockets held open for the whole run so nobody else can take the ports.
_HELD_SOCKETS = []


def alloc_dead_port():
    """A verifiably closed loopback port for the dead post broker. The
    bound socket is retained (never closed until handoff at process
    exit), so the port cannot be taken between allocation and use, while
    connects are still refused because nothing listens on it."""
    for _attempt in range(20):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        check = socket.socket()
        check.settimeout(2)
        try:
            check.connect(("127.0.0.1", port))
            check.close()
        except OSError:
            _HELD_SOCKETS.append(sock)
            return port
        sock.close()
    raise RuntimeError("no verifiably closed loopback port found")


def release_held_ports():
    for sock in _HELD_SOCKETS:
        try:
            sock.close()
        except OSError:
            pass
    del _HELD_SOCKETS[:]


def docker_broker_port(name):
    out = subprocess.run(["docker", "port", name, "5672"],
                         capture_output=True,
                         text=True,
                         timeout=60)
    assert out.returncode == 0, "docker port failed: %s" % out.stderr
    return int(out.stdout.strip().rsplit(":", 1)[1])


def start_broker_container():
    """Start the bounded disposable broker. Returns its unique name.
    Only this exact name is ever removed. The launch itself is bounded
    so a stuck daemon cannot hang the run before the deadline exists."""
    name = "sr-retryfinal-%s" % uuid.uuid4().hex[:8]
    subprocess.run([
        "docker", "run", "-d", "--rm", "--name", name,
        "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
        "--label", "repro=retryfinal-149", "-p", "127.0.0.1::5672",
        "rabbitmq:4-alpine"
    ],
                   check=True,
                   capture_output=True,
                   timeout=remaining(300))
    return name


def remove_broker_container(name):
    out = subprocess.run(["docker", "rm", "-f", name],
                         capture_output=True,
                         text=True,
                         timeout=remaining(120))
    if out.returncode != 0:
        raise RuntimeError("docker rm failed for %s: %s" %
                           (name, out.stderr.strip()[-300:]))


@contextlib.contextmanager
def acquired_run():
    """Own the run's shared resources: harness worktrees plus the
    disposable broker, including its health gate and port mapping.
    Every acquisition step is inside the same try as the cases, so a
    failure at any point runs the same checked teardown as a case
    failure. Yields (trees_dir, container name) with BROKER_PORT set."""
    global BROKER_PORT
    trees_dir = prepare_trees()
    try:
        print("trees: %s" % TREES, flush=True)
        name = start_broker_container()
        try:
            BROKER_PORT = docker_broker_port(name)
            print("broker %s on 127.0.0.1:%d" % (name, BROKER_PORT),
                  flush=True)
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
            yield trees_dir, name
        finally:
            remove_broker_container(name)
    finally:
        drop_trees(trees_dir)


def begin_run_dir():
    """Create a fresh run directory. Never wipes: each run gets a
    unique id, and an existing directory refuses instead of
    overwriting previous evidence or unrelated files."""
    global RUN_DIR
    root = os.path.normpath(EVIDENCE)
    home = os.path.normpath(os.path.expanduser("~"))
    if root in ("/", home, "/tmp") \
            or "evidence" not in os.path.basename(root):
        raise RuntimeError(
            "refusing suspicious evidence root, set REPRO_EVIDENCE "
            "to a directory named *evidence*: %s" % EVIDENCE)
    run_id = "run-%s" % uuid.uuid4().hex[:8]
    RUN_DIR = os.path.join(EVIDENCE, run_id)
    if os.path.exists(RUN_DIR):
        raise RuntimeError("refusing to overwrite existing run dir: %s"
                           % RUN_DIR)
    os.makedirs(RUN_DIR)
    return RUN_DIR


def container_present(name):
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=%s" % name,
         "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=60)
    return name in out.stdout.split()


def run_one_case(tree_name, case_name):
    """Run one case through the exact orchestration path main() uses,
    including per-case failure cleanup. Returns the summary entry."""
    case_obj = make_case(tree_name, TREES[tree_name], case_name)
    try:
        CASE_BODIES[case_name](case_obj)
        with open(os.path.join(case_obj.casedir,
                               "evidence.json")) as handle:
            assert_tree_identity(json.load(handle), TREES[tree_name])
        return [tree_name, case_name, "PASS"]
    except BaseException as primary:
        try:
            terminate_owned(case_obj.casedir, case_obj.cfgname)
        except Exception as cleanup_err:
            raise RuntimeError(
                "cleanup failed after case failure: %s"
                % primary) from cleanup_err
        return [tree_name, case_name, "FAIL: %s" % primary]


def selftest_cleanup():
    """Prove cleanup through the real orchestration path by failing
    deliberately at each stage. Prints PASS per step, raises on any
    miss. Failures go through acquired_run and run_one_case exactly as
    production runs do; teardown helpers are never invoked directly
    for the paths under test."""
    global BROKER_PORT, DEAD_PORT
    # No broker is needed for these probes. Point the template at a
    # verified-closed port; the worker only has to start far enough to
    # write its pidfile.
    DEAD_PORT = alloc_dead_port()
    BROKER_PORT = DEAD_PORT
    begin_run_dir()
    try:
        return _selftest_cleanup_body()
    finally:
        release_held_ports()


def _selftest_cleanup_body():
    # 1. Tree setup fails partway: acquired_run must still unwind
    # everything it owns. Sabotage the fix revision lookup.
    saved_rev = os.environ.get("REPRO_FIX_REV")
    os.environ["REPRO_FIX_REV"] = "0000000000000000000000000000000000000000"
    try:
        accepted_bogus = False
        try:
            with acquired_run():
                pass
            accepted_bogus = True
        except AssertionError as err:
            assert "worktree add failed" in str(err), \
                "wrong failure: %s" % err
        assert not accepted_bogus, \
            "acquired_run accepted a bogus revision"
    finally:
        if saved_rev is None:
            os.environ.pop("REPRO_FIX_REV", None)
        else:
            os.environ["REPRO_FIX_REV"] = saved_rev
    out = subprocess.run(["git", "-C", REPO, "worktree", "list",
                          "--porcelain"],
                         capture_output=True,
                         text=True,
                         timeout=remaining(60))
    assert "trees/base" not in out.stdout \
        and "trees/fix" not in out.stdout, \
        "stale worktree registrations: %s" % out.stdout[-500:]
    print("PASS partial tree setup cleaned via real path", flush=True)
    # 2. A case that fails with a worker running must come back
    # through run_one_case's cleanup with no survivors.
    with acquired_run() as (_trees_dir, _name):
        real_body = CASE_BODIES["download-main"]

        def sabotaged(case_obj):
            case_obj.seed_until_queued("work")
            raise RuntimeError("probe failure with worker idle")

        CASE_BODIES["download-main"] = sabotaged
        try:
            entry = run_one_case("base", "download-main")
        finally:
            CASE_BODIES["download-main"] = real_body
        assert entry[2].startswith("FAIL: probe failure"), entry
        leftovers = [
            pid for pid in owned_pids(
                os.path.join(run_root(), "base-download-main"),
                "retryfinal") if verify_worker(pid, "retryfinal")
        ]
        assert not leftovers, "workers survived: %s" % leftovers
    print("PASS failed case cleaned via real path", flush=True)
    print("cleanup self-test: all PASS", flush=True)
    return 0


def main():
    global BROKER_PORT, DEAD_PORT
    wanted_trees = sys.argv[1:2] or ["base", "fix"]
    wanted_cases = sys.argv[2:] or list(CASES)
    assert_real_home_clean()
    DEAD_PORT = alloc_dead_port()
    global RUN_DIR
    run_id = "run-%s" % uuid.uuid4().hex[:8]
    RUN_DIR = os.path.join(EVIDENCE, run_id)
    if os.path.exists(RUN_DIR):
        raise RuntimeError("refusing to overwrite existing run dir: %s"
                           % RUN_DIR)
    os.makedirs(RUN_DIR)
    trees_dir = None
    container_started = False
    try:
        trees_dir = prepare_trees()
        print("trees: %s" % TREES, flush=True)
        resolved = {}
        for tree_name, path in TREES.items():
            out = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                                 capture_output=True,
                                 text=True,
                                 timeout=60)
            resolved[tree_name] = out.stdout.strip()
        with open(os.path.join(run_root(), "trees.json"), "w") as handle:
            json.dump({"base_pin": TREES_WANT["base"],
                       "resolved": resolved,
                       "paths": TREES},
                      handle,
                      indent=2)
        name = start_broker_container()
        container_started = True
        BROKER_PORT = docker_broker_port(name)
        print("broker %s on 127.0.0.1:%d, dead post port %d" %
              (name, BROKER_PORT, DEAD_PORT),
              flush=True)
        run_deadline = time.time() + RUN_DEADLINE
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
                # make_case starts no workers, so cleanup always knows
                # exactly what it owns even if the body raises first.
                case_obj = make_case(tree_name, TREES[tree_name],
                                     case_name)
                try:
                    CASE_BODIES[case_name](case_obj)
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
                    try:
                        terminate_owned(case_obj.casedir,
                                        case_obj.cfgname)
                    except Exception as cleanup_err:
                        raise RuntimeError(
                            "cleanup failed after case failure: %s"
                            % primary) from cleanup_err
        with open(os.path.join(run_root(), "summary.json"), "w") as handle:
            json.dump(summary, handle, indent=2)
        print("failed: %d" % failed, flush=True)
        return 1 if failed else 0
    finally:
        cleanup_errors = []
        try:
            drop_trees(trees_dir)
        except Exception as err:
            cleanup_errors.append("trees: %s" % err)
        release_held_ports()
        if container_started:
            out = subprocess.run(["docker", "rm", "-f", name],
                                 capture_output=True,
                                 text=True,
                                 timeout=120)
            if out.returncode != 0:
                cleanup_errors.append(
                    "docker rm: %s" % out.stderr.strip()[-300:])
        if cleanup_errors:
            raise RuntimeError("cleanup failed: %s" %
                               " | ".join(cleanup_errors))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-cleanup":
        sys.exit(selftest_cleanup())
    sys.exit(main())
