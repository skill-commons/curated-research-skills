import importlib.util
import json
import os
import sys
from contextlib import suppress
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OPERATOR_SCRIPT = ROOT / "skills" / "reana-operator" / "scripts" / "reana_operator.py"
EXPECTED_TOKEN = "synthetic-reana-token-for-tests"
EXPECTED_SERVER = "https://reana.example.org"


def _load_operator():
    spec = importlib.util.spec_from_file_location("crs_reana_operator", OPERATOR_SCRIPT)
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


def _write_fake_client(tmp_path: Path) -> Path:
    client = tmp_path / "reana-client"
    client.write_text(
        f"""#!{sys.executable}
import json
import os
import sys

arguments = sys.argv[1:]
if arguments == ["version"]:
    print(json.dumps({{
        "argv": arguments,
        "env_keys": sorted(os.environ),
        "server_present": "REANA_SERVER_URL" in os.environ,
        "token_present": "REANA_ACCESS_TOKEN" in os.environ,
    }}, sort_keys=True))
    raise SystemExit(0)

read_only = {{"ping", "info", "list", "status", "logs", "ls", "du"}}
if not arguments or arguments[0] not in read_only:
    print("unexpected mutating or unknown command", file=sys.stderr)
    raise SystemExit(91)

token = os.environ.get("REANA_ACCESS_TOKEN", "")
print(f"access_token={{token}}")
print("Authorization: Bearer abcdefghijklmnop")
print("eyJabcdefgh.ijklmnop.qrstuvwx")
print("operator@example.org")
print(json.dumps({{
    "argv": arguments,
    "env_keys": sorted(os.environ),
    "home": os.environ.get("HOME"),
    "server": os.environ.get("REANA_SERVER_URL"),
    "token_matches": token == {EXPECTED_TOKEN!r},
    "unrelated_secret": os.environ.get("UNRELATED_SECRET"),
}}, sort_keys=True))
print(f"password={{token}}", file=sys.stderr)
""",
        encoding="utf-8",
    )
    client.chmod(0o755)
    return client


def _configure_environment(monkeypatch: pytest.MonkeyPatch, client: Path) -> None:
    monkeypatch.setenv("REANA_CLIENT_BIN", str(client))
    monkeypatch.setenv("REANA_SERVER_URL", EXPECTED_SERVER)
    monkeypatch.setenv("REANA_ACCESS_TOKEN", EXPECTED_TOKEN)
    monkeypatch.setenv("REANA_ALLOWED_SERVER_ORIGINS", EXPECTED_SERVER)
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-reach-client")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("LC_ALL", "C")
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"):
        monkeypatch.delenv(name, raising=False)


def _json_objects(output: str) -> list[dict]:
    objects = []
    for line in output.splitlines():
        opening = line.find("{")
        if opening >= 0:
            with suppress(json.JSONDecodeError):
                objects.append(json.loads(line[opening:]))
    return objects


def test_parser_exposes_only_the_read_only_allowlist() -> None:
    module = _load_operator()
    parser = module.build_parser()
    subparser_action = next(
        action for action in parser._actions if action.__class__.__name__ == "_SubParsersAction"
    )
    assert set(subparser_action.choices) == {
        "doctor",
        "ping",
        "info",
        "list",
        "status",
        "logs",
        "files",
        "usage",
    }


@pytest.mark.parametrize(
    "operation",
    [
        "create",
        "upload",
        "run",
        "start",
        "restart",
        "stop",
        "delete",
        "download",
        "rm",
        "mv",
        "prune",
        "open",
        "close",
        "secrets-add",
        "share-add",
    ],
)
def test_parser_rejects_mutating_operations(operation: str) -> None:
    parser = _load_operator().build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([operation])


def test_parser_rejects_passthrough_client_options() -> None:
    parser = _load_operator().build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["ping", "--access-token", "must-not-be-accepted"])
    with pytest.raises(SystemExit):
        parser.parse_args(["status", "--workflow", "safe", "--force"])


@pytest.mark.parametrize(
    ("arguments", "expected_native_arguments"),
    [
        (["ping"], ["ping"]),
        (["info"], ["info", "--json"]),
        (
            ["list", "--limit", "7"],
            ["list", "--json", "--page", "1", "--size", "7"],
        ),
        (
            ["status", "--workflow", "analysis.42"],
            ["status", "--workflow", "analysis.42", "--json"],
        ),
        (
            ["logs", "--workflow", "analysis.42", "--limit", "8"],
            [
                "logs",
                "--workflow",
                "analysis.42",
                "--json",
                "--page",
                "1",
                "--size",
                "8",
            ],
        ),
        (
            ["files", "--workflow", "analysis.42", "--limit", "9"],
            [
                "ls",
                "--workflow",
                "analysis.42",
                "--json",
                "--page",
                "1",
                "--size",
                "9",
            ],
        ),
        (
            ["usage", "--workflow", "analysis.42"],
            ["du", "--workflow", "analysis.42", "--summarize"],
        ),
    ],
)
def test_fake_client_receives_exact_argv_and_restricted_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    expected_native_arguments: list[str],
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)

    assert module.main(arguments) == 0
    captured = capsys.readouterr()
    payloads = _json_objects(captured.out)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["argv"] == expected_native_arguments
    assert payload["server"] == EXPECTED_SERVER
    assert payload["token_matches"] is True
    assert payload["unrelated_secret"] is None
    assert set(payload["env_keys"]) <= {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "REANA_ACCESS_TOKEN",
        "REANA_SERVER_URL",
        "__CF_USER_TEXT_ENCODING",
    }
    assert {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "REANA_ACCESS_TOKEN",
        "REANA_SERVER_URL",
    } <= set(payload["env_keys"])
    assert Path(payload["home"]).name.startswith("crs-reana-home-")
    assert not Path(payload["home"]).exists()


