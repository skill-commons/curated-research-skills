---
name: drphub-cards
description: "Manage and publish DRP Hub research products via REST. Supports full CRUD, clone, maturity, publish, audit, lineage, human-review, bookmarks, likes, sharing, and SSE event streaming against the production API at drp-term.kube.aip.de/api/v1/."
version: 2.1.2
author: Ori (Hermes Agent)
license: MIT
platforms: [linux]
metadata:
  hermes:
    category: data
    tags:
      - drp
      - hub
      - cards
      - products
      - rest-api
      - punch4nfdi
  drphub:
    tags: [drp, hub, cards, products, rest-api, punch4nfdi]
    homepage: https://drphub-p4n.aip.de
    api_base: https://drp-term.kube.aip.de/api/v1
    docs: https://drp-term.kube.aip.de/api/v1/docs#/
    related_skills: [reana-aip, reana-serial-python, docs-mcp-at-aip]
---

# DRP Hub Card/Product Management

Manage DRP Hub Digital Research Products (DRPs) via the **production REST API**. The API is backed by `drp_products` table and bidirectionally mirrored with the legacy `drp_cards` table used by the web UI.

**API base URL:** `https://drp-term.kube.aip.de/api/v1`
**OpenAPI spec:** `https://drp-term.kube.aip.de/api/v1/openapi.json`
**Full API reference (parsed):** `references/api-spec.md`

**Web-UI share link (the URL you give to HUMANS):**
`https://drphub-p4n.aip.de/share/<product-id>`
This is the Hub's only public card-view route (`/share/:cardId`). Do NOT
construct `https://drphub-p4n.aip.de/product/<id>` — no such web route exists
(it 404s); the `/products/{id}` path is REST-API-only, not a web page.

## Authentication

Two auth modes — **end-user JWT** or **Hermes service token**:

1. **End-user JWT** (Supabase or Keycloak): Send `Authorization: Bearer <token>`
2. **Hermes service token**: Send `Authorization: Bearer <service_token>` + `X-Acting-User-Id: <user-uuid>`

For end-user auth, set the env var `DRPHUB_TOKEN`:
```bash
hermes config env set DRPHUB_TOKEN <your_supabase_or_keycloak_jwt>
```

For service-token auth:
```bash
hermes config env set DRPHUB_SERVICE_TOKEN <service_token>
hermes config env set DRPHUB_ACTING_USER_ID <user-uuid>
```

All **mutating** endpoints accept an optional `Idempotency-Key` header (client-generated UUID, unique per actor+route+body). Replays return the original response.

