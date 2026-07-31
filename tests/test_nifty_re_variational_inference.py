import base64
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "nifty-re-variational-inference"
SCRIPT = SKILL / "scripts" / "nifty_re_linear_gaussian.py"
ASSET = SKILL / "assets" / "nifty-re-certification-environment"
INTEGRATION_PYTHON = os.environ.get("CRS_NIFTY_RE_PYTHON")
INTEGRATION_ENABLED = os.environ.get("CRS_NIFTY_RE_INTEGRATION") == "1"


def _load_module():
    spec = importlib.util.spec_from_file_location("crs_nifty_re_benchmark", SCRIPT)
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
def benchmark():
    return _load_module()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_hash(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode("ascii")
    return f"sha256={encoded.rstrip('=')}"


def test_certification_configuration_is_fixed(benchmark) -> None:
    assert benchmark.CERTIFICATION_SEED == 42
    assert benchmark.CERTIFICATION_SAMPLE_PAIRS == 128

    parsed = benchmark.build_parser().parse_args(
        ["--output", "/tmp/report.json", "--cache-dir", "/tmp/cache"]
    )
    assert parsed.output == "/tmp/report.json"
    assert parsed.cache_dir == "/tmp/cache"
    with pytest.raises(SystemExit):
        benchmark.build_parser().parse_args(
            [
                "--output",
                "/tmp/report.json",
                "--cache-dir",
                "/tmp/cache",
                "--seed",
                "7",
            ]
        )


def test_frozen_python_312_asset_matches_reviewed_hashes(benchmark) -> None:
    project = tomllib.loads((ASSET / "pyproject.toml").read_text(encoding="utf-8"))
    lock_text = (ASSET / "uv.lock").read_text(encoding="utf-8")

    assert project["project"]["requires-python"] == "==3.12.*"
    assert set(project["project"]["dependencies"]) == {
        "ducc0==0.41.0",
        "jax==0.11.0",
        "jaxbind==1.3.1",
        "jaxlib==0.11.0",
        "nifty[re]==9.2.0",
        "numpy==2.5.1",
        "scipy==1.18.0",
    }
    assert _digest(ASSET / "pyproject.toml") == benchmark.REVIEWED_PYPROJECT_SHA256
    assert _digest(ASSET / "uv.lock") == benchmark.REVIEWED_LOCK_SHA256
    assert f"sha256:{benchmark.NIFTY_WHEEL_SHA256}" in lock_text
    assert 'requires-python = "==3.12.*"' in lock_text


def test_nonfinite_values_are_sanitized_and_digest_is_verifiable(benchmark) -> None:
    report = benchmark._finalize_report(
        {
            "validation_passed": False,
            "checks": {"all_values_finite": False},
            "values": [float("nan"), float("inf"), float("-inf")],
        }
    )

    assert report["values"] == [
        "nonfinite:nan",
        "nonfinite:positive-infinity",
        "nonfinite:negative-infinity",
    ]
    assert report["serialization"]["nonfinite_values_replaced"] == 3
    assert benchmark._canonical_payload_digest_is_valid(report)
    json.dumps(report, allow_nan=False)

    report["checks"]["all_values_finite"] = True
    assert not benchmark._canonical_payload_digest_is_valid(report)


def test_report_writer_is_exclusive_private_and_verifiable(tmp_path: Path, benchmark) -> None:
    output = benchmark._resolve_new_output(str(tmp_path / "report.json"))
    report = benchmark._finalize_report({"validation_passed": True})
    benchmark._write_report(output, report)

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert benchmark._verify_report(output)["canonical_payload_digest_valid"] is True
    with pytest.raises(FileExistsError):
        benchmark._write_report(output, benchmark._finalize_report({"changed": True}))


def test_report_verifier_detects_tampering(tmp_path: Path, benchmark) -> None:
    output = tmp_path / "report.json"
    report = benchmark._finalize_report({"validation_passed": True, "metric": 1.0})
    benchmark._write_report(output, report)
    parsed = json.loads(output.read_text(encoding="utf-8"))
    parsed["metric"] = 2.0
    output.write_text(json.dumps(parsed), encoding="utf-8")

    summary = benchmark._verify_report(output)
    assert summary["canonical_payload_digest_valid"] is False
    assert len(summary["report_file_sha256"]) == 64


def test_private_cache_is_new_external_and_mode_0700(tmp_path: Path, benchmark) -> None:
    cache = benchmark._create_private_cache(str(tmp_path / "jax-cache"))

    assert cache.is_dir()
    assert stat.S_IMODE(cache.stat().st_mode) == 0o700
    with pytest.raises(benchmark.WorkflowError):
        benchmark._create_private_cache(str(cache))
    with pytest.raises(benchmark.WorkflowError):
        benchmark._resolve_new_output(str(ROOT / "must-not-be-written.json"))


def test_outputs_are_rejected_inside_any_git_worktree(tmp_path: Path, benchmark) -> None:
    checkout = tmp_path / "other-checkout"
    output_parent = checkout / "results"
    output_parent.mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")

    with pytest.raises(benchmark.WorkflowError, match="Git worktree"):
        benchmark._resolve_new_output(str(output_parent / "report.json"))


def test_runtime_environment_is_allowlisted_before_scientific_imports(
    benchmark, monkeypatch
) -> None:
    secret = "not-for-scientific-package-code"
    monkeypatch.setenv("HTTPS_PROXY", secret)
    monkeypatch.setenv("JAX_DISABLE_JIT", "1")
    monkeypatch.setenv("XLA_FLAGS", "--unexpected")
    monkeypatch.setenv("CRS_UNRELATED_TEST_VALUE", "retained")

    removed = benchmark._sanitize_runtime_environment()

    assert {"HTTPS_PROXY", "JAX_DISABLE_JIT", "XLA_FLAGS"}.issubset(removed)
    assert "HTTPS_PROXY" not in os.environ
    assert os.environ["JAX_PLATFORMS"] == "cpu"
    assert os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"] == "false"
    assert os.environ["CRS_UNRELATED_TEST_VALUE"] == "retained"


def test_private_clean_runtime_environment_is_attested(
    tmp_path: Path, benchmark, monkeypatch
) -> None:
    home = tmp_path / "home"
    temporary = tmp_path / "tmp"
    for path in (home, temporary):
        path.mkdir(mode=0o700)
        path.chmod(0o700)

    for name in tuple(os.environ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temporary))
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")

    evidence = benchmark._runtime_environment_attestation()
    assert evidence["allowlisted_environment"] is True
    assert evidence["recorded_environment_value_names"] == ["HOME", "TMPDIR"]
    assert evidence["transport_and_control_values_recorded"] is False
    assert evidence["home"]["mode"] == "0700"
    assert evidence["temporary_directory"]["mode"] == "0700"

    monkeypatch.setenv("CRS_UNRELATED_TEST_VALUE", "must-be-rejected")
    with pytest.raises(benchmark.WorkflowError, match="unexpected variable names"):
        benchmark._runtime_environment_attestation()
    monkeypatch.delenv("CRS_UNRELATED_TEST_VALUE")

    home.chmod(0o755)
    with pytest.raises(benchmark.WorkflowError, match="HOME mode must be 0700"):
        benchmark._runtime_environment_attestation()


