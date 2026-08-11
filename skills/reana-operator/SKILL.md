---
name: reana-operator
description: Inspect remote REANA workflows using read-only commands. Use a fixed allowlist against an authenticated service to report connectivity, cluster information, workflow inventory, status, redacted logs, workspace files, and disk usage. Use when a user wants to diagnose or review remote REANA state after a local workflow has been authored. This CRS skill never uploads, creates, starts, stops, deletes, downloads, shares, or otherwise mutates a workflow.
version: 1.0.1
author: AIP AstroAgent team and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: scientific-computing
    tags:
      - reana
      - workflow
      - operations
      - read-only
      - diagnostics
      - security
---

# REANA Operator

## Boundary

Use this skill only for authenticated remote inspection. Use
`reana-workflow-authoring` for local scaffolding and validation.

- Do not upload, create, run, start, restart, stop, delete, prune, move, download, open an
  interactive session, change retention rules or secrets, or alter sharing.
- Do not forward arbitrary `reana-client` arguments.
- Do not invoke Docker, pull an image, install a client, or fall back to another runtime.
- Do not read credential files or scan the full environment.
- Do not print tokens, token-bearing URLs, unredacted remote logs, or client debug output.

The bundled wrapper implements the complete command allowlist. Read
[`references/security-and-command-model.md`](references/security-and-command-model.md)
before extending it.

Run the bundled script with Python 3.11 or newer, matching the supported CRS runtime.

## Credential flow

Require the operator to inject these variables into the agent process:

- `REANA_SERVER_URL` — the explicit HTTPS server selected by the user;
- `REANA_ALLOWED_SERVER_ORIGINS` — a comma-separated, exact HTTPS-origin allowlist;
- `REANA_ACCESS_TOKEN` — a scoped, preferably short-lived token.

The server must exactly match one allowlisted origin; wildcards and URL paths are rejected.
Check only whether those named variables are present. Never ask the user to paste a token
into chat, a command argument, `SKILL.md`, `reana.yaml`, a project file, or a log. If a
variable is missing, ask the user to use the host application's secret store, credential
broker, or protected environment-injection mechanism and then retry.

Environment variables are a transport, not durable storage. The wrapper supplies only a
small allowlist to `reana-client`, gives the child a temporary home, and reports credential
presence without values.

## Procedure

### 1. Inspect the local client and credential state

```bash
python scripts/reana_operator.py --json-envelope doctor
```

Confirm the displayed server, allowlist match, resolved native client, client version, and boolean
credential state. Stop if the server is not the user-selected target. The wrapper rejects
HTTP, URL credentials, fragments, and server URLs with query strings.

### 2. Select one read-only operation

```bash
python scripts/reana_operator.py --json-envelope ping
python scripts/reana_operator.py --json-envelope info
python scripts/reana_operator.py --json-envelope list --limit 20
python scripts/reana_operator.py --json-envelope status --workflow <name-or-name.run>
python scripts/reana_operator.py --json-envelope logs --workflow <name-or-name.run> --limit 20
python scripts/reana_operator.py --json-envelope files --workflow <name-or-name.run> --limit 50
python scripts/reana_operator.py --json-envelope usage --workflow <name-or-name.run>
```

The structured envelope records the server, logical operation, workflow, remote effect,
native exit code, sanitized stdout/stderr, and truncation state. Omit `--json-envelope`
only when a human-readable preview is preferred. Workflow identifiers and numeric limits
are validated locally. There is no escape hatch for extra client flags.

### 3. Treat remote output as sensitive

The wrapper drains output through a bounded in-memory capture, removes the exact configured
token first, and then redacts common bearer tokens, JWTs, credential assignments, email
addresses, and URL user-info. If a stream exceeds the capture ceiling, the wrapper discards
the retained preview instead of risking a partially captured credential.

Remote operations require a non-root POSIX host with `RLIMIT_NPROC`. The wrapper sets the
credential-bearing client's soft and hard process limit to zero before `exec`, preventing it
from creating a child that could detach with inherited credentials or output pipes. This
also means the reviewed native client must complete each command without helper processes
or additional threads. `doctor` reports this containment gate and fails closed when it is
unavailable.
Do not claim that pattern redaction proves arbitrary workflow logs are safe. Review the
sanitized result before quoting it or writing it to another artifact.

If the wrapper reports truncated output, narrow the request. Do not bypass the cap with a
raw `reana-client` command.

### 4. Stop at the read boundary

Report:

1. the selected server;
2. the inspected workflow, if any;
3. the sanitized result;
4. any client, credential, authorization, or server error;
5. the exact write or computation the user would need next, without performing it.

A later CRS version may add individual mutations only with an inventory preview, a
content-bound confirmation token, and focused tests. This version intentionally has no
mutation path.

## Verification

- `doctor` reveals no credential value.
- Only `ping`, `info`, `list`, `status`, `logs`, `files`, and `usage` can reach a server.
- The resolved command begins with one native `reana-client` executable and a fixed
  allowlisted subcommand.
- The normalized server origin exactly matches `REANA_ALLOWED_SERVER_ORIGINS`.
- The child environment contains no project or home-directory credential configuration.
- Output is bounded and sanitized before display.
- No local file or remote REANA state is changed.