## All 14 API Endpoints

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/products` | List products (cursor-paginated) | JWT / Service |
| POST | `/products` | Create product | JWT / Service |
| GET | `/products/{id}` | Get single product | JWT / Service |
| PATCH | `/products/{id}` | Update product (owner/admin) | JWT / Service |
| DELETE | `/products/{id}` | Soft-delete product | JWT / Service |
| POST | `/products/{id}/clone` | Deep clone a product | JWT / Service |
| GET | `/products/{id}/maturity` | Maturity level + missing gates | JWT / Service |
| POST | `/products/{id}/publish` | Publish (requires L4 gates) | JWT / Service |
| POST | `/products/{id}/human-review` | Mark human-reviewed (humans only) | JWT only |
| GET | `/products/{id}/lineage` | Ancestors + children | JWT / Service |
| GET | `/products/{id}/audit` | Audit log (owner/admin) | JWT / Service |
| GET | `/products/{id}/events` | SSE stream of audit events | JWT / Service |
| GET | `/config` | API capabilities | JWT / Service |
| GET | `/health` | Liveness + DB probe | None (public) |

## Product Schema (full fields)

**Read fields** (returned by GET/PATCH):

| Field | Type | Notes |
|-------|------|-------|
| `id` | uuid | Primary key |
| `owner_user_id` | uuid | Owner |
| `title` | string | |
| `description` | string/null | |
| `avatar` | string/null | URL |
| `category` | string | |
| `tags` | string[] | |
| `visibility` | enum | `private`, `internal`, `shared`, `public` |
| `product_status` | enum | `draft`, `generated`, `validating`, `validated`, `failed_validation`, `human_reviewed`, `published`, `archived` |
| `source_type` | enum | `manual`, `repo`, `clone`, `template`, `ai_generated`, `imported` |
| `git_url` | string/null | |
| `git_branch` | string/null | |
| `git_commit` | string/null | |
| `entry_command` | string/null | |
| `workflow_file` | string/null | |
| `license` | string/null | |
| `citation_cff_url` | string/null | |
| `release_tag` | string/null | |
| `env_image` | string/null | |
| `env_image_digest` | string/null | |
| `authors_orcid` | string[] | |
| `maturity_level` | integer | |
| `maturity_override` | integer/null | |
| `reproducibility_depth` | enum | `D0`, `D1`, `D2`, `D3`, `D4` |
| `validation_status` | enum | `not_validated`, `pending`, `running`, `passed`, `failed`, `waived` |
| `validation_scope` | object/null | |
| `provenance_url` | string/null | |
| `expected_outputs` | object[] | |
| `has_s3` | bool | |
| `has_hpc` | bool | |
| `has_reana` | bool | |
| `human_reviewed` | bool | |
| `human_reviewed_by` | uuid/null | |
| `human_reviewed_at` | datetime/null | |
| `doi` | string/null | |
| `archive_url` | string/null | |
| `harvest_endpoint` | string/null | |
| `oai_published` | bool | |
| `published_at` | datetime/null | |
| `cloned_from_product_id` | uuid/null | |
| `root_product_id` | uuid/null | |
| `clone_mode` | enum/null | `metadata_only`, `template`, `snapshot`, `fork` |
| `clone_count` | integer | |
| `run_count` | integer | |
| `likes` | integer | |
| `created_at` | datetime | |
| `updated_at` | datetime | |
| `computed_maturity_level` | integer | |
| `maturity_gates` | object | `{l1Ok, l2Ok, l3Ok, l4Ok}` |
| `maturity_missing` | object | `{L1: [], L2: [], L3: [], L4: []}` |

**Create fields** (POST /products):

**Required:**
- `title` — Product title
- `category` — One of: `analysis`, `tool`, `data`, `workflow`, `service`, `publication`
- `visibility` — One of: `private`, `shared`, `public`
- `source_type` — One of: `manual`, `repo`, `clone`, `template`, `ai_generated`, `imported`
- `git_url` — Repository URL
- `git_branch` — Branch name (e.g. `main`)
- `git_commit` — Commit SHA (or `HEAD` for latest)
- `env_image` — Container image (e.g. `docker.io/user/reana-env:latest`)
- `reproducibility_depth` — One of: `D0`, `D1`, `D2`, `D3`, `D4`

**Optional:**
- `description` — Longer description (≥80 chars recommended for L1 maturity). **MUST be GitHub-flavored Markdown** (headings, lists, **bold**, [label](https://example.com) links) — it renders as the card body in the Hub UI. Use real newlines, not the two-character sequence `\n`. Do NOT wrap the value in a fenced code block. Plain prose without Markdown is discouraged (per OpenAPI `ProductCreate` schema).
- `tags` — Comma-separated tags array
- `entry_command` — Command to run workflow
- `workflow_file` — Workflow file name (e.g. `reana.yaml`)
- `license` — SPDX license identifier
- `citation_cff_url` — URL to CITATION.cff
- `release_tag` — Version tag (e.g. `v1.0.0`)
- `env_image_digest` — SHA256 digest of the image
- `authors_orcid` — Array of ORCID IDs
- `validation_scope` — Object with test/reproducibility flags
- `provenance_url` — DOI or provenance link
- `expected_outputs` — Array of expected output files
- `has_s3` — Has S3 storage (boolean)
- `has_hpc` — Has HPC execution (boolean)
- `has_reana` — Has REANA workflow (boolean)

**Patch fields** (PATCH /products/{id} — all optional, supply only what you want to change): same as create plus `maturity_override`, `product_status`, `doi`, `archive_url`, `harvest_endpoint`

## Helper Function

Use this as your base for all API calls:

```python
import os, json, uuid, urllib.request, urllib.error

BASE = os.environ.get("DRPHUB_BASE", "https://drp-term.kube.aip.de/api/v1")
TOKEN = os.environ.get("DRPHUB_TOKEN", "")
SERVICE_TOKEN = os.environ.get("DRPHUB_SERVICE_TOKEN", "")
ACTING_USER_ID = os.environ.get("DRPHUB_ACTING_USER_ID", "")

