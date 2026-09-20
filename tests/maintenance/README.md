# Local maintenance and Python API checks

Author: Rob Jarawan - Data Interchange

This check exercises the installed SR3 package with two subscriber configurations,
one one-shot post configuration, five generated files, and a disposable local
RabbitMQ. It replaces the maintenance job's downloads of examples from upstream
branch names and its dependence on public data feeds.

The separate static/protocol flow suites still cover the wider flow fixture set,
including C components. This focused Python maintenance check does not replace
those suites or claim a complete flow/platform verification gate.

## What is checked

| Operation | Required result |
| --- | --- |
| `sr3 add` | Each generated configuration is copied without changes |
| `sr3 declare` | Both named queues exist and start empty |
| Moth publisher/subscriber API | Five distinct products arrive with the expected sizes and identities; each is acknowledged |
| Subscribe Flow API | A bounded worker downloads exactly the five complete files and drains its queue |
| `sr3 cleanup` | Both queues disappear; the selected count includes the one-shot post configuration |
| `sr3 remove` | All three generated configuration files disappear |

SR3 can return zero after logging a refused maintenance action. The check therefore
verifies broker/filesystem outcomes as well as command exit status. An empty poll
is not counted as a delivered message. It rejects execution against a source-tree
import and prints the installed module path and Python version.

The fixture finalizes the added configurations through the Python API before
running maintenance commands, including creation of their private cache directories.
This check does not claim coverage of startup with missing cache directories.

## Reproduce locally

Prerequisites: Docker and public package/image downloads. Run from this PR's
checkout. No host home directory, credentials, or operational configuration is
mounted. The test creates its own private XDG directories.

Build an image containing the installed package and the matching test script:

```bash
docker build -t sr-maintenance-check -f - . <<'DOCKERFILE'
FROM python:3.10-slim
WORKDIR /package
COPY . .
RUN pip install --no-cache-dir '.[amqp]'
RUN useradd --create-home maintenance
USER maintenance
WORKDIR /tmp
CMD ["python", "/package/tests/maintenance/run.py"]
DOCKERFILE
```

Start a dedicated broker without external networking or published ports:

```bash
docker run -d --name sr-maintenance-check-broker --network none --memory 384m --cpus 1 \
  -e 'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS=+S 2:2 +A 2' rabbitmq:4-alpine
docker exec sr-maintenance-check-broker rabbitmq-diagnostics -q check_port_connectivity
```

Wait until the readiness check succeeds. Then run the check:

```bash
docker run --rm --network container:sr-maintenance-check-broker --memory 512m --cpus 1 \
  --pids-limit 128 sr-maintenance-check
```

Success ends with a JSON `PASS` result naming the five products and reporting
three removed configurations and two removed queues. The workflow runs this same
script against the package installed on Ubuntu 22.04/Python 3.10 and
Ubuntu 24.04/Python 3.12, using the runner's local RabbitMQ service.

## Failure controls

Each command below is expected to exit nonzero. Run them separately from the
successful case so an expected failure does not stop the comparison.

```bash
docker run --rm --network container:sr-maintenance-check-broker --memory 512m --cpus 1 \
  sr-maintenance-check python /package/tests/maintenance/run.py --publish-count 4
docker run --rm --network container:sr-maintenance-check-broker --memory 512m --cpus 1 \
  sr-maintenance-check python /package/tests/maintenance/run.py --legacy-cleanup-count
```

The first case must reject four delivered products instead of counting empty polls
as the fifth. The second reproduces the former `sr3 status | grep stop` count
against the actual CLI and must detect the queue left after refused cleanup.
The failed CI's HTTP 404 path is removed entirely: neither GitHub branch names
nor downloaded examples participate in this test.

## Cleanup and rollback

The script attempts every cleanup operation for its uniquely named broker
resources and always attempts to close the connection, including after an
assertion failure. A cleanup-only failure is fatal, and a prior fixture failure
remains the reported failure if cleanup also has a problem. The `PASS` result is
printed only after cleanup succeeds. CLI and Flow subprocesses have deadlines;
the workflow also has a ten-minute timeout. If broker cleanup itself fails,
stopping and removing the disposable broker is the final fallback:

```bash
docker stop sr-maintenance-check-broker
docker rm sr-maintenance-check-broker
```

Reverting the isolated CI commit restores the old runner; it does not change SR3
runtime code or deploy anything. Keep the observed logs before removing images.
Contact: Rob Jarawan. Related fork issue: #139.
