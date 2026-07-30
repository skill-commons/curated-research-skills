import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "dt4acc-operations" / "scripts" / "dt4acc_operations.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("crs_dt4acc_operations", SCRIPT)
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
def operations():
    return _load_module()


def _canonical_architecture() -> str:
    return {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }[platform.machine().lower()]


def _fake_manifest(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    executable = tmp_path / "apptainer"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    executable_sha = hashlib.sha256(executable.read_bytes()).hexdigest()
    image = tmp_path / "dt4acc-reviewed.sif"
    image.write_bytes(b"synthetic reviewed SIF fixture\n")
    image_sha = hashlib.sha256(image.read_bytes()).hexdigest()
    digest = "a" * 64
    manifest = {
        "schema_version": 1,
        "mode": "simulation",
        "session": "crs-sim-test-session",
        "runtime": {
            "executable": str(executable),
            "executable_sha256": executable_sha,
            "version": "apptainer version 1.4.2",
            "image": str(image),
            "image_sha256": image_sha,
            "architecture": _canonical_architecture(),
        },
        "sources": [
            {
                "repository": "https://github.com/dt4acc/dt4acc",
                "commit": "1" * 40,
            },
            {
                "repository": "https://github.com/dt4acc/dt4acc-lib",
                "commit": "2" * 40,
            },
            {
                "repository": "https://github.com/hz-b/lat2db",
                "commit": "3" * 40,
            },
        ],
        "build_provenance": {
            "base_image": f"registry.example.org/python@sha256:{digest}",
            "python_lock_sha256": "b" * 64,
            "system_packages_lock_sha256": "c" * 64,
        },
        "resources": {
            "max_runtime_seconds": 600,
            "cpus": "2",
            "memory": "4G",
            "pids_limit": 256,
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, executable, image, manifest


def _probe_result(arguments: list[str], version: str = "apptainer version 1.4.2"):
    is_version = arguments[-1] == "--version"
    return subprocess.CompletedProcess(
        args=arguments,
        returncode=0,
        stdout=version + "\n" if is_version else "",
        stderr="",
    )


def test_valid_fake_manifest_preflight_and_exact_command(
    tmp_path: Path, operations, monkeypatch, capsys
) -> None:
    manifest_path, executable, image, _manifest = _fake_manifest(tmp_path)
    probes: list[list[str]] = []

    def probe(arguments, **_kwargs):
        probes.append(arguments)
        return _probe_result(arguments)

    monkeypatch.setattr(operations.subprocess, "run", probe)

    assert (
        operations.main(
            ["preflight", "--manifest", str(manifest_path)],
            environ={"PATH": "/usr/bin:/bin"},
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)

    assert report["status"] == "preflight_passed"
    assert report["runtime_content_pinned"] is True
    assert report["sif_header_probe_passed"] is True
    assert report["image_execution_verified"] is False
    assert report["provenance_binding_verified"] is False
    assert report["publication_ready"] is False
    assert report["network"] == "none"
    assert probes
    assert all(arguments[0] != str(executable) for arguments in probes)
    assert all(str(image) not in arguments for arguments in probes)
    assert report["command_uses_private_verified_snapshots"] is True
    assert report["command_template"] == [
        "<RUN_DIR>/artifacts/apptainer",
        "run",
        "--cleanenv",
        "--containall",
        "--no-eval",
        "--no-home",
        "--no-privs",
        "--no-mount",
        "home,cwd,bind-paths,hostfs",
        "--cwd",
        "/",
        "--net",
        "--network",
        "none",
        "--cpus",
        "2",
        "--memory",
        "4G",
        "--pids-limit",
        "256",
        "--runscript-timeout",
        "600s",
        "<RUN_DIR>/artifacts/dt4acc.sif",
    ]
    plan = operations._start_plan(
        operations._load_manifest(manifest_path, {}),
        tmp_path / "planned-run",
    )
    assert plan["executed"] is False
    assert plan["publication_ready"] is False
    assert plan["command"][0] == str(tmp_path / "planned-run" / "artifacts" / "apptainer")
    assert plan["command"][-1] == str(tmp_path / "planned-run" / "artifacts" / "dt4acc.sif")
    assert plan["artifact_sources"]["runtime"]["source"] == str(executable)
    assert plan["artifact_sources"]["image"]["source"] == str(image)
    assert plan["preflight_evidence"] == {
        "runtime_content_pinned": True,
        "sif_header_probe_passed": True,
        "image_execution_verified": False,
        "provenance_binding_verified": False,
    }


def test_facility_environment_is_rejected_without_echoing_value(
    tmp_path: Path, operations, monkeypatch
) -> None:
    manifest_path, _executable, _image, _manifest = _fake_manifest(tmp_path)
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda *args, **kwargs: _probe_result(args[0]),
    )
    candidate = "do-not-echo-this-facility-value"

    with pytest.raises(operations.OperationsError) as caught:
        operations._load_manifest(
            manifest_path,
            {"EPICS_CA_ADDR_LIST": candidate, "PATH": "/usr/bin:/bin"},
        )

    assert "EPICS_CA_ADDR_LIST" in str(caught.value)
    assert candidate not in str(caught.value)


def test_runtime_digest_is_checked_before_executing_the_probe(
    tmp_path: Path, operations, monkeypatch
) -> None:
    manifest_path, executable, _image, _manifest = _fake_manifest(tmp_path)
    executable.write_text("#!/bin/sh\nprintf 'changed runtime\\n'\n", encoding="utf-8")

    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("an unpinned runtime must not execute"),
    )
    with pytest.raises(operations.OperationsError, match="executable SHA-256"):
        operations._load_manifest(manifest_path, {})


def test_group_writable_manifest_is_rejected_before_runtime_probe(
    tmp_path: Path, operations, monkeypatch
) -> None:
    manifest_path, _executable, _image, _manifest = _fake_manifest(tmp_path)
    manifest_path.chmod(0o664)
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("an untrusted manifest must not invoke runtime"),
    )

    with pytest.raises(operations.OperationsError, match="group/world writable"):
        operations._load_manifest(manifest_path, {})


def test_sif_structure_probe_must_pass(tmp_path: Path, operations, monkeypatch) -> None:
    manifest_path, _executable, _image, _manifest = _fake_manifest(tmp_path)

    def probe(arguments, **_kwargs):
        if arguments[-1] == "--version":
            return _probe_result(arguments)
        return subprocess.CompletedProcess(arguments, 2, "", "")

    monkeypatch.setattr(operations.subprocess, "run", probe)
    with pytest.raises(operations.OperationsError, match="invalid SIF"):
        operations._load_manifest(manifest_path, {})


def test_manifest_rejects_hash_architecture_and_version_mismatches(
    tmp_path: Path, operations, monkeypatch
) -> None:
    manifest_path, _executable, image, manifest = _fake_manifest(tmp_path)
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda *args, **kwargs: _probe_result(args[0]),
    )

    image.write_bytes(b"changed after review\n")
    with pytest.raises(operations.OperationsError, match="SHA-256"):
        operations._load_manifest(manifest_path, {})

    image.write_bytes(b"synthetic reviewed SIF fixture\n")
    manifest["runtime"]["architecture"] = (
        "aarch64" if _canonical_architecture() == "x86_64" else "x86_64"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(operations.OperationsError, match="architecture"):
        operations._load_manifest(manifest_path, {})

    manifest["runtime"]["architecture"] = _canonical_architecture()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda *args, **kwargs: _probe_result(
            args[0],
            "apptainer version 9.9.9",
        ),
    )
    with pytest.raises(operations.OperationsError, match="version"):
        operations._load_manifest(manifest_path, {})


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value.__setitem__("sources", value["sources"][:-1]),
            "dt4acc, dt4acc-lib, and lat2db",
        ),
        (
            lambda value: value["sources"][0].__setitem__("commit", "main"),
            "full lowercase Git object IDs",
        ),
        (
            lambda value: value["build_provenance"].__setitem__(
                "base_image", "registry.example.org/python:latest"
            ),
            "pinned by @sha256",
        ),
        (
            lambda value: value["build_provenance"].__setitem__(
                "base_image",
                "user:password@registry.example.org/python@sha256:" + "a" * 64,
            ),
            "pinned by @sha256",
        ),
        (
            lambda value: value["build_provenance"].__setitem__(
                "python_lock_sha256", "not-a-digest"
            ),
            "python_lock_sha256",
        ),
    ],
)
def test_manifest_rejects_missing_sources_and_mutable_or_short_pins(
    tmp_path: Path, operations, monkeypatch, mutation, message
) -> None:
    manifest_path, _executable, _image, manifest = _fake_manifest(tmp_path)
    mutation(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda *args, **kwargs: _probe_result(args[0]),
    )

    with pytest.raises(operations.OperationsError, match=message):
        operations._load_manifest(manifest_path, {})


