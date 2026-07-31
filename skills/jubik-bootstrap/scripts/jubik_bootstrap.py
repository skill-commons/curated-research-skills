#!/usr/bin/env python3
"""Persist, confirm, and apply one wheel-only J-UBIK core installation plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = Path(__file__).resolve()
LOCK_PROJECT = SKILL_ROOT / "assets" / "jubik-core-environment"
LOCK_FILE = LOCK_PROJECT / "uv.lock"
PROJECT_FILE = LOCK_PROJECT / "pyproject.toml"
WHEEL_FILE = LOCK_PROJECT / "wheels" / "jubik-0.3-py3-none-any.whl"
WHEEL_PROVENANCE = LOCK_PROJECT / "JUBIK-WHEEL-BUILD-PROVENANCE.md"
WHEEL_LICENSE = LOCK_PROJECT / "JUBIK-WHEEL-LICENSE.txt"
SMOKE_SCRIPT = SKILL_ROOT / "scripts" / "jubik_core_smoke.py"
JUBIK_COMMIT = "58a1c7c23477d2099774523278c0ed65f27afde3"
JUBIK_TREE = "675b98f7d0ae9575e5f6081428005063cf942401"
JUBIK_WHEEL_SHA256 = "30953812cbc922aa909e4f7ac5d86cd2c64f69747b8af7e97b00a98f15936b68"
EXPECTED_NIFTY_VERSION = "9.2.0"
MINIMUM_FREE_BYTES = 2 * 1024**3
MINIMUM_UV_VERSION = (0, 10, 0)
MINIMUM_GLIBC_VERSION = (2, 27)
MINIMUM_MACOS_VERSION = (12, 0)
MAX_CAPTURE_BYTES = 65_536
MAX_EVIDENCE_CHARS = 4_096
MAX_JSON_BYTES = 1024 * 1024
PLAN_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
UV_VERSION_RE = re.compile(r"^uv (\d+)\.(\d+)\.(\d+)(?:[^\s]*)?(?: .*)?$")
URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://)[^\s/@]+@")
AUTH_HEADER_RE = re.compile(r"(?im)^((?:proxy-)?authorization\s*:\s*).+$")
PYTHON_BINDING_PROGRAM = (
    "import json,sys;"
    "print(json.dumps({'version':list(sys.version_info[:3]),"
    "'base_executable':sys._base_executable,'prefix':sys.prefix}))"
)
TRANSPORT_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)
ASSET_LAYOUT = {
    "pyproject": (PROJECT_FILE, "pyproject.toml"),
    "uv_lock": (LOCK_FILE, "uv.lock"),
    "jubik_wheel": (WHEEL_FILE, f"wheels/{WHEEL_FILE.name}"),
    "wheel_provenance": (WHEEL_PROVENANCE, WHEEL_PROVENANCE.name),
    "wheel_license": (WHEEL_LICENSE, WHEEL_LICENSE.name),
    "smoke_script": (SMOKE_SCRIPT, f"scripts/{SMOKE_SCRIPT.name}"),
    "bootstrap_script": (BOOTSTRAP_SCRIPT, f"scripts/{BOOTSTRAP_SCRIPT.name}"),
}


class BootstrapError(RuntimeError):
    """A structured, bounded bootstrap failure."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "bootstrap_failed",
        evidence: dict[str, Any] | None = None,
        retained_paths: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.evidence = evidence or {}
        self.retained_paths = retained_paths or {}

    def as_report(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "status": "failed",
            "category": self.category,
            "message": str(self),
            "evidence": self.evidence,
            "retained_paths": self.retained_paths,
        }


class _StreamCapture:
    def __init__(self) -> None:
        self.total_bytes = 0
        self.digest = hashlib.sha256()
        self.tail = bytearray()

    def consume(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        self.digest.update(chunk)
        self.tail.extend(chunk)
        if len(self.tail) > MAX_CAPTURE_BYTES:
            del self.tail[:-MAX_CAPTURE_BYTES]

    def text(self) -> str:
        return bytes(self.tail).decode("utf-8", errors="replace")


@dataclass(frozen=True)
class _ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_sha256: str
    stderr_sha256: str


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded(value: str, limit: int = MAX_CAPTURE_BYTES) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) > limit:
        encoded = encoded[-limit:]
    return encoded.decode("utf-8", errors="replace")


def _redact(value: str) -> str:
    redacted = value
    candidates: set[str] = set()
    for name in TRANSPORT_ENV_NAMES:
        raw = os.environ.get(name)
        if raw:
            candidates.add(raw)
            with suppress(ValueError):
                parsed = __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(raw)
                if parsed.username and len(parsed.username) >= 3:
                    candidates.add(parsed.username)
                if parsed.password and len(parsed.password) >= 3:
                    candidates.add(parsed.password)
    for candidate in sorted(candidates, key=len, reverse=True):
        redacted = redacted.replace(candidate, "[redacted]")
    redacted = URL_CREDENTIAL_RE.sub(r"\1[redacted]@", redacted)
    redacted = AUTH_HEADER_RE.sub(r"\1[redacted]", redacted)
    return _bounded(redacted, MAX_EVIDENCE_CHARS)