def drphub_request(method, path, body=None, headers_extra=None, dry_run=False):
    """
    Make a DRP Hub API request.
    
    Args:
        method: "GET", "POST", "PATCH", "DELETE"
        path: endpoint path (e.g. "/products", "/products/{id}/clone")
        body: dict with data for POST/PATCH/clone (or None)
        headers_extra: extra dict of headers (optional)
        dry_run: if True, append ?dry_run=true to PATCH/clone/publish
    
    Returns:
        dict/list from API, or None on error
    """
    url = f"{BASE}{path}"
    if dry_run:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}dry_run=true"
    # NOTE: `?dry_run=true` must be appended BEFORE any path params with `?`
    # (e.g. from `?include=links`). Always call with dry_run=True as a separate
    # parameter, not chaining with other query params that already have `?`.
    # If you need multiple query params, build the URL manually:
    # url = f"{BASE}/products/{id}?include=links&dry_run=true"
    
    auth_headers = {}
    if SERVICE_TOKEN:
        auth_headers["Authorization"] = f"Bearer {SERVICE_TOKEN}"
        if ACTING_USER_ID:
            auth_headers["X-Acting-User-Id"] = ACTING_USER_ID
    elif TOKEN:
        auth_headers["Authorization"] = f"Bearer {TOKEN}"
    
    if headers_extra:
        auth_headers.update(headers_extra)
    
    # Idempotency key for mutating operations
    if method in ("POST", "PATCH", "DELETE") and method != "GET":
        auth_headers["Idempotency-Key"] = str(uuid.uuid4())
    
    headers = {
        "Content-Type": "application/json",
        **auth_headers,
    }
    
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req) as resp:
            resp_body = resp.read().decode()
            return json.loads(resp_body) if resp_body else None
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        raise Exception(f"DRP Hub {method} {path} failed ({e.code}): {err_body}")
```

## Operations

### Health Check (no auth needed)

```python
health = drphub_request("GET", "/health")
# Returns: {"status": "ok", "api_version": "v1", "db": True}
```

### List Products

```python
# Basic list (paginated, cursor-based)
products = drphub_request("GET", "/products")
items = products.get("items", [])
next_cursor = products.get("next_cursor")  # pass as cursor param for next page

# Filter by visibility
public = drphub_request("GET", "/products?visibility=public")

# Filter to only my products
mine = drphub_request("GET", "/products?mine=true")

# Search by keyword (title+description+tags)
galaxy = drphub_request("GET", "/products?q=galaxy&q_in=all")

# Search only title
astro = drphub_request("GET", "/products?q=astro&q_in=title")

# Limit + fields projection (keep response small for LLM context)
minimal = drphub_request("GET", "/products?limit=10&fields=id,title,maturity_level,updated_at")

# Include HATEOAS links
with_links = drphub_request("GET", "/products?include=links&limit=5")
```

### ⚠️ RUNNABLE-card pre-flight (do this BEFORE creating a card with has_reana)

A card's **"Run on REANA"** button executes `workflow_file` from
`git_url@git_branch/git_commit` on the clicking user's REANA — the REPO
content runs, not your workspace. A card created from an unvalidated repo is
broken for every future user. Checklist:

1. **Author the workflow with the `reana-aip` skill** (canonical reana.yaml
   template + the approved AIP environment images). Never hand-invent the
   yaml structure or environment refs; for spec questions use the
   `docs-mcp-at-aip` server if available.
2. **`reana-client validate -f reana.yaml` MUST pass** (all three checks) on
   the exact file that is pushed to the repo.
3. **Repo reality check:** `reana.yaml` sits at the REPO ROOT (or exactly at
   the `workflow_file` path); every script in `inputs.files` is committed;
   the project on gitlab-p4n.aip.de has `internal` or `public` visibility so
   DRP-Hub can clone it; `git_branch` exists and `git_commit` (or `HEAD`)
   resolves.
4. **Card fields must MATCH the repo:** `workflow_file` = the yaml's path;
   `env_image` = the environment ref used inside reana.yaml (from the
   approved list — not a made-up docker.io path); `entry_command` = the real
   run command (e.g. `reana-client run -w <name>`); set `has_reana: true`.
5. After POST, GET the product back and confirm the git/env fields round-trip
   correctly — then tell the user the card's SHARE link
   `https://drphub-p4n.aip.de/share/<product-id>` (NOT `/product/<id>` — that
   route does not exist) and that Run on REANA is ready.