def test_child_environment_is_constructed_from_an_exact_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)
    home = str(tmp_path / "isolated-home")

    assert module._child_environment(home, EXPECTED_SERVER, EXPECTED_TOKEN) == {
        "HOME": home,
        "PATH": os.environ.get("PATH", os.defpath),
        "REANA_SERVER_URL": EXPECTED_SERVER,
        "REANA_ACCESS_TOKEN": EXPECTED_TOKEN,
        "LANG": "C",
        "LC_ALL": "C",
    }


def test_doctor_invokes_only_local_version_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)

    assert module.main(["doctor"]) == 0
    captured = capsys.readouterr()
    payloads = _json_objects(captured.out)
    assert len(payloads) == 1
    assert payloads[0]["argv"] == ["version"]
    assert payloads[0]["server_present"] is False
    assert payloads[0]["token_present"] is False
    assert set(payloads[0]["env_keys"]) <= {
        "HOME",
        "LC_CTYPE",
        "PATH",
        "__CF_USER_TEXT_ENCODING",
    }


def test_missing_credentials_fail_before_client_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    monkeypatch.setenv("REANA_CLIENT_BIN", str(client))
    monkeypatch.delenv("REANA_SERVER_URL", raising=False)
    monkeypatch.delenv("REANA_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("REANA_ALLOWED_SERVER_ORIGINS", raising=False)

    assert module.main(["ping"]) == 2
    captured = capsys.readouterr()
    assert "REANA_SERVER_URL" in captured.err
    assert "REANA_ACCESS_TOKEN" in captured.err
    assert "REANA_ALLOWED_SERVER_ORIGINS" in captured.err
    assert not _json_objects(captured.out)


def test_remote_output_redacts_tokens_and_common_secret_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)

    assert module.main(["logs", "--workflow", "analysis.42", "--limit", "2"]) == 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert EXPECTED_TOKEN not in combined
    assert "abcdefghijk" not in combined
    assert "operator@example.org" not in combined
    assert "[REDACTED]" in combined
    assert "[REDACTED_JWT]" in combined
    assert "[REDACTED_EMAIL]" in combined


