# Reproduce sanity recovery after losing a live instance's PID file

Author: Rob Jarawan - Data Interchange

I use an idle native flow with a one-second graceful shutdown delay to make
the replacement/shutdown race repeatable. The container has its own process
namespace and no broker connections or host filesystem mounts.

From the repository root, with Docker installed:

```bash
docker build -f tests/manual/sanity/Dockerfile -t sr3-sanity-test .
docker run --rm --network none --cpus 1 --memory 512m --pids-limit 64 -it sr3-sanity-test
```

Inside that fresh container, run:

```bash
sr3 start flow/recovery
pidfile="$XDG_CACHE_HOME/sr3/flow/recovery/flow_recovery_01.pid"
cat "$pidfile"
rm -- "$pidfile"
sr3 sanity
sleep 2
sr3 status flow/recovery
cat "$pidfile"
sr3 sanity
sleep 2
sr3 status flow/recovery
cat "$pidfile"
ps -eo pid,args
exit
```

On the unfixed source, I see `miss 0/1`: the old process exits and removes
the replacement's PID file. The second pass repeats the failure. On the fixed
source, I see `run 1/1`, one flow worker, and the same PID in both final checks.

For a healthy control, repeat in a fresh container and omit only
`rm -- "$pidfile"`. Both sanity passes should retain the original worker.

The `exit` command ends the disposable container and removes its workers and
temporary state. To compare the baseline, build the same fixture with the
package source from the PR's base commit. No installed host package needs to
be replaced. Questions and results belong in the linked fork issue #188.
