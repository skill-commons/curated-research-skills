---
name: dt4acc-operations
description: Preflight, plan, start, inspect, and stop a local dt4acc simulation packaged as an already-built, digest-pinned Apptainer SIF. Use when an operator needs a bounded simulation IOC lifecycle with exact source/build provenance, a clean child environment, no host or facility network, content-bound start/stop confirmation, and exact owned-process cleanup. This first CRS version never builds or pulls images, connects to facility services, accesses live PVs, imports MongoDB data, or performs PV writes.
version: 1.0.0
author: AIP AstroAgent team and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: scientific-computing
    tags:
      - dt4acc
      - digital-twin
      - simulation
      - apptainer
      - epics
      - operations
      - safety
---

# dt4acc Operations

## Simulation-Only Boundary

Run `dt4acc-host-smoke-test` first. Use this skill only after the core in-memory simulator
passes and an operator supplies a reviewed local SIF plus provenance manifest.

- Do not build, pull, clone, install, patch, or update an image or source repository.
- Do not use Docker, TANGO, MongoDB, private SOLEIL data, host networking, facility
  endpoints, remote registries, sudo, root, fakeroot, writable overlays, or host binds.
- Do not read or write PVs. This version starts an isolated simulation IOC but exposes no
  client or facility adapter.
- Do not forward a command, container argument, environment variable, mount, capability,
  device, or network option.
- Do not use `fuser`, `pkill`, `pgrep`, globs, `kill -9`, or Apptainer `--all`.

Read [`references/safety-and-manifest.md`](references/safety-and-manifest.md) before
creating or reviewing a manifest.

Run the bundled script with Python 3.11 or newer, matching the supported CRS runtime.

## Workflow

### 1. Prepare immutable inputs

Create a JSON manifest outside the source repositories using the exact schema in the
reference. It must identify:

- one canonical local Apptainer executable, its SHA-256, and exact version;
- one regular, non-symlink `.sif` file and its SHA-256;
- host/image architecture;
- full commits for `dt4acc`, `dt4acc-lib`, and `lat2db`;
- base-image, Python-lock, and system-package-lock digests;
- CPU, memory, PID, and runtime limits;
- a `crs-sim-...` session name.

The skill does not manufacture missing pins or treat a tag, branch, `latest`, mutable
package range, or SIF filename as evidence.

### 2. Preflight without starting the image

Keep facility variables unset, then run:

```bash
python scripts/dt4acc_operations.py preflight \
  --manifest <simulation-manifest.json>
```

Preflight opens and copies the runtime and SIF into a private temporary directory while
hashing the same file descriptors, then runs only the verified runtime snapshot's fixed
`--version` and `sif header IMAGE` probes. It verifies provenance shape and architecture,
rejects inherited EPICS/TANGO/MongoDB/facility variables, and prints the isolated command
template. It does not execute the image or write a run directory.

Treat `status: preflight_passed` narrowly: the runtime content matched the manifest and
that runtime accepted the SIF header probe, but the image was not executed and the
claimed source/build provenance was not cryptographically bound to it. The report
therefore keeps `publication_ready` false.

### 3. Render and show the start plan

Choose a new absolute run directory outside every repository:

```bash
python scripts/dt4acc_operations.py plan-start \
  --manifest <simulation-manifest.json> \
  --run-dir <new-run-directory>
```

Show the complete JSON plan to the user: `executed: false`, host support, source and
private-snapshot paths, runtime and SIF digests, sources, build locks, architecture,
resources, network `none`, evidence limits, command, and run directory. Obtain fresh
explicit confirmation for that exact plan digest.

### 4. Start only the confirmed plan

```bash
python scripts/dt4acc_operations.py start \
  --manifest <simulation-manifest.json> \
  --run-dir <new-run-directory> \
  --confirm <start-plan-sha256>
```

Start is Linux-only, refuses root, creates one previously absent mode-0700 run directory,
copies the runtime and image again from verified file descriptors into mode-0500/0400
private snapshots, and launches those snapshots:

```text
apptainer run --cleanenv --containall --no-eval --no-home --no-privs \
  --no-mount home,cwd,bind-paths,hostfs --cwd / --net --network none \
  --cpus ... --memory ... --pids-limit ... \
  --runscript-timeout ...s IMAGE.sif
```

The child receives a minimal environment and dedicated home/config/cache paths. No host
project, home, secret, facility variable, or network is passed.

### 5. Inspect and stop the exact owned process

```bash
python scripts/dt4acc_operations.py status --run-dir <run-directory>
python scripts/dt4acc_operations.py plan-stop --run-dir <run-directory>
python scripts/dt4acc_operations.py stop \
  --run-dir <run-directory> \
  --confirm <stop-plan-sha256>
```

State binds the UID, PID, Linux process start token, command digest, manifest digest, and
image digest. Stop binds confirmation to the complete state document, re-verifies the
exact command and process identity, signals only the recorded process group with SIGTERM,
and waits until both its leader and every remaining group member are gone. It never
escalates to a broad or force kill. Preserve the private artifacts and mode-0600 state and
log files for review.

## Rollback and Failure Rules

There is no live PV or external mutable state in this version. Its only mutable scope is a
new dedicated run directory and the exact child process.

- If launch fails before owned state is recorded, terminate the complete new process
  group and verify that the group is empty.
- If process identity cannot be re-established, refuse cleanup and request manual
  operator review.
- If SIGTERM does not stop the exact process before the deadline, report manual
  intervention; never guess or escalate automatically.
- Do not describe process exit as restoration of external state. No external state was
  admitted.

## Verification and Publication Gate

- The command contains `--net --network none`, clean/contained environment flags, fixed
  limits, and one local SIF path.
- The runtime executable and SIF are both copied from hashed file descriptors into
  private snapshots before any probe or start path runs.
- No facility variable, bind, secret, mutable image, privilege option, or arbitrary
  payload reaches Apptainer.
- Start and stop each require a different content-bound confirmation digest.
- Cleanup targets one verified owned Linux process group and does not report success
  while any member remains.
- Unit and fake-runtime lifecycle tests pass without facility access.

This macOS curation workspace has no reviewed Apptainer runtime or real dt4acc SIF.
Before advertising a specific image, build provenance, or architecture as verified, a
human reviewer must source the runtime digest through an approved package/signature
channel and add Linux evidence that the exact image digest starts under `network none`,
respects termination, exposes only simulation-prefixed PVs inside its namespace, and
performs no host or repository mutation. The wrapper is portable; an internally
consistent manifest is not an attestation, and an untested image is not verified.
