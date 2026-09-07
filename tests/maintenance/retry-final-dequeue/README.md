# Flow reproduction for issue #148 (PR #149)

Maintainer-runnable proof that a disk-backed retry batch dequeued for
processing survives an `SR3` process death before its outcome is
recorded. Complements the automated regression in
`tests/sarracenia/flowcb/retry_completion_test.py`.

## Manual steps (download path; the harness automates exactly this)

Work in a scratch directory so nothing touches real SR3 state:

```bash
export SCRATCH=/tmp/retryfinal-manual
mkdir -p $SCRATCH/downloads
export XDG_CONFIG_HOME=$SCRATCH/home/.config
export XDG_CACHE_HOME=$SCRATCH/home/.cache
export PYTHONPATH=$SCRATCH:$PWD
export REPRO_RUNDIR=$SCRATCH REPRO_KILL=0 REPRO_MODE=download
```

Start the disposable broker and note its mapped port:

```bash
docker run -d --rm --name sr-retryfinal-manual -p 127.0.0.1::5672 \
  --memory 512m --cpus 1 --pids-limit 128 rabbitmq:4-alpine
PORT=$(docker port sr-retryfinal-manual 5672 | awk -F: '{print $NF}')
export REPRO_AMQP_URL="amqp://guest:guest@127.0.0.1:${PORT}//"
export REPRO_EXCHANGE=xs_retryfinal
python3 inject.py setup
```

Install the config with scratch paths substituted (`__QUEUE__` any fresh
name, `__SUBTOPIC__` a fresh `name.#`, `__BATCH__` 4, `__PORT__` the
mapped port, `__DOWNLOADS__` the scratch downloads directory):

```bash
mkdir -p $XDG_CONFIG_HOME/sr3/subscribe
sed -e "s/__QUEUE__/q_repro.manual/" -e "s/__SUBTOPIC__/manual.#/" \
  -e "s/__BATCH__/4/" -e "s/__PORT__/$PORT/" \
  -e "s|__DOWNLOADS__|$SCRATCH/downloads|" \
  templates/subscribe/retryfinal.conf \
  > $XDG_CONFIG_HOME/sr3/subscribe/retryfinal.conf
cp repro_cb.py $SCRATCH/
```

Publish two messages pointing at files that do not exist:

```bash
REPRO_TAG=manual REPRO_BASEURL=file://$SCRATCH/missing \
  REPRO_ROUTING=v03.post.manual.data python3 inject.py inject
```

Start the flow and wait until the retry file holds 2 records, then stop:

```bash
sr3 start subscribe/retryfinal
Q=$XDG_CACHE_HOME/sr3/subscribe/retryfinal/diskqueue_work_retry_00
for i in $(seq 1 150); do sleep 2; [ "$(wc -l < $Q)" = 2 ] && break; done
wc -l $Q
sr3 stop subscribe/retryfinal
```

Armed restart: the config-loaded callback kills the worker right after
the final batch is dequeued:

```bash
export REPRO_KILL=1
sr3 start subscribe/retryfinal
# wait for $SCRATCH/kill.json to appear; the worker is then dead
```

Confirm the dead worker ran the intended checkout, not an installed copy:

```bash
python3 -c "import json; print(json.load(open('$SCRATCH/kill.json'))['sarracenia_file'])"
```

Second restart, disarmed, and observe what comes back:

```bash
export REPRO_KILL=0
rm -f $SCRATCH/flow-events.jsonl
sr3 start subscribe/retryfinal
sleep 60
sr3 stop subscribe/retryfinal
python3 -c "import json; print(sorted({r for e in map(json.loads, open('$SCRATCH/flow-events.jsonl')) for r in e['isRetry']}))"
wc -l $XDG_CACHE_HOME/sr3/subscribe/retryfinal/diskqueue_work_retry_00
```

Observed: on the baseline revision the queue file is gone after the
kill and nothing arrives on restart (`0/2`); on the fixed revision the
file is intact and both tagged records arrive (`2/2`).

Exact cleanup when done:

```bash
sr3 stop subscribe/retryfinal
docker rm -f sr-retryfinal-manual
rm -rf $SCRATCH
```

No other container, process, queue, config or state dir is touched:
PIDs signalled are only the case's own, the container is created and
removed by name, and every path above sits under `$SCRATCH`.

## Automated matrix

`python3 run_flow.py` runs both revisions through the download and post
main cases plus the pre-dequeue, partial-batch and retire controls. It
is a convenience wrapper around the manual steps above, with the same
isolation and cleanup rules enforced in code. It builds its own
worktrees for both revisions, verifies each tree imports from itself,
and asserts every worker ran the intended tree; a mismatch aborts the
case instead of producing plausible-looking results from the wrong code.

## Setup

- Docker with the `rabbitmq:4-alpine` image (disposable container only).
- Python 3.10+ with `py-amqp` (already an SR3 dependency).
- `sr3` on `PATH`.
- Two checkouts: the baseline and the revision under test, e.g.:

```bash
git worktree add /tmp/wt149base 24de015c
git worktree add /tmp/wt149fix <revision-under-test>
```

The `TREES` map at the top of `run_flow.py` defaults to those paths;
adjust it if your checkouts live elsewhere. The broker entries in
`templates/` use RabbitMQ's public default credentials for the
disposable local container only.

## Run

```bash
python3 run_flow.py                 # both revisions, all cases
python3 run_flow.py fix C1-pre-dequeue   # one revision, one case
```

Each case runs real `sr3 start/stop` cycles in an isolated home
directory, publishes two notification messages through the disposable
broker, and kills the worker with SIGKILL from the config-loaded
`repro_cb.KillAfterDequeue` callback after the final retry batch is
dequeued but before any completion is recorded.

## Expected result

| Case | Baseline recovered | Fixed recovered |
| ---- | ------------------ | --------------- |
| Final download batch killed | 0/2 | 2/2 |
| Final post batch killed | 0/2 | 2/2 |
| C1 restart before dequeue | 2/2 | 2/2 |
| C2 partial batch killed (batch-2 config) | file intact, 2 lines | file intact, 2 lines |
| C3 completed work restarted | 0, no arrivals | 0, no arrivals |

`C3` proves completed work is not resurrected. The harness asserts
every expectation and exits nonzero on any miss, leaving per-case
evidence (config, markers, state listings, logs) in `evidence/`.