def test_start_confirmation_refuses_before_directory_or_process_write(
    tmp_path: Path, operations, monkeypatch
) -> None:
    manifest_path, _executable, _image, _manifest = _fake_manifest(tmp_path)
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda *args, **kwargs: _probe_result(args[0]),
    )
    manifest = operations._load_manifest(manifest_path, {})
    run_dir = tmp_path / "new-run"
    called = False

    def forbidden_popen(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Popen must not run for a mismatched confirmation")

    monkeypatch.setattr(operations.subprocess, "Popen", forbidden_popen)
    with pytest.raises(operations.OperationsError, match="confirmation"):
        operations._start(manifest, run_dir, "0" * 64)

    assert not called
    assert not run_dir.exists()


def test_run_directory_rejects_nonsticky_writable_ancestors(tmp_path: Path, operations) -> None:
    unsafe_parent = tmp_path / "unsafe-parent"
    unsafe_parent.mkdir(mode=0o700)
    unsafe_parent.chmod(0o777)

    with pytest.raises(operations.OperationsError, match="ancestors"):
        operations._new_run_dir(unsafe_parent / "run")


def test_start_rechecks_artifact_content_after_plan_confirmation(
    tmp_path: Path, operations, monkeypatch
) -> None:
    manifest_path, _executable, image, _manifest = _fake_manifest(tmp_path)
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda *args, **kwargs: _probe_result(args[0]),
    )
    manifest = operations._load_manifest(manifest_path, {})
    run_dir = tmp_path / "new-run"
    monkeypatch.setattr(operations.sys, "platform", "linux")
    monkeypatch.setattr(operations.os, "geteuid", lambda: 1000)
    plan = operations._start_plan(manifest, run_dir)
    image.write_bytes(b"replaced after plan confirmation\n")

    monkeypatch.setattr(
        operations.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("a replaced SIF must not be executed"),
    )
    with pytest.raises(operations.OperationsError, match="SIF image SHA-256"):
        operations._start(manifest, run_dir, plan["confirmation_sha256"])

    assert not (run_dir / "state.json").exists()


