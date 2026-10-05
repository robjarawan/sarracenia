"""Config-loaded callbacks for the #148 flow reproduction (PR #149).

Loaded through the normal SR3 callback configuration::

    flowCallback repro_cb.Observe
    flowCallback repro_cb.KillAfterDequeue

``Observe`` only records the batches the Flow hands to work and post; it
never changes them. ``KillAfterDequeue`` models a crash: once a final
retry batch has been dequeued and tracked, but before any outcome is
recorded, it writes a marker and SIGKILLs the worker process. That is
the SIGKILL/OOM-killer/power-loss window under test.

Behavior is controlled by environment so the configuration file stays
identical for every run of a case:

* ``REPRO_RUNDIR``: per-case directory for markers and event logs.
* ``REPRO_KILL``: ``1`` enables the kill, ``0`` only observes.
* ``REPRO_MODE``: ``download`` kills in ``after_accept`` once 2 retry
  messages are incoming; ``partial`` kills once 1 is incoming;
  ``post`` kills in ``after_work`` once 2 retry messages are ready.
"""

import json
import logging
import os
import signal

import sarracenia.flowcb

logger = logging.getLogger(__name__)

STATE = os.environ.get("REPRO_RUNDIR", "/tmp/repro-unknown")
KILL = os.environ.get("REPRO_KILL", "0") == "1"
MODE = os.environ.get("REPRO_MODE", "download")


def _runtime():
    import sarracenia
    import sys
    return {
        "sarracenia_file": sarracenia.__file__,
        "sarracenia_version": sarracenia.__version__,
        "python": sys.version.split()[0],
        "pid": os.getpid(),
    }


def _record(event, messages):
    record = {"event": event}
    record.update(_runtime())
    record["relPaths"] = sorted(m.get("relPath", "?") for m in messages)
    record["isRetry"] = sorted(m.get("relPath", "?") for m in messages
                               if m.get("_isRetry"))
    with open(os.path.join(STATE, "flow-events.jsonl"), "a") as handle:
        handle.write(json.dumps(record) + "\n")


def _kill_marker(mode, messages):
    record = {"event": "kill-" + mode}
    record.update(_runtime())
    record["dequeued"] = len(messages)
    record["dequeued_ids"] = sorted(m.get("relPath", "?")
                                    for m in messages)
    path = os.path.join(STATE, "kill.json")
    with open(path, "w") as handle:
        json.dump(record, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())


class Observe(sarracenia.flowcb.FlowCB):

    def __init__(self, options):
        super().__init__(options, logger)

    def after_accept(self, worklist):
        if worklist.incoming:
            _record("after_accept", worklist.incoming)

    def after_work(self, worklist):
        if worklist.ok:
            _record("after_work", worklist.ok)


class KillAfterDequeue(sarracenia.flowcb.FlowCB):

    def __init__(self, options):
        super().__init__(options, logger)

    def after_accept(self, worklist):
        if not KILL or MODE not in ("download", "partial"):
            return
        retries = [m for m in worklist.incoming if m.get("_isRetry")]
        threshold = 2 if MODE == "download" else 1
        if len(retries) >= threshold:
            _kill_marker(MODE, worklist.incoming)
            os.kill(os.getpid(), signal.SIGKILL)

    def after_work(self, worklist):
        if not KILL or MODE != "post":
            return
        retries = [m for m in worklist.ok if m.get("_isRetry")]
        if len(retries) >= 2:
            _kill_marker(MODE, worklist.ok)
            os.kill(os.getpid(), signal.SIGKILL)
