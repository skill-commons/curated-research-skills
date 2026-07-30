# dt4acc operations safety and manifest

## Manifest schema

Use UTF-8 JSON with no duplicate or unknown keys:

```json
{
  "schema_version": 1,
  "mode": "simulation",
  "session": "crs-sim-bessyii-review",
  "runtime": {
    "executable": "/usr/bin/apptainer",
    "executable_sha256": "64 lowercase hexadecimal characters",
    "version": "apptainer version 1.4.2",
    "image": "/srv/reviewed/dt4acc-bessyii.sif",
    "image_sha256": "64 lowercase hexadecimal characters",
    "architecture": "x86_64"
  },
  "sources": [
    {
      "repository": "https://github.com/dt4acc/dt4acc",
      "commit": "full 40-hex commit"
    },
    {
      "repository": "https://github.com/dt4acc/dt4acc-lib",
      "commit": "full 40-hex commit"
    },
    {
      "repository": "https://github.com/dt4acc/lat2db",
      "commit": "full 40-hex commit"
    }
  ],
  "build_provenance": {
    "base_image": "registry.example.org/base/python@sha256:64-hex-digest",
    "python_lock_sha256": "64 lowercase hexadecimal characters",
    "system_packages_lock_sha256": "64 lowercase hexadecimal characters"
  },
  "resources": {
    "max_runtime_seconds": 600,
    "cpus": "2",
    "memory": "4G",
    "pids_limit": 256
  }
}
```

Pass the manifest by a canonical absolute path. It must be a regular non-symlink file and
must not be group- or world-writable.

Choose a new run directory beneath a private canonical parent. Non-sticky group- or
world-writable ancestor directories are rejected. The start path copies the runtime and
SIF into the run directory from already-open descriptors while computing their expected
digests, then executes only those private snapshots.

The repository owner/path may differ, but the final component must identify `dt4acc`,
`dt4acc-lib`, and `lat2db`. Each source coordinate is provenance, not an instruction to
clone it. The initial CRS host-smoke evidence used:

- `dt4acc` `d8c1774d3c414ec23025b95a8e4de8b451a33422`;
- `dt4acc-lib` `28576ed6548d96ba48574086f8ce6f11f1e9e921`;
- `lat2db` `7b130297576fb875ab371867606ae5f5760dbf06`.

Those pins are not evidence for an independently built SIF. Do not combine them with the
older HIFIS container generation unless the complete combination is rebuilt and tested.

## Why no build recipe is bundled

The historical candidates mixed incompatible repositories and Python versions, used
mutable base images and package ranges, attempted unquoted shell constraints, and copied
the standard-library `uuid.py` over third-party package locations. They also disagreed
about `%runscript` versus `%startscript` and interactive versus headless IOC startup.

A publishable build needs:

- base image by manifest digest;
- package repository snapshot and exact system package versions;
- a hash-locked Python resolution that excludes the obsolete third-party `uuid` package;
- full source commits from one compatible generation;
- a supported non-interactive pythonSoftIOC entry point;
- matching `%runscript`, `%startscript`, signal handling, `%test`, and provenance labels;
- Linux amd64 and/or arm64 execution evidence for each advertised architecture.

Building is a separate privileged/local-mutation workflow and is intentionally absent.

Obtain `runtime.executable_sha256` from an approved package, signature, or software
inventory channel and then confirm the installed file matches it. Merely hashing an
unknown local executable and copying that value into the manifest does not establish
trust. The fixed local probes establish runtime version and SIF structural readability
using the documented
[`apptainer sif header`](https://apptainer.org/docs/user/main/cli/apptainer_sif_header.html)
operation; they do not attest that the manifest's source commits or build locks produced
the SIF.

## Environment denylist

Fail when a variable name starts with or equals:

- `EPICS_`, `TANGO_`, `MONGODB_`;
- `DT4ACC_LIVE_`, `DT4ACC_FACILITY_`, `FACILITY_`;
- `APPTAINERENV_EPICS_`, `APPTAINERENV_TANGO_`, `APPTAINERENV_MONGODB_`;
- `SINGULARITYENV_EPICS_`, `SINGULARITYENV_TANGO_`, `SINGULARITYENV_MONGODB_`;
- `MONGODB_URL`, `TANGO_HOST`.

The wrapper constructs the child environment from scratch, but the preflight also rejects
these host variables to make an unsafe operator context visible.

## Network and process evidence

Apptainer integrates with the host by default. `--cleanenv` does not isolate networking.
The fixed `--net --network none` combination creates a network namespace with loopback
only on installations that permit the `none` CNI configuration.

Process cleanup uses Linux `/proc` UID, start-time, and exact argument-vector evidence to
prevent PID-reuse or foreign-process signaling. Stop confirmation binds the complete
state document; a changed state file is refused. A missing or mismatched `/proc` record
is a stop, not a reason to search for another process, and group emptiness is checked
separately so a surviving child cannot be reported as stopped.

## Future live adapters

Do not add live facility reads or writes to this skill. A future read-only adapter needs
an operator-owned, expiring policy containing exact facility and machine identity,
numeric endpoint allowlists, PV-prefix allowlists, and explicit live-read confirmation.

Facility writes additionally require per-PV type/range policy, baseline snapshot,
write/verify/restore in `finally`, post-restore tolerance checks, rollback escalation, and
end-to-end tests on a facility-approved simulator before any production review.