def _child_environment(
    *,
    environment: Path | None = None,
    forward_transport: bool = False,
    temporary_directory: Path | None = None,
) -> dict[str, str]:
    child = {
        "PATH": os.defpath,
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if temporary_directory is not None:
        child.update(
            {
                "TMPDIR": str(temporary_directory),
                "TMP": str(temporary_directory),
                "TEMP": str(temporary_directory),
            }
        )
    if forward_transport:
        for name in TRANSPORT_ENV_NAMES:
            value = os.environ.get(name)
            if value:
                child[name] = value
    if environment is not None:
        child["UV_PROJECT_ENVIRONMENT"] = str(environment)
    return child


def _run_process(
    command: list[str],
    *,
    environment: dict[str, str],
    timeout: int,
    cwd: Path | None = None,
) -> _ProcessResult:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise OSError("could not create bounded subprocess pipes")

    stdout_capture = _StreamCapture()
    stderr_capture = _StreamCapture()

    def drain(stream: Any, capture: _StreamCapture) -> None:
        try:
            while chunk := stream.read(8192):
                capture.consume(chunk)
        finally:
            stream.close()

    threads = [
        threading.Thread(target=drain, args=(process.stdout, stdout_capture), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr_capture), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        for thread in threads:
            thread.join(timeout=2)
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout_capture.text(),
            stderr=stderr_capture.text(),
        ) from error
    for thread in threads:
        thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads):
        raise OSError("bounded subprocess output reader did not terminate")
    return _ProcessResult(
        returncode=returncode,
        stdout=stdout_capture.text(),
        stderr=stderr_capture.text(),
        stdout_bytes=stdout_capture.total_bytes,
        stderr_bytes=stderr_capture.total_bytes,
        stdout_sha256=stdout_capture.digest.hexdigest(),
        stderr_sha256=stderr_capture.digest.hexdigest(),
    )


def _process_evidence(role: str, completed: _ProcessResult) -> dict[str, Any]:
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = f"{stdout}\n{stderr}".lower()
    markers = []
    for marker, tokens in {
        "proxy_authentication": ("407", "proxy authentication required"),
        "proxy_connection": ("proxy connect", "proxy error"),
        "tls_certificate": ("certificate", "tls", "ssl"),
        "network": ("dns", "resolve host", "network", "timed out"),
        "disk": ("no space", "disk quota"),
        "wheel_unavailable": ("no wheel", "no binary", "build is disabled"),
    }.items():
        if any(token in combined for token in tokens):
            markers.append(marker)
    transport_present = role == "uv_sync" and any(
        os.environ.get(name) for name in TRANSPORT_ENV_NAMES
    )
    return {
        "role": role,
        "returncode": completed.returncode,
        "stdout_bytes": completed.stdout_bytes,
        "stderr_bytes": completed.stderr_bytes,
        "stdout_sha256": completed.stdout_sha256,
        "stderr_sha256": completed.stderr_sha256,
        "stdout_truncated": completed.stdout_bytes > MAX_CAPTURE_BYTES,
        "stderr_truncated": completed.stderr_bytes > MAX_CAPTURE_BYTES,
        "stdout_tail": (
            "[withheld because transport configuration was forwarded]"
            if transport_present
            else _redact(stdout)
        ),
        "stderr_tail": (
            "[withheld because transport configuration was forwarded]"
            if transport_present
            else _redact(stderr)
        ),
        "diagnostic_markers": markers,
    }


def _resolve_executable(raw: str | None, label: str) -> Path:
    candidate = raw if raw is not None else shutil.which(label)
    if not candidate:
        raise BootstrapError(f"{label} executable was not found")
    try:
        path = Path(candidate).resolve(strict=True)
    except OSError as error:
        raise BootstrapError(f"{label} executable does not exist") from error
    if not path.is_file() or not os.access(path, os.X_OK):
        raise BootstrapError(f"{label} path is not an executable file")
    status = path.stat()
    allowed_owners = {os.getuid(), 0} if hasattr(os, "getuid") else {status.st_uid}
    if status.st_uid not in allowed_owners:
        raise BootstrapError(f"{label} executable must be owned by the current user or root")
    if stat.S_IMODE(status.st_mode) & 0o022:
        raise BootstrapError(f"{label} executable must not be group/world writable")
    return path


