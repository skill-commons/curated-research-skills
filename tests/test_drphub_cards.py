"""Exercise the installable skill's examples without contacting a remote service."""

import io
import re
import textwrap
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "drphub-cards" / "SKILL.md"


def _block(heading, index=0):
    section = SKILL.read_text().split(heading, 1)[1].split("\n## ", 1)[0]
    return re.findall(r"```python\n(.*?)\n[ \t]*```", section, re.S)[index]


@pytest.fixture
def client(monkeypatch):
    for name in (
        "DRPHUB_BASE",
        "DRPHUB_TOKEN",
        "DRPHUB_SERVICE_TOKEN",
        "DRPHUB_ACTING_USER_ID",
        "DRPHUB_SUPABASE_URL",
        "DRPHUB_SUPABASE_TOKEN",
        "SUPABASE_ANON_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    namespace = {}
    exec(compile(_block("## Helper Function"), str(SKILL), "exec"), namespace)
    calls = []

    def open_request(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(b'{"id":"example","deleted_at":"2026-09-06"}')

    namespace["OPENER"] = SimpleNamespace(open=open_request)
    return namespace, calls


def test_service_auth_requires_actor_and_preserves_retry_identity(client):
    namespace, calls = client
    namespace["SERVICE_TOKEN"] = "fixture-service-token"
    request = namespace["drphub_request"]
    with pytest.raises(ValueError, match="ACTING_USER"):
        request("POST", "/products", body={})
    assert not calls
    namespace["ACTING_USER_ID"] = "fixture-actor"
    for _ in range(2):
        assert (
            request(
                "POST", "/products", body={}, headers_extra={"Idempotency-Key": "same-operation"}
            )["id"]
            == "example"
        )
    for call, timeout in calls:
        assert call.get_header("Authorization") == "Bearer fixture-service-token"
        assert call.get_header("X-acting-user-id") == "fixture-actor"
        assert call.get_header("Idempotency-key") == "same-operation"
        assert call.data == b"{}"
        assert timeout == 30


def test_health_omits_credentials_and_human_review_uses_human_jwt(client):
    namespace, calls = client
    namespace.update(SERVICE_TOKEN="service", TOKEN="human", ACTING_USER_ID="actor")
    namespace["drphub_request"]("GET", "/health")
    assert calls[-1][0].get_header("Authorization") is None
    namespace["drphub_request"]("POST", "/products/example/human-review")
    assert calls[-1][0].get_header("Authorization") == "Bearer human"
    assert calls[-1][0].get_header("X-acting-user-id") is None
    namespace["TOKEN"] = ""
    with pytest.raises(ValueError, match="human's JWT"):
        namespace["drphub_request"]("POST", "/products/example/human-review")


@pytest.mark.parametrize("method,path", [("POST", "/products"), ("DELETE", "/products/id")])
def test_unsupported_dry_run_never_sends_request(client, method, path):
    namespace, calls = client
    with pytest.raises(ValueError, match="Dry run"):
        namespace["drphub_request"](method, path, dry_run=True)
    assert not calls


def test_dry_run_appends_to_existing_query(client):
    namespace, calls = client
    namespace["drphub_request"]("PATCH", "/products/id?include=links", dry_run=True)
    assert calls[0][0].full_url.endswith("?include=links&dry_run=true")


def test_untrusted_destinations_and_auth_override_are_rejected(client):
    namespace, calls = client
    namespace["BASE"] = "http://example.invalid/api/v1"
    with pytest.raises(ValueError, match="HTTPS"):
        namespace["drphub_request"]("GET", "/products")
    namespace["BASE"] = "https://example.invalid/api/v1"
    with pytest.raises(ValueError, match="relative"):
        namespace["drphub_request"]("GET", "//other.invalid/products")
    with pytest.raises(ValueError, match="headers"):
        namespace["drphub_request"]("GET", "/health", headers_extra={"Authorization": "secret"})
    with pytest.raises(ValueError, match="redirects"):
        namespace["NoRedirect"]().redirect_request(None, None, 302, "", {}, "https://other")
    assert not calls


def test_errors_do_not_echo_private_server_bodies(client):
    namespace, _calls = client

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://example.invalid", 403, "private message", {}, io.BytesIO(b"private token")
        )

    namespace["OPENER"].open = fail
    with pytest.raises(RuntimeError, match=r"^DRP Hub PATCH failed \(HTTP 403\)$"):
        namespace["drphub_request"]("PATCH", "/products/id")


def test_delete_readback_example_matches_json_return_contract(client):
    namespace, calls = client
    namespace["product_id"] = "example"
    exec(compile(_block("### Delete Product", index=1), str(SKILL), "exec"), namespace)
    assert [call.get_method() for call, _ in calls] == ["DELETE", "GET"]
    assert namespace["body"]["deleted_at"]


def test_social_listing_is_get_with_separate_credentials(client, monkeypatch):
    namespace, calls = client
    namespace["TOKEN"] = "drp-jwt-not-for-supabase"
    social = _block("## Social Features")
    exec(compile(social.split("# Bookmark a card", 1)[0], str(SKILL), "exec"), namespace)
    with pytest.raises(ValueError, match="own end-user JWT"):
        namespace["supabase_request"]("drp_card_shared_with_me", method="GET")
    assert not calls
    monkeypatch.setenv("DRPHUB_SUPABASE_TOKEN", "supabase-jwt")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    exec(compile(social.split("# List shared", 1)[1], str(SKILL), "exec"), namespace)
    request, timeout = calls[-1]
    assert request.get_method() == "GET"
    assert request.data is None
    assert request.get_header("Authorization") == "Bearer supabase-jwt"
    assert request.get_header("Apikey") == "anon-key"
    assert timeout == 30


def test_search_encodes_query_and_retains_pagination(client):
    namespace, calls = client
    exec(compile(_block("### Search One Page"), str(SKILL), "exec"), namespace)
    namespace["search_public"]("stars & galaxies")
    query = namespace["urllib"].parse.parse_qs(
        namespace["urllib"].parse.urlsplit(calls[-1][0].full_url).query
    )
    assert query == {"visibility": ["public"], "q_in": ["all"], "q": ["stars & galaxies"]}


def test_empty_and_oversized_responses(client):
    namespace, _calls = client
    namespace["OPENER"].open = lambda *a, **kw: io.BytesIO(b"")
    assert namespace["drphub_request"]("DELETE", "/products/id") is None
    namespace["OPENER"].open = lambda *a, **kw: io.BytesIO(b"x" * 2_000_001)
    with pytest.raises(ValueError, match="too large"):
        namespace["drphub_request"]("GET", "/products")


def test_all_python_examples_compile():
    for block in re.findall(r"```python\n(.*?)\n[ \t]*```", SKILL.read_text(), re.S):
        compile(textwrap.dedent(block), str(SKILL), "exec")
