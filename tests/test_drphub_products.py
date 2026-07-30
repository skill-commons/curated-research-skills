import importlib.util
import io
import json
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "drphub-products" / "scripts" / "drphub_products.py"
BASE = "https://hub.example.org/api/v1"
ORIGIN_ENV = {"DRPHUB_ALLOWED_ORIGIN": "https://hub.example.org"}
PRODUCT_ID = "11111111-2222-3333-4444-555555555555"


def _load_client():
    spec = importlib.util.spec_from_file_location("crs_drphub_products", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


class FakeResponse:
    def __init__(
        self,
        value,
        *,
        url=BASE + "/health",
        status=200,
        headers=None,
        raw=None,
    ):
        self._raw = raw if raw is not None else json.dumps(value).encode()
        self._url = url
        self.status = status
        self.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(self._raw)),
            **(headers or {}),
        }

    def read(self, amount=-1):
        return self._raw if amount < 0 else self._raw[:amount]

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeOpener:
    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _auth_env():
    return {**ORIGIN_ENV, "DRPHUB_TOKEN": "synthetic-user-token"}


def test_target_requires_explicit_https_and_exact_allowed_origin():
    module = _load_client()
    with pytest.raises(module.ClientError, match="HTTPS"):
        module.resolve_target("http://hub.example.org/api/v1", ORIGIN_ENV)
    with pytest.raises(module.ClientError, match="does not match"):
        module.resolve_target(
            BASE,
            {"DRPHUB_ALLOWED_ORIGIN": "https://other.example.org"},
        )
    with pytest.raises(module.ClientError, match="embedded credentials"):
        module.resolve_target(
            "https://user:pass@hub.example.org/api/v1",
            ORIGIN_ENV,
        )
    assert module.resolve_target(BASE, ORIGIN_ENV).origin == "https://hub.example.org"


def test_auth_modes_are_exclusive_and_service_mode_requires_actor():
    module = _load_client()
    with pytest.raises(module.ClientError, match="mutually exclusive"):
        module.resolve_authentication(
            {
                "DRPHUB_TOKEN": "user-token",
                "DRPHUB_SERVICE_TOKEN": "service-token",
            }
        )
    with pytest.raises(module.ClientError, match="DRPHUB_ACTING_USER_ID is required"):
        module.resolve_authentication({"DRPHUB_SERVICE_TOKEN": "service-token"})
    auth = module.resolve_authentication(
        {
            "DRPHUB_SERVICE_TOKEN": "service-token",
            "DRPHUB_ACTING_USER_ID": PRODUCT_ID,
        }
    )
    assert auth.mode == "service"
    assert auth.acting_user_id == PRODUCT_ID


def test_doctor_never_prints_secret_value(capsys):
    module = _load_client()
    secret = "synthetic-user-token-never-print"
    result = module.main(
        ["--base-url", BASE, "doctor"],
        environ={**ORIGIN_ENV, "DRPHUB_TOKEN": secret},
    )
    captured = capsys.readouterr()
    assert result == 0
    assert secret not in captured.out
    assert secret not in captured.err
    report = json.loads(captured.out)
    assert report["ready_for_authenticated_reads"] is True
    assert report["authentication_mode"] == "user"


def test_health_is_get_only_and_sends_no_credentials(capsys):
    module = _load_client()
    opener = FakeOpener(FakeResponse({"status": "ok"}))
    result = module.main(
        ["--base-url", BASE, "health"],
        environ={**ORIGIN_ENV, "DRPHUB_TOKEN": "must-not-be-sent"},
        opener=opener,
    )
    assert result == 0
    capsys.readouterr()
    request, timeout = opener.requests[0]
    assert request.get_method() == "GET"
    assert request.full_url == BASE + "/health"
    assert request.get_header("Authorization") is None
    assert timeout == 20


def test_fixed_route_and_query_allowlists_reject_arbitrary_input():
    module = _load_client()
    client = module.DRPHubClient(
        module.resolve_target(BASE, ORIGIN_ENV),
        module.resolve_authentication(_auth_env()),
        opener=FakeOpener(),
    )
    with pytest.raises(module.ClientError, match="route"):
        client.get("delete")
    with pytest.raises(module.ClientError, match="query"):
        client.get("list", query={"arbitrary": "value"})
    with pytest.raises(module.ClientError, match="UUID"):
        client.get("get", product_id="../admin")


