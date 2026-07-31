import base64
import hashlib
import importlib.util
import json
import stat
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "jubik-bootstrap"
BOOTSTRAP_SCRIPT = SKILL / "scripts" / "jubik_bootstrap.py"
SMOKE_SCRIPT = SKILL / "scripts" / "jubik_core_smoke.py"
ASSET = SKILL / "assets" / "jubik-core-environment"
WHEEL = ASSET / "wheels" / "jubik-0.3-py3-none-any.whl"
PROVENANCE = ASSET / "JUBIK-WHEEL-BUILD-PROVENANCE.md"
UPSTREAM_LICENSE = ASSET / "JUBIK-WHEEL-LICENSE.txt"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
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


@pytest.fixture
def bootstrap():
    return _load_module(BOOTSTRAP_SCRIPT, "crs_jubik_bootstrap")


@pytest.fixture
def smoke():
    return _load_module(SMOKE_SCRIPT, "crs_jubik_core_smoke")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_hash(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode("ascii")
    return f"sha256={encoded.rstrip('=')}"


class _FakeDistribution:
    def __init__(self, value):
        self.value = value

    def read_text(self, name):
        assert name == "direct_url.json"
        return json.dumps(self.value)


def _wheel_direct_url(smoke, wheel: Path, **overrides):
    value = {
        "url": wheel.as_uri(),
        "archive_info": {
            "hash": f"sha256={smoke.EXPECTED_WHEEL_SHA256}",
            "hashes": {"sha256": smoke.EXPECTED_WHEEL_SHA256},
        },
    }
    value.update(overrides)
    return value


def test_reviewed_wheel_and_python_312_lock_are_content_bound(smoke) -> None:
    project = tomllib.loads((ASSET / "pyproject.toml").read_text(encoding="utf-8"))
    lock = (ASSET / "uv.lock").read_text(encoding="utf-8")
    provenance = PROVENANCE.read_text(encoding="utf-8")

    assert project["project"]["requires-python"] == ">=3.12,<3.13"
    assert project["tool"]["uv"]["package"] is False
    assert project["tool"]["uv"]["sources"]["jubik"] == {
        "path": "wheels/jubik-0.3-py3-none-any.whl"
    }
    assert _digest(WHEEL) == smoke.EXPECTED_WHEEL_SHA256
    assert smoke.EXPECTED_WHEEL_SHA256 in lock
    assert 'requires-python = "==3.12.*"' in lock
    assert smoke.JUBIK_COMMIT in provenance
    assert smoke.JUBIK_TREE in provenance
    assert smoke.EXPECTED_WHEEL_SHA256 in provenance
    assert "BSD 2-Clause License" in UPSTREAM_LICENSE.read_text(encoding="utf-8")


def test_smoke_accepts_only_the_reviewed_regular_wheel(tmp_path: Path, smoke, monkeypatch) -> None:
    monkeypatch.setattr(smoke.os, "getuid", lambda: WHEEL.stat().st_uid)
    assert smoke._resolve_wheel(str(WHEEL)) == WHEEL.resolve()

    symlink = tmp_path / smoke.EXPECTED_WHEEL_NAME
    symlink.symlink_to(WHEEL)
    with pytest.raises(smoke.SmokeError, match="symlink"):
        smoke._resolve_wheel(str(symlink))

    altered = tmp_path / smoke.EXPECTED_WHEEL_NAME
    altered.unlink()
    altered.write_bytes(WHEEL.read_bytes() + b"drift")
    with pytest.raises(smoke.SmokeError, match="SHA-256"):
        smoke._resolve_wheel(str(altered))


def test_direct_url_must_name_the_staged_wheel_and_exact_hash(tmp_path: Path, smoke) -> None:
    wheel = tmp_path / smoke.EXPECTED_WHEEL_NAME
    wheel.write_bytes(WHEEL.read_bytes())
    expected = wheel.resolve()

    evidence = smoke._read_direct_url(
        _FakeDistribution(_wheel_direct_url(smoke, expected)), expected
    )
    assert evidence["wheel_sha256"] == smoke.EXPECTED_WHEEL_SHA256

    no_recorded_hash = _wheel_direct_url(smoke, expected)
    no_recorded_hash["archive_info"] = {}
    evidence = smoke._read_direct_url(_FakeDistribution(no_recorded_hash), expected)
    assert evidence["wheel_sha256"] == smoke.EXPECTED_WHEEL_SHA256
    assert evidence["direct_url_hash_recorded"] is False

    wrong_hash = _wheel_direct_url(smoke, expected)
    wrong_hash["archive_info"]["hashes"]["sha256"] = "0" * 64
    with pytest.raises(smoke.SmokeError, match="conflicting wheel hashes"):
        smoke._read_direct_url(_FakeDistribution(wrong_hash), expected)

    with pytest.raises(smoke.SmokeError, match="planned local wheel"):
        smoke._read_direct_url(
            _FakeDistribution(
                _wheel_direct_url(smoke, expected, url=f"{expected.as_uri()}?subdirectory=x")
            ),
            expected,
        )

    with pytest.raises(smoke.SmokeError, match="planned local wheel"):
        smoke._read_direct_url(
            _FakeDistribution(
                _wheel_direct_url(smoke, expected, url="file://localhost:444/tmp/jubik.whl")
            ),
            expected,
        )


def test_record_hash_decoder_is_strict(smoke) -> None:
    payload = b"reviewed package bytes\n"
    assert (
        smoke._decode_record_hash(_record_hash(payload), "test")
        == hashlib.sha256(payload).hexdigest()
    )
    with pytest.raises(smoke.SmokeError, match="non-SHA-256"):
        smoke._decode_record_hash("md5=deadbeef", "test")
    with pytest.raises(smoke.SmokeError, match="invalid"):
        smoke._decode_record_hash("sha256=not-base64!", "test")


def test_installed_record_is_anchored_to_reviewed_wheel(tmp_path: Path, smoke, monkeypatch) -> None:
    prefix = tmp_path / "environment"
    prefix.mkdir(mode=0o700)
    with zipfile.ZipFile(WHEEL) as archive:
        archive.extractall(prefix)
        record_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/RECORD")
        )

    class FakeDistribution:
        files = [Path(record_name)]

        @staticmethod
        def locate_file(relative):
            return prefix / str(relative)

    monkeypatch.setattr(smoke.sys, "prefix", str(prefix))
    evidence = smoke.verify_installed_record(FakeDistribution(), WHEEL.resolve())

    assert evidence["verified_installed_files"] > 0
    assert evidence["unexpected_installed_files"] == 0

    package_file = next((prefix / "jubik").glob("*.py"))
    package_file.write_bytes(package_file.read_bytes() + b"\n# drift\n")
    with pytest.raises(smoke.SmokeError, match="size|digest"):
        smoke.verify_installed_record(FakeDistribution(), WHEEL.resolve())


