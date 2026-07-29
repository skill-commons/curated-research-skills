---
name: reana-workflow-authoring
description: Scaffold, edit, and conservatively validate provider-neutral local REANA Serial workflow projects, including reana.yaml structure, declared inputs and outputs, runtime-image reproducibility, path containment, symlinks, and accidental secrets. Use when a user asks to create or review a local REANA workflow definition before operational handoff. This skill never authenticates, contacts a REANA server or registry, uploads, submits, starts, monitors, downloads, or mutates a remote workflow.
version: 1.0.0
author: AIP AstroAgent team and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: scientific-computing
    tags:
      - reana
      - workflow
      - reproducibility
      - yaml
      - validation
      - containers
---

# REANA Workflow Authoring

## Local-Only Boundary

Keep this workflow entirely inside the user-approved project directory.

- Do not read REANA authentication configuration or request a token.
- Do not set server variables or invoke `reana-client` control-plane commands.
- Do not upload, create, submit, start, run, stop, delete, inspect, or download a remote
  workflow.
- Do not invoke Docker, Apptainer, Kubernetes, a registry, or a remote endpoint.
- Do not install packages or pull images while authoring.
- Do not publish to Git or create a DRP product.
- Do not execute user analysis code unless the user separately requests that action.

The bundled helper performs local file writes only for its explicit `scaffold` command.
Its `validate` command is read-only and imports no network or process-launching library.

## Workflow

### 1. Define the local contract

Identify:

- an explicit project directory;
- the workflow inputs, commands, and expected outputs;
- a user-selected runtime image;
- optional backend-specific resource settings supplied by the target operator.

Do not invent a provider URL, image, backend, memory limit, timeout, or organization
policy. Keep substantial analysis code in separate declared files instead of embedding
heredocs or long shell fragments in YAML.

### 2. Scaffold safely

For a new single-step Python workflow, use the bundled
[`scripts/reana_workflow_authoring.py`](scripts/reana_workflow_authoring.py):

```bash
python scripts/reana_workflow_authoring.py scaffold \
  --project <new-project-directory> \
  --image '<registry>/<repository>:<version>@sha256:<64-hex-digest>' \
  --script analysis.py \
  --output results/summary.json
```

The command requires a missing or empty project directory, refuses overwrite, writes
`reana.yaml` with `yaml.safe_dump`, and creates a small offline `analysis.py` placeholder.
It never runs that script.

Use `--allow-mutable-image` only when the target REANA deployment cannot accept a digest.
In that exceptional case, use a specific version or build tag, record the resolved digest
in the project handoff, and keep the waiver visible. Bare images and `latest` are always
rejected.

### 3. Author the canonical Serial shape

Use the current
[REANA specification field structure](https://docs.reana.io/reference/reana-yaml/):

```yaml
inputs:
  files:
    - analysis.py
workflow:
  type: serial
  specification:
    steps:
      - name: analysis
        environment: registry.example.org/research/analysis:1.0.0@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
        commands:
          - cd "${REANA_WORKSPACE:?}" && python analysis.py
outputs:
  files:
    - results/summary.json
```

Keep `environment` a string and `commands` a non-empty list of strings. Put `steps` under
`workflow.specification`, and declare final artifacts only under top-level `outputs`.
Do not carry forward legacy step-level `resources`, `env`, or `outputs` blocks.

Add `compute_backend`, `kubernetes_memory_limit`, or `kubernetes_job_timeout` only when
the target operator documents and approves those step-level settings. Never assume
Kubernetes or a fixed memory allocation.

Use quoted YAML strings for values that could be parsed as numbers, booleans, or dates.
Generate YAML through a safe serializer rather than interpolating user values into raw
YAML text.

### 4. Inventory the project

Before handoff:

- list every file and directory that is intended as an input;
- use normalized relative paths contained by the project root;
- reject absolute paths, `..`, `~`, NUL characters, special files, and symlinks;
- confirm declared files and directories exist with the correct type;
- keep outputs inside the project even when they do not exist yet;
- inspect undeclared project files so credentials and private data are not accidentally
  carried into a later upload.

Do not put credentials in YAML, scripts, notebooks, configuration files, or environment
files. The validator reports only detector label, path, and line number; it never prints
the matching value.

### 5. Keep execution reproducible

- Prefer a runtime image pinned by manifest digest.
- If only a tag is accepted, use a specific immutable build tag and record its resolved
  digest separately.
- Bake dependencies into the image. Reject runtime `pip`, `conda`, `apt`, `apk`, `dnf`,
  `yum`, or similar installation commands.
- Declare every executable script and required local file under `inputs`.
- Use `cd "${REANA_WORKSPACE:?}"` before relative commands.
- Keep output names synchronized between commands and top-level `outputs`.

### 6. Run offline preflight

Run the conservative CRS validator:

```bash
python scripts/reana_workflow_authoring.py validate \
  --project <project-directory> \
  --strict
```

The validator checks duplicate YAML keys, the portable Serial schema, unknown legacy
keys, contained paths, missing inputs, special files, symlinks, image mutability, runtime
installers, inline heredocs, recursive REANA operations, binary-input review, and
high-confidence secret indicators. It does not execute workflow commands.

Use `--allow-binary-input` only after manually reviewing the named binary or otherwise
unscannable inputs. Use `--json` for a machine-readable, secret-safe report.

If `reana-client` is already installed, the user may separately approve this additional
local schema check:

```bash
reana-client validate -f <project-directory>/reana.yaml
```

Do not use `--environments` by default: it can contact registries and pull images. Do not
install the client solely to complete this authoring workflow.

### 7. Stop at the handoff boundary

Deliver:

1. the local project inventory;
2. the authored `reana.yaml` and declared scripts;
3. the offline validation report;
4. unresolved provider, image, resource, or binary-review decisions;
5. the recorded image digest or explicit tag-only waiver.

Stop before authentication or remote execution. A separate operational skill and explicit
user approval must handle any later REANA server interaction.

## Verification

- `reana.yaml` uses `workflow.type: serial` and `workflow.specification.steps`.
- Every step has a string image and non-empty string commands.
- Inputs exist, outputs stay contained, and neither uses symlinks or path traversal.
- No credential value or sensitive filename is present in the project inventory.
- No bare or `latest` image, runtime package installation, or inline heredoc remains.
- The static preflight passes and any warning or waiver is recorded.
- No server, registry, container runtime, process launcher, or remote workflow was touched.