### Create Product (via Web Form API)

Create a product by matching the **DRP Hub web form** structure:

```python
product = drphub_request("POST", "/products", body={
    # === Basic Info ===
    "title": "My Analysis Workflow",
    "tags": ["reana", "analysis", "astrophysics"],
    "category": "analysis",  # analysis, tool, data, workflow, service, publication
    "visibility": "private",  # private, internal, shared, public
    "source_type": "repo",  # manual, repo, clone, template, ai_generated, imported
    
    # === Git Info (see the runnable-card pre-flight above!) ===
    "git_url": "https://gitlab-p4n.aip.de/<namespace>/<project>",  # internal/public visibility
    "git_branch": "main",
    "git_commit": "HEAD",  # or specific SHA
    "workflow_file": "reana.yaml",          # path inside the repo (root!)
    "entry_command": "reana-client run -w my-workflow",
    
    # === Environment: MUST equal the environment ref inside reana.yaml ===
    # (approved AIP list in the reana-aip skill, e.g.
    #  gitlab-p4n.aip.de:5005/p4nreana/reana-env:py311-astro.9845)
    "env_image": "gitlab-p4n.aip.de:5005/p4nreana/reana-env:py311-astro.9845",
    "env_image_digest": "",  # optional SHA256 digest
    
    # === Metadata ===
    "license": "MIT",
    "citation_cff_url": "https://github.com/user/repo/blob/main/CITATION.cff",
    "release_tag": "v1.0.0",
    "authors_orcid": ["0000-0000-0000-0001"],  # array of ORCID strings
    
    # === Validation ===
    "reproducibility_depth": "D3",  # D0, D1, D2, D3, D4
    "validation_scope": {
        "tests": True,
        "reproducibility": True,
        "performance": False,
        "documentation": False,
        "security": False
    },
    
    # === Storage ===
    "has_s3": False,
    "has_hpc": False,
    "has_reana": True,
    
    # === Expected Outputs ===
    "expected_outputs": [
        {"name": "figures/result.pdf", "type": "figure", "format": "pdf"}
    ],
    
    # === Optional (for later maturity levels) ===
    "provenance_url": "",  # DOI or provenance link (needed for L3)
})
product_id = product["id"]
print(f"Created: {product['title']} (ID: {product_id})")
```

**Notes:**
- `description` is set separately via PATCH after creation (not in create form)
- `provenance_url`, `doi`, `archive_url` are set after validation passes (for L3/L4)
- To update description/title after creation (description MUST be GFM Markdown):
  ```python
  drphub_request("PATCH", f"/products/{product_id}", body={
      "description": (
          "## Summary\n\n"
          "Short overview of the workflow and **key outputs**.\n\n"
          "- Docs: [example docs](https://example.com/docs)\n"
          "- Repo: [example-repo](https://example.com/repo)"
      ),
  })
  ```

### Get Single Product

```python
product = drphub_request("GET", f"/products/{product_id}")

# With HATEOAS links
with_links = drphub_request("GET", f"/products/{product_id}?include=links")
# Returns _links dict: self, maturity, lineage, clone, human-review, publish, audit, events

# Conditional GET (etag-based caching)
# Add If-None-Match header:
# drphub_request("GET", f"/products/{product_id}", headers_extra={"If-None-Match": "etag-value"})
```

### Update Product (PATCH — all fields optional)

```python
updated = drphub_request("PATCH", f"/products/{product_id}", body={
    "title": "Updated Title",
    "visibility": "public",  # change visibility
    "tags": ["x-ray", "spectroscopy", "reana", "updated"],
    "product_status": "validated",  # advance status
})

# Dry run (validate without writing)
dry = drphub_request("PATCH", f"/products/{product_id}", body={
    "visibility": "public",
}, dry_run=True)
```

### Delete Product (Soft Delete)

```python
drphub_request("DELETE", f"/products/{product_id}")
```