def test_stop_confirmation_refuses_before_signal_or_state_write(
    tmp_path: Path, operations, monkeypatch
) -> None:
    state = {
        "schema_version": 1,
        "status": "running",
        "mode": "simulation",
        "session": "crs-sim-test-session",
        "uid": 1000,
        "pid": 4242,
        "process_start_token": "100",
        "command": ["/usr/bin/apptainer", "run", "/srv/reviewed.sif"],
        "command_sha256": operations._command_digest(
            ["/usr/bin/apptainer", "run", "/srv/reviewed.sif"]
        ),
        "manifest_sha256": "a" * 64,
        "image_sha256": "b" * 64,
        "started_at": "2026-07-30T00:00:00+00:00",
        "stopped_at": None,
    }
    monkeypatch.setattr(operations, "_load_state", lambda _run_dir: dict(state))
    monkeypatch.setattr(operations, "_verify_running_state", lambda _state: (True, "running"))
    monkeypatch.setattr(
        operations.os,
        "killpg",
        lambda *args: pytest.fail("stop must not signal before digest confirmation"),
    )
    monkeypatch.setattr(
        operations,
        "_atomic_write",
        lambda *args: pytest.fail("stop must not write before digest confirmation"),
    )

    with pytest.raises(operations.OperationsError, match="confirmation"):
        operations._stop(tmp_path, "f" * 64, 1)


