#!/usr/bin/env python3
"""Bounded, read-only client for a DRP Hub-compatible products API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

ALLOWED_ORIGIN_ENV = "DRPHUB_ALLOWED_ORIGIN"
USER_TOKEN_ENV = "DRPHUB_TOKEN"
SERVICE_TOKEN_ENV = "DRPHUB_SERVICE_TOKEN"
ACTING_USER_ENV = "DRPHUB_ACTING_USER_ID"

MAX_RESPONSE_BYTES = 1_048_576
MAX_ERROR_BYTES = 65_536
MAX_REDACTION_DEPTH = 40
MAX_OUTPUT_STRING = 4_096

SAFE_PRODUCT_FIELDS = (
    "id",
    "title",
    "category",
    "visibility",
    "product_status",
    "maturity_level",
    "computed_maturity_level",
    "reproducibility_depth",
    "validation_status",
    "git_commit",
    "env_image",
    "env_image_digest",
    "updated_at",
)
SAFE_HEALTH_FIELDS = ("status", "api_version", "db")
SAFE_CONFIG_FIELDS = (
    "service",
    "version",
    "api_version",
    "auth_modes",
    "maturity_levels",
    "enum_values",
    "enums",
    "rate_limits",
    "capabilities",
    "deferred",
    "deferred_endpoints",
)
SAFE_MATURITY_FIELDS = ("level", "override", "gates", "missing")
SAFE_MATURITY_GATE_FIELDS = ("l1Ok", "l2Ok", "l3Ok", "l4Ok")
SAFE_MATURITY_MISSING_FIELDS = ("L1", "L2", "L3", "L4")
PRODUCT_STATUS_VALUES = {
    "draft",
    "generated",
    "validating",
    "validated",
    "failed_validation",
    "human_reviewed",
    "published",
    "archived",
}
REPRODUCIBILITY_DEPTH_VALUES = {"D0", "D1", "D2", "D3", "D4"}
VALIDATION_STATUS_VALUES = {
    "not_validated",
    "pending",
    "running",
    "passed",
    "failed",
    "waived",
}
CLONE_MODE_VALUES = {"metadata_only", "template", "snapshot", "fork"}

PRIVATE_PRODUCT_KEY_PARTS = {
    "category",
    "description",
    "entry_command",
    "env_image",
    "env_image_digest",
    "expected_outputs",
    "git_branch",
    "git_commit",
    "git_url",
    "provenance_url",
    "release_tag",
    "tags",
    "title",
    "workflow_file",
}

ROUTES = {
    "health": "/health",
    "contract": "/openapi.json",
    "config": "/config",
    "list": "/products",
    "get": "/products/{id}",
    "maturity": "/products/{id}/maturity",
    "lineage": "/products/{id}/lineage",
}

PUBLIC_ROUTES = {"health", "contract"}
ROUTE_QUERY_KEYS = {
    "health": frozenset(),
    "contract": frozenset(),
    "config": frozenset(),
    "list": frozenset(
        {
            "limit",
            "cursor",
            "q",
            "q_in",
            "fields",
            "visibility",
            "mine",
        }
    ),
    "get": frozenset({"fields"}),
    "maturity": frozenset(),
    "lineage": frozenset(),
}

REQUIRED_CONTRACT_ROUTES = {
    "/health": "get",
    "/config": "get",
    "/products": "get",
    "/products/{id}": "get",
    "/products/{id}/maturity": "get",
    "/products/{id}/lineage": "get",
}

SENSITIVE_KEY_PARTS = {
    "access_token",
    "acting_service",
    "acting_user",
    "after",
    "api_key",
    "apikey",
    "audit",
    "authorization",
    "authors_orcid",
    "before",
    "cookie",
    "credential",
    "human_reviewed_by",
    "owner_user_id",
    "password",
    "refresh_token",
    "secret",
    "service_token",
    "signed_url",
    "token",
}
SENSITIVE_QUERY_PARTS = {
    "auth",
    "credential",
    "key",
    "secret",
    "sig",
    "signature",
    "token",
    "x_amz",
}

BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
DRP_TOKEN_RE = re.compile(r"(?i)\bdrp_pat_[A-Za-z0-9._~-]+")
JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
    r"[A-Za-z0-9_-]{10,}(?![A-Za-z0-9_-])"
)
SAFE_ERROR_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


class ClientError(RuntimeError):
    """A secret-safe precondition, contract, or transport failure."""


@dataclass(frozen=True, repr=False)
class Authentication:
    mode: str
    authorization: str
    acting_user_id: str | None = None


@dataclass(frozen=True)
class Target:
    base_url: str
    origin: str


@dataclass(frozen=True)
class JSONResponse:
    status: int
    value: Any | None
    etag: str | None
    sha256: str | None
    not_modified: bool = False


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Return redirects to the caller as HTTP errors instead of following them."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        return None


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _sensitive_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _sanitize_url_query(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.query:
        return value
    try:
        names = [_normalized_key(name) for name, _ in urllib.parse.parse_qsl(parsed.query)]
    except ValueError:
        names = []
    if any(any(part in name for part in SENSITIVE_QUERY_PARTS) for name in names):
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, "[REDACTED]", parsed.fragment)
        )
    return value


def _redact_string(value: str, exact_values: tuple[str, ...] = ()) -> str:
    for exact in exact_values:
        if exact:
            value = value.replace(exact, "[REDACTED]")
    redacted = BEARER_RE.sub("Bearer [REDACTED]", value)
    redacted = DRP_TOKEN_RE.sub("[REDACTED]", redacted)
    redacted = JWT_RE.sub("[REDACTED]", redacted)
    redacted = _sanitize_url_query(redacted)
    if len(redacted) > MAX_OUTPUT_STRING:
        redacted = redacted[:MAX_OUTPUT_STRING] + "…[TRUNCATED]"
    return redacted


def redact(
    value: Any,
    *,
    key: str | None = None,
    depth: int = 0,
    exact_values: tuple[str, ...] = (),
    private_product: bool = False,
) -> Any:
    """Recursively remove credentials and personal/private metadata."""

    if key is not None and _sensitive_key(key):
        return "[REDACTED]"
    if depth >= MAX_REDACTION_DEPTH:
        return "[REDACTED_DEPTH_LIMIT]"
    if isinstance(value, dict):
        object_is_private = private_product or (
            isinstance(value.get("visibility"), str) and value["visibility"].casefold() != "public"
        )
        return {
            str(child_key): (
                "[REDACTED_PRIVATE_METADATA]"
                if object_is_private
                and _normalized_key(str(child_key)) in PRIVATE_PRODUCT_KEY_PARTS
                else redact(
                    child_value,
                    key=str(child_key),
                    depth=depth + 1,
                    exact_values=exact_values,
                    private_product=object_is_private,
                )
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            redact(
                item,
                depth=depth + 1,
                exact_values=exact_values,
                private_product=private_product,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            redact(
                item,
                depth=depth + 1,
                exact_values=exact_values,
                private_product=private_product,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _redact_string(value, exact_values)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[REDACTED_UNSUPPORTED_VALUE]"


def _canonical_origin(parts: urllib.parse.SplitResult) -> str:
    hostname = parts.hostname
    if hostname is None:
        raise ClientError("URL must include a hostname")
    try:
        port = parts.port
    except ValueError as exc:
        raise ClientError("URL has an invalid port") from exc
    host = hostname.casefold()
    if ":" in host:
        host = f"[{host}]"
    if port is None or (parts.scheme.casefold() == "https" and port == 443):
        return f"{parts.scheme.casefold()}://{host}"
    return f"{parts.scheme.casefold()}://{host}:{port}"


def _parse_https_url(raw: str, *, origin_only: bool) -> tuple[urllib.parse.SplitResult, str]:
    if not raw or any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise ClientError("URL is missing or contains control characters")
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError as exc:
        raise ClientError("URL is malformed") from exc
    if parts.scheme.casefold() != "https":
        raise ClientError("URL must use HTTPS")
    if parts.username is not None or parts.password is not None:
        raise ClientError("URL must not contain embedded credentials")
    if parts.query or parts.fragment:
        raise ClientError("URL must not contain a query string or fragment")
    if "\\" in parts.path:
        raise ClientError("URL path must not contain backslashes")
    try:
        decoded_path = urllib.parse.unquote(parts.path, errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ClientError("URL path contains invalid escaping") from exc
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        raise ClientError("URL path must not contain traversal segments")
    if origin_only and parts.path not in {"", "/"}:
        raise ClientError(f"{ALLOWED_ORIGIN_ENV} must contain an origin without a path")
    origin = _canonical_origin(parts)
    return parts, origin


def resolve_target(base_url: str, environ: Mapping[str, str]) -> Target:
    allowed_raw = environ.get(ALLOWED_ORIGIN_ENV, "")
    if not allowed_raw:
        raise ClientError(
            f"{ALLOWED_ORIGIN_ENV} is required and must name the exact allowed HTTPS origin"
        )
    _allowed_parts, allowed_origin = _parse_https_url(allowed_raw, origin_only=True)
    base_parts, base_origin = _parse_https_url(base_url, origin_only=False)
    if base_origin != allowed_origin:
        raise ClientError(f"base URL origin does not match {ALLOWED_ORIGIN_ENV}")
    base_path = base_parts.path.rstrip("/")
    if not base_path:
        raise ClientError("base URL must include the API base path")
    normalized = urllib.parse.urlunsplit(
        (base_parts.scheme.casefold(), base_parts.netloc, base_path, "", "")
    )
    return Target(base_url=normalized, origin=base_origin)


def _present(environ: Mapping[str, str], name: str) -> bool:
    return bool(environ.get(name))


def _validate_token(value: str, variable: str) -> str:
    if len(value) > 16_384 or any(
        ord(character) < 33 or ord(character) == 127 for character in value
    ):
        raise ClientError(f"{variable} is malformed")
    return value


def resolve_authentication(environ: Mapping[str, str]) -> Authentication:
    user_token = environ.get(USER_TOKEN_ENV, "")
    service_token = environ.get(SERVICE_TOKEN_ENV, "")
    acting_user = environ.get(ACTING_USER_ENV, "")
    if user_token and service_token:
        raise ClientError(f"{USER_TOKEN_ENV} and {SERVICE_TOKEN_ENV} are mutually exclusive")
    if user_token:
        if acting_user:
            raise ClientError(f"{ACTING_USER_ENV} is only valid with {SERVICE_TOKEN_ENV}")
        token = _validate_token(user_token, USER_TOKEN_ENV)
        return Authentication(mode="user", authorization=f"Bearer {token}")
    if service_token:
        token = _validate_token(service_token, SERVICE_TOKEN_ENV)
        if not acting_user:
            raise ClientError(f"{ACTING_USER_ENV} is required with {SERVICE_TOKEN_ENV}")
        try:
            acting_uuid = str(uuid.UUID(acting_user))
        except (ValueError, AttributeError) as exc:
            raise ClientError(f"{ACTING_USER_ENV} must be a UUID") from exc
        if acting_uuid != acting_user:
            raise ClientError(f"{ACTING_USER_ENV} must use canonical UUID form")
        return Authentication(
            mode="service",
            authorization=f"Bearer {token}",
            acting_user_id=acting_uuid,
        )
    if acting_user:
        raise ClientError(f"{ACTING_USER_ENV} requires {SERVICE_TOKEN_ENV}")
    raise ClientError(
        "No DRP Hub credential is injected. Configure either DRPHUB_TOKEN or "
        "DRPHUB_SERVICE_TOKEN with DRPHUB_ACTING_USER_ID through the runtime's "
        "secret manager outside chat, then retry."
    )


def credential_diagnostic(environ: Mapping[str, str]) -> dict[str, Any]:
    try:
        authentication = resolve_authentication(environ)
    except ClientError as exc:
        missing = not any(
            _present(environ, name) for name in (USER_TOKEN_ENV, SERVICE_TOKEN_ENV, ACTING_USER_ENV)
        )
        return {
            "ready_for_authenticated_reads": False,
            "authentication_mode": "missing" if missing else "invalid",
            "guidance": str(exc),
        }
    return {
        "ready_for_authenticated_reads": True,
        "authentication_mode": authentication.mode,
    }


def _validate_product_id(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ClientError("product ID must be a UUID") from exc
    canonical = str(parsed)
    if canonical != value:
        raise ClientError("product ID must use canonical UUID form")
    return canonical


def _validate_etag(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not value
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ClientError("ETag is malformed")
    return value


def _bounded_text(value: str, *, label: str, maximum: int) -> str:
    if len(value) > maximum or any(
        ord(character) < 32 and character not in {"\t"} for character in value
    ):
        raise ClientError(f"{label} is malformed or too long")
    return value


def _bounded_positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _safe_error_value(value: Any) -> str | None:
    if isinstance(value, str) and SAFE_ERROR_VALUE_RE.fullmatch(value):
        return value
    return None


def _header(headers: Any, name: str) -> str | None:
    value = headers.get(name) if headers is not None else None
    return value if isinstance(value, str) else None


def _read_bounded(response: Any, maximum: int) -> bytes:
    declared = _header(response.headers, "Content-Length")
    if declared is not None:
        try:
            declared_length = int(declared)
        except ValueError as exc:
            raise ClientError("response has an invalid Content-Length") from exc
        if declared_length < 0 or declared_length > maximum:
            raise ClientError("response exceeds the configured size limit")
    payload = response.read(maximum + 1)
    if len(payload) > maximum:
        raise ClientError("response exceeds the configured size limit")
    return payload


def _decode_json(payload: bytes, headers: Any) -> Any:
    content_type = (_header(headers, "Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise ClientError("response Content-Type is not JSON")
    try:
        decoded = payload.decode("utf-8")
        return json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ClientError("response body is not bounded valid UTF-8 JSON") from exc


class DRPHubClient:
    """Fixed-route GET-only client."""

    def __init__(
        self,
        target: Target,
        authentication: Authentication | None,
        *,
        timeout: int = 20,
        opener: Any | None = None,
    ) -> None:
        if not 1 <= timeout <= 60:
            raise ClientError("timeout must be between 1 and 60 seconds")
        self.target = target
        self.authentication = authentication
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(RefuseRedirects())

    def get(
        self,
        route: str,
        *,
        product_id: str | None = None,
        query: Mapping[str, str | int] | None = None,
        if_none_match: str | None = None,
    ) -> JSONResponse:
        if route not in ROUTES:
            raise ClientError("route is not in the read-only allowlist")
        if route not in PUBLIC_ROUTES and self.authentication is None:
            raise ClientError("authenticated route requested without authentication")
        if product_id is not None:
            product_id = _validate_product_id(product_id)
        template = ROUTES[route]
        if "{id}" in template:
            if product_id is None:
                raise ClientError("this route requires a product ID")
            path = template.format(id=product_id)
        else:
            if product_id is not None:
                raise ClientError("this route does not accept a product ID")
            path = template

        query = dict(query or {})
        unknown_query = set(query).difference(ROUTE_QUERY_KEYS[route])
        if unknown_query:
            raise ClientError("query contains keys outside the route allowlist")
        url = f"{self.target.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        response_origin = _canonical_origin(urllib.parse.urlsplit(url))
        if response_origin != self.target.origin:
            raise ClientError("constructed request escaped the allowed origin")

        headers = {
            "Accept": "application/json",
            "User-Agent": "skill-commons-drphub-products/1.0",
        }
        if route not in PUBLIC_ROUTES:
            assert self.authentication is not None
            headers["Authorization"] = self.authentication.authorization
            if self.authentication.acting_user_id is not None:
                headers["X-Acting-User-Id"] = self.authentication.acting_user_id
        conditional = _validate_etag(if_none_match)
        if conditional is not None:
            if route != "get":
                raise ClientError("If-None-Match is allowed only for get")
            headers["If-None-Match"] = conditional

        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                final_url = response.geturl()
                if _canonical_origin(urllib.parse.urlsplit(final_url)) != self.target.origin:
                    raise ClientError("response escaped the allowed origin")
                if final_url != request.full_url:
                    raise ClientError("response URL changed; redirect or transport rewrite refused")
                payload = _read_bounded(response, MAX_RESPONSE_BYTES)
                value = _decode_json(payload, response.headers)
                return JSONResponse(
                    status=int(response.status),
                    value=value,
                    etag=_header(response.headers, "ETag"),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and route == "get":
                return JSONResponse(
                    status=304,
                    value=None,
                    etag=_header(exc.headers, "ETag") or conditional,
                    sha256=None,
                    not_modified=True,
                )
            if 300 <= exc.code < 400:
                raise ClientError("redirect refused") from None
            details: list[str] = []
            try:
                payload = exc.read(MAX_ERROR_BYTES + 1)
                if len(payload) <= MAX_ERROR_BYTES:
                    value = _decode_json(payload, exc.headers)
                    if isinstance(value, dict):
                        error = value.get("error")
                        if isinstance(error, dict):
                            code = _safe_error_value(error.get("code"))
                            request_id = _safe_error_value(error.get("request_id"))
                            if code:
                                details.append(f"code={code}")
                            if request_id:
                                details.append(f"request_id={request_id}")
            except (ClientError, OSError):
                pass
            suffix = f" ({', '.join(details)})" if details else ""
            raise ClientError(f"DRP Hub returned HTTP {exc.code}{suffix}") from None
        except urllib.error.URLError:
            raise ClientError("network request failed") from None
        except TimeoutError:
            raise ClientError("network request timed out") from None
        except OSError:
            raise ClientError("network request failed") from None


def _require_object(response: JSONResponse, label: str) -> dict[str, Any]:
    if not isinstance(response.value, dict):
        raise ClientError(f"{label} response must be a JSON object")
    return response.value


def _project_object(
    response: JSONResponse,
    label: str,
    allowed_fields: tuple[str, ...],
) -> dict[str, Any]:
    source = _require_object(response, label)
    projected = {field: source[field] for field in allowed_fields if field in source}
    if not projected:
        raise ClientError(f"{label} response contains no recognized fields")
    return projected


def _product_summary(product: Any) -> dict[str, Any]:
    if not isinstance(product, dict):
        raise ClientError("product response must be a JSON object")
    product_id = product.get("id")
    if not isinstance(product_id, str):
        raise ClientError("product response is missing a canonical UUID")
    _validate_product_id(product_id)
    visibility = product.get("visibility")
    if visibility not in {"private", "internal", "shared", "public"}:
        raise ClientError("product response has an invalid or missing visibility")
    text_limits = {
        "title": 512,
        "category": 128,
        "product_status": 64,
        "reproducibility_depth": 8,
        "validation_status": 64,
        "git_commit": 128,
        "env_image": 2_048,
        "env_image_digest": 128,
        "updated_at": 128,
    }
    nullable_text = {"git_commit", "env_image", "env_image_digest", "updated_at"}
    for field, maximum in text_limits.items():
        if field not in product:
            continue
        field_value = product[field]
        if field_value is None and field in nullable_text:
            continue
        if not isinstance(field_value, str):
            raise ClientError(f"product {field} must be a string")
        _bounded_text(field_value, label=f"product {field}", maximum=maximum)
    for field in ("maturity_level", "computed_maturity_level"):
        if field not in product:
            continue
        field_value = product[field]
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or not 0 <= field_value <= 4
        ):
            raise ClientError(f"product {field} must be an integer from 0 through 4")
    for field, allowed in (
        ("product_status", PRODUCT_STATUS_VALUES),
        ("reproducibility_depth", REPRODUCIBILITY_DEPTH_VALUES),
        ("validation_status", VALIDATION_STATUS_VALUES),
    ):
        if field in product and product[field] not in allowed:
            raise ClientError(f"product {field} is outside the supported API contract")
    projected = {field: product[field] for field in SAFE_PRODUCT_FIELDS if field in product}
    return redact(projected, private_product=visibility != "public")


def _maturity_summary(response: JSONResponse) -> dict[str, Any]:
    maturity = _require_object(response, "maturity")
    projected: dict[str, Any] = {}
    if "level" in maturity:
        level = maturity["level"]
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 4:
            raise ClientError("maturity level must be an integer from 0 through 4")
        projected["level"] = level
    if "override" in maturity:
        override = maturity["override"]
        if override is not None and (
            isinstance(override, bool) or not isinstance(override, int) or not 0 <= override <= 4
        ):
            raise ClientError("maturity override must be null or an integer from 0 through 4")
        projected["override"] = override
    gates = maturity.get("gates")
    if gates is not None:
        if not isinstance(gates, dict):
            raise ClientError("maturity gates must be an object")
        projected_gates = {
            field: gates[field] for field in SAFE_MATURITY_GATE_FIELDS if field in gates
        }
        if not all(isinstance(value, bool) for value in projected_gates.values()):
            raise ClientError("recognized maturity gates must be boolean")
        projected["gates"] = projected_gates
    missing = maturity.get("missing")
    if missing is not None:
        if not isinstance(missing, dict):
            raise ClientError("maturity missing must be an object")
        projected_missing: dict[str, list[str]] = {}
        for field in SAFE_MATURITY_MISSING_FIELDS:
            if field not in missing:
                continue
            values = missing[field]
            if (
                not isinstance(values, list)
                or len(values) > 100
                or not all(_safe_error_value(value) is not None for value in values)
            ):
                raise ClientError("recognized maturity missing entries must be bounded identifiers")
            projected_missing[field] = values
        projected["missing"] = projected_missing
    if not projected:
        raise ClientError("maturity response contains no recognized fields")
    return projected


def _contract_summary(response: JSONResponse) -> tuple[dict[str, Any], bool]:
    document = _require_object(response, "contract")
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise ClientError("contract response has no paths object")
    available: list[str] = []
    missing: list[str] = []
    for path, method in REQUIRED_CONTRACT_ROUTES.items():
        operations = paths.get(path)
        if isinstance(operations, dict) and isinstance(operations.get(method), dict):
            available.append(f"{method.upper()} {path}")
        else:
            missing.append(f"{method.upper()} {path}")
    info = document.get("info")
    title = info.get("title") if isinstance(info, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    compatible = not missing
    return (
        {
            "compatible": compatible,
            "sha256": response.sha256,
            "title": title if isinstance(title, str) else None,
            "version": version if isinstance(version, str) else None,
            "available_read_routes": available,
            "missing_read_routes": missing,
            "mutation_routes_imported": False,
        },
        compatible,
    )


def _immutable_warnings(product: Mapping[str, Any]) -> list[str]:
    if product.get("visibility") != "public":
        return []
    warnings: list[str] = []
    commit = product.get("git_commit")
    if commit is not None and (
        not isinstance(commit, str)
        or re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit) is None
    ):
        warnings.append("git_commit is not a full immutable object ID")
    image = product.get("env_image")
    separate_digest = product.get("env_image_digest")
    embedded_digest: str | None = None
    if isinstance(image, str):
        match = re.search(r"@sha256:([0-9a-fA-F]{64})$", image)
        if match:
            embedded_digest = f"sha256:{match.group(1).lower()}"
        elif image:
            warnings.append("env_image is not pinned by a sha256 manifest digest")
    if separate_digest is not None:
        if (
            not isinstance(separate_digest, str)
            or re.fullmatch(r"sha256:[0-9a-fA-F]{64}", separate_digest) is None
        ):
            warnings.append("env_image_digest is not a valid sha256 digest")
        elif embedded_digest is not None and separate_digest.casefold() != embedded_digest:
            warnings.append("env_image and env_image_digest disagree")
    return warnings


def _lineage_summary(response: JSONResponse) -> dict[str, Any]:
    lineage = _require_object(response, "lineage")
    allowed = {
        "id",
        "clone_mode",
        "cloned_from_product_id",
        "root_product_id",
        "created_at",
        "updated_at",
    }
    summary: dict[str, Any] = {}
    for direction in ("ancestors", "children"):
        records = lineage.get(direction, [])
        if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
            raise ClientError(f"lineage {direction} must be an array of objects")
        if len(records) > 500:
            raise ClientError(f"lineage {direction} exceeds the record limit")
        projected_records: list[dict[str, Any]] = []
        for item in records:
            item_id = item.get("id")
            if not isinstance(item_id, str):
                raise ClientError("lineage records must include a canonical UUID")
            _validate_product_id(item_id)
            for key in ("cloned_from_product_id", "root_product_id"):
                identifier = item.get(key)
                if identifier is not None:
                    if not isinstance(identifier, str):
                        raise ClientError(f"lineage {key} must be a UUID or null")
                    _validate_product_id(identifier)
            clone_mode = item.get("clone_mode")
            if clone_mode is not None and (
                not isinstance(clone_mode, str) or clone_mode not in CLONE_MODE_VALUES
            ):
                raise ClientError("lineage clone_mode is outside the supported API contract")
            for key in ("created_at", "updated_at"):
                timestamp = item.get(key)
                if timestamp is not None:
                    if not isinstance(timestamp, str):
                        raise ClientError(f"lineage {key} must be a string or null")
                    _bounded_text(timestamp, label=f"lineage {key}", maximum=128)
            projected_records.append({key: item[key] for key in allowed if key in item})
        summary[direction] = projected_records
    return summary


def _run_list(client: DRPHubClient, args: argparse.Namespace) -> dict[str, Any]:
    filters: dict[str, str | int] = {
        "fields": ",".join(SAFE_PRODUCT_FIELDS),
    }
    if args.cursor:
        filters["cursor"] = _bounded_text(args.cursor, label="cursor", maximum=4_096)
    if args.query:
        filters["q"] = _bounded_text(args.query, label="query", maximum=512)
        filters["q_in"] = args.q_in
    if args.visibility:
        filters["visibility"] = args.visibility
    if args.mine:
        filters["mine"] = "true"

    items: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    pages = 0
    cursor = filters.get("cursor")
    seen_cursors: set[str] = set()
    next_cursor: str | None = None
    while pages < args.max_pages and len(items) < args.max_items:
        remaining = args.max_items - len(items)
        query = dict(filters)
        query["limit"] = min(args.limit, remaining)
        if cursor:
            query["cursor"] = cursor
        else:
            query.pop("cursor", None)
        response = client.get("list", query=query)
        page = _require_object(response, "list")
        page_items = page.get("items")
        if not isinstance(page_items, list):
            raise ClientError("list response items must be an array")
        if len(page_items) > int(query["limit"]):
            raise ClientError("list response exceeds the requested page limit")
        for raw_item in page_items:
            summary = _product_summary(raw_item)
            items.append(summary)
            immutable_warnings = _immutable_warnings(raw_item)
            if immutable_warnings:
                warnings.append(
                    {
                        "id": summary.get("id"),
                        "warnings": immutable_warnings,
                    }
                )
        pages += 1
        raw_cursor = page.get("next_cursor")
        if raw_cursor is not None and not isinstance(raw_cursor, str):
            raise ClientError("list next_cursor must be a string or null")
        next_cursor = raw_cursor
        if not next_cursor:
            break
        if next_cursor in seen_cursors or next_cursor == cursor:
            raise ClientError("pagination cursor loop detected")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return {
        "items": items,
        "next_cursor": next_cursor,
        "pages_fetched": pages,
        "truncated": bool(next_cursor),
        "reproducibility_warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        required=True,
        help="explicit HTTPS API base; origin must match DRPHUB_ALLOWED_ORIGIN",
    )
    parser.add_argument(
        "--timeout",
        type=_bounded_positive,
        default=20,
        help="network timeout in seconds (1-60, default: 20)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="validate local destination and credential readiness")
    subparsers.add_parser("health", help="public read-only liveness probe")
    subparsers.add_parser("contract", help="public read-only OpenAPI compatibility summary")
    subparsers.add_parser("config", help="authenticated read-only capability inspection")

    list_parser = subparsers.add_parser("list", help="list a bounded product projection")
    list_parser.add_argument("--query")
    list_parser.add_argument("--q-in", choices=("title", "all"), default="all")
    list_parser.add_argument(
        "--visibility",
        choices=("private", "internal", "shared", "public"),
    )
    list_parser.add_argument("--mine", action="store_true")
    list_parser.add_argument("--cursor")
    list_parser.add_argument("--limit", type=_bounded_positive, default=25)
    list_parser.add_argument("--max-pages", type=_bounded_positive, default=1)
    list_parser.add_argument("--max-items", type=_bounded_positive, default=100)

    get_parser = subparsers.add_parser("get", help="inspect one projected product")
    get_parser.add_argument("product_id")
    get_parser.add_argument("--if-none-match")

    maturity_parser = subparsers.add_parser("maturity", help="inspect maturity gates")
    maturity_parser.add_argument("product_id")

    lineage_parser = subparsers.add_parser("lineage", help="inspect clone relationships")
    lineage_parser.add_argument("product_id")
    return parser


def _validate_bounds(args: argparse.Namespace) -> None:
    if not 1 <= args.timeout <= 60:
        raise ClientError("timeout must be between 1 and 60 seconds")
    if args.command == "list":
        if not 1 <= args.limit <= 200:
            raise ClientError("limit must be between 1 and 200")
        if not 1 <= args.max_pages <= 10:
            raise ClientError("max-pages must be between 1 and 10")
        if not 1 <= args.max_items <= 500:
            raise ClientError("max-items must be between 1 and 500")


def emit(value: Any, environ: Mapping[str, str] | None = None) -> None:
    environment = os.environ if environ is None else environ
    exact_values = tuple(
        value
        for value in (
            environment.get(USER_TOKEN_ENV),
            environment.get(SERVICE_TOKEN_ENV),
            environment.get(ACTING_USER_ENV),
        )
        if value
    )
    print(
        json.dumps(
            redact(value, exact_values=exact_values),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    opener: Any | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    environment = os.environ if environ is None else environ

    def safe_emit(value: Any) -> None:
        emit(value, environment)

    try:
        _validate_bounds(args)
        target = resolve_target(args.base_url, environment)
        if args.command == "doctor":
            safe_emit(
                {
                    "status": "ok",
                    "base_url": target.base_url,
                    "allowed_origin": target.origin,
                    "ready_for_public_reads": True,
                    **credential_diagnostic(environment),
                }
            )
            return 0

        authentication = (
            None if args.command in PUBLIC_ROUTES else resolve_authentication(environment)
        )
        client = DRPHubClient(
            target,
            authentication,
            timeout=args.timeout,
            opener=opener,
        )
        if args.command == "health":
            safe_emit(_project_object(client.get("health"), "health", SAFE_HEALTH_FIELDS))
            return 0
        if args.command == "contract":
            summary, compatible = _contract_summary(client.get("contract"))
            safe_emit(summary)
            return 0 if compatible else 1
        if args.command == "config":
            safe_emit(_project_object(client.get("config"), "config", SAFE_CONFIG_FIELDS))
            return 0
        if args.command == "list":
            safe_emit(_run_list(client, args))
            return 0
        if args.command == "get":
            response = client.get(
                "get",
                product_id=args.product_id,
                query={"fields": ",".join(SAFE_PRODUCT_FIELDS)},
                if_none_match=args.if_none_match,
            )
            if response.not_modified:
                safe_emit(
                    {
                        "not_modified": True,
                        "etag": response.etag,
                        "product": None,
                    }
                )
            else:
                raw_product = _require_object(response, "get")
                product = _product_summary(raw_product)
                safe_emit(
                    {
                        "not_modified": False,
                        "etag": response.etag,
                        "product": product,
                        "reproducibility_warnings": _immutable_warnings(raw_product),
                    }
                )
            return 0
        if args.command == "maturity":
            safe_emit(_maturity_summary(client.get("maturity", product_id=args.product_id)))
            return 0
        if args.command == "lineage":
            safe_emit(_lineage_summary(client.get("lineage", product_id=args.product_id)))
            return 0
        raise ClientError("command is not implemented")
    except ClientError as exc:
        print(f"drphub-products: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
