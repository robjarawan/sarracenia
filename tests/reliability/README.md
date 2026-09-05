# Retry consolidation when the filesystem fills

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

Run separately; the base is expected to exit nonzero:

```bash
docker run --rm --network none --memory 512m --cpus 1 \
  --mount type=tmpfs,destination=/retry,tmpfs-size=1048576,tmpfs-mode=1777 \
  sr-proof-base python -m tests.reliability.housekeeping_proof
docker run --rm --network none --memory 512m --cpus 1 \
  --mount type=tmpfs,destination=/retry,tmpfs-size=1048576,tmpfs-mode=1777 \
  sr-proof-fixed python -m tests.reliability.housekeeping_proof
```

| Case | Base recovered after restart | Fix recovered after restart |
| --- | --- | --- |
| Healthy filesystem | first.bin and second.bin | first.bin and second.bin |
| Full filesystem during consolidation | Nothing | first.bin and second.bin |

Two real failed file downloads enter Retry through Flow.filter/work. The child fills the filesystem only after both records are stored, invokes normal retry housekeeping, and exits without graceful cleanup. The parent frees the fixture filler and opens a new DiskQueue, consolidates and reads the result. The fixed failure path also leaves the original retry input byte-for-byte intact.

This proves handling of a full filesystem and process restart, with an unconstrained-space control. The unit regressions separately cover partial serialization, promotion failure and control exceptions. Power-loss ordering and post-commit duplicate cleanup are outside this patch; so is the separate final-dequeue completion contract.

Automated regression tests are in tests/sarracenia/diskqueue_test.py. Copy the changed test files into the base worktree as well if comparing pytest results there. The standalone proofs above need only the harness copy. The broader offline suite excludes Azure integration when no Azurite service is available.

Verification gate: static/protocol CI is requested in the fork. dynamic_flow is excluded specifically for this fork because it consumes live datamarts; this does not count as passing it. Full static_flow, flakey_broker, transform_flow and dynamic_flow verification plus independent review remain prerequisites for upstream readiness. These PRs stay draft while that gate is incomplete.

Rollback and cleanup: the runs remove their own temporary directories and --rm containers. Stop/remove the dedicated broker for the ACK proof. Keep logs before removing images or the detached base worktree. Nothing is deployed or merged by this recipe.

Contact: Rob Jarawan. Related issue: https://github.com/robjarawan/sarracenia/issues/29.