def test_doctor_reports_transport_names_without_secret_values(bootstrap, monkeypatch) -> None:
    secret = "do-not-print-this-proxy-password"
    monkeypatch.setenv("HTTPS_PROXY", f"https://user:{secret}@proxy.invalid")
    monkeypatch.setattr(bootstrap, "_select_python", lambda _raw: Path("/python"))
    monkeypatch.setattr(
        bootstrap,
        "_resolve_executable",
        lambda _raw, _label: Path("/uv"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_probe_python",
        lambda _path: {
            "path": "/python",
            "version": "3.12.4",
            "supported": True,
            "controlled_baseline": True,
        },
    )
    monkeypatch.setattr(
        bootstrap,
        "_probe_uv",
        lambda _path: {"path": "/uv", "version": "0.10.12", "supported": True},
    )
    monkeypatch.setattr(
        bootstrap,
        "_platform_probe",
        lambda: {"system": "linux", "machine": "x86_64", "supported": True},
    )

    report = bootstrap.doctor(None, None)
    serialized = json.dumps(report)

    assert report["ready_to_plan"] is True
    assert "HTTPS_PROXY" in report["transport_environment_names_present"]
    assert secret not in serialized


def test_doctor_returns_structured_failure_when_uv_is_missing(bootstrap, monkeypatch) -> None:
    monkeypatch.setattr(bootstrap, "_select_python", lambda _raw: Path("/python"))
    monkeypatch.setattr(
        bootstrap,
        "_probe_python",
        lambda _path: {
            "path": "/python",
            "version": "3.12.4",
            "supported": True,
            "controlled_baseline": True,
        },
    )
    monkeypatch.setattr(
        bootstrap,
        "_resolve_executable",
        lambda _raw, _label: (_ for _ in ()).throw(
            bootstrap.BootstrapError("uv executable was not found")
        ),
    )

    report = bootstrap.doctor(None, None)

    assert report["ready_to_plan"] is False
    assert report["checks"]["supported_uv"] is False
    assert report["uv"]["error"] == "uv executable was not found"


def test_plan_failure_preserves_safe_doctor_evidence(
    tmp_path: Path, bootstrap, monkeypatch
) -> None:
    tmp_path.chmod(0o700)
    report = {
        "schema_version": 2,
        "action": "doctor",
        "ready_to_plan": False,
        "checks": {
            "supported_python": True,
            "supported_uv": False,
            "supported_platform": True,
            "bundled_assets_valid": True,
        },
        "uv": {"error": "uv executable has an unsafe owner"},
        "transport_environment_names_present": ["HTTPS_PROXY"],
    }
    monkeypatch.setattr(bootstrap, "doctor", lambda _python, _uv: report)

    with pytest.raises(bootstrap.BootstrapError) as caught:
        bootstrap.create_plan(
            str(tmp_path / "environment"),
            str(tmp_path / "plan.json"),
            None,
            None,
        )

    assert caught.value.category == "doctor_failed"
    assert caught.value.evidence == {"doctor": report}
    assert caught.value.as_report()["evidence"]["doctor"]["checks"]["supported_uv"] is False


def test_parser_has_persisted_plan_create_flow_and_no_verify(bootstrap) -> None:
    parser = bootstrap.build_parser()
    action = next(
        item for item in parser._actions if item.__class__.__name__ == "_SubParsersAction"
    )
    assert set(action.choices) == {"doctor", "plan", "create"}

    plan_args = parser.parse_args(
        [
            "plan",
            "--environment",
            "/tmp/jubik-environment",
            "--output",
            "/tmp/jubik-plan.json",
        ]
    )
    assert plan_args.output == "/tmp/jubik-plan.json"
    create_args = parser.parse_args(
        ["create", "--plan", "/tmp/jubik-plan.json", "--confirm", "a" * 64]
    )
    assert create_args.plan == "/tmp/jubik-plan.json"
    with pytest.raises(SystemExit):
        parser.parse_args(["verify", "--environment", "/tmp/jubik-environment"])


def _doctor_report(bootstrap, python: Path, uv: Path) -> dict:
    return {
        "schema_version": 2,
        "action": "doctor",
        "ready_to_plan": True,
        "checks": {
            "supported_python": True,
            "supported_uv": True,
            "supported_platform": True,
            "bundled_assets_valid": True,
        },
        "python": {
            **bootstrap._file_binding(python),
            "version": "3.12.4",
            "supported": True,
        },
        "uv": {
            **bootstrap._file_binding(uv),
            "version": "0.10.12",
            "supported": True,
        },
        "host": {
            "system": "linux",
            "machine": "x86_64",
            "os_version": "",
            "libc_name": "glibc",
            "libc_version": "2.40",
            "supported": True,
            "reason": None,
        },
        "assets": bootstrap._asset_bindings(),
        "network_checked": False,
        "secrets_required": False,
        "transport_environment_names_present": [],
    }


def test_persisted_plan_binds_executables_assets_commands_and_boundaries(
    tmp_path: Path, bootstrap, monkeypatch
) -> None:
    python = tmp_path / "python3.12"
    uv = tmp_path / "uv"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    uv.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o700)
    uv.chmod(0o700)
    report = _doctor_report(bootstrap, python, uv)
    monkeypatch.setattr(bootstrap, "doctor", lambda _python, _uv: report)

    plan_path = tmp_path / "install-plan.json"
    plan = bootstrap.create_plan(
        str(tmp_path / "jubik-environment"), str(plan_path), str(python), str(uv)
    )
    unsigned = dict(plan)
    claimed = unsigned.pop("plan_sha256")

    assert claimed == hashlib.sha256(bootstrap._canonical_json(unsigned)).hexdigest()
    assert plan["schema_version"] == 2
    assert plan["paths"]["plan"] == str(plan_path)
    assert plan["python"] == report["python"]
    assert plan["uv"] == report["uv"]
    for binding in (plan["python"], plan["uv"]):
        assert {
            "path",
            "sha256",
            "size",
            "owner_uid",
            "mode",
            "device",
            "inode",
        } <= set(binding)
    assert plan["assets"] == report["assets"]
    assert set(plan["assets"]) == set(bootstrap.ASSET_LAYOUT)
    assert plan["sources"]["jubik_commit"] == bootstrap.JUBIK_COMMIT
    assert plan["sources"]["jubik_wheel_sha256"] == bootstrap.JUBIK_WHEEL_SHA256
    assert plan["secrets"] == {
        "required": False,
        "stored": False,
        "forwarded_transport_environment_names": [],
    }
    assert plan["build_policy"] == {
        "source_builds_allowed": False,
        "bundled_jubik_wheel": True,
    }
    assert plan["instrument_adapters_in_scope"] == []
    assert plan["scientific_research_ready"] is False

    sync = plan["commands"]["sync"]
    assert "--frozen" in sync
    assert "--no-build" in sync
    assert "--no-config" in sync
    assert "--no-python-downloads" in sync
    assert sync[sync.index("--default-index") + 1] == "https://pypi.org/simple"
    assert plan["commands"]["sync_check"][-2:] == ["--check", "--offline"]
    assert plan["commands"]["smoke"][1:3] == ["-I", "-B"]

    saved = bootstrap.save_plan(plan)
    assert saved == plan_path
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    loaded_path, loaded = bootstrap.load_plan(str(saved))
    assert loaded_path == plan_path
    assert loaded == plan

    value = json.loads(saved.read_text(encoding="utf-8"))
    value["paths"]["environment"] = str(tmp_path / "different")
    saved.write_text(json.dumps(value), encoding="utf-8")
    saved.chmod(0o600)
    with pytest.raises(bootstrap.BootstrapError, match="digest"):
        bootstrap.load_plan(str(saved))


