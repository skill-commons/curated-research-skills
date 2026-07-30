#!/usr/bin/env python3
"""Plan and control one isolated, digest-pinned dt4acc Apptainer simulation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_MANIFEST_BYTES = 65_536
MAX_STATE_BYTES = 65_536
SESSION_RE = re.compile(r"^crs-sim-[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
BASE_IMAGE_RE = re.compile(r"^[^\s]+@sha256:[0-9a-f]{64}$")
RUNTIME_VERSION_RE = re.compile(r"^apptainer version [0-9][0-9A-Za-z.+~_-]{0,127}$")
CPU_RE = re.compile(r"^(?:[1-9]\d?|0\.[1-9]\d*)$")
MEMORY_RE = re.compile(r"^[1-9]\d*(?:K|M|G|T)$")
FACILITY_ENV_PREFIXES = (
    "EPICS_",
    "TANGO_",
    "MONGODB_",
    "DT4ACC_LIVE_",
    "DT4ACC_FACILITY_",
    "FACILITY_",
    "APPTAINERENV_EPICS_",
    "APPTAINERENV_TANGO_",
    "APPTAINERENV_MONGODB_",
    "SINGULARITYENV_EPICS_",
    "SINGULARITYENV_TANGO_",
    "SINGULARITYENV_MONGODB_",
)
REQUIRED_SOURCES = {"dt4acc", "dt4acc-lib", "lat2db"}


class OperationsError(RuntimeError):
    """A safe operations error."""


class ProcessAbsentError(OperationsError):
    """The exact recorded Linux process no longer exists."""


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OperationsError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path, maximum: int) -> tuple[dict[str, Any], str]:
    if not path.is_absolute():
        raise OperationsError("manifest/state path must be absolute")
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise OperationsError("manifest/state must be a canonical regular non-symlink file")
    if path.stat().st_mode & 0o022:
        raise OperationsError("manifest/state must not be group/world writable")
    data = path.read_bytes()
    if len(data) > maximum:
        raise OperationsError("manifest/state exceeds the size limit")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OperationsError("manifest/state is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise OperationsError("manifest/state root must be an object")
    return value, hashlib.sha256(data).hexdigest()


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = expected.difference(value)
    extra = set(value).difference(expected)
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown " + ", ".join(sorted(extra)))
        raise OperationsError(f"{label} keys are invalid: {'; '.join(details)}")


def _canonical_architecture(value: str) -> str:
    if not isinstance(value, str):
        raise OperationsError("architecture must be a string")
    normalized = value.lower()
    aliases = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }
    if normalized not in aliases:
        raise OperationsError("architecture must be x86_64/amd64 or aarch64/arm64")
    return aliases[normalized]


def _regular_canonical_path(raw: str, label: str, *, executable: bool = False) -> Path:
    if not isinstance(raw, str):
        raise OperationsError(f"{label} must be a path string")
    path = Path(raw)
    if not path.is_absolute():
        raise OperationsError(f"{label} must be an absolute path")
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise OperationsError(f"{label} must be a canonical regular non-symlink file")
    if executable and not os.access(path, os.X_OK):
        raise OperationsError(f"{label} must be executable")
    if path.stat().st_mode & 0o022:
        raise OperationsError(f"{label} must not be group/world writable")
    return path


def _materialize_verified_file(
    source: Path,
    destination: Path,
    expected_sha256: str,
    label: str,
    *,
    executable: bool = False,
) -> Path:
    _regular_canonical_path(str(source), label, executable=executable)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    source_descriptor = os.open(source, flags)
    destination_descriptor: int | None = None
    try:
        source_status = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_status.st_mode):
            raise OperationsError(f"{label} changed into a non-regular file")
        if source_status.st_mode & 0o022:
            raise OperationsError(f"{label} must not be group/world writable")
        if executable and not source_status.st_mode & 0o111:
            raise OperationsError(f"{label} must be executable")
        destination_mode = 0o500 if executable else 0o400
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            destination_mode,
        )
        digest = hashlib.sha256()
        with (
            os.fdopen(source_descriptor, "rb", closefd=False) as source_stream,
            os.fdopen(destination_descriptor, "wb", closefd=False) as destination_stream,
        ):
            while chunk := source_stream.read(1024 * 1024):
                digest.update(chunk)
                destination_stream.write(chunk)
            destination_stream.flush()
            os.fsync(destination_stream.fileno())
        if digest.hexdigest() != expected_sha256:
            raise OperationsError(f"{label} SHA-256 does not match the manifest")
        os.chmod(destination, destination_mode)
        return destination
    except BaseException:
        with suppress(FileNotFoundError):
            destination.unlink()
        raise
    finally:
        os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def _source_name(repository: str) -> str:
    if not repository or any(
        ord(character) < 32 or ord(character) == 127 for character in repository
    ):
        raise OperationsError("source repositories must be credential-free HTTPS URLs")
    try:
        parsed = urlsplit(repository)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        _ = parsed.port
    except (UnicodeError, ValueError):
        raise OperationsError("source repositories must be credential-free HTTPS URLs") from None
    if (
        parsed.scheme != "https"
        or not hostname
        or username is not None
        or password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise OperationsError("source repositories must be credential-free HTTPS URLs")
    name = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return name.removesuffix(".git")


def _facility_environment(environ: dict[str, str]) -> list[str]:
    return sorted(
        name for name in environ if any(name.startswith(prefix) for prefix in FACILITY_ENV_PREFIXES)
    )


def _validate_manifest(
    value: dict[str, Any],
    manifest_sha256: str,
    *,
    environ: dict[str, str],
) -> dict[str, Any]:
    _exact_keys(
        value,
        {
            "schema_version",
            "mode",
            "session",
            "runtime",
            "sources",
            "build_provenance",
            "resources",
        },
        "manifest",
    )
    if value["schema_version"] != 1 or value["mode"] != "simulation":
        raise OperationsError("only schema version 1 simulation manifests are accepted")
    session = value["session"]
    if not isinstance(session, str) or not SESSION_RE.fullmatch(session):
        raise OperationsError("session must match crs-sim-[a-z0-9-]")
    contaminated = _facility_environment(environ)
    if contaminated:
        raise OperationsError(
            "facility/control environment variables must be unset: " + ", ".join(contaminated)
        )

    runtime = value["runtime"]
    if not isinstance(runtime, dict):
        raise OperationsError("runtime must be an object")
    _exact_keys(
        runtime,
        {
            "executable",
            "executable_sha256",
            "version",
            "image",
            "image_sha256",
            "architecture",
        },
        "runtime",
    )
    executable = _regular_canonical_path(
        runtime["executable"], "runtime executable", executable=True
    )
    executable_sha = runtime["executable_sha256"]
    if not isinstance(executable_sha, str) or not DIGEST_RE.fullmatch(executable_sha):
        raise OperationsError(
            "runtime executable_sha256 must be 64 lowercase hexadecimal characters"
        )
    image = _regular_canonical_path(runtime["image"], "SIF image")
    if image.suffix.lower() != ".sif":
        raise OperationsError("runtime image must be a local .sif file")
    image_sha = runtime["image_sha256"]
    if not isinstance(image_sha, str) or not DIGEST_RE.fullmatch(image_sha):
        raise OperationsError("image_sha256 must be 64 lowercase hexadecimal characters")
    version = runtime["version"]
    if not isinstance(version, str) or not RUNTIME_VERSION_RE.fullmatch(version):
        raise OperationsError("runtime version must match 'apptainer version <version>'")
    try:
        with tempfile.TemporaryDirectory(prefix="crs-dt4acc-runtime-probe-") as temporary:
            probe_root = Path(temporary)
            snapshot_executable = _materialize_verified_file(
                executable,
                probe_root / "apptainer",
                executable_sha,
                "runtime executable",
                executable=True,
            )
            snapshot_image = _materialize_verified_file(
                image,
                probe_root / "image.sif",
                image_sha,
                "SIF image",
            )
            probe_environment = {
                "HOME": str(probe_root / "home"),
                "PATH": os.defpath,
                "LANG": "C",
                "APPTAINER_CONFIGDIR": str(probe_root / "config"),
                "APPTAINER_CACHEDIR": str(probe_root / "cache"),
            }
            for directory in ("home", "config", "cache"):
                (probe_root / directory).mkdir(mode=0o700)
            completed = subprocess.run(
                [str(snapshot_executable), "--version"],
                check=False,
                capture_output=True,
                text=True,
                env=probe_environment,
                timeout=10,
            )
            sif_probe = subprocess.run(
                [str(snapshot_executable), "sif", "header", str(snapshot_image)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=probe_environment,
                timeout=10,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OperationsError("could not complete the fixed local Apptainer probes") from error
    observed_version = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or observed_version != version:
        raise OperationsError("observed Apptainer version does not match the manifest")
    if sif_probe.returncode != 0:
        raise OperationsError("Apptainer rejected the file as an invalid SIF image")
    architecture = _canonical_architecture(runtime["architecture"])
    host_architecture = _canonical_architecture(platform.machine())
    if architecture != host_architecture:
        raise OperationsError("image architecture does not match the host")

    raw_sources = value["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise OperationsError("sources must be a non-empty array")
    sources: list[dict[str, str]] = []
    names: set[str] = set()
    for raw_source in raw_sources:
        if not isinstance(raw_source, dict):
            raise OperationsError("each source must be an object")
        _exact_keys(raw_source, {"repository", "commit"}, "source")
        repository = raw_source["repository"]
        commit = raw_source["commit"]
        if not isinstance(repository, str) or not isinstance(commit, str):
            raise OperationsError("source repository and commit must be strings")
        name = _source_name(repository)
        if name in names:
            raise OperationsError("source repositories must be unique")
        if not COMMIT_RE.fullmatch(commit):
            raise OperationsError("source commits must be full lowercase Git object IDs")
        names.add(name)
        sources.append({"repository": repository.rstrip("/"), "commit": commit})
    if not REQUIRED_SOURCES.issubset(names):
        raise OperationsError("sources must pin dt4acc, dt4acc-lib, and lat2db")

    provenance = value["build_provenance"]
    if not isinstance(provenance, dict):
        raise OperationsError("build_provenance must be an object")
    _exact_keys(
        provenance,
        {"base_image", "python_lock_sha256", "system_packages_lock_sha256"},
        "build_provenance",
    )
    base_image = provenance["base_image"]
    if (
        not isinstance(base_image, str)
        or len(base_image) > 512
        or base_image.count("@") != 1
        or "://" in base_image
        or not BASE_IMAGE_RE.fullmatch(base_image)
    ):
        raise OperationsError("base_image must be pinned by @sha256 digest")
    for key in ("python_lock_sha256", "system_packages_lock_sha256"):
        digest = provenance[key]
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            raise OperationsError(f"{key} must be a SHA-256 digest")

    resources = value["resources"]
    if not isinstance(resources, dict):
        raise OperationsError("resources must be an object")
    _exact_keys(
        resources,
        {"max_runtime_seconds", "cpus", "memory", "pids_limit"},
        "resources",
    )
    maximum = resources["max_runtime_seconds"]
    pids = resources["pids_limit"]
    cpus = resources["cpus"]
    memory = resources["memory"]
    if not isinstance(maximum, int) or not 10 <= maximum <= 86_400:
        raise OperationsError("max_runtime_seconds must be between 10 and 86400")
    if not isinstance(pids, int) or not 16 <= pids <= 4096:
        raise OperationsError("pids_limit must be between 16 and 4096")
    if not isinstance(cpus, str) or not CPU_RE.fullmatch(cpus) or float(cpus) > 64:
        raise OperationsError("cpus must be a decimal string greater than 0 and at most 64")
    if not isinstance(memory, str) or not MEMORY_RE.fullmatch(memory):
        raise OperationsError("memory must be an integer with K/M/G/T suffix")

    return {
        "schema_version": 1,
        "mode": "simulation",
        "session": session,
        "manifest_sha256": manifest_sha256,
        "runtime": {
            "executable": str(executable),
            "executable_sha256": executable_sha,
            "version": version,
            "image": str(image),
            "image_sha256": image_sha,
            "architecture": architecture,
        },
        "sources": sources,
        "build_provenance": dict(provenance),
        "resources": dict(resources),
    }


def _load_manifest(path: Path, environ: dict[str, str]) -> dict[str, Any]:
    value, digest = _read_json(path, MAX_MANIFEST_BYTES)
    return _validate_manifest(value, digest, environ=environ)


def _runtime_command(
    manifest: dict[str, Any],
    *,
    executable: str | None = None,
    image: str | None = None,
) -> list[str]:
    runtime = manifest["runtime"]
    resources = manifest["resources"]
    return [
        executable or runtime["executable"],
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
        resources["cpus"],
        "--memory",
        resources["memory"],
        "--pids-limit",
        str(resources["pids_limit"]),
        "--runscript-timeout",
        f"{resources['max_runtime_seconds']}s",
        image or runtime["image"],
    ]


def _start_supported_on_this_host() -> bool:
    return sys.platform == "linux" and os.geteuid() != 0


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _new_run_dir(path: Path) -> Path:
    if not path.is_absolute():
        raise OperationsError("run directory must be absolute")
    if path.exists() or path.is_symlink():
        raise OperationsError("run directory must not already exist")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.resolve() != parent:
        raise OperationsError("run directory parent must be a canonical directory")
    for ancestor in (parent, *parent.parents):
        ancestor_status = ancestor.stat()
        writable_by_others = bool(ancestor_status.st_mode & 0o022)
        sticky = bool(ancestor_status.st_mode & stat.S_ISVTX)
        if writable_by_others and not sticky:
            raise OperationsError(
                "run directory ancestors must not be group/world writable unless sticky"
            )
    return path


def _artifact_paths(run_dir: Path) -> tuple[Path, Path]:
    artifact_directory = run_dir / "artifacts"
    return artifact_directory / "apptainer", artifact_directory / "dt4acc.sif"


def _start_plan(manifest: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    run_dir = _new_run_dir(run_dir)
    runtime_snapshot, image_snapshot = _artifact_paths(run_dir)
    plan = {
        "action": "start",
        "executed": False,
        "mode": "simulation",
        "session": manifest["session"],
        "start_supported_on_this_host": _start_supported_on_this_host(),
        "publication_ready": False,
        "run_dir": str(run_dir),
        "manifest_sha256": manifest["manifest_sha256"],
        "runtime": manifest["runtime"],
        "sources": manifest["sources"],
        "build_provenance": manifest["build_provenance"],
        "resources": manifest["resources"],
        "network": "none",
        "preflight_evidence": {
            "runtime_content_pinned": True,
            "sif_header_probe_passed": True,
            "image_execution_verified": False,
            "provenance_binding_verified": False,
        },
        "artifact_sources": {
            "runtime": {
                "source": manifest["runtime"]["executable"],
                "sha256": manifest["runtime"]["executable_sha256"],
                "snapshot": str(runtime_snapshot),
            },
            "image": {
                "source": manifest["runtime"]["image"],
                "sha256": manifest["runtime"]["image_sha256"],
                "snapshot": str(image_snapshot),
            },
        },
        "command": _runtime_command(
            manifest,
            executable=str(runtime_snapshot),
            image=str(image_snapshot),
        ),
    }
    plan["confirmation_sha256"] = _digest(plan)
    return plan


def _state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    data = json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise


def _proc_identity(pid: int) -> tuple[int, str, list[str]]:
    if sys.platform != "linux":
        raise OperationsError("process identity verification is Linux-only")
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        raw_command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError as error:
        raise ProcessAbsentError("recorded process is absent") from error
    except (PermissionError, OSError) as error:
        raise OperationsError("recorded process cannot be inspected") from error
    right_parenthesis = stat_text.rfind(")")
    if right_parenthesis < 0:
        raise OperationsError("recorded process stat is malformed")
    fields_after_command = stat_text[right_parenthesis + 1 :].split()
    if len(fields_after_command) <= 19:
        raise OperationsError("recorded process stat is malformed")
    uid_match = re.search(r"^Uid:\s+(\d+)", status, flags=re.MULTILINE)
    if not uid_match:
        raise OperationsError("recorded process UID is unavailable")
    command = [item.decode("utf-8", errors="replace") for item in raw_command.split(b"\0") if item]
    if not command:
        raise OperationsError("recorded process command is unavailable")
    return int(uid_match.group(1)), fields_after_command[19], command


def _command_digest(command: list[str]) -> str:
    return hashlib.sha256("\0".join(command).encode()).hexdigest()


def _load_state(run_dir: Path) -> dict[str, Any]:
    if not run_dir.is_absolute() or run_dir.is_symlink() or run_dir.resolve() != run_dir:
        raise OperationsError("run directory must be canonical and absolute")
    value, _state_sha = _read_json(_state_path(run_dir), MAX_STATE_BYTES)
    required = {
        "schema_version",
        "status",
        "mode",
        "session",
        "uid",
        "pid",
        "process_start_token",
        "command",
        "command_sha256",
        "manifest_sha256",
        "image_sha256",
        "started_at",
        "stopped_at",
    }
    _exact_keys(value, required, "state")
    if (
        value["schema_version"] != 1
        or value["mode"] != "simulation"
        or value["status"] not in {"running", "stopped"}
        or not isinstance(value["pid"], int)
        or value["pid"] <= 1
        or not isinstance(value["uid"], int)
        or not isinstance(value["command"], list)
        or not all(isinstance(item, str) for item in value["command"])
        or value["command_sha256"] != _command_digest(value["command"])
        or not isinstance(value["process_start_token"], str)
    ):
        raise OperationsError("state validation failed")
    return value


def _verify_running_state(state: dict[str, Any]) -> tuple[bool, str]:
    if state["status"] == "stopped":
        return False, "stopped"
    try:
        uid, start_token, command = _proc_identity(state["pid"])
    except ProcessAbsentError:
        return False, "absent"
    if uid != state["uid"] or uid != os.getuid():
        raise OperationsError("recorded process UID does not match the current operator")
    if start_token != state["process_start_token"]:
        raise OperationsError("recorded PID has been reused or its start token changed")
    if command != state["command"]:
        raise OperationsError("recorded process command does not match owned state")
    return True, "running"


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError as error:
        raise OperationsError("owned process group cannot be inspected") from error
    return True


def _terminate_started_process_group(
    process: subprocess.Popen[bytes],
    *,
    timeout: int = 5,
) -> None:
    process_group_id = process.pid
    try:
        observed_group = os.getpgid(process.pid)
    except ProcessLookupError:
        observed_group = None
    if observed_group is not None and observed_group != process_group_id:
        raise OperationsError("new child is not its expected owned process-group leader")
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        process.poll()
        return
    except PermissionError as error:
        raise OperationsError("could not signal the new owned process group") from error
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process.poll()
        if not _process_group_exists(process_group_id):
            return
        time.sleep(0.1)
    raise OperationsError(
        f"new owned process group {process_group_id} did not terminate; "
        "manual operator intervention is required"
    )


def _child_environment(run_dir: Path) -> dict[str, str]:
    return {
        "HOME": str(run_dir / "home"),
        "LANG": "C",
        "PATH": os.defpath,
        "APPTAINER_CONFIGDIR": str(run_dir / "apptainer-config"),
        "APPTAINER_CACHEDIR": str(run_dir / "apptainer-cache"),
    }


def _start(manifest: dict[str, Any], run_dir: Path, confirmation: str) -> dict[str, Any]:
    plan = _start_plan(manifest, run_dir)
    if confirmation != plan["confirmation_sha256"]:
        raise OperationsError("start confirmation does not match the current plan")
    if sys.platform != "linux":
        raise OperationsError("start is Linux-only")
    if os.geteuid() == 0:
        raise OperationsError("refusing to start an Apptainer simulation as root")
    run_dir.mkdir(mode=0o700)
    for directory in ("home", "apptainer-config", "apptainer-cache", "artifacts"):
        (run_dir / directory).mkdir(mode=0o700)
    process: subprocess.Popen[bytes] | None = None
    try:
        runtime_snapshot, image_snapshot = _artifact_paths(run_dir)
        _materialize_verified_file(
            Path(manifest["runtime"]["executable"]),
            runtime_snapshot,
            manifest["runtime"]["executable_sha256"],
            "runtime executable",
            executable=True,
        )
        _materialize_verified_file(
            Path(manifest["runtime"]["image"]),
            image_snapshot,
            manifest["runtime"]["image_sha256"],
            "SIF image",
        )
        log_path = run_dir / "runtime.log"
        log_descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(log_descriptor, "wb", closefd=True) as log_stream:
            process = subprocess.Popen(
                plan["command"],
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                env=_child_environment(run_dir),
                cwd=run_dir,
                start_new_session=True,
            )
        time.sleep(0.2)
        if process.poll() is not None:
            raise OperationsError("Apptainer exited during startup; inspect runtime.log")
        uid, start_token, command = _proc_identity(process.pid)
        if uid != os.getuid():
            raise OperationsError("new process UID does not match the current operator")
        if command != plan["command"]:
            raise OperationsError("new process command could not be verified")
        state = {
            "schema_version": 1,
            "status": "running",
            "mode": "simulation",
            "session": manifest["session"],
            "uid": uid,
            "pid": process.pid,
            "process_start_token": start_token,
            "command": plan["command"],
            "command_sha256": _command_digest(plan["command"]),
            "manifest_sha256": manifest["manifest_sha256"],
            "image_sha256": manifest["runtime"]["image_sha256"],
            "started_at": datetime.now(UTC).isoformat(),
            "stopped_at": None,
        }
        _atomic_write(_state_path(run_dir), state)
        return state
    except BaseException as original_error:
        if process is not None:
            try:
                _terminate_started_process_group(process)
            except OperationsError as cleanup_error:
                raise OperationsError(
                    f"startup failed and cleanup was incomplete: {cleanup_error}"
                ) from original_error
        raise


def _stop_plan(run_dir: Path) -> dict[str, Any]:
    state = _load_state(run_dir)
    running, observed = _verify_running_state(state)
    plan = {
        "action": "stop",
        "executed": False,
        "mode": "simulation",
        "session": state["session"],
        "run_dir": str(run_dir),
        "uid": state["uid"],
        "pid": state["pid"],
        "process_start_token": state["process_start_token"],
        "command_sha256": state["command_sha256"],
        "manifest_sha256": state["manifest_sha256"],
        "image_sha256": state["image_sha256"],
        "state_sha256": _digest(state),
        "observed": observed,
        "will_signal": running,
    }
    plan["confirmation_sha256"] = _digest(plan)
    return plan


def _stop(run_dir: Path, confirmation: str, timeout: int) -> dict[str, Any]:
    plan = _stop_plan(run_dir)
    if confirmation != plan["confirmation_sha256"]:
        raise OperationsError("stop confirmation does not match the current plan")
    if not plan["will_signal"]:
        raise OperationsError("recorded process is not verifiably running")
    state = _load_state(run_dir)
    if _digest(state) != plan["state_sha256"]:
        raise OperationsError("owned state changed after stop confirmation")
    _verify_running_state(state)
    try:
        if os.getpgid(state["pid"]) != state["pid"]:
            raise OperationsError("recorded process is not the expected owned process-group leader")
    except ProcessLookupError as error:
        raise OperationsError("recorded process disappeared before stop") from error
    try:
        os.killpg(state["pid"], signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as error:
        raise OperationsError("could not signal the exact owned process group") from error
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        running, _observed = _verify_running_state(state)
        group_exists = _process_group_exists(state["pid"])
        if not running and not group_exists:
            stopped = dict(state)
            stopped["status"] = "stopped"
            stopped["stopped_at"] = datetime.now(UTC).isoformat()
            _atomic_write(_state_path(run_dir), stopped)
            return stopped
        time.sleep(0.1)
    raise OperationsError(
        "exact owned process group did not stop after SIGTERM; "
        "manual operator intervention is required"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "plan-start", "start"):
        operation = subparsers.add_parser(name)
        operation.add_argument("--manifest", required=True, type=Path)
        if name != "preflight":
            operation.add_argument("--run-dir", required=True, type=Path)
        if name == "start":
            operation.add_argument("--confirm", required=True)
    for name in ("status", "plan-stop", "stop"):
        operation = subparsers.add_parser(name)
        operation.add_argument("--run-dir", required=True, type=Path)
        if name == "stop":
            operation.add_argument("--confirm", required=True)
            operation.add_argument("--timeout", type=int, default=10)
    return parser


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None, *, environ: dict[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    environment = dict(os.environ if environ is None else environ)
    try:
        if args.command in {"preflight", "plan-start", "start"}:
            manifest = _load_manifest(args.manifest, environment)
            if args.command == "preflight":
                _emit(
                    {
                        "status": "preflight_passed",
                        "mode": "simulation",
                        "session": manifest["session"],
                        "network": "none",
                        "start_supported_on_this_host": _start_supported_on_this_host(),
                        "runtime_content_pinned": True,
                        "sif_header_probe_passed": True,
                        "image_execution_verified": False,
                        "provenance_binding_verified": False,
                        "publication_ready": False,
                        "manifest_sha256": manifest["manifest_sha256"],
                        "image_sha256": manifest["runtime"]["image_sha256"],
                        "architecture": manifest["runtime"]["architecture"],
                        "sources": manifest["sources"],
                        "build_provenance": manifest["build_provenance"],
                        "resources": manifest["resources"],
                        "command_uses_private_verified_snapshots": True,
                        "command_template": _runtime_command(
                            manifest,
                            executable="<RUN_DIR>/artifacts/apptainer",
                            image="<RUN_DIR>/artifacts/dt4acc.sif",
                        ),
                    }
                )
                return 0
            if args.command == "plan-start":
                _emit(_start_plan(manifest, args.run_dir))
                return 0
            _emit(_start(manifest, args.run_dir, args.confirm))
            return 0
        if args.command == "status":
            state = _load_state(args.run_dir)
            running, observed = _verify_running_state(state)
            _emit(
                {
                    "mode": state["mode"],
                    "session": state["session"],
                    "status": state["status"],
                    "observed": observed,
                    "running": running,
                    "pid": state["pid"],
                    "image_sha256": state["image_sha256"],
                    "manifest_sha256": state["manifest_sha256"],
                    "started_at": state["started_at"],
                    "stopped_at": state["stopped_at"],
                }
            )
            return 0
        if args.command == "plan-stop":
            _emit(_stop_plan(args.run_dir))
            return 0
        if args.command == "stop":
            if not 1 <= args.timeout <= 60:
                raise OperationsError("stop timeout must be between 1 and 60 seconds")
            _emit(_stop(args.run_dir, args.confirm, args.timeout))
            return 0
        raise OperationsError("unsupported operation")
    except (OperationsError, OSError, subprocess.SubprocessError) as error:
        print(f"dt4acc-operations: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
