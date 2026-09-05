# Failed-message acknowledgement and retry persistence

Author: Rob Jarawan - Data Interchange

This proof runs only against synthetic data in disposable containers. It prints the imported source path and observed outcomes. The storage cases use a 1 MiB tmpfs and an actual ENOSPC from the OS; no queue or transfer method is mocked. Flow phases are driven through the Python API with a parsed configuration, rather than a long-running sr3 daemon.

Prerequisites: Docker, Git and public package/image downloads. The proof containers do not mount a home directory or production configuration. Run from a checkout of this PR branch.

Build both revisions:

```bash
git rev-parse HEAD
docker build -f tests/reliability/Dockerfile -t sr-proof-fixed .
git worktree add --detach ../sr-proof-base 24de015ccbdd20419ff4cc58e560478009d6ed20
cp -a tests/reliability ../sr-proof-base/tests/
docker build -f ../sr-proof-base/tests/reliability/Dockerfile -t sr-proof-base ../sr-proof-base
```

Only the proof harness is copied into the base checkout; its product source stays at the stated base. Use a fresh worktree path if that name already exists. Both images install SR3; the proof imports the matching checkout at /work, which is printed in the result.

Start a dedicated broker with no published ports or external network:

```bash
docker run -d --name sr-proof-broker --network none --memory 384m --cpus 1 \
  -e 'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS=+S 2:2 +A 2' rabbitmq:4-alpine
docker exec sr-proof-broker rabbitmq-diagnostics -q check_port_connectivity
```

Wait for the readiness command to succeed before running the tests. Initial broker startup can take about a minute. If it fails, inspect this container's logs; do not point the proof at another broker.

Run these commands separately so the base's expected nonzero result does not stop the fixed run:

```bash
docker run --rm --network container:sr-proof-broker --memory 512m --cpus 1 \
  --mount type=tmpfs,destination=/retry,tmpfs-size=1048576,tmpfs-mode=1777 \
  sr-proof-base python -m tests.reliability.retry_ack_proof
docker run --rm --network container:sr-proof-broker --memory 512m --cpus 1 \
  --mount type=tmpfs,destination=/retry,tmpfs-size=1048576,tmpfs-mode=1777 \
  sr-proof-fixed python -m tests.reliability.retry_ack_proof
docker stop sr-proof-broker
docker rm sr-proof-broker
```

| Case | Base | Fix |
| --- | --- | --- |
| Healthy retry storage, failed file download | Source acknowledged; one retry recovered | Same |
| Full retry filesystem, then abrupt worker exit | No broker redelivery, no retry: message lost | Broker redelivers; source ownership preserved |
| Full filesystem, then free space in the same worker | No retry remains after restart | Pending delivery persists, gets acknowledged, and one retry recovers |

The parent publishes one ordinary file notification. The child uses actual Gather, Flow.filter/work/post, Retry and DiskQueue. The file source is intentionally absent, producing a real failed download. The filler consumes only the bounded tmpfs. os._exit models process interruption after the work/post cycle; the parent checks actual RabbitMQ redelivery and retry recovery. The local broker uses its built-in disposable guest account.

This proves the failed-download ownership boundary. It does not fix successful-work-to-post crash windows, destructive final retry dequeue, power-loss durability, or every Redis failure mode. Pending persistence deliberately pauses intake and can delay graceful stop while the store stays unavailable. Partial writes can replay; this is not an exactly-once claim.

Automated regression tests are in tests/sarracenia/flow/work_retry_ack_test.py. Copy the changed test files into the base worktree as well if comparing pytest results there. The standalone proofs above need only the harness copy. The broader offline suite excludes Azure integration when no Azurite service is available.

Verification gate: static/protocol CI is requested in the fork. dynamic_flow is excluded specifically for this fork because it consumes live datamarts; this does not count as passing it. Full static_flow, flakey_broker, transform_flow and dynamic_flow verification plus independent review remain prerequisites for upstream readiness. These PRs stay draft while that gate is incomplete.

Rollback and cleanup: the runs remove their own temporary directories and --rm containers. Stop/remove the dedicated broker for the ACK proof. Keep logs before removing images or the detached base worktree. Nothing is deployed or merged by this recipe.

Contact: Rob Jarawan. Related issue: https://github.com/robjarawan/sarracenia/issues/25.