def test_untrusted_executable_and_confirmation_fail_before_create(
    tmp_path: Path, bootstrap, monkeypatch
) -> None:
    executable = tmp_path / "uv"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o722)
    with pytest.raises(bootstrap.BootstrapError, match="group/world writable"):
        bootstrap._resolve_executable(str(executable), "uv")

    monkeypatch.setattr(
        bootstrap,
        "_stage_assets",
        lambda *_args: pytest.fail("staging must not start"),
    )
    with pytest.raises(bootstrap.BootstrapError, match="exactly match"):
        bootstrap.apply_plan(tmp_path / "plan.json", {"plan_sha256": "a" * 64}, "b" * 64)


def test_transport_values_reach_only_the_fetch_environment(
    tmp_path: Path, bootstrap, monkeypatch
) -> None:
    secret = "transport-value-not-for-scientific-code"
    monkeypatch.setenv("HTTPS_PROXY", f"https://user:{secret}@proxy.invalid")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-forwarded")

    fetch = bootstrap._child_environment(forward_transport=True, temporary_directory=tmp_path)
    runtime = bootstrap._child_environment(temporary_directory=tmp_path)

    assert fetch["HTTPS_PROXY"].endswith("@proxy.invalid")
    assert "HTTPS_PROXY" not in runtime
    assert "UNRELATED_SECRET" not in fetch
    assert "UNRELATED_SECRET" not in runtime
    assert secret not in json.dumps(
        {
            "transport_environment_names_present": sorted(
                name for name in bootstrap.TRANSPORT_ENV_NAMES if name in fetch and fetch[name]
            )
        }
    )