def test_list_enforces_projection_bounds_and_recursive_redaction(capsys):
    module = _load_client()
    secret = "drp_pat_synthetic_never_print"
    page = {
        "items": [
            {
                "id": PRODUCT_ID,
                "title": "Fixture",
                "visibility": "public",
                "git_commit": "HEAD",
                "env_image": "example/image:latest",
                "owner_user_id": PRODUCT_ID,
                "authors_orcid": ["0000-0000-0000-0001"],
                "metadata": {"authorization": f"Bearer {secret}"},
            }
        ],
        "next_cursor": None,
    }
    opener = FakeOpener(
        FakeResponse(
            page,
            url=BASE
            + "/products?fields="
            + urllib.parse.quote(",".join(module.SAFE_PRODUCT_FIELDS), safe="")
            + "&limit=1",
        )
    )
    result = module.main(
        ["--base-url", BASE, "list", "--limit", "1"],
        environ=_auth_env(),
        opener=opener,
    )
    captured = capsys.readouterr()
    assert result == 0
    assert secret not in captured.out
    report = json.loads(captured.out)
    item = report["items"][0]
    assert "owner_user_id" not in item
    assert "authors_orcid" not in item
    assert "metadata" not in item
    assert len(report["reproducibility_warnings"][0]["warnings"]) == 2
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.requests[0][0].full_url).query)
    assert query["limit"] == ["1"]
    assert query["fields"][0] == ",".join(module.SAFE_PRODUCT_FIELDS)
    assert "owner_user_id" not in query["fields"][0]