**IMPORTANT — soft-delete GET behavior:** After a soft delete, `GET /products/{id}` returns **HTTP 200** (not 404). The record persists with `deleted_at` and `deleted_by` fields populated. To confirm deletion, check for `deleted_at` in the response. Soft-deleted products are automatically filtered from listing endpoints (e.g., `GET /products?mine=true`).

```python
resp, body = drphub_request("DELETE", f"/products/{product_id}")
# Verify soft-delete:
resp, body = drphub_request("GET", f"/products/{product_id}")
assert body.get("deleted_at") is not None, "Product not soft-deleted!"
```

### Clone Product

```python
# Clone with different modes: metadata_only, template, snapshot, fork
cloned = drphub_request("POST", f"/products/{product_id}/clone", body={
    "title": "Clone of My Analysis",  # new title for clone
    "clone_mode": "metadata_only",  # metadata_only, template, snapshot, fork
})
cloned_id = cloned["id"]

# Dry run
dry = drphub_request("POST", f"/products/{product_id}/clone", body={
    "title": "Clone preview",
    "clone_mode": "template",
}, dry_run=True)
```

### Maturity Check

```python
maturity = drphub_request("GET", f"/products/{product_id}/maturity")
# Returns:
# {
#   "product_id": "...",
#   "level": 2,           # current maturity level
#   "override": None,     # manual override (if set)
#   "gates": {"l1Ok": true, "l2Ok": true, "l3Ok": false, "l4Ok": false},
#   "missing": {
#     "L1": ["field1"],
#     "L2": ["field2"],
#     "L3": ["doi", "archive_url"],
#     "L4": []
#   }
# }
```

### Publish Product (requires L4 gates passing)

```python
published = drphub_request("POST", f"/products/{product_id}/publish")
# Returns updated product with status="published"

# Dry run (validate without publishing)
dry = drphub_request("POST", f"/products/{product_id}/publish", dry_run=True)
```

### Human Review

```python
# Mark as human-reviewed (JWT only, rejects service tokens)
reviewed = drphub_request("POST", f"/products/{product_id}/human-review", body={
    # body depends on API definition — check /config for required fields
})
```

### Lineage (Clone History)

```python
lineage = drphub_request("GET", f"/products/{product_id}/lineage")
# Returns:
# {
#   "product_id": "...",
#   "ancestors": [{"id": "...", "title": "...", ...}],  # parents
#   "children": [{"id": "...", "title": "...", ...}]     # clones
# }
```

### Audit Log

```python
# Get audit events for a product
audit = drphub_request("GET", f"/products/{product_id}/audit")
events = audit.get("items", [])

# Paginated
audit_page2 = drphub_request("GET", f"/products/{product_id}/audit?cursor=<next_cursor>")
```

### SSE Event Stream (Real-time Audit)

```python
# Long-lived SSE connection — heartbeats every 15s, caps at 5 minutes
# Reconnect when connection drops
import json, time

req = urllib.request.Request(
    f"{BASE}/products/{product_id}/events",
    headers={"Authorization": f"Bearer {TOKEN}"}
)
with urllib.request.urlopen(req) as resp:
    for line in resp:
        text = line.decode().strip()
        if text.startswith("data:"):
            event = json.loads(text[5:])
            print(f"Event: {event}")
        elif text == ":heartbeat":
            pass  # skip heartbeat
        else:
            pass  # comment or other
```

### API Config (Capabilities)

```python
config = drphub_request("GET", "/config")
# Returns API capabilities, supported features, limits
print(json.dumps(config, indent=2))
```

### Tools.json (AI Function Descriptors)

```python
# OpenAI/Anthropic tool descriptors — ships ready-to-use function call schemas
tools = drphub_request("GET", "/tools.json")
# Use these directly with OpenAI/Anthropic function calling
```

## Social Features (via Supabase sidecar)

The REST API is mirrored with the legacy `drp_cards` table. Bookmarks, likes, and sharing are managed via the Supabase sidecar:

