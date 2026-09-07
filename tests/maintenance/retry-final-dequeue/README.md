# Flow reproduction for issue #148 (PR #149)

Maintainer-runnable proof that a disk-backed retry batch dequeued for
processing survives an `SR3` process death before its outcome is
recorded. Complements the automated regression in
`tests/sarracenia/flowcb/retry_completion_test.py`.

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