def test_stop_refuses_if_state_changes_after_confirmation(
    tmp_path: Path, operations, monkeypatch
) -> None:
    original = {
        "schema_version": 1,
        "status": "running",
        "mode": "simulation",
        "session": "crs-sim-test-session",
        "uid": 1000,
        "pid": 4242,
        "process_start_token": "100",
        "command": ["/usr/bin/apptainer", "run", "/srv/reviewed.sif"],
        "command_sha256": operations._command_digest(
            ["/usr/bin/apptainer", "run", "/srv/reviewed.sif"]
        ),
        "manifest_sha256": "a" * 64,
        "image_sha256": "b" * 64,
        "started_at": "2026-07-30T00:00:00+00:00",
        "stopped_at": None,
    }
    changed = dict(original)
    changed["pid"] = 5252
    changed["process_start_token"] = "200"
    monkeypatch.setattr(operations, "_load_state", lambda _run_dir: dict(original))
    monkeypatch.setattr(operations, "_verify_running_state", lambda _state: (True, "running"))
    confirmation = operations._stop_plan(tmp_path)["confirmation_sha256"]
    reads = iter((dict(original), dict(changed)))
    monkeypatch.setattr(operations, "_load_state", lambda _run_dir: next(reads))
    monkeypatch.setattr(
        operations.os,
        "killpg",
        lambda *args: pytest.fail("changed state must not redirect a signal"),
    )

    with pytest.raises(operations.OperationsError, match="state changed"):
        operations._stop(tmp_path, confirmation, 1)