```python
# Setup (env vars: DRPHUB_SUPABASE_URL, SUPABASE_ANON_KEY)
SUPABASE_URL = os.environ.get("DRPHUB_SUPABASE_URL", "https://rrgnjinkabvqavwwzyfs.supabase.co")

def supabase_request(table, path="", body=None, token=TOKEN):
    url = f"{SUPABASE_URL}/rest/v1/{table}{path}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "apikey": os.environ.get("SUPABASE_ANON_KEY", ""),
        "Prefer": "return=representation",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

# Bookmark a card
supabase_request("drp_card_bookmarks", body={
    "user_id": "<your-user-id>",
    "card_id": product_id,
})

# Like/unlike (uses RPC)
supabase_request("rpc/toggle_card_like", body={"card_id": product_id})

# Share
supabase_request("drp_card_shared_with_me", body={
    "user_id": "<your-user-id>",
    "card_id": product_id,
})

# List shared
shared = supabase_request("drp_card_shared_with_me", f"?user_id=eq.<your-user-id>&select=card_id")
```

## Common Patterns

### Search All Public Products in Category

```python
def search_category(category, q=""):
    params = f"?visibility=public&q_in=title&q={category}"
    if q:
        params += f"&q_in=all&q={q}"
    products = drphub_request("GET", f"/products{params}")
    return products.get("items", [])
```

### Batch Update Multiple Products

```python
def batch_update(product_ids, patch_body):
    results = []
    for pid in product_ids:
        try:
            result = drphub_request("PATCH", f"/products/{pid}", body=patch_body)
            results.append({"id": pid, "status": "ok"})
        except Exception as e:
            results.append({"id": pid, "error": str(e)})
    return results
```

### Check Maturity Before Publish

```python
def can_publish(product_id):
    maturity = drphub_request("GET", f"/products/{product_id}/maturity")
    gates = maturity.get("gates", {})
    return gates.get("l4Ok", False)

if can_publish(product_id):
    drphub_request("POST", f"/products/{product_id}/publish")
else:
    maturity = drphub_request("GET", f"/products/{product_id}/maturity")
    print("Cannot publish — missing gates:")
    for level, items in maturity["missing"].items():
        if items:
            print(f"  {level}: {', '.join(items)}")
```

## Troubleshooting

- **401 Unauthorized**: Token expired or invalid. Refresh your JWT or service token.
- **403 Forbidden**: Token lacks permission for this resource. Check Keycloak roles/claims.
- **409 Conflict**: Idempotency key reused with different body, or resource already exists.
- **412 Precondition Failed**: Etag mismatch (use `If-Match` header with current etag).
- **Description shows as raw text in Hub UI**: it wasn't GFM Markdown — rewrite `description` using headings/lists/[label](https://example.com) links (see the field definition in "Product Schema → Create fields").
- **422 Unprocessable Entity**: Validation failure — check request body against schema.
- **429 Too Many Requests**: Rate limited — back off and retry.
- **Empty results**: Verify your token has visibility permissions. Public products are visible to all; private/internal require appropriate claims.
- **SSE connection drops**: Reconnect after 5-minute cap or network interruption. Heartbeats are sent every 15s.

## Token Handling Pitfall (CRITICAL)

The Hermes tool system (write_file, execute_code, terminal) truncates strings longer than ~48 characters when they contain `drp_pat_` prefix tokens. **Always write the token to a .txt file, then read it in your script** — never pass the token directly in tool call arguments or Python string literals inside tool calls.

```python
# WRONG — gets truncated in tool output:
TOKEN = "drp_pat_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

# CORRECT — write to file once, read at runtime:
with open('/path/to/drphub_token.txt') as f:
    TOKEN = f.read().strip()
```

**General rule:** Any secret/token 40+ characters that gets truncated in tool calls should be written to a file and read at runtime — not passed directly in tool arguments. This applies to GitHub PATs, API keys, and any long string secrets.


## Quick Reference: Common Field Values

**Visibility:** `private` → `internal` → `shared` → `public`
**Source type:** `manual`, `repo`, `clone`, `template`, `ai_generated`, `imported`
**Product status:** `draft` → `generated` → `validating` → `validated` → `human_reviewed` → `published` → `archived`
**Reproducibility depth:** `D0` (none) → `D1` → `D2` → `D3` → `D4` (full)
**Validation:** `not_validated` → `pending` → `running` → `passed`/`failed`/`waived`
**Clone mode:** `metadata_only`, `template`, `snapshot`, `fork`