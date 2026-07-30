# REANA operator security and command model

## Scope

This version is a read-only bridge to a native `reana-client`. It was reviewed against
REANA Client `0.95.0a5` source commit
`759f20017a4335d2565b6e91071382a7d4e854fd` and the public REANA documentation.
The wrapper does not install or pin that executable; the operator owns the environment and
must review the resolved version shown by `doctor`.

## Fixed mapping

| Wrapper operation | Native client shape | Remote effect |
|---|---|---|
| `ping` | `reana-client ping` | Read authentication and server status |
| `info` | `reana-client info --json` | Read cluster information and quotas |
| `list` | `reana-client list --json --page 1 --size N` | Read workflow inventory |
| `status` | `reana-client status --workflow NAME --json` | Read one workflow status |
| `logs` | `reana-client logs --workflow NAME --json --page 1 --size N` | Read bounded log records |
| `files` | `reana-client ls --workflow NAME --json --page 1 --size N` | Read workspace inventory |
| `usage` | `reana-client du --workflow NAME --summarize` | Read workspace usage |

`doctor` and the client version probe are local. The wrapper never accepts a raw
subcommand, arbitrary option, local project path, output path, or file name.

## Credential handling

- Accept only `REANA_SERVER_URL` and `REANA_ACCESS_TOKEN` as REANA credentials.
- Require the normalized server to match one exact origin in
  `REANA_ALLOWED_SERVER_ORIGINS`; do not support wildcards or path prefixes.
- Reject token values supplied as command-line arguments.
- Use an explicit child environment and an empty temporary `HOME`.
- Report token presence only.
- Replace the exact configured token before applying generic redaction patterns.
- Drain subprocess output through a bounded capture; if a stream exceeds the ceiling,
  discard its preview rather than emitting a possibly partial credential.
- Run only as a non-root user on POSIX, with the credential-bearing client constrained by
  a zero soft and hard `RLIMIT_NPROC`; this prevents fork/clone escape from the owned
  process group and intentionally disallows helper processes and extra threads.
- Never enable debug logging because client debug output can include request details.

Environment variables can still be read by sufficiently privileged same-host processes.
Prefer a runtime secret store that injects a scoped, short-lived token immediately before
use. Rotate or revoke it according to the REANA operator's policy.

## Mutation admission criteria

Do not add a write command merely by extending the allowlist. A future change must:

1. inventory the server, native client version, workflow name, local source commit,
   runtime image digest, requested resources, declared inputs, and declared outputs;
2. validate the local project using `reana-workflow-authoring`;
3. render the exact remote effect and a digest-bound confirmation token;
4. require a fresh user confirmation for each upload, creation, computation start,
   stop, deletion, download destination, sharing change, or secret change;
5. preserve and test retry/idempotency behavior;
6. add mocked failure tests and an isolated non-production forward test.

Docker fallback remains excluded until a client image is pinned by digest and its mounts,
environment, network, and output handling receive separate review.