def test_environment_and_plan_paths_are_rejected_inside_git_worktrees(
    tmp_path: Path, bootstrap
) -> None:
    checkout = tmp_path / "checkout"
    parent = checkout / "private"
    parent.mkdir(parents=True, mode=0o700)
    (checkout / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")

    with pytest.raises(bootstrap.BootstrapError, match="Git worktree"):
        bootstrap._resolve_new_path(str(parent / "environment"), "environment")
    with pytest.raises(bootstrap.BootstrapError, match="Git worktree"):
        bootstrap._resolve_new_path(str(parent / "plan.json"), "plan")


def test_platform_surface_is_narrow_and_enforces_native_floors(bootstrap, monkeypatch) -> None:
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(bootstrap.platform, "mac_ver", lambda: ("11.7.10", (), ""))
    assert bootstrap._platform_probe()["supported"] is False
    monkeypatch.setattr(bootstrap.platform, "mac_ver", lambda: ("12.0", (), ""))
    assert bootstrap._platform_probe()["supported"] is True

    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(bootstrap.platform, "libc_ver", lambda: ("glibc", "2.40"))
    assert bootstrap._platform_probe()["supported"] is False
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(bootstrap.platform, "libc_ver", lambda: ("glibc", "2.26"))
    assert bootstrap._platform_probe()["supported"] is False
    monkeypatch.setattr(bootstrap.platform, "libc_ver", lambda: ("glibc", "2.27"))
    assert bootstrap._platform_probe()["supported"] is True


def test_install_failures_are_classified_redacted_and_bounded(bootstrap, monkeypatch) -> None:
    secret = "proxy-password-must-not-survive"
    monkeypatch.setenv("HTTPS_PROXY", f"https://user:{secret}@proxy.invalid")
    raw = "x" * (bootstrap.MAX_CAPTURE_BYTES * 2) + (
        f" HTTP 407 https://user:{secret}@proxy.invalid"
    )
    category, message = bootstrap._classify_install_failure(raw)
    raw_bytes = raw.encode()
    completed = bootstrap._ProcessResult(
        returncode=1,
        stdout=raw[-bootstrap.MAX_CAPTURE_BYTES :],
        stderr=raw[-bootstrap.MAX_CAPTURE_BYTES :],
        stdout_bytes=len(raw_bytes),
        stderr_bytes=len(raw_bytes),
        stdout_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        stderr_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )
    evidence = bootstrap._process_evidence("uv_sync", completed)

    assert category == "proxy_authentication_failed"
    assert "407" in message
    assert "proxy_authentication" in evidence["diagnostic_markers"]
    assert secret not in json.dumps(evidence)
    assert len(evidence["stdout_tail"].encode()) <= bootstrap.MAX_EVIDENCE_CHARS
    assert len(evidence["stderr_tail"].encode()) <= bootstrap.MAX_EVIDENCE_CHARS


def _valid_report(bootstrap) -> dict:
    report = {
        "schema_version": 2,
        "core_ready": True,
        "scientific_research_ready": False,
        "checks": {"locked_versions_exact": True, "installed_record_verified": True},
        "adapters": {
            "jwst_adapter_ready": False,
            "chandra_adapter_ready": False,
            "erosita_adapter_ready": False,
        },
        "source": {
            "wheel_sha256": bootstrap.JUBIK_WHEEL_SHA256,
            "upstream_commit": bootstrap.JUBIK_COMMIT,
        },
    }
    report["content_sha256"] = hashlib.sha256(bootstrap._canonical_json(report)).hexdigest()
    return report


def test_core_report_schema_fails_closed_on_readiness_drift(tmp_path: Path, bootstrap) -> None:
    report_path = tmp_path / "report.json"
    valid = _valid_report(bootstrap)
    report_path.write_text(json.dumps(valid), encoding="utf-8")
    report_path.chmod(0o600)
    assert bootstrap._load_report(report_path) == valid

    for mutate in (
        lambda report: report["checks"].update({"installed_record_verified": False}),
        lambda report: report["adapters"].update({"jwst_adapter_ready": True}),
        lambda report: report["source"].update({"upstream_commit": "0" * 40}),
        lambda report: report.update({"scientific_research_ready": True}),
    ):
        changed = _valid_report(bootstrap)
        mutate(changed)
        changed.pop("content_sha256")
        changed["content_sha256"] = hashlib.sha256(bootstrap._canonical_json(changed)).hexdigest()
        report_path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(bootstrap.BootstrapError, match="bounded core readiness"):
            bootstrap._load_report(report_path)


def test_state_persists_complete_plan_and_keeps_all_boundaries_false(
    tmp_path: Path, bootstrap
) -> None:
    environment = tmp_path / "environment"
    environment.mkdir(mode=0o700)
    plan = {
        "plan_sha256": "a" * 64,
        "paths": {
            "state": str(environment / "crs-jubik-bootstrap-state.json"),
            "report": str(environment / "crs-jubik-core-report.json"),
            "cache": str(tmp_path / "cache"),
            "provenance_assets": str(environment / ".crs-jubik-provenance"),
        },
        "commands": {"sync": ["uv", "sync", "--frozen", "--no-build"]},
        "scientific_research_ready": False,
        "instrument_adapters_in_scope": [],
    }
    report = _valid_report(bootstrap)
    evidence = {"uv_sync": {"returncode": 0}, "smoke": {"returncode": 0}}

    state_path = bootstrap._write_state(plan, report, evidence)
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert state["plan"] == plan
    assert state["plan_sha256"] == plan["plan_sha256"]
    assert state["process_evidence"] == evidence
    assert state["scientific_research_ready"] is False
    assert not any(state["adapters"].values())
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_skill_declares_core_boundary_secrets_and_every_support_asset() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    assert "core_ready" in text
    assert "JWST, Chandra, and eROSITA readiness" in text
    assert "scientific_research_ready" in text
    assert "Never put a secret in chat" in text
    assert "no independent `verify`" in text
    for relative in (
        "scripts/jubik_bootstrap.py",
        "scripts/jubik_core_smoke.py",
        "references/readiness-and-troubleshooting.md",
        "assets/jubik-core-environment/pyproject.toml",
        "assets/jubik-core-environment/uv.lock",
        "assets/jubik-core-environment/wheels/jubik-0.3-py3-none-any.whl",
        "assets/jubik-core-environment/JUBIK-WHEEL-BUILD-PROVENANCE.md",
        "assets/jubik-core-environment/JUBIK-WHEEL-LICENSE.txt",
    ):
        assert f"]({relative})" in text
