Same-name post retries
======================

This recipe uses a disposable machine or container with an otherwise unused
local RabbitMQ broker. Do not run it against a shared pump or broker. Install
the proposed Sarracenia revision and its AMQP dependencies there first.

From the repository root, run::

    python tests/reproducers/post_retry_same_name.py --overwrite
    python tests/reproducers/post_retry_same_name.py

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

Each command prints the generated config's activity and a final JSON result.
The result names the imported package and compares the received content
identity with the complete local body. Temporary files and the exclusive
subscriber queue are removed. The exchange is confined to the disposable
broker, which has to be destroyed after the recipe.

Scope
-----

The check covers locally downloaded, whole-file post retries with MD5 or
SHA512 content identities. It does not create an archive of overwritten
versions or guarantee that external writers cannot change a file between
verification and publication. Forwarded messages, remote sender targets,
file operations, blocks and non-content identities keep their existing
retry behavior. Unreadable local files stay queued, subject to the existing
retry count and expiry policies.