def test_json_envelope_keeps_the_complete_wrapper_stdout_machine_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)

    assert (
        module.main(
            [
                "--json-envelope",
                "logs",
                "--workflow",
                "analysis.42",
                "--limit",
                "2",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert captured.err == ""
    assert report["server"] == EXPECTED_SERVER
    assert report["operation"] == "logs"
    assert report["workflow"] == "analysis.42"
    assert report["native_exit_code"] == 0
    assert report["truncated"] is False
    assert EXPECTED_TOKEN not in captured.out
    assert "abcdefghijk" not in captured.out
    assert "[REDACTED]" in report["stdout"]


def test_redaction_preserves_json_and_removes_complete_url_userinfo() -> None:
    module = _load_operator()
    source = json.dumps(
        {
            "access_token": "another-synthetic-secret",
            "message": f"access_token={EXPECTED_TOKEN}",
            "structured_secret": "password=hunter2",
            "url": "https://demo-user:demo-pass@storage.example.org/input",
        }
    )

    redacted = module._redact(source, EXPECTED_TOKEN)
    parsed = json.loads(redacted)

    assert parsed["access_token"] == "[REDACTED]"
    assert parsed["message"] == "access_token=[REDACTED]"
    assert parsed["structured_secret"] == "password=[REDACTED]"
    assert parsed["url"] == "https://[REDACTED]@storage.example.org/input"
    assert "demo-user" not in redacted
    assert "demo-pass" not in redacted


def test_redaction_happens_before_the_output_boundary_is_truncated() -> None:
    module = _load_operator()
    prefix = b"x" * (module.MAX_CAPTURE_BYTES - len(EXPECTED_TOKEN.encode()) + 1)

    bounded, truncated = module._bounded(prefix + EXPECTED_TOKEN.encode(), EXPECTED_TOKEN)

    assert truncated is True
    assert EXPECTED_TOKEN not in bounded
    assert EXPECTED_TOKEN[:-1] not in bounded


def test_exact_token_redaction_precedes_overlapping_generic_redaction() -> None:
    module = _load_operator()
    token = "abcdefghijklmnopqrstuvwxsecret=A"

    bounded, truncated = module._bounded(token.encode(), token)

    assert truncated is False
    assert bounded == "[REDACTED_TOKEN]"
    assert "abcdefghijklmnopqrstuvwx" not in bounded


def test_subprocess_capture_is_bounded_while_the_child_is_running() -> None:
    module = _load_operator()
    command = [
        sys.executable,
        "-c",
        f"import os; os.write(1, b'x' * ({module.MAX_RAW_CAPTURE_BYTES} + 65536))",
    ]

    completed, stdout_overflow, stderr_overflow = module._run_bounded(
        command,
        env={"PATH": os.environ.get("PATH", os.defpath)},
        timeout=10,
    )
    bounded, truncated = module._bounded(
        completed.stdout,
        source_truncated=stdout_overflow,
    )

    assert completed.returncode == 0
    assert len(completed.stdout) == module.MAX_RAW_CAPTURE_BYTES
    assert completed.stderr == b""
    assert stdout_overflow is True
    assert stderr_overflow is False
    assert bounded == "[OUTPUT OMITTED: capture limit exceeded]"
    assert truncated is True


def test_native_client_cannot_fork_or_detach_a_credential_bearing_child() -> None:
    module = _load_operator()
    command = [
        sys.executable,
        "-c",
        (
            "import os\n"
            "try:\n"
            "    os.fork()\n"
            "except OSError:\n"
            "    print('fork-blocked')\n"
            "    raise SystemExit(0)\n"
            "print('fork-succeeded')\n"
            "raise SystemExit(91)\n"
        ),
    ]

    completed, stdout_overflow, stderr_overflow = module._run_bounded(
        command,
        env={
            "PATH": os.environ.get("PATH", os.defpath),
            "REANA_ACCESS_TOKEN": EXPECTED_TOKEN,
        },
        timeout=10,
    )

    assert completed.returncode == 0
    assert completed.stdout == b"fork-blocked\n"
    assert completed.stderr == b""
    assert stdout_overflow is False
    assert stderr_overflow is False


def test_process_containment_fails_closed_for_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_operator()
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)

    with pytest.raises(module.OperatorError, match="not enforceable"):
        module._run_bounded(
            [sys.executable, "-c", "raise SystemExit(0)"],
            env={"PATH": os.environ.get("PATH", os.defpath)},
            timeout=10,
        )


def test_malformed_nfkc_origin_never_echoes_embedded_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)
    secret = "hunter2"
    monkeypatch.setenv(
        "REANA_SERVER_URL",
        f"https://user:{secret}＠reana.example.org",
    )

    assert module.main(["--json-envelope", "ping"]) == 2
    captured = capsys.readouterr()
    assert "malformed HTTPS origin" in captured.err
    assert secret not in captured.out + captured.err


@pytest.mark.parametrize(
    "server",
    [
        "http://reana.example.org",
        "https://user:hunter2@reana.example.org",
        "https://reana.example.org/api",
        "https://reana.example.org?debug=true",
        "https://reana.example.org#fragment",
        "https://reana.example.org:99999",
    ],
)
def test_invalid_server_url_fails_closed_before_client_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    server: str,
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)
    monkeypatch.setenv("REANA_SERVER_URL", server)

    assert module.main(["ping"]) == 2
    captured = capsys.readouterr()
    assert "ERROR:" in captured.err
    assert "hunter2" not in captured.err
    assert not _json_objects(captured.out)


def test_server_must_match_an_exact_allowlisted_origin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)
    monkeypatch.setenv("REANA_ALLOWED_SERVER_ORIGINS", "https://other.example.org")

    assert module.main(["ping"]) == 2
    captured = capsys.readouterr()
    assert "not in REANA_ALLOWED_SERVER_ORIGINS" in captured.err
    assert not _json_objects(captured.out)


def test_origin_matching_normalizes_case_root_slash_and_default_https_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)
    monkeypatch.setenv("REANA_SERVER_URL", "https://REANA.EXAMPLE.ORG:443/")
    monkeypatch.setenv(
        "REANA_ALLOWED_SERVER_ORIGINS",
        "https://other.example.org, https://reana.example.org",
    )

    assert module.main(["ping"]) == 0
    captured = capsys.readouterr()
    payload = _json_objects(captured.out)[0]
    assert payload["server"] == EXPECTED_SERVER


def test_allowlist_rejects_wildcards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)
    monkeypatch.setenv("REANA_ALLOWED_SERVER_ORIGINS", "https://*.example.org")

    assert module.main(["ping"]) == 2
    captured = capsys.readouterr()
    assert "does not permit wildcards" in captured.err
    assert not _json_objects(captured.out)


def test_read_operation_does_not_mutate_the_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_operator()
    client = _write_fake_client(tmp_path)
    _configure_environment(monkeypatch, client)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = workspace / "sentinel.txt"
    sentinel.write_text("unchanged\n", encoding="utf-8")
    before = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    monkeypatch.chdir(workspace)

    assert module.main(["status", "--workflow", "analysis.42"]) == 0
    capsys.readouterr()
    after = {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }
    assert after == before
