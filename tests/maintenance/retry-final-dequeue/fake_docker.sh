#!/bin/sh
# Test double for the `docker` CLI, used ONLY by run_flow.py
# --test-cleanup to simulate a create-then-timeout broker launch.
#
# Behavior:
#   docker run ... --name <name> ...   records <name> as created, then
#                                      hangs past any harness timeout, so
#                                      the caller observes TimeoutExpired
#                                      exactly as with a stuck daemon.
#   docker rm -f <name>                drops the creation record and
#                                      exits 0, like removing that container.
#   anything else                      exit 99, unsupported.
#
# FAKE_DOCKER_MARKERS must point at an empty directory. Only basenames
# under it are ever touched; one marker file exists per created name.
if [ "$1" = "run" ]; then
    name=""
    prev=""
    for arg in "$@"; do
        if [ "$prev" = "--name" ]; then name="$arg"; fi
        prev="$arg"
    done
    case "$name" in
        ""|*/*) echo "fake docker: bad name: $name" >&2; exit 99 ;;
    esac
    mkdir -p "$FAKE_DOCKER_MARKERS"
    touch "$FAKE_DOCKER_MARKERS/$name.created"
    sleep 300
fi
if [ "$1" = "rm" ]; then
    for arg in "$@"; do
        case "$arg" in
            -*) continue ;;
            */*) echo "fake docker: bad name: $arg" >&2; exit 99 ;;
            *) rm -f "$FAKE_DOCKER_MARKERS/$arg.created" ;;
        esac
    done
    exit 0
fi
echo "fake docker: unsupported invocation: $*" >&2
exit 99
