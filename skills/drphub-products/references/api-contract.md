# Curated DRP Hub API Contract

This skill intentionally supports a small read-only subset:

| Client command | Method | Path | Authentication |
|---|---|---|---|
| `health` | GET | `/health` | none |
| `contract` | GET | `/openapi.json` | none |
| `config` | GET | `/config` | user or service |
| `list` | GET | `/products` | user or service |
| `get` | GET | `/products/{id}` | user or service |
| `maturity` | GET | `/products/{id}/maturity` | user or service |
| `lineage` | GET | `/products/{id}/lineage` | user or service |

The client does not accept an arbitrary method, path, query key, header, response field,
or remote tool descriptor. Product IDs must be UUIDs. List pagination is bounded and
cursors are opaque.

The curated response contract is also narrow: product and lineage fields must have the
documented bounded scalar types; maturity `level` and `override` are integers from 0
through 4 (or `null` for the override), `gates` contains only boolean `l1Ok` through
`l4Ok`, and `missing` contains bounded identifier lists under `L1` through `L4`.
Unknown fields are discarded, while malformed values under approved fields fail closed.

## Reviewed Evidence

The historical input was
`arm2arm/AstroAgentAssistant@ef78afcf1412575dd23e8e88c01dbf50b8b02836`,
path `astronomy/drphub-cards`, directory Git tree
`c9819e4e69215f4cb64f9c2129802507680dd091`.

During curation on 2026-07-30, the public AIP deployment returned:

- OpenAPI SHA-256
  `bf146f7a9edc7aa1f5eb529aa115fe82a366f05f195051f628535aa1937347b5`;
- tools document SHA-256
  `d831b128a6348177244174e574c2f355f012a6da836be96dc251f3df9e434e48`.

These digests record reviewed evidence, not permanent pins. The `contract` command reports
the current OpenAPI digest and fails when a required read route disappears.

## Known Ambiguities

The reviewed API materials disagree about several behaviors:

- global OpenAPI security covers `/health`, while the observed endpoint was public;
- product-create requirements differ across the historical skill and OpenAPI;
- `If-Match` is structurally optional but described as required;
- ETag response headers are observed but absent from response schemas;
- global prose says every mutation supports idempotency while endpoint parameters differ;
- create, delete, and human-review lack documented dry-run behavior;
- historical notes disagree whether GET after soft deletion returns 200 or 404;
- the public tool descriptor advertises HTTP URLs while the production OpenAPI server is
  HTTPS.

These ambiguities are why this version excludes every mutation. Do not expand the client
until an authoritative versioned contract, mocked tests, and human review cover ETags,
retry-stable idempotency, dry-run previews, confirmation, and post-write verification.