def _file_binding(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise BootstrapError(f"required file is missing or not regular: {path.name}")
    status = path.stat()
    allowed_owners = {os.getuid(), 0} if hasattr(os, "getuid") else {status.st_uid}
    if status.st_uid not in allowed_owners:
        raise BootstrapError(f"required file has an untrusted owner: {path.name}")
    mode = stat.S_IMODE(status.st_mode)
    if mode & 0o022:
        raise BootstrapError(f"required file is group/world writable: {path.name}")
    return {
        "path": str(path.resolve(strict=True)),
        "sha256": _sha256(path),
        "size": status.st_size,
        "owner_uid": status.st_uid,
        "mode": mode,
        "device": status.st_dev,
        "inode": status.st_ino,
    }


def _probe_python(path: Path) -> dict[str, Any]:
    program = (
        "import json,platform,sys;"
        "print(json.dumps({'implementation':platform.python_implementation(),"
        "'version':list(sys.version_info[:3]),'executable':sys.executable}))"
    )
    try:
        completed = _run_process(
            [str(path), "-I", "-S", "-c", program],
            environment=_child_environment(),
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BootstrapError("could not run the selected Python interpreter") from error
    if completed.returncode != 0 or len(completed.stdout.encode()) > MAX_CAPTURE_BYTES:
        raise BootstrapError("the selected Python interpreter failed its fixed probe")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise BootstrapError(
            "the selected Python interpreter returned invalid probe data"
        ) from error
    version = value.get("version")
    if (
        value.get("implementation") != "CPython"
        or not isinstance(version, list)
        or len(version) != 3
        or not all(isinstance(part, int) for part in version)
    ):
        raise BootstrapError("the selected interpreter must be CPython")
    binding = _file_binding(path)
    binding.update(
        {
            "version": ".".join(str(part) for part in version),
            "supported": tuple(version[:2]) == (3, 12),
        }
    )
    return binding


def _select_python(raw: str | None) -> Path:
    if raw is not None:
        return _resolve_executable(raw, "python")
    candidates = [
        candidate for candidate in (shutil.which("python3.12"), sys.executable) if candidate
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            path = Path(candidate).resolve(strict=True)
            if path in seen:
                continue
            seen.add(path)
            if _probe_python(path)["supported"]:
                return path
        except BootstrapError:
            continue
    raise BootstrapError("no supported CPython 3.12 interpreter was found")


def _probe_uv(path: Path) -> dict[str, Any]:
    try:
        completed = _run_process(
            [str(path), "--version"], environment=_child_environment(), timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BootstrapError("could not run uv") from error
    observed = completed.stdout.strip()
    match = UV_VERSION_RE.fullmatch(observed)
    if completed.returncode != 0 or match is None:
        raise BootstrapError("uv returned an unsupported version string")
    parsed = tuple(int(match.group(index)) for index in (1, 2, 3))
    binding = _file_binding(path)
    binding.update(
        {
            "version": observed.removeprefix("uv "),
            "supported": parsed >= MINIMUM_UV_VERSION,
        }
    )
    return binding


def _version_tuple(raw: str) -> tuple[int, ...] | None:
    if not raw:
        return None
    parts = raw.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _platform_probe() -> dict[str, Any]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    libc_name, libc_version = platform.libc_ver()
    os_version = platform.mac_ver()[0] if system == "darwin" else ""
    reason: str | None
    if system == "darwin":
        parsed = _version_tuple(os_version)
        supported = machine in {"arm64", "aarch64"} and parsed is not None
        supported = supported and parsed[:2] >= MINIMUM_MACOS_VERSION
        reason = None if supported else "requires Apple-silicon macOS 12 or newer"
    elif system == "linux":
        parsed = _version_tuple(libc_version)
        supported = (
            machine in {"x86_64", "amd64"}
            and libc_name.lower() == "glibc"
            and parsed is not None
            and parsed[:2] >= MINIMUM_GLIBC_VERSION
        )
        reason = None if supported else "requires glibc 2.27+ Linux on x86_64"
    else:
        supported = False
        reason = "requires Apple-silicon macOS or glibc Linux x86_64"
    return {
        "system": system,
        "machine": machine,
        "os_version": os_version,
        "libc_name": libc_name,
        "libc_version": libc_version,
        "supported": supported,
        "reason": reason,
    }


def _asset_bindings() -> dict[str, dict[str, Any]]:
    bindings = {}
    for name, (path, staged_relative) in ASSET_LAYOUT.items():
        binding = _file_binding(path)
        binding["staged_relative_path"] = staged_relative
        bindings[name] = binding
    if bindings["jubik_wheel"]["sha256"] != JUBIK_WHEEL_SHA256:
        raise BootstrapError("bundled J-UBIK wheel SHA-256 is not the reviewed value")
    return bindings


def doctor(python_raw: str | None, uv_raw: str | None) -> dict[str, Any]:
    try:
        python = _probe_python(_select_python(python_raw))
    except BootstrapError as error:
        python = {"path": None, "version": None, "supported": False, "error": str(error)}
    try:
        uv = _probe_uv(_resolve_executable(uv_raw, "uv"))
    except BootstrapError as error:
        uv = {"path": None, "version": None, "supported": False, "error": str(error)}
    try:
        assets = _asset_bindings()
        assets_valid = True
    except BootstrapError as error:
        assets = {"error": str(error)}
        assets_valid = False
    host = _platform_probe()
    checks = {
        "supported_python": python["supported"],
        "supported_uv": uv["supported"],
        "supported_platform": host["supported"],
        "bundled_assets_valid": assets_valid,
    }
    return {
        "schema_version": 2,
        "action": "doctor",
        "ready_to_plan": all(checks.values()),
        "checks": checks,
        "python": python,
        "uv": uv,
        "host": host,
        "assets": assets,
        "network_checked": False,
        "secrets_required": False,
        "transport_environment_names_present": sorted(
            name for name in TRANSPORT_ENV_NAMES if os.environ.get(name)
        ),
    }


def _git_worktree_root(path: Path) -> Path | None:
    for ancestor in (path, *path.parents):
        marker = ancestor / ".git"
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise BootstrapError("could not inspect Git worktree boundary") from error
        return ancestor
    return None


def _private_parent(raw_path: Path, label: str) -> Path:
    try:
        parent = raw_path.parent.resolve(strict=True)
    except OSError as error:
        raise BootstrapError(f"{label} parent must already exist") from error
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        raise BootstrapError(f"{label} parent must be a writable directory")
    status = parent.stat()
    if hasattr(os, "getuid") and status.st_uid != os.getuid():
        raise BootstrapError(f"{label} parent must be owned by the current user")
    if stat.S_IMODE(status.st_mode) & 0o022:
        raise BootstrapError(f"{label} parent must not be group/world writable")
    root = _git_worktree_root(parent)
    if root is not None:
        raise BootstrapError(f"{label} must be outside Git worktrees (found {root})")
    return parent


def _resolve_new_path(raw: str, label: str, *, disk_check: bool = False) -> tuple[Path, int]:
    path = Path(raw)
    if not path.is_absolute():
        raise BootstrapError(f"{label} must be an absolute path")
    parent = _private_parent(path, label)
    path = parent / path.name
    if path.exists() or path.is_symlink():
        raise BootstrapError(f"{label} must not already exist")
    free_bytes = shutil.disk_usage(parent).free
    if disk_check and free_bytes < MINIMUM_FREE_BYTES:
        raise BootstrapError(f"{label} parent has less than 2 GiB free")
    return path, free_bytes


def _derived_paths(environment: Path) -> dict[str, str]:
    parent = environment.parent
    stem = environment.name
    return {
        "environment": str(environment),
        "stage": str(parent / f".{stem}.crs-jubik-stage"),
        "cache": str(parent / f".{stem}.crs-jubik-cache"),
        "report": str(environment / "crs-jubik-core-report.json"),
        "state": str(environment / "crs-jubik-bootstrap-state.json"),
        "provenance_assets": str(environment / ".crs-jubik-provenance"),
    }


def _commands(paths: dict[str, str], python_path: str, uv_path: str) -> dict[str, list[str]]:
    stage = Path(paths["stage"])
    environment = Path(paths["environment"])
    sync = [
        uv_path,
        "sync",
        "--frozen",
        "--no-build",
        "--no-dev",
        "--no-install-project",
        "--project",
        str(stage),
        "--python",
        python_path,
        "--default-index",
        "https://pypi.org/simple",
        "--index-strategy",
        "first-index",
        "--keyring-provider",
        "disabled",
        "--no-python-downloads",
        "--link-mode",
        "copy",
        "--no-cache",
        "--no-progress",
        "--no-config",
    ]
    return {
        "sync": sync,
        "sync_check": [*sync, "--check", "--offline"],
        "python_binding": [
            str(environment / "bin" / "python"),
            "-I",
            "-B",
            "-c",
            PYTHON_BINDING_PROGRAM,
        ],
        "pip_check": [
            uv_path,
            "pip",
            "check",
            "--python",
            str(environment / "bin" / "python"),
            "--no-python-downloads",
            "--no-cache",
            "--no-config",
        ],
        "smoke": [
            str(environment / "bin" / "python"),
            "-I",
            "-B",
            str(stage / "scripts" / SMOKE_SCRIPT.name),
            "--output",
            paths["report"],
            "--wheel",
            str(stage / "wheels" / WHEEL_FILE.name),
            "--cache-dir",
            paths["cache"],
        ],
    }


def create_plan(
    environment_raw: str,
    output_raw: str,
    python_raw: str | None,
    uv_raw: str | None,
) -> dict[str, Any]:
    environment, free_bytes = _resolve_new_path(environment_raw, "--environment", disk_check=True)
    plan_path, _ = _resolve_new_path(output_raw, "--output")
    paths = _derived_paths(environment)
    paths["plan"] = str(plan_path)
    for label in ("stage", "cache"):
        candidate = Path(paths[label])
        if candidate.exists() or candidate.is_symlink():
            raise BootstrapError(f"planned {label} path must not already exist")
    if len(set(paths.values())) != len(paths):
        raise BootstrapError("planned paths must be distinct")

    report = doctor(python_raw, uv_raw)
    if not report["ready_to_plan"]:
        raise BootstrapError(
            "doctor checks did not pass; inspect the attached doctor evidence",
            category="doctor_failed",
            evidence={"doctor": report},
        )
    plan: dict[str, Any] = {
        "schema_version": 2,
        "action": "create-jubik-core-environment",
        "executed": False,
        "paths": paths,
        "python": report["python"],
        "uv": report["uv"],
        "host": report["host"],
        "assets": report["assets"],
        "free_bytes_at_plan": free_bytes,
        "minimum_free_bytes": MINIMUM_FREE_BYTES,
        "sources": {
            "jubik_release": "v0.3",
            "jubik_repository": "https://github.com/NIFTy-PPL/J-UBIK",
            "jubik_commit": JUBIK_COMMIT,
            "jubik_tree": JUBIK_TREE,
            "jubik_wheel_sha256": JUBIK_WHEEL_SHA256,
            "nifty_version": EXPECTED_NIFTY_VERSION,
            "package_index": "https://pypi.org/simple",
        },
        "network_hosts": ["pypi.org", "files.pythonhosted.org"],
        "secrets": {
            "required": False,
            "stored": False,
            "forwarded_transport_environment_names": report["transport_environment_names_present"],
        },
        "build_policy": {
            "source_builds_allowed": False,
            "bundled_jubik_wheel": True,
        },
        "commands": _commands(paths, report["python"]["path"], report["uv"]["path"]),
        "post_install": [
            "offline uv sync --check",
            "uv pip check",
            "installed RECORD and bounded CPU-only SkyModel smoke",
        ],
        "instrument_adapters_in_scope": [],
        "scientific_research_ready": False,
    }
    plan["plan_sha256"] = hashlib.sha256(_canonical_json(plan)).hexdigest()
    return plan


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(payload.encode("utf-8")) > MAX_JSON_BYTES:
        raise BootstrapError("JSON artifact exceeded the size limit")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            path.unlink()
        raise
    finally:
        os.close(descriptor)


def save_plan(plan: dict[str, Any]) -> Path:
    path = Path(plan["paths"]["plan"])
    if path.exists() or path.is_symlink():
        raise BootstrapError("plan output appeared before it could be persisted")
    _write_private_json(path, plan)
    return path


def load_plan(raw: str) -> tuple[Path, dict[str, Any]]:
    path = Path(raw)
    if not path.is_absolute():
        raise BootstrapError("--plan must be an absolute path")
    if path.is_symlink():
        raise BootstrapError("--plan must not be a symlink")
    try:
        path = path.resolve(strict=True)
    except OSError as error:
        raise BootstrapError("--plan does not exist") from error
    if not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise BootstrapError("--plan is not a bounded regular file")
    status = path.stat()
    if hasattr(os, "getuid") and status.st_uid != os.getuid():
        raise BootstrapError("--plan must be owned by the current user")
    if stat.S_IMODE(status.st_mode) & 0o077:
        raise BootstrapError("--plan must be private")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
    except OSError as error:
        raise BootstrapError("--plan could not be read safely") from error
    if len(raw) > MAX_JSON_BYTES:
        raise BootstrapError("--plan exceeded the size limit")
    try:
        plan = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BootstrapError("--plan is invalid JSON") from error
    if not isinstance(plan, dict):
        raise BootstrapError("--plan must contain one JSON object")
    claimed = plan.get("plan_sha256")
    unsigned = dict(plan)
    unsigned.pop("plan_sha256", None)
    observed = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    if not isinstance(claimed, str) or claimed != observed:
        raise BootstrapError("--plan content digest is invalid")
    return path, plan


def _validate_plan(plan_path: Path, plan: dict[str, Any], confirmation: str) -> None:
    if not PLAN_DIGEST_RE.fullmatch(confirmation) or confirmation != plan.get("plan_sha256"):
        raise BootstrapError("--confirm must exactly match the persisted plan_sha256")
    try:
        paths = plan["paths"]
        python_path = plan["python"]["path"]
        uv_path = plan["uv"]["path"]
    except (KeyError, TypeError) as error:
        raise BootstrapError("persisted plan schema is incomplete") from error
    if (
        plan.get("schema_version") != 2
        or plan.get("action") != "create-jubik-core-environment"
        or plan.get("executed") is not False
        or plan.get("instrument_adapters_in_scope") != []
        or plan.get("scientific_research_ready") is not False
        or paths.get("plan") != str(plan_path)
    ):
        raise BootstrapError("persisted plan schema or readiness boundary is invalid")

    environment, _ = _resolve_new_path(paths["environment"], "planned environment", disk_check=True)
    expected_paths = _derived_paths(environment)
    expected_paths["plan"] = str(plan_path)
    if paths != expected_paths:
        raise BootstrapError("persisted plan path bindings are invalid")
    for label in ("stage", "cache"):
        candidate = Path(paths[label])
        if candidate.exists() or candidate.is_symlink():
            raise BootstrapError(f"planned {label} path appeared after planning")

    current = doctor(python_path, uv_path)
    if not current["ready_to_plan"]:
        raise BootstrapError(
            "doctor checks no longer pass for the persisted plan",
            category="doctor_changed",
            evidence={"doctor": current},
        )
    if current["python"] != plan.get("python") or current["uv"] != plan.get("uv"):
        raise BootstrapError("planned executable binding changed")
    if current["host"] != plan.get("host") or current["assets"] != plan.get("assets"):
        raise BootstrapError("planned host or asset binding changed")
    if current["transport_environment_names_present"] != plan.get("secrets", {}).get(
        "forwarded_transport_environment_names"
    ):
        raise BootstrapError("transport environment names changed after planning")
    expected_commands = _commands(paths, python_path, uv_path)
    if plan.get("commands") != expected_commands:
        raise BootstrapError("persisted plan commands are invalid")


def _copy_exclusive(source: Path, destination: Path, expected_sha256: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        with (
            source.open("rb") as source_stream,
            os.fdopen(descriptor, "wb", closefd=False) as destination_stream,
        ):
            shutil.copyfileobj(source_stream, destination_stream, 1024 * 1024)
            destination_stream.flush()
            os.fsync(destination_stream.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            destination.unlink()
        raise
    finally:
        os.close(descriptor)
    if _sha256(destination) != expected_sha256:
        raise BootstrapError("a privately staged asset failed its SHA-256 check")


def _stage_assets(plan_path: Path, plan: dict[str, Any]) -> Path:
    stage = Path(plan["paths"]["stage"])
    try:
        stage.mkdir(mode=0o700)
        (stage / "wheels").mkdir(mode=0o700)
        (stage / "scripts").mkdir(mode=0o700)
        (stage / ".tmp").mkdir(mode=0o700)
        for binding in plan["assets"].values():
            destination = stage / binding["staged_relative_path"]
            _copy_exclusive(Path(binding["path"]), destination, binding["sha256"])
        _copy_exclusive(plan_path, stage / "crs-jubik-install-plan.json", _sha256(plan_path))
    except BaseException as error:
        if isinstance(error, BootstrapError):
            raise
        raise BootstrapError(
            "could not privately stage the confirmed assets",
            retained_paths={"stage": str(stage), "plan": str(plan_path)},
        ) from error
    return stage


def _retained(plan: dict[str, Any]) -> dict[str, str]:
    paths = plan["paths"]
    retained = {"plan": paths["plan"], "environment": paths["environment"]}
    for label in ("stage", "cache", "report", "provenance_assets"):
        if Path(paths[label]).exists() or Path(paths[label]).is_symlink():
            retained[label] = paths[label]
    return retained


def _classify_install_failure(output: str) -> tuple[str, str]:
    lowered = output.lower()
    if "407" in lowered or "proxy authentication required" in lowered:
        return "proxy_authentication_failed", "proxy authentication failed (HTTP 407)"
    if "proxy connect" in lowered or "proxy error" in lowered:
        return "proxy_connection_failed", "the configured proxy connection failed"
    if any(token in lowered for token in ("certificate", "tls", "ssl")):
        return "tls_certificate_failed", "TLS or certificate validation failed"
    if any(token in lowered for token in ("dns", "resolve host", "network", "timed out")):
        return "network_failed", "public package network access failed"
    if any(token in lowered for token in ("no space", "disk quota")):
        return "disk_full", "the destination ran out of disk space"
    if any(token in lowered for token in ("no wheel", "no binary", "build is disabled")):
        return "wheel_unavailable", "a required locked wheel is unavailable for this host"
    return "uv_sync_failed", "uv sync failed"


def _run_checked_process(
    role: str,
    command: list[str],
    *,
    child_environment: dict[str, str],
    timeout: int,
    plan: dict[str, Any],
    cwd: Path | None = None,
) -> dict[str, Any]:
    try:
        completed = _run_process(command, environment=child_environment, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as error:
        raise BootstrapError(
            f"{role} exceeded its time limit",
            category=f"{role}_timeout",
            retained_paths=_retained(plan),
        ) from error
    except OSError as error:
        raise BootstrapError(
            f"{role} could not start",
            category=f"{role}_start_failed",
            retained_paths=_retained(plan),
        ) from error
    evidence = _process_evidence(role, completed)
    if completed.returncode != 0:
        if role == "uv_sync":
            category, message = _classify_install_failure(f"{completed.stdout}\n{completed.stderr}")
        else:
            category, message = f"{role}_failed", f"{role} failed"
        raise BootstrapError(
            message,
            category=category,
            evidence=evidence,
            retained_paths=_retained(plan),
        )
    return evidence


def _environment_python(environment: Path) -> Path:
    candidate = environment / "bin" / "python"
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise BootstrapError("created environment has no Python executable") from error
    if not candidate.is_file() or not resolved.is_file() or not os.access(candidate, os.X_OK):
        raise BootstrapError("created environment Python is not executable")
    return candidate


def _verify_environment_python(plan: dict[str, Any]) -> dict[str, Any]:
    command = plan["commands"]["python_binding"]
    try:
        completed = _run_process(
            command,
            environment=_child_environment(),
            timeout=10,
            cwd=Path(plan["paths"]["stage"]),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BootstrapError(
            "created environment Python binding probe failed",
            category="python_binding_probe_failed",
            retained_paths=_retained(plan),
        ) from error
    evidence = _process_evidence("python_binding", completed)
    if completed.returncode != 0 or completed.stdout_bytes > MAX_CAPTURE_BYTES:
        raise BootstrapError(
            "created environment Python binding probe failed",
            category="python_binding_probe_failed",
            evidence=evidence,
            retained_paths=_retained(plan),
        )
    try:
        observed = json.loads(completed.stdout)
        base_executable = Path(observed["base_executable"]).resolve(strict=True)
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as error:
        raise BootstrapError(
            "created environment Python returned invalid binding evidence",
            category="python_binding_invalid",
            evidence=evidence,
            retained_paths=_retained(plan),
        ) from error
    if (
        str(base_executable) != plan["python"]["path"]
        or observed.get("version") != [int(part) for part in plan["python"]["version"].split(".")]
        or observed.get("prefix") != plan["paths"]["environment"]
    ):
        raise BootstrapError(
            "created environment is not bound to the planned Python interpreter",
            category="python_binding_mismatch",
            evidence=evidence,
            retained_paths=_retained(plan),
        )
    evidence["planned_base_executable"] = plan["python"]["path"]
    evidence["environment_prefix"] = observed["prefix"]
    return evidence


def _load_report(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise BootstrapError("J-UBIK smoke report must not be a symlink")
    try:
        status = path.stat()
    except OSError as error:
        raise BootstrapError("J-UBIK smoke report was not created") from error
    if not path.is_file() or status.st_size > MAX_JSON_BYTES:
        raise BootstrapError("J-UBIK smoke report exceeded the size limit")
    if hasattr(os, "getuid") and status.st_uid != os.getuid():
        raise BootstrapError("J-UBIK smoke report has an unexpected owner")
    if stat.S_IMODE(status.st_mode) & 0o077:
        raise BootstrapError("J-UBIK smoke report is not private")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
    except OSError as error:
        raise BootstrapError("J-UBIK smoke report could not be read safely") from error
    if len(raw) > MAX_JSON_BYTES:
        raise BootstrapError("J-UBIK smoke report exceeded the size limit")
    try:
        report = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BootstrapError("J-UBIK smoke report is invalid JSON") from error
    if not isinstance(report, dict):
        raise BootstrapError("J-UBIK smoke report is not an object")
    checks = report.get("checks")
    adapters = report.get("adapters")
    expected_adapters = {
        "jwst_adapter_ready": False,
        "chandra_adapter_ready": False,
        "erosita_adapter_ready": False,
    }
    if (
        report.get("schema_version") != 2
        or report.get("core_ready") is not True
        or report.get("scientific_research_ready") is not False
        or not isinstance(checks, dict)
        or not checks
        or not all(value is True for value in checks.values())
        or adapters != expected_adapters
        or report.get("source", {}).get("wheel_sha256") != JUBIK_WHEEL_SHA256
        or report.get("source", {}).get("upstream_commit") != JUBIK_COMMIT
    ):
        raise BootstrapError("J-UBIK smoke report did not establish bounded core readiness")
    claimed = report.get("content_sha256")
    unsigned = dict(report)
    unsigned.pop("content_sha256", None)
    if claimed != hashlib.sha256(_canonical_json(unsigned)).hexdigest():
        raise BootstrapError("J-UBIK smoke report content digest is invalid")
    return report


def _promote_stage(plan: dict[str, Any]) -> Path:
    stage = Path(plan["paths"]["stage"])
    temporary = stage / ".tmp"
    if temporary.exists():
        shutil.rmtree(temporary)
    provenance = Path(plan["paths"]["provenance_assets"])
    if provenance.exists() or provenance.is_symlink():
        raise BootstrapError("provenance asset destination unexpectedly exists")
    os.replace(stage, provenance)
    return provenance


def _write_state(plan: dict[str, Any], report: dict[str, Any], evidence: dict[str, Any]) -> Path:
    state_path = Path(plan["paths"]["state"])
    state = {
        "schema_version": 2,
        "status": "core_ready",
        "plan": plan,
        "plan_sha256": plan["plan_sha256"],
        "report": plan["paths"]["report"],
        "report_content_sha256": report["content_sha256"],
        "cache_root": plan["paths"]["cache"],
        "provenance_assets": plan["paths"]["provenance_assets"],
        "process_evidence": evidence,
        "scientific_research_ready": False,
        "adapters": report["adapters"],
    }
    _write_private_json(state_path, state)
    return state_path


def apply_plan(plan_path: Path, plan: dict[str, Any], confirmation: str) -> dict[str, Any]:
    _validate_plan(plan_path, plan, confirmation)
    stage = _stage_assets(plan_path, plan)
    environment = Path(plan["paths"]["environment"])
    cache = Path(plan["paths"]["cache"])
    temporary = stage / ".tmp"
    process_evidence: dict[str, Any] = {}

    process_evidence["uv_sync"] = _run_checked_process(
        "uv_sync",
        plan["commands"]["sync"],
        child_environment=_child_environment(
            environment=environment,
            forward_transport=True,
            temporary_directory=temporary,
        ),
        timeout=900,
        plan=plan,
        cwd=stage,
    )
    if not environment.is_dir() or environment.is_symlink():
        raise BootstrapError(
            "uv did not create a regular environment directory",
            retained_paths=_retained(plan),
        )
    os.chmod(environment, 0o700)
    _environment_python(environment)
    process_evidence["python_binding"] = _verify_environment_python(plan)

    process_evidence["uv_sync_check"] = _run_checked_process(
        "uv_sync_check",
        plan["commands"]["sync_check"],
        child_environment=_child_environment(
            environment=environment, temporary_directory=temporary
        ),
        timeout=120,
        plan=plan,
        cwd=stage,
    )
    process_evidence["uv_pip_check"] = _run_checked_process(
        "uv_pip_check",
        plan["commands"]["pip_check"],
        child_environment=_child_environment(temporary_directory=temporary),
        timeout=60,
        plan=plan,
        cwd=stage,
    )

    cache.mkdir(mode=0o700)
    child = _child_environment(temporary_directory=temporary)
    child["JAX_PLATFORMS"] = "cpu"
    child["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"
    process_evidence["smoke"] = _run_checked_process(
        "smoke",
        plan["commands"]["smoke"],
        child_environment=child,
        timeout=180,
        plan=plan,
        cwd=stage,
    )
    report = _load_report(Path(plan["paths"]["report"]))

    current_assets = _asset_bindings()
    if current_assets != plan["assets"]:
        raise BootstrapError("bundled assets changed during create", retained_paths=_retained(plan))
    provenance = _promote_stage(plan)
    state_path = _write_state(plan, report, process_evidence)
    return {
        "schema_version": 2,
        "status": "core_ready",
        "environment": str(environment),
        "report": plan["paths"]["report"],
        "state": str(state_path),
        "cache_root": str(cache),
        "provenance_assets": str(provenance),
        "plan_sha256": plan["plan_sha256"],
        "report_content_sha256": report["content_sha256"],
        "scientific_research_ready": False,
        "adapters": report["adapters"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preflight, persist, and apply a wheel-only J-UBIK core plan."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--python")
    doctor_parser.add_argument("--uv")
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--environment", required=True)
    plan_parser.add_argument("--output", required=True)
    plan_parser.add_argument("--python")
    plan_parser.add_argument("--uv")
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--plan", required=True)
    create_parser.add_argument("--confirm", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor(args.python, args.uv)
            exit_code = 0 if result["ready_to_plan"] else 1
        elif args.command == "plan":
            result = create_plan(args.environment, args.output, args.python, args.uv)
            save_plan(result)
            exit_code = 0
        else:
            plan_path, plan = load_plan(args.plan)
            result = apply_plan(plan_path, plan, args.confirm)
            exit_code = 0
    except BootstrapError as error:
        print(json.dumps(error.as_report(), sort_keys=True, allow_nan=False), file=sys.stderr)
        return 2
    except (KeyError, OSError, TypeError, ValueError):
        failure = BootstrapError("bootstrap encountered an invalid local artifact")
        print(json.dumps(failure.as_report(), sort_keys=True, allow_nan=False), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