def test_stop_does_not_report_success_while_group_members_remain(
    tmp_path: Path, operations, monkeypatch
) -> None:
    state = {
        "schema_version": 1,
        "status": "running",
        "mode": "simulation",
        "session": "crs-sim-test-session",
        "uid": 1000,
        "pid": 4242,
        "process_start_token": "100",
        "command": ["/usr/bin/apptainer", "run", "/srv/reviewed.sif"],
        "command_sha256": operations._command_digest(
            ["/usr/bin/apptainer", "run", "/srv/reviewed.sif"]
        ),
        "manifest_sha256": "a" * 64,
        "image_sha256": "b" * 64,
        "started_at": "2026-07-30T00:00:00+00:00",
        "stopped_at": None,
    }
    signaled = {"value": False}
    monkeypatch.setattr(operations, "_load_state", lambda _run_dir: dict(state))
    monkeypatch.setattr(
        operations,
        "_verify_running_state",
        lambda _state: (
            not signaled["value"],
            "running" if not signaled["value"] else "absent",
        ),
    )
    monkeypatch.setattr(operations.os, "getpgid", lambda pid: pid)

    def fake_killpg(_pid: int, sent_signal: int) -> None:
        if sent_signal == operations.signal.SIGTERM:
            signaled["value"] = True
        elif sent_signal != 0:
            pytest.fail("unexpected signal")

    monkeypatch.setattr(operations.os, "killpg", fake_killpg)
    clock = {"value": 0.0}

    def fake_monotonic() -> float:
        clock["value"] += 0.6
        return clock["value"]

    monkeypatch.setattr(operations.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(operations.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        operations,
        "_atomic_write",
        lambda *args: pytest.fail("a live group must not be recorded as stopped"),
    )
    confirmation = operations._stop_plan(tmp_path)["confirmation_sha256"]

    with pytest.raises(operations.OperationsError, match="process group did not stop"):
        operations._stop(tmp_path, confirmation, 1)


def test_parser_exposes_no_build_pull_or_live_operation(operations) -> None:
    parser = operations.build_parser()
    subparsers_action = next(
        action
        for action in parser._actions
        if isinstance(action, operations.argparse._SubParsersAction)
    )
    assert set(subparsers_action.choices) == {
        "preflight",
        "plan-start",
        "start",
        "status",
        "plan-stop",
        "stop",
    }
    for forbidden in ("build", "pull", "live", "exec", "shell"):
        with pytest.raises(SystemExit):
            parser.parse_args([forbidden])


def test_failed_start_terminates_the_complete_new_process_group(
    tmp_path: Path, operations, monkeypatch
) -> None:
    manifest_path, _executable, _image, _raw_manifest = _fake_manifest(tmp_path)
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda *args, **kwargs: _probe_result(args[0]),
    )
    manifest = operations._load_manifest(manifest_path, {})
    run_dir = tmp_path / "run"
    group_alive = {"value": True}
    signals: list[int] = []

    class FailingPopen:
        pid = 4321

        def __init__(self, _command, **_kwargs):
            pass

        def poll(self):
            return None if group_alive["value"] else 0

    def fake_killpg(pid: int, sent_signal: int) -> None:
        assert pid == 4321
        signals.append(sent_signal)
        if sent_signal == operations.signal.SIGTERM:
            group_alive["value"] = False
        elif sent_signal == 0 and not group_alive["value"]:
            raise ProcessLookupError

    def fail_identity(_pid: int):
        raise operations.OperationsError("identity failure")

    monkeypatch.setattr(operations.sys, "platform", "linux")
    monkeypatch.setattr(operations.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(operations.subprocess, "Popen", FailingPopen)
    monkeypatch.setattr(operations, "_proc_identity", fail_identity)
    monkeypatch.setattr(operations.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(operations.os, "killpg", fake_killpg)
    monkeypatch.setattr(operations.time, "sleep", lambda _seconds: None)
    plan = operations._start_plan(manifest, run_dir)

    with pytest.raises(operations.OperationsError, match="identity failure"):
        operations._start(manifest, run_dir, plan["confirmation_sha256"])

    assert operations.signal.SIGTERM in signals
    assert 0 in signals
    assert not (run_dir / "state.json").exists()


def test_fake_linux_start_status_stop_lifecycle(tmp_path: Path, operations, monkeypatch) -> None:
    manifest_path, _executable, _image, _raw_manifest = _fake_manifest(tmp_path)
    monkeypatch.setattr(
        operations.subprocess,
        "run",
        lambda *args, **kwargs: _probe_result(args[0]),
    )
    manifest = operations._load_manifest(manifest_path, {})
    run_dir = tmp_path / "run"
    alive = {"value": True}
    launched: dict[str, object] = {}

    class FakePopen:
        pid = 4321

        def __init__(self, command, **kwargs):
            launched["command"] = command
            launched["kwargs"] = kwargs
            self.terminated = False

        def poll(self):
            return None if alive["value"] else 0

        def terminate(self):
            self.terminated = True
            alive["value"] = False

        def wait(self, timeout=None):
            return 0

    def fake_identity(pid: int):
        assert pid == 4321
        if not alive["value"]:
            raise operations.ProcessAbsentError("absent")
        return 1000, "777", list(launched["command"])

    def fake_killpg(pid: int, sent_signal: int):
        assert pid == 4321
        if sent_signal == operations.signal.SIGTERM:
            alive["value"] = False
        elif sent_signal == 0:
            if not alive["value"]:
                raise ProcessLookupError
        else:
            pytest.fail("unexpected signal")

    monkeypatch.setattr(operations.sys, "platform", "linux")
    monkeypatch.setattr(operations.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(operations.os, "getuid", lambda: 1000)
    monkeypatch.setattr(operations.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(operations, "_proc_identity", fake_identity)
    monkeypatch.setattr(operations.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(operations.os, "killpg", fake_killpg)
    monkeypatch.setattr(operations.time, "sleep", lambda _seconds: None)

    start_plan = operations._start_plan(manifest, run_dir)
    state = operations._start(
        manifest,
        run_dir,
        start_plan["confirmation_sha256"],
    )
    assert state["status"] == "running"
    assert launched["command"] == start_plan["command"]
    assert launched["command"][0] == str(run_dir / "artifacts" / "apptainer")
    assert launched["command"][-1] == str(run_dir / "artifacts" / "dt4acc.sif")
    child_env = launched["kwargs"]["env"]
    assert set(child_env) == {
        "HOME",
        "LANG",
        "PATH",
        "APPTAINER_CONFIGDIR",
        "APPTAINER_CACHEDIR",
    }
    assert all(not name.startswith(("EPICS_", "TANGO_", "MONGODB_")) for name in child_env)
    assert (run_dir / "state.json").stat().st_mode & 0o777 == 0o600
    assert (run_dir / "runtime.log").stat().st_mode & 0o777 == 0o600
    assert (run_dir / "artifacts" / "apptainer").stat().st_mode & 0o777 == 0o500
    assert (run_dir / "artifacts" / "dt4acc.sif").stat().st_mode & 0o777 == 0o400

    stop_plan = operations._stop_plan(run_dir)
    stopped = operations._stop(
        run_dir,
        stop_plan["confirmation_sha256"],
        timeout=1,
    )
    assert stopped["status"] == "stopped"
    assert stopped["stopped_at"] is not None


def test_static_runner_contains_no_broad_or_privileged_escape_hatches() -> None:
    text = SCRIPT.read_text(encoding="utf-8").lower()
    forbidden = (
        "--bind",
        "--network host",
        "fuser",
        "pkill",
        "pgrep",
        "kill -9",
        "--all",
        "sudo",
        "fakeroot",
        "apptainer build",
        "apptainer pull",
        "shell=true",
    )
    for pattern in forbidden:
        assert pattern not in text
    assert "subprocess.popen(" in text
    assert "start_new_session=true" in text
    assert "os.killpg(" in text
