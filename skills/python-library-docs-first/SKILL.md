---
name: python-library-docs-first
description: Verify Python APIs against version-matched documentation. Check version-sensitive third-party APIs against authoritative documentation before writing, reviewing, fixing, or explaining code.
version: 2.0.1
author: AIP AstroAgent team and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: software-development
    tags:
      - python
      - documentation
      - api
      - versioning
      - code-review
---

# Python Library Documentation First

## When to Use

Use this skill when a task depends on a third-party Python library and an API signature,
version difference, deprecation, interoperability detail, or current behavior could affect
the answer. It is especially useful for unfamiliar libraries and for Dask, pandas, and
Datashader workflows.

Do not invoke it merely because a task contains Python. Standard-library-only work and
stable project-local APIs normally need no external documentation search.

## Workflow

### 1. Establish the target

Identify the relevant Python interpreter and platform, distributions, optional backends,
and APIs from the request, imports, traceback, project metadata, and lockfile. Resolve the
target version in this order:

1. a version explicitly required by the user;
2. the project's lockfile or exact dependency pin;
3. the installed distribution version;
4. the current stable release, clearly labeled as an assumption.

Never silently substitute current documentation for an older pinned environment. Never
upgrade a dependency unless the user requested it.

### 2. Select evidence by authority and version fit

Prefer, in order:

1. documentation or dependency source for the exact pinned version or commit, including
   vendored source;
2. official versioned documentation and migration or release notes;
3. the installed package's signature, docstring, type hints, and source;
4. official current documentation, with any version mismatch disclosed;
5. a vetted documentation index as a discovery aid.

Search snippets, generated summaries, and model memory are leads, not authoritative
evidence. If a connected documentation index is available, use only its read-only
enumeration, search, and fetch capabilities. Index administration, scraping, refreshing,
and removal are outside this skill.

### 3. Research the exact behavior

Search for the concrete class, function, method, parameter, exception, or compatibility
question. Read the central API page and any directly relevant migration note. Use the
focused prompts in [library query patterns](references/library-query-patterns.md) for
Dask, pandas, and Datashader.

Record the package version, source URL or local path, and the claim it supports. The
[evidence template](references/source-evidence.md) is useful when several libraries or
versions interact.

### 4. Implement the smallest supported slice

Use documented public APIs, explicit imports, and current argument names for the target
version. Preserve the project's dependency constraints and surrounding style. Avoid
introducing a compatibility shim until evidence shows that more than one supported
version requires it.

### 5. Verify locally

Run the smallest safe check that exercises the disputed behavior:

- inspect the installed version and callable signature;
- import or compile the changed module;
- execute a minimal example with synthetic data;
- run the narrow project test, then broader tests if justified.

Documentation supports an implementation; it does not replace an executable check. Do
not contact production services or write external state merely to validate an API.

### 6. Report evidence and uncertainty

Briefly state the target version, authoritative source, verification performed, and any
remaining mismatch or ambiguity. If suitable documentation is unavailable, say so and
fall back to local signatures, source, and tests rather than guessing.

## Privacy and Credentials

Before sending a query to any remote documentation service, remove private source code,
tokens, internal hostnames, customer data, and unnecessary traceback values. This skill
does not require credentials, an AIP endpoint, Hermes, MCP, or any particular search
provider.

## Completion Check

- The target Python runtime, distributions, versions, and relevant optional backends are
  explicit.
- Each version-sensitive claim has authoritative evidence with suitable version fit.
- The implementation uses public documented APIs and preserves dependency constraints.
- A safe local check exercised the relevant behavior.
- Source/version mismatches and unresolved uncertainty are disclosed.
