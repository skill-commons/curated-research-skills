---
name: drphub-products
description: Inspect Digital Research Product Hub products and health. Report API capabilities, product summaries, maturity, and lineage through a bounded read-only REST client. Use when a user needs to diagnose a DRP Hub endpoint, search or inspect products, verify immutable Git and image identities, or review maturity and clone relationships without creating, changing, publishing, sharing, reviewing, or deleting remote data.
version: 1.0.1
author: AIP AstroAgent team and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: data
    tags:
      - drp
      - research-products
      - provenance
      - reproducibility
      - rest-api
      - read-only
---

# DRP Hub Products

## Read-Only Boundary

This skill performs GET requests only. It cannot create, patch, delete, clone, publish,
review, share, validate, or batch-update a product. It also excludes audit logs, event
streams, validation runs, administrative usage, and direct database or Supabase access.

Use the bundled [`scripts/drphub_products.py`](scripts/drphub_products.py). The client has
a fixed route allowlist, refuses redirects, bounds every response, applies a conservative
field projection to products, and recursively redacts credentials and personal or private
metadata before printing.

Run the bundled script with Python 3.11 or newer, matching the supported CRS runtime.

## Configure the Destination

Supply the API base explicitly on every invocation and independently allow its exact
origin:

```bash
export DRPHUB_ALLOWED_ORIGIN='https://hub.example.org'
python scripts/drphub_products.py \
  --base-url 'https://hub.example.org/api/v1' \
  doctor
```

The client requires HTTPS, rejects embedded credentials, query strings, fragments, path
traversal, and any base whose normalized origin differs from
`DRPHUB_ALLOWED_ORIGIN`. It never follows redirects, including same-origin redirects.

For the historical AIP deployment, read
[`references/aip-adapter.md`](references/aip-adapter.md). Do not treat that adapter as a
portable default.

## Inject Credentials Safely

`health` and `contract` are public contract probes. `config`, `list`, `get`, `maturity`,
and `lineage` require exactly one authentication mode:

- user mode: `DRPHUB_TOKEN`;
- service mode: `DRPHUB_SERVICE_TOKEN` plus `DRPHUB_ACTING_USER_ID`.

Use the agent runtime, shell, or organization secret manager to inject the secret into the
client process. If it is absent, stop and ask the user to configure secret injection
outside the conversation, then retry. Never ask the user to paste a token into chat,
place it in a command argument, or write it to `.env`, YAML, a project file, a temporary
file, shell history, or logs.

The two token variables are mutually exclusive. User mode rejects an acting-user value;
service mode requires a valid UUID. `doctor` reports only the mode's readiness and never
prints, fingerprints, measures, or validates a credential against the server.

## Inspect the Service

Run an unauthenticated liveness probe:

```bash
python scripts/drphub_products.py \
  --base-url 'https://hub.example.org/api/v1' \
  health
```

Inspect the public OpenAPI document through a small compatibility summary:

```bash
python scripts/drphub_products.py \
  --base-url 'https://hub.example.org/api/v1' \
  contract
```

The summary records the document digest and checks only this skill's read routes. It does
not import remote tool descriptors or expose mutation operations. For known contract
ambiguities, read [`references/api-contract.md`](references/api-contract.md).

After credential injection, inspect server capabilities:

```bash
python scripts/drphub_products.py \
  --base-url 'https://hub.example.org/api/v1' \
  config
```

## List and Inspect Products

List one bounded page by default:

```bash
python scripts/drphub_products.py \
  --base-url 'https://hub.example.org/api/v1' \
  list --query 'stellar spectra' --visibility public --limit 25
```

Use `--mine`, `--q-in title`, an opaque `--cursor`, or bounded `--max-pages` and
`--max-items` when needed. Keep the same filters while following a cursor. The client
detects cursor loops, never retries automatically, and never requests owner UUIDs,
ORCIDs, audit records, descriptions, private repository URLs, or arbitrary fields.

Inspect one product:

```bash
python scripts/drphub_products.py \
  --base-url 'https://hub.example.org/api/v1' \
  get '<product-uuid>'
```

The result includes the response ETag when supplied. For a conditional re-read:

```bash
python scripts/drphub_products.py \
  --base-url 'https://hub.example.org/api/v1' \
  get '<product-uuid>' --if-none-match 'W/"previous-etag"'
```

HTTP 304 is reported as `not_modified: true`; it is not treated as a failure.

Inspect maturity or clone relationships:

```bash
python scripts/drphub_products.py \
  --base-url 'https://hub.example.org/api/v1' \
  maturity '<product-uuid>'

python scripts/drphub_products.py \
  --base-url 'https://hub.example.org/api/v1' \
  lineage '<product-uuid>'
```

Maturity levels and overrides follow the reviewed API contract: integer `0` through `4`
(or `null` for an override), with boolean `l1Ok`–`l4Ok` gates. Contract drift fails
closed rather than being rendered as arbitrary metadata.

## Interpret Reproducibility Metadata

For repository-backed products:

- accept only a full Git object ID, normally 40 hexadecimal characters;
- treat `HEAD`, a branch, a tag, or an abbreviated hash as mutable;
- accept an OCI runtime only when the image is pinned with
  `@sha256:<64-hex-digest>` or an equivalent digest field matches it exactly;
- treat `latest`, a bare image, or a tag without a digest as mutable;
- report mutable metadata as a warning during reads; never describe it as reproducible.

This skill does not resolve branches, tags, commits, images, or signatures over the
network. A separate, explicitly authorized workflow must perform that verification.

## Verification

- The base URL is explicit HTTPS and matches `DRPHUB_ALLOWED_ORIGIN`.
- Authentication uses exactly one injected mode and no credential value is printed.
- Only the fixed GET routes documented above are contacted.
- Product responses use the curated projection and all output passes recursive redaction.
- Pagination, response size, timeout, and nesting are bounded.
- Redirects, malformed JSON, unexpected content types, and contract drift fail closed.
- No remote or local product state was changed.