def test_private_product_metadata_and_exact_custom_token_are_redacted(capsys):
    module = _load_client()
    custom_token = "synthetic-token-with-an-unrecognized-shape"
    page = {
        "items": [
            {
                "id": PRODUCT_ID,
                "visibility": "private",
                "title": "Private title",
                "category": "private-category",
                "git_commit": "a" * 40,
                "env_image": "registry.example/image@sha256:" + "b" * 64,
                "metadata": {"note": custom_token},
            }
        ],
        "next_cursor": None,
    }
    result = module.main(
        ["--base-url", BASE, "list", "--limit", "1"],
        environ={**ORIGIN_ENV, "DRPHUB_TOKEN": custom_token},
        opener=FakeOpener(
            FakeResponse(
                page,
                url=BASE
                + "/products?fields="
                + urllib.parse.quote(",".join(module.SAFE_PRODUCT_FIELDS), safe="")
                + "&limit=1",
            )
        ),
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 0
    item = report["items"][0]
    assert item["title"] == "[REDACTED_PRIVATE_METADATA]"
    assert item["category"] == "[REDACTED_PRIVATE_METADATA]"
    assert item["git_commit"] == "[REDACTED_PRIVATE_METADATA]"
    assert item["env_image"] == "[REDACTED_PRIVATE_METADATA]"
    assert "metadata" not in item
    assert custom_token not in json.dumps(report)


def test_approved_product_and_lineage_fields_still_require_bounded_scalar_shapes():
    module = _load_client()
    with pytest.raises(module.ClientError, match="env_image must be a string"):
        module._product_summary(
            {
                "id": PRODUCT_ID,
                "visibility": "public",
                "env_image": {"private_repository_url": "must not survive"},
            }
        )

    response = module.JSONResponse(
        status=200,
        value={
            "ancestors": [
                {
                    "id": PRODUCT_ID,
                    "clone_mode": {"internal_review_notes": "must not survive"},
                }
            ],
            "children": [],
        },
        etag=None,
        sha256=None,
    )
    with pytest.raises(module.ClientError, match="clone_mode"):
        module._lineage_summary(response)


def test_maturity_output_uses_a_strict_nested_projection(capsys):
    module = _load_client()
    response = {
        "level": 3,
        "override": None,
        "gates": {
            "l1Ok": True,
            "l2Ok": True,
            "l3Ok": False,
            "internal_review_notes": "must not be emitted",
        },
        "missing": {
            "L1": [],
            "L2": [],
            "L3": ["immutable_git"],
            "private_repository_url": "must not be emitted",
        },
        "private_repository_url": "must not be emitted",
    }
    result = module.main(
        ["--base-url", BASE, "maturity", PRODUCT_ID],
        environ=_auth_env(),
        opener=FakeOpener(
            FakeResponse(
                response,
                url=BASE + f"/products/{PRODUCT_ID}/maturity",
            )
        ),
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report == {
        "gates": {"l1Ok": True, "l2Ok": True, "l3Ok": False},
        "level": 3,
        "missing": {"L1": [], "L2": [], "L3": ["immutable_git"]},
        "override": None,
    }


def test_get_preserves_etag_and_handles_304(capsys):
    module = _load_client()
    etag = 'W/"fixture"'
    not_modified = urllib.error.HTTPError(
        BASE + f"/products/{PRODUCT_ID}",
        304,
        "Not Modified",
        {"ETag": etag},
        io.BytesIO(b""),
    )
    opener = FakeOpener(not_modified)
    result = module.main(
        [
            "--base-url",
            BASE,
            "get",
            PRODUCT_ID,
            "--if-none-match",
            etag,
        ],
        environ=_auth_env(),
        opener=opener,
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report == {"etag": etag, "not_modified": True, "product": None}
    assert opener.requests[0][0].get_header("If-none-match") == etag


def test_lineage_output_is_reduced_to_relationship_identifiers(capsys):
    module = _load_client()
    response = {
        "ancestors": [
            {
                "id": PRODUCT_ID,
                "title": "must not be emitted",
                "owner_user_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "clone_mode": "snapshot",
            }
        ],
        "children": [],
    }
    result = module.main(
        ["--base-url", BASE, "lineage", PRODUCT_ID],
        environ=_auth_env(),
        opener=FakeOpener(
            FakeResponse(
                response,
                url=BASE + f"/products/{PRODUCT_ID}/lineage",
            )
        ),
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report == {
        "ancestors": [{"clone_mode": "snapshot", "id": PRODUCT_ID}],
        "children": [],
    }


def test_redirects_are_refused_and_error_body_is_not_echoed(capsys):
    module = _load_client()
    secret = "drp_pat_error_body_must_not_leak"
    redirect = urllib.error.HTTPError(
        BASE + "/config",
        302,
        "Found",
        {
            "Location": "https://evil.example/api/v1/config",
            "Content-Type": "application/json",
        },
        io.BytesIO(json.dumps({"secret": secret}).encode()),
    )
    result = module.main(
        ["--base-url", BASE, "config"],
        environ=_auth_env(),
        opener=FakeOpener(redirect),
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "redirect refused" in captured.err
    assert "evil.example" not in captured.err
    assert secret not in captured.err

    same_origin_rewrite = FakeResponse(
        {"api_version": "v1"},
        url=BASE + "/rewritten-config",
    )
    result = module.main(
        ["--base-url", BASE, "config"],
        environ=_auth_env(),
        opener=FakeOpener(same_origin_rewrite),
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "response URL changed" in captured.err


def test_response_size_and_content_type_fail_closed(capsys):
    module = _load_client()
    oversized = FakeResponse(
        {},
        headers={"Content-Length": str(module.MAX_RESPONSE_BYTES + 1)},
    )
    result = module.main(
        ["--base-url", BASE, "health"],
        environ=ORIGIN_ENV,
        opener=FakeOpener(oversized),
    )
    assert result == 2
    assert "size limit" in capsys.readouterr().err

    wrong_type = FakeResponse(
        {},
        headers={"Content-Type": "text/html"},
    )
    result = module.main(
        ["--base-url", BASE, "health"],
        environ=ORIGIN_ENV,
        opener=FakeOpener(wrong_type),
    )
    assert result == 2
    assert "not JSON" in capsys.readouterr().err


def test_contract_reports_missing_required_read_route(capsys):
    module = _load_client()
    document = {
        "info": {"title": "Fixture", "version": "1"},
        "paths": {
            path: {method: {}}
            for path, method in module.REQUIRED_CONTRACT_ROUTES.items()
            if path != "/products/{id}/lineage"
        },
    }
    result = module.main(
        ["--base-url", BASE, "contract"],
        environ=ORIGIN_ENV,
        opener=FakeOpener(FakeResponse(document, url=BASE + "/openapi.json")),
    )
    report = json.loads(capsys.readouterr().out)
    assert result == 1
    assert report["compatible"] is False
    assert report["missing_read_routes"] == ["GET /products/{id}/lineage"]
    assert report["mutation_routes_imported"] is False


def test_skill_client_contains_no_mutation_route_or_method():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in (
        'method="POST"',
        'method="PATCH"',
        'method="DELETE"',
        '"/products/{id}/publish"',
        '"/products/{id}/clone"',
        '"/products/{id}/human-review"',
        '"/products/{id}/audit"',
        '"/products/{id}/events"',
    ):
        assert forbidden not in source
