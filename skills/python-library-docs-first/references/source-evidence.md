# Source Evidence Template

Use a compact table when a solution depends on several libraries, versions, or
documentation sources.

| Claim or decision | Runtime, distribution, and target version | Authoritative source | Version fit | Local verification |
|---|---|---|---|---|
| `<specific API behavior>` | `<name> <version>` | `<official URL, local path, or commit>` | exact / older / newer | `<command or test result>` |

## Version Mismatch Rules

- **Exact:** Apply the evidence, then verify the smallest runnable example.
- **Older source:** Look for a later migration note or inspect the installed API before
  using it.
- **Newer source:** Do not backport the documented behavior by assumption. Find the
  older version's docs, tag, source, or tests.
- **Unversioned index:** Treat it as discovery only until the underlying page or source
  version is known.
- **Conflicting sources:** Prefer the exact-version public contract. Report the conflict
  if local behavior differs.

## Minimal Evidence Note

For a simple task, a short note is enough:

```text
Target: CPython 3.11 on Linux; pandas 2.2.x from uv.lock
Evidence: pandas 2.2 API reference for DataFrame.convert_dtypes
Check: focused unit test with nullable integer and missing value
Mismatch: none
```