def test_runtime_environment_is_rejected_inside_git_worktree(
    tmp_path: Path, benchmark, monkeypatch
) -> None:
    (tmp_path / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    home = tmp_path / "home"
    temporary = tmp_path / "tmp"
    for path in (home, temporary):
        path.mkdir(mode=0o700)
        path.chmod(0o700)
    for name in tuple(os.environ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(temporary))
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")

    with pytest.raises(benchmark.WorkflowError, match="outside every Git worktree"):
        benchmark._runtime_environment_attestation()


def test_reviewed_platform_enforces_reported_abi_floors(benchmark, monkeypatch) -> None:
    monkeypatch.setattr(benchmark.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(benchmark.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(benchmark.platform, "mac_ver", lambda: ("11.7.10", (), ""))
    assert benchmark._reviewed_platform_abi() is False
    monkeypatch.setattr(benchmark.platform, "mac_ver", lambda: ("12.0", (), ""))
    assert benchmark._reviewed_platform_abi() is True

    monkeypatch.setattr(benchmark.platform, "system", lambda: "Linux")
    monkeypatch.setattr(benchmark.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(benchmark.platform, "libc_ver", lambda: ("glibc", "2.26"))
    assert benchmark._reviewed_platform_abi() is False
    monkeypatch.setattr(benchmark.platform, "libc_ver", lambda: ("glibc", "2.27"))
    assert benchmark._reviewed_platform_abi() is True


def test_distribution_record_verifier_detects_corruption(tmp_path: Path, benchmark) -> None:
    package = tmp_path / "nifty" / "__init__.py"
    installer = tmp_path / "nifty-9.2.0.dist-info" / "INSTALLER"
    record = tmp_path / "nifty-9.2.0.dist-info" / "RECORD"
    package.parent.mkdir()
    installer.parent.mkdir()
    package.write_bytes(b"version = '9.2.0'\n")
    installer.write_bytes(b"uv")
    record.write_text(
        "\n".join(
            [
                f"nifty/__init__.py,{_record_hash(package.read_bytes())},{package.stat().st_size}",
                (
                    "nifty-9.2.0.dist-info/INSTALLER,"
                    f"{_record_hash(installer.read_bytes())},{installer.stat().st_size}"
                ),
                "nifty-9.2.0.dist-info/RECORD,,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeDistribution:
        files = [Path("nifty-9.2.0.dist-info/RECORD")]

        @staticmethod
        def locate_file(relative):
            return tmp_path / str(relative)

    valid = benchmark._verify_distribution_record(FakeDistribution())
    assert valid["integrity_verified"] is True
    assert valid["hashed_entries_verified"] == 2
    assert valid["payload_entry_count"] == 1

    package.write_bytes(b"corrupted\n")
    invalid = benchmark._verify_distribution_record(FakeDistribution())
    assert invalid["integrity_verified"] is False
    assert any("mismatch" in issue for issue in invalid["issues"])


def test_main_writes_fixed_report_and_separate_file_digest(
    tmp_path: Path, benchmark, monkeypatch, capsys
) -> None:
    report = benchmark._finalize_report(
        {"validation_passed": True, "checks": {"fixed_configuration": True}}
    )
    monkeypatch.setattr(benchmark, "_require_certification_process", lambda: None)
    monkeypatch.setattr(benchmark, "run_benchmark", lambda cache_dir: report)
    output = tmp_path / "benchmark.json"
    cache = tmp_path / "cache"

    assert benchmark.main(["--output", str(output), "--cache-dir", str(cache)]) == 0
    summary = json.loads(capsys.readouterr().out)

    assert summary["validation_passed"] is True
    assert summary["canonical_payload_sha256"] == report["canonical_payload_sha256"]
    assert summary["report_file_sha256"] == _digest(output)
    assert set(tmp_path.iterdir()) == {output, cache}


def test_main_writes_failed_report_and_returns_one(
    tmp_path: Path, benchmark, monkeypatch, capsys
) -> None:
    report = benchmark._finalize_report(
        {"validation_passed": False, "checks": {"all_values_finite": False}}
    )
    monkeypatch.setattr(benchmark, "_require_certification_process", lambda: None)
    monkeypatch.setattr(benchmark, "run_benchmark", lambda cache_dir: report)
    output = tmp_path / "failed.json"

    assert benchmark.main(["--output", str(output), "--cache-dir", str(tmp_path / "cache")]) == 1
    assert json.loads(output.read_text(encoding="utf-8"))["validation_passed"] is False
    assert json.loads(capsys.readouterr().out)["validation_passed"] is False


def test_main_verifies_report_without_nifty(tmp_path: Path, benchmark, capsys) -> None:
    output = tmp_path / "report.json"
    benchmark._write_report(output, benchmark._finalize_report({"value": 1}))

    assert benchmark.main(["--verify-report", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["canonical_payload_digest_valid"] is True


def test_help_does_not_require_nifty_or_jax() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "fixed CPU NIFTy.re analytic certification" in completed.stdout
    assert "--verify-report" in completed.stdout


def test_skill_keeps_nonlinear_and_certification_boundaries_explicit() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    assert "scientific validity" in text
    assert "posterior predictive" in text
    assert "python -I -B" in text
    assert "env -i" in text
    assert "macOS" in text and "12-or-newer" in text
    assert "canonical_payload_sha256" in text
    assert "nifty-re-certification-environment/uv.lock" in text
    assert 'sample_mode="linear_resample"' in text
    assert "nonlinear_resample" in text
    assert "JVP and VJP" in text


@pytest.mark.skipif(
    not INTEGRATION_ENABLED or not INTEGRATION_PYTHON,
    reason=(
        "set CRS_NIFTY_RE_INTEGRATION=1 and CRS_NIFTY_RE_PYTHON to the externally "
        "synchronized frozen environment interpreter"
    ),
)
def test_frozen_environment_certification_integration(tmp_path: Path) -> None:
    interpreter = Path(INTEGRATION_PYTHON)
    assert interpreter.is_absolute()
    assert interpreter.is_file()
    output = tmp_path / "certification.json"
    cache = tmp_path / "jax-cache"
    runtime_home = tmp_path / "runtime-home"
    runtime_tmp = tmp_path / "runtime-tmp"
    for path in (runtime_home, runtime_tmp):
        path.mkdir(mode=0o700)
        path.chmod(0o700)
    controlled_environment = {
        "HOME": str(runtime_home),
        "TMPDIR": str(runtime_tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    completed = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-B",
            str(SCRIPT),
            "--output",
            str(output),
            "--cache-dir",
            str(cache),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=controlled_environment,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert report["schema_version"] == 2
    assert report["configuration"]["seed"] == 42
    assert report["configuration"]["requested_sample_pairs"] == 128
    assert report["validation_passed"] is True
    assert report["scientific_conclusion_ready"] is False
    assert all(report["checks"].values())
    assert report["observed_evidence"]["installation"]["installer"] == "uv"
    assert report["observed_evidence"]["installation"]["record"]["integrity_verified"]
    assert report["environment"]["runtime_boundary"]["allowlisted_environment"] is True
    assert report["checks"]["bytecode_writes_disabled"] is True
    assert report["checks"]["reviewed_platform_abi"] is True
    assert report["serialization"]["nonfinite_values_replaced"] == 0
    assert summary["canonical_payload_sha256"] == report["canonical_payload_sha256"]
    assert summary["report_file_sha256"] == _digest(output)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert stat.S_IMODE(cache.stat().st_mode) == 0o700

    verified = subprocess.run(
        [str(interpreter), "-I", "-B", str(SCRIPT), "--verify-report", str(output)],
        check=False,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=controlled_environment,
        timeout=30,
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["canonical_payload_digest_valid"] is True
