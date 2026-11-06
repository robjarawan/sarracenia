Same-name post retries
======================

This recipe uses a disposable machine or container with an otherwise unused
local RabbitMQ broker. Do not run it against a shared pump or broker. Install
the proposed Sarracenia revision and its AMQP dependencies there first.

From the repository root, run::

    python tests/reproducers/post_retry_same_name.py --overwrite
    python tests/reproducers/post_retry_same_name.py
    python tests/reproducers/post_retry_same_name.py --overwrite --inline

The first command runs three SR3 foreground processes. The first copies A to
``latest.txt`` and saves a failed announcement in the normal disk retry queue.
A configured callback supplies the file event and injects that failure. A
second, independent config replaces the same target with B and announces B
using the built-in AMQP publisher. The original config then reopens its retry
state in a fresh process.

Before the fix, the queue contains B's correct announcement followed by A's
old announcement. A's checksum does not describe the body now at that path.
The command exits with status 1. With the fix, it receives only B's valid
announcement and exits with status 0. The second command is a control: A has
not been overwritten, so its saved announcement has to publish on restart.

The third command keeps the original bytes inside each announcement. Both
announcements remain usable, so both have to publish even though the local
pathname was overwritten. Each foreground process checks its loaded retry
module's SHA256 against the proposed source before supplying file events.

Each command prints the generated config's activity and a final JSON result.
The result names the imported package and compares the received content
identity with the complete local body. Temporary files and the exclusive
subscriber queue are removed. The exchange is confined to the disposable
broker, which has to be destroyed after the recipe.

Foreground phases
-----------------

The accompanying ``post_retry_same_name.conf`` contains the complete config.
Its relevant settings are::

    download True
    mirror False
    filename None
    inflight .tmp
    post_baseDir ${POST_RETRY_CASE}/output
    directory ${POST_RETRY_CASE}/output
    flowCallback post_retry_same_name.Probe
    flowCallback sarracenia.flowcb.post.message.Message

To run its phases directly on the disposable machine, start in the repository
root and use a separate shell. Bind a subscriber to ``x_post_retry_probe``
before running the phases if the actual wire messages are to be inspected::

    export POST_RETRY_CASE="$(mktemp -d)"
    export HOME="$POST_RETRY_CASE"
    export XDG_CONFIG_HOME="$HOME/config"
    export XDG_CACHE_HOME="$HOME/cache"
    export PYTHONPATH="$PWD/tests/reproducers"
    export POST_RETRY_EXPECTED_HASH="$(sha256sum sarracenia/flowcb/retry.py | cut -d ' ' -f 1)"
    mkdir -p "$POST_RETRY_CASE/input" "$POST_RETRY_CASE/output" "$XDG_CONFIG_HOME/sr3/flow"
    cp tests/reproducers/post_retry_same_name.conf "$XDG_CONFIG_HOME/sr3/flow/probe.conf"
    cp tests/reproducers/post_retry_same_name.conf "$XDG_CONFIG_HOME/sr3/flow/update.conf"
    POST_RETRY_PHASE=prepare sr3 foreground flow/probe
    POST_RETRY_PHASE=replace sr3 foreground flow/update
    POST_RETRY_PHASE=reopen sr3 --retryEmptyBeforeExit True foreground flow/probe

The prepare phase logs the injected failure and a nonempty post retry queue.
The replace phase publishes B. On the base the final phase publishes A's old
identity; with the fix it reports the changed body and does not publish A.
Skip the replace phase for the unchanged-file control. Destroy the disposable
test machine or container afterward, including its temporary files and broker.

Scope
-----

The check covers locally downloaded, whole-file post retries with MD5 or
SHA512 content identities. It does not create an archive of overwritten
versions or guarantee that external writers cannot change a file between
verification and publication. Forwarded messages, remote sender targets,
file operations, blocks and non-content identities keep their existing
retry behavior. Inline notices also remain usable after the file changes and
keep their existing behavior. Unreadable local files stay queued, subject to the existing
retry count and expiry policies.
