#!/usr/bin/env python3
"""Run a fixed, bounded NIFTy.re certification benchmark with an analytic answer."""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import csv
import hashlib
import importlib.metadata
import io
import json
import logging
import math
import operator
import os
import platform
import re
import stat
import sys
import warnings
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

EXPECTED_NIFTY_VERSION = "9.2.0"
NIFTY_RELEASE_COMMIT = "17093190a42cde92a1c3922c57d441bbd8b6f9a1"
NIFTY_WHEEL_SHA256 = "c9184918df1aa7b1894d77cca74f0a98b8594ebdc8773477168ea3d9143c105c"
NIFTY_REVIEWED_RECORD_PAYLOAD_SHA256 = (
    "d488afa7a6838e11b2e5a1e6a48eb0dd6fe8e79ab1993353a39b427ba6a613ec"
)
REVIEWED_LOCK_SHA256 = "befa2e27ab52682e712c4e2ed59059bc8acfb9a8af551394f29d66486592d419"
REVIEWED_PYPROJECT_SHA256 = "cc4ee43efa33703ec32c86310ae412ffc0b3490f7710d891ec55e0fa8c76f362"
CERTIFICATION_SEED = 42
CERTIFICATION_SAMPLE_PAIRS = 128
MAX_CAPTURED_MESSAGES = 40
MAX_CAPTURED_MESSAGE_CHARS = 500
MAX_REPORT_BYTES = 2_000_000
TRANSPORT_ENV_NAMES = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}
FIXED_RUNTIME_ENV_NAMES = {"CUDA_VISIBLE_DEVICES", *TRANSPORT_ENV_NAMES}
CERTIFICATION_ENV_NAMES = {
    "HOME",
    "LANG",
    "LC_ALL",
    "PYTHONDONTWRITEBYTECODE",
    "TMPDIR",
}
DARWIN_INJECTED_ENV_NAMES = {"__CF_USER_TEXT_ENCODING"}
MAX_RECORD_BYTES = 1_000_000
MAX_RECORD_ENTRIES = 1_000

EXPECTED_DISTRIBUTIONS = {
    "ducc0": "0.41.0",
    "jax": "0.11.0",
    "jaxbind": "1.3.1",
    "jaxlib": "0.11.0",
    "ml-dtypes": "0.5.4",
    "nifty": EXPECTED_NIFTY_VERSION,
    "numpy": "2.5.1",
    "opt-einsum": "3.4.0",
    "packaging": "26.2",
    "scipy": "1.18.0",
}

SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
SOURCE_REPOSITORY_ROOT = SCRIPT_PATH.parents[3]
ENVIRONMENT_ASSET = SKILL_ROOT / "assets" / "nifty-re-certification-environment"
LOCK_PATH = ENVIRONMENT_ASSET / "uv.lock"
ENVIRONMENT_PYPROJECT = ENVIRONMENT_ASSET / "pyproject.toml"


class WorkflowError(RuntimeError):
    """A bounded workflow error suitable for showing to a user."""


class _BoundedMessageStore:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []
        self.dropped = 0

    def add(self, category: str, message: str) -> None:
        if len(self.messages) >= MAX_CAPTURED_MESSAGES:
            self.dropped += 1
            return
        normalized = " ".join(str(message).split())
        self.messages.append(
            {
                "category": str(category)[:100],
                "message": normalized[:MAX_CAPTURED_MESSAGE_CHARS],
            }
        )

    def report(self) -> dict[str, Any]:
        return {
            "captured": self.messages,
            "dropped": self.dropped,
            "maximum_captured": MAX_CAPTURED_MESSAGES,
            "maximum_message_characters": MAX_CAPTURED_MESSAGE_CHARS,
        }


class _BoundedLogHandler(logging.Handler):
    def __init__(self, store: _BoundedMessageStore) -> None:
        super().__init__(level=logging.WARNING)
        self._store = store

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception as error:  # pragma: no cover - defensive logging boundary
            message = f"unformattable logging record: {type(error).__name__}"
        self._store.add(f"{record.name}:{record.levelname}", message)


class _BoundedWarningCapture:
    def __init__(self) -> None:
        self.store = _BoundedMessageStore()
        self._context = warnings.catch_warnings()
        self._previous_showwarning: Any = None

    def __enter__(self) -> _BoundedWarningCapture:
        self._context.__enter__()
        warnings.simplefilter("always")
        self._previous_showwarning = warnings.showwarning
        warnings.showwarning = self._showwarning
        return self

    def _showwarning(
        self,
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: Any = None,
        line: str | None = None,
    ) -> None:
        del filename, lineno, file, line
        self.store.add(category.__name__, str(message))

    def __exit__(self, *exc_info: Any) -> None:
        warnings.showwarning = self._previous_showwarning
        self._context.__exit__(*exc_info)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sanitize_for_json(value: Any) -> tuple[Any, int]:
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "nonfinite:nan", 1
        return ("nonfinite:positive-infinity" if value > 0 else "nonfinite:negative-infinity"), 1
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        replacements = 0
        for key, item in value.items():
            safe_item, item_replacements = _sanitize_for_json(item)
            sanitized[str(key)] = safe_item
            replacements += item_replacements
        return sanitized, replacements
    if isinstance(value, (list, tuple)):
        sanitized_items = []
        replacements = 0
        for item in value:
            safe_item, item_replacements = _sanitize_for_json(item)
            sanitized_items.append(safe_item)
            replacements += item_replacements
        return sanitized_items, replacements
    return value, 0


def _canonical_payload_sha256(report: dict[str, Any]) -> str:
    payload = dict(report)
    payload.pop("canonical_payload_sha256", None)
    return _sha256_bytes(_canonical_json(payload))


def _finalize_report(report: dict[str, Any]) -> dict[str, Any]:
    sanitized, replacements = _sanitize_for_json(report)
    sanitized["serialization"] = {
        "nonfinite_values_replaced": replacements,
        "replacement_encoding": [
            "nonfinite:nan",
            "nonfinite:positive-infinity",
            "nonfinite:negative-infinity",
        ],
    }
    sanitized["canonical_payload_sha256"] = _canonical_payload_sha256(sanitized)
    return sanitized


def _canonical_payload_digest_is_valid(report: dict[str, Any]) -> bool:
    observed = report.get("canonical_payload_sha256")
    return isinstance(observed, str) and observed == _canonical_payload_sha256(report)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _inside_git_worktree(path: Path) -> bool:
    """Detect ordinary and linked Git worktrees without invoking ambient Git config."""

    return any(
        marker.exists() or marker.is_symlink()
        for candidate in (path, *path.parents)
        for marker in (candidate / ".git",)
    )


def _resolve_new_path(raw: str, option: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise WorkflowError(f"{option} must be an absolute path")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise WorkflowError(f"{option} parent must be an existing directory") from error
    path = parent / path.name
    if path.exists() or path.is_symlink():
        raise WorkflowError(f"{option} must not already exist")
    if not parent.is_dir():
        raise WorkflowError(f"{option} parent must be an existing directory")
    if not os.access(parent, os.W_OK):
        raise WorkflowError(f"{option} parent is not writable")
    if _is_within(path, SOURCE_REPOSITORY_ROOT) or _inside_git_worktree(parent):
        raise WorkflowError(f"{option} must be outside every Git worktree")
    return path


def _resolve_new_output(raw: str) -> Path:
    return _resolve_new_path(raw, "--output")


def _create_private_cache(raw: str) -> Path:
    if os.name != "posix":
        raise WorkflowError("the reviewed certification currently requires a POSIX host")
    path = _resolve_new_path(raw, "--cache-dir")
    os.mkdir(path, 0o700)
    os.chmod(path, 0o700)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o700:
        raise WorkflowError(f"--cache-dir mode must be 0700, observed {mode:04o}")
    return path


def _resolve_existing_report(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise WorkflowError("--verify-report must be an absolute path")
    if path.is_symlink():
        raise WorkflowError("--verify-report must not be a symbolic link")
    try:
        path = path.resolve(strict=True)
    except OSError as error:
        raise WorkflowError("--verify-report must be an existing file") from error
    if not path.is_file():
        raise WorkflowError("--verify-report must be an existing file")
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise WorkflowError(f"--verify-report exceeds the {MAX_REPORT_BYTES}-byte limit")
    return path


def _write_report(path: Path, report: dict[str, Any]) -> None:
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        raise
    finally:
        os.close(descriptor)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _verify_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant)
    if not isinstance(report, dict):
        raise WorkflowError("report root must be a JSON object")
    digest_valid = _canonical_payload_digest_is_valid(report)
    return {
        "canonical_payload_digest_valid": digest_valid,
        "canonical_payload_sha256": report.get("canonical_payload_sha256"),
        "report_file_sha256": _sha256_file(path),
        "report": str(path),
    }


def _private_runtime_directory(name: str) -> dict[str, Any]:
    raw = os.environ.get(name)
    if not raw:
        raise WorkflowError(f"certification requires {name} in its clean environment")
    supplied = Path(raw)
    if not supplied.is_absolute() or supplied.is_symlink():
        raise WorkflowError(f"certification {name} must be an absolute, non-symlink directory")
    try:
        path = supplied.resolve(strict=True)
        status = path.stat()
    except OSError as error:
        raise WorkflowError(f"certification {name} must already exist") from error
    if not path.is_dir():
        raise WorkflowError(f"certification {name} must be a directory")
    if not os.access(path, os.W_OK | os.X_OK):
        raise WorkflowError(f"certification {name} must be writable and searchable")
    if hasattr(os, "getuid") and status.st_uid != os.getuid():
        raise WorkflowError(f"certification {name} must be owned by the current user")
    mode = stat.S_IMODE(status.st_mode)
    if mode != 0o700:
        raise WorkflowError(f"certification {name} mode must be 0700, observed {mode:04o}")
    if _is_within(path, SOURCE_REPOSITORY_ROOT) or _inside_git_worktree(path):
        raise WorkflowError(f"certification {name} must be outside every Git worktree")
    return {"path": str(path), "mode": "0700", "owner_uid": status.st_uid}


def _runtime_environment_attestation() -> dict[str, Any]:
    allowed = set(CERTIFICATION_ENV_NAMES) | FIXED_RUNTIME_ENV_NAMES
    if platform.system() == "Darwin":
        allowed.update(DARWIN_INJECTED_ENV_NAMES)
    unexpected = sorted(
        name for name in os.environ if name not in allowed and not name.startswith(("JAX_", "XLA_"))
    )
    if unexpected:
        shown = ", ".join(unexpected[:12])
        suffix = f" (+{len(unexpected) - 12} more)" if len(unexpected) > 12 else ""
        raise WorkflowError(
            "certification requires an env -i allowlisted runtime; unexpected variable "
            f"names: {shown}{suffix}"
        )
    if os.environ.get("LANG") != "C.UTF-8" or os.environ.get("LC_ALL") != "C.UTF-8":
        raise WorkflowError("certification requires LANG=C.UTF-8 and LC_ALL=C.UTF-8")
    return {
        "allowlisted_environment": True,
        "input_environment_names": sorted(os.environ),
        "home": _private_runtime_directory("HOME"),
        "temporary_directory": _private_runtime_directory("TMPDIR"),
        "recorded_environment_value_names": ["HOME", "TMPDIR"],
        "transport_and_control_values_recorded": False,
    }


def _require_certification_process() -> dict[str, Any]:
    if sys.version_info[:2] != (3, 12):
        raise WorkflowError(
            f"certification requires Python 3.12, observed {platform.python_version()}"
        )
    if not (
        bool(sys.flags.isolated)
        and bool(sys.flags.no_user_site)
        and bool(getattr(sys.flags, "safe_path", False))
    ):
        raise WorkflowError("certification must be run with the selected environment's python -I")
    if not sys.dont_write_bytecode:
        raise WorkflowError("certification must disable bytecode writes with python -B")
    if Path(sys.prefix).resolve() == Path(sys.base_prefix).resolve():
        raise WorkflowError("certification must run inside an isolated virtual environment")
    if not _reviewed_platform_abi():
        raise WorkflowError(
            "certification supports the reported macOS arm64 or glibc Linux x86_64 ABI only; "
            f"observed {platform.system()} {platform.machine()} {platform.libc_ver()}"
        )
    return _runtime_environment_attestation()


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return ()


def _reviewed_platform_abi() -> bool:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine == "arm64":
        return _version_tuple(platform.mac_ver()[0]) >= (12,)
    libc_name, libc_version = platform.libc_ver()
    return (
        system == "Linux"
        and machine in {"x86_64", "amd64"}
        and libc_name.lower() == "glibc"
        and _version_tuple(libc_version) >= (2, 27)
    )


def _sanitize_runtime_environment() -> list[str]:
    """Remove inherited transport and JAX/XLA controls before importing scientific code."""

    removed: list[str] = []
    for name in tuple(os.environ):
        if name in FIXED_RUNTIME_ENV_NAMES or name.startswith(("JAX_", "XLA_")):
            removed.append(name)
            del os.environ[name]
    os.environ["JAX_PLATFORMS"] = "cpu"
    os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"
    return sorted(removed)


def _metadata_file(distribution: importlib.metadata.Distribution, name: str) -> Path | None:
    for entry in distribution.files or ():
        entry_path = Path(str(entry))
        if entry_path.name == name and any(
            part.endswith(".dist-info") for part in entry_path.parts
        ):
            return Path(distribution.locate_file(entry)).resolve()
    return None


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _decode_record_hash(encoded: str) -> bytes:
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    return base64.urlsafe_b64decode(encoded + padding)


def _record_payload_fingerprint(rows: list[list[str]]) -> tuple[str, int]:
    retained: list[tuple[str, str, str]] = []
    dynamic_names = {"INSTALLER", "REQUESTED", "RECORD", "direct_url.json"}
    for row in rows:
        if len(row) != 3:
            continue
        relative, encoded_hash, size = row
        if Path(relative).name in dynamic_names or not encoded_hash or not size:
            continue
        retained.append((relative, encoded_hash, size))
    payload = "".join(",".join(row) + "\n" for row in sorted(retained)).encode("utf-8")
    return _sha256_bytes(payload), len(retained)


def _verify_distribution_record(
    distribution: importlib.metadata.Distribution,
) -> dict[str, Any]:
    root = Path(distribution.locate_file("")).resolve()
    record_path = _metadata_file(distribution, "RECORD")
    result: dict[str, Any] = {
        "present": record_path is not None,
        "integrity_verified": False,
        "record_entries": 0,
        "hashed_entries_verified": 0,
        "unhashed_entries": [],
        "payload_entry_count": 0,
        "payload_sha256": None,
        "record_sha256": None,
        "issues": [],
        "recorded_paths": [],
    }
    if record_path is None or not record_path.is_file():
        result["issues"].append("installed distribution has no RECORD file")
        return result
    if record_path.stat().st_size > MAX_RECORD_BYTES:
        result["issues"].append(f"installed RECORD exceeds {MAX_RECORD_BYTES} bytes")
        return result

    result["record_sha256"] = _sha256_file(record_path)
    try:
        rows = list(csv.reader(io.StringIO(record_path.read_text(encoding="utf-8"))))
    except (OSError, UnicodeError, csv.Error) as error:
        result["issues"].append(f"could not parse RECORD: {type(error).__name__}")
        return result

    result["record_entries"] = len(rows)
    if len(rows) > MAX_RECORD_ENTRIES:
        result["issues"].append(f"installed RECORD exceeds {MAX_RECORD_ENTRIES} entries")
        return result
    payload_sha256, payload_count = _record_payload_fingerprint(rows)
    result["payload_sha256"] = payload_sha256
    result["payload_entry_count"] = payload_count
    recorded_paths: set[str] = set()
    unhashed: list[str] = []
    verified = 0

    for row in rows:
        if len(row) != 3:
            result["issues"].append("RECORD row does not contain exactly three fields")
            continue
        relative, encoded_hash, encoded_size = row
        recorded_paths.add(relative)
        if not encoded_hash:
            unhashed.append(relative)
            continue
        try:
            algorithm, encoded_digest = encoded_hash.split("=", 1)
        except ValueError:
            result["issues"].append(f"malformed RECORD hash for {relative}")
            continue
        if algorithm != "sha256":
            result["issues"].append(f"unsupported RECORD hash algorithm for {relative}")
            continue
        try:
            candidate = (root / relative).resolve(strict=True)
        except OSError:
            result["issues"].append(f"missing RECORD file {relative}")
            continue
        if not _is_within(candidate, root) or not candidate.is_file():
            result["issues"].append(f"RECORD path escapes the distribution root: {relative}")
            continue
        try:
            expected_size = int(encoded_size)
        except ValueError:
            result["issues"].append(f"invalid RECORD size for {relative}")
            continue
        if candidate.stat().st_size != expected_size:
            result["issues"].append(f"RECORD size mismatch for {relative}")
            continue
        try:
            expected_digest = _decode_record_hash(encoded_digest)
        except (ValueError, binascii.Error):
            result["issues"].append(f"invalid RECORD digest encoding for {relative}")
            continue
        if hashlib.sha256(candidate.read_bytes()).digest() != expected_digest:
            result["issues"].append(f"RECORD digest mismatch for {relative}")
            continue
        verified += 1

    result["hashed_entries_verified"] = verified
    result["unhashed_entries"] = sorted(unhashed)
    result["recorded_paths"] = sorted(recorded_paths)
    result["issues"] = result["issues"][:20]
    expected_unhashed = [str(record_path.relative_to(root))]
    result["integrity_verified"] = (
        not result["issues"]
        and verified + len(unhashed) == len(rows)
        and sorted(unhashed) == expected_unhashed
    )
    return result


def _module_origin_evidence(module: Any, root: Path, recorded_paths: set[str]) -> dict[str, Any]:
    raw_origin = getattr(module, "__file__", None)
    evidence = {
        "origin": str(raw_origin) if raw_origin is not None else None,
        "inside_distribution_root": False,
        "listed_in_record": False,
    }
    if raw_origin is None:
        return evidence
    try:
        origin = Path(raw_origin).resolve(strict=True)
        relative = str(origin.relative_to(root))
    except (OSError, ValueError):
        return evidence
    evidence["origin"] = str(origin)
    evidence["inside_distribution_root"] = True
    evidence["listed_in_record"] = relative in recorded_paths
    return evidence


def _inspect_installation(nifty_package: Any, jft: Any) -> tuple[dict[str, Any], dict[str, bool]]:
    observed_versions: dict[str, str | None] = {}
    for name in EXPECTED_DISTRIBUTIONS:
        try:
            observed_versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            observed_versions[name] = None

    distribution = importlib.metadata.distribution("nifty")
    installed_distributions = list(importlib.metadata.distributions())
    installed_distribution_names = sorted(
        _normalized_distribution_name(str(item.metadata["Name"]))
        for item in installed_distributions
        if item.metadata["Name"] is not None
    )
    expected_distribution_names = sorted(
        _normalized_distribution_name(name) for name in EXPECTED_DISTRIBUTIONS
    )
    installers: dict[str, str | None] = {}
    direct_url_distributions: list[str] = []
    origin_distributions: list[str] = []
    for name in EXPECTED_DISTRIBUTIONS:
        item = importlib.metadata.distribution(name)
        item_installer_path = _metadata_file(item, "INSTALLER")
        installers[name] = (
            item_installer_path.read_text(encoding="utf-8").strip()
            if item_installer_path is not None and item_installer_path.is_file()
            else None
        )
        item_direct_url_path = _metadata_file(item, "direct_url.json")
        if item_direct_url_path is not None and item_direct_url_path.is_file():
            direct_url_distributions.append(name)
        if getattr(item, "origin", None) is not None:
            origin_distributions.append(name)
    distribution_root = Path(distribution.locate_file("")).resolve()
    record = _verify_distribution_record(distribution)
    recorded_paths = set(record.pop("recorded_paths"))
    installer_path = _metadata_file(distribution, "INSTALLER")
    direct_url_path = _metadata_file(distribution, "direct_url.json")
    installer = None
    if installer_path is not None and installer_path.is_file():
        installer = installer_path.read_text(encoding="utf-8").strip()

    nifty_origin = _module_origin_evidence(nifty_package, distribution_root, recorded_paths)
    nifty_re_origin = _module_origin_evidence(jft, distribution_root, recorded_paths)
    lock_sha256 = _sha256_file(LOCK_PATH) if LOCK_PATH.is_file() else None
    pyproject_sha256 = (
        _sha256_file(ENVIRONMENT_PYPROJECT) if ENVIRONMENT_PYPROJECT.is_file() else None
    )
    lock_text = LOCK_PATH.read_text(encoding="utf-8") if LOCK_PATH.is_file() else ""

    observed = {
        "versions": observed_versions,
        "installed_distribution_names": installed_distribution_names,
        "installers": installers,
        "direct_url_distributions": sorted(direct_url_distributions),
        "origin_distributions": sorted(origin_distributions),
        "distribution_root": str(distribution_root),
        "installer": installer,
        "direct_url_metadata_present": bool(
            direct_url_path is not None and direct_url_path.is_file()
        ),
        "importlib_distribution_origin_present": getattr(distribution, "origin", None) is not None,
        "record": record,
        "module_origins": {
            "nifty": nifty_origin,
            "nifty.re": nifty_re_origin,
        },
        "environment_asset": {
            "lock": str(LOCK_PATH),
            "lock_sha256": lock_sha256,
            "pyproject": str(ENVIRONMENT_PYPROJECT),
            "pyproject_sha256": pyproject_sha256,
        },
    }
    checks = {
        "isolated_import_mode": bool(sys.flags.isolated)
        and bool(sys.flags.no_user_site)
        and bool(getattr(sys.flags, "safe_path", False)),
        "isolated_virtual_environment": Path(sys.prefix).resolve()
        != Path(sys.base_prefix).resolve(),
        "python_3_12": sys.version_info[:2] == (3, 12),
        "reviewed_platform_abi": _reviewed_platform_abi(),
        "reviewed_resolution_exact": observed_versions == EXPECTED_DISTRIBUTIONS,
        "installed_distribution_set_exact": installed_distribution_names
        == expected_distribution_names,
        "all_distributions_installed_by_uv": all(value == "uv" for value in installers.values()),
        "no_distribution_direct_urls": not direct_url_distributions and not origin_distributions,
        "installed_by_uv": installer == "uv",
        "registry_install_not_direct_url": not observed["direct_url_metadata_present"]
        and not observed["importlib_distribution_origin_present"],
        "record_integrity_verified": bool(record["integrity_verified"]),
        "record_matches_reviewed_wheel_payload": record["payload_sha256"]
        == NIFTY_REVIEWED_RECORD_PAYLOAD_SHA256,
        "module_origins_match_distribution": all(
            item["inside_distribution_root"] and item["listed_in_record"]
            for item in (nifty_origin, nifty_re_origin)
        ),
        "environment_asset_matches_reviewed": lock_sha256 == REVIEWED_LOCK_SHA256
        and pyproject_sha256 == REVIEWED_PYPROJECT_SHA256
        and f"sha256:{NIFTY_WHEEL_SHA256}" in lock_text,
    }
    return observed, checks


def _cache_inventory(path: Path) -> dict[str, int]:
    file_count = 0
    total_bytes = 0
    for candidate in path.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            file_count += 1
            total_bytes += candidate.stat().st_size
    return {"files": file_count, "bytes": total_bytes}


def run_benchmark(cache_dir: Path) -> dict[str, Any]:
    """Execute the fixed linear-Gaussian VI certification problem."""

    runtime_environment = _require_certification_process()
    if not cache_dir.is_absolute() or not cache_dir.is_dir() or cache_dir.is_symlink():
        raise WorkflowError("cache directory must be the new private directory created by main")
    if stat.S_IMODE(cache_dir.stat().st_mode) != 0o700:
        raise WorkflowError("cache directory must have mode 0700")

    removed_environment_names = _sanitize_runtime_environment()
    os.environ["JAX_COMPILATION_CACHE_DIR"] = str(cache_dir)

    python_warning_capture = _BoundedWarningCapture()
    log_store = _BoundedMessageStore()
    with python_warning_capture:
        try:
            import jax
            import jax.numpy as jnp
            import nifty as nifty_package
            import nifty.re as jft
            import numpy as np
            from jax import random
        except ModuleNotFoundError as error:
            missing = error.name or "an unknown dependency"
            raise WorkflowError(
                f"the selected certification environment is missing {missing}; "
                "synchronize the bundled frozen environment asset"
            ) from error

        observed_nifty = importlib.metadata.version("nifty")
        if observed_nifty != EXPECTED_NIFTY_VERSION:
            raise WorkflowError(
                f"expected nifty {EXPECTED_NIFTY_VERSION}, observed {observed_nifty}; "
                "do not silently run this benchmark against another API"
            )

        observed_installation, provenance_checks = _inspect_installation(nifty_package, jft)
        jax.config.update("jax_enable_x64", True)

        noise_std = 0.25
        x = jnp.linspace(-2.0, 2.0, 21, dtype=jnp.float64)
        truth = jnp.array([1.75, -0.4], dtype=jnp.float64)
        _unused, noise_key, initial_key, optimization_key = random.split(
            random.PRNGKey(CERTIFICATION_SEED), 4
        )
        data = (
            truth[0] * x + truth[1] + noise_std * random.normal(noise_key, x.shape, dtype=x.dtype)
        )

        slope = jft.NormalPrior(0.0, 2.0, name="slope")
        intercept = jft.NormalPrior(0.0, 2.0, name="intercept")
        model = jft.Model(
            lambda position: slope(position) * x + intercept(position),
            init=slope.init | intercept.init,
        )
        likelihood = jft.Gaussian(
            data,
            noise_cov_inv=partial(operator.mul, noise_std**-2),
            noise_std_inv=partial(operator.mul, noise_std**-1),
        ).amend(model)
        initial = jft.Vector(likelihood.init(initial_key))

        log_handler = _BoundedLogHandler(log_store)
        previous_log_level = jft.logger.level
        jft.logger.addHandler(log_handler)
        jft.logger.setLevel(logging.WARNING)
        try:
            samples, state = jft.optimize_kl(
                likelihood,
                initial,
                key=optimization_key,
                n_total_iterations=1,
                n_samples=CERTIFICATION_SAMPLE_PAIRS,
                draw_linear_kwargs={
                    "cg_name": None,
                    "cg_kwargs": {"absdelta": 1e-10, "maxiter": 50},
                },
                kl_kwargs={
                    "minimize_kwargs": {
                        "name": None,
                        "xtol": 1e-10,
                        "cg_kwargs": {"name": None},
                        "maxiter": 20,
                    }
                },
                sample_mode="linear_resample",
                odir=None,
                resume="",
            )
        finally:
            jft.logger.removeHandler(log_handler)
            jft.logger.setLevel(previous_log_level)

        posterior_parameters = np.asarray(
            [[float(slope(sample)), float(intercept(sample))] for sample in samples],
            dtype=float,
        )
        posterior_mean = posterior_parameters.mean(axis=0)
        posterior_covariance = np.cov(posterior_parameters, rowvar=False, ddof=1)

        design = np.column_stack((np.asarray(x), np.ones(x.shape)))
        exact_covariance = np.linalg.inv(np.eye(2) / 4.0 + design.T @ design / noise_std**2)
        exact_mean = exact_covariance @ (design.T @ np.asarray(data) / noise_std**2)

        initial_prediction = np.asarray(model(initial), dtype=float)
        posterior_position_prediction = np.asarray(model(samples.pos), dtype=float)
        data_array = np.asarray(data, dtype=float)
        initial_rmse = float(np.sqrt(np.mean((initial_prediction - data_array) ** 2)))
        posterior_rmse = float(np.sqrt(np.mean((posterior_position_prediction - data_array) ** 2)))
        reduced_chi_squared = float(
            np.sum(((data_array - posterior_position_prediction) / noise_std) ** 2)
            / (data_array.size - 2)
        )
        posterior_mean_max_error = float(np.max(np.abs(posterior_mean - exact_mean)))
        covariance_relative_error = float(
            np.linalg.norm(posterior_covariance - exact_covariance)
            / np.linalg.norm(exact_covariance)
        )
        sample_state = np.asarray(state.sample_state)
        minimization_success = bool(state.minimization_state.success)
        minimization_status = int(state.minimization_state.status)
        devices = [
            {
                "id": int(device.id),
                "platform": str(device.platform),
                "kind": str(device.device_kind),
            }
            for device in jax.devices()
        ]

        numeric_values = (
            data_array,
            initial_prediction,
            posterior_position_prediction,
            posterior_parameters,
            posterior_mean,
            posterior_covariance,
            exact_mean,
            exact_covariance,
            initial_rmse,
            posterior_rmse,
            reduced_chi_squared,
            posterior_mean_max_error,
            covariance_relative_error,
        )
        finite_values = all(np.all(np.isfinite(value)) for value in numeric_values)
        checks = {
            **provenance_checks,
            "clean_private_runtime_environment": bool(
                runtime_environment["allowlisted_environment"]
            ),
            "bytecode_writes_disabled": bool(sys.dont_write_bytecode),
            "nifty_version_exact": observed_nifty == EXPECTED_NIFTY_VERSION,
            "cpu_backend": jax.default_backend() == "cpu"
            and all(device["platform"] == "cpu" for device in devices),
            "jax_x64_enabled": bool(jax.config.jax_enable_x64),
            "private_external_jax_cache": os.environ.get("JAX_COMPILATION_CACHE_DIR")
            == str(cache_dir)
            and stat.S_IMODE(cache_dir.stat().st_mode) == 0o700
            and not _is_within(cache_dir, SOURCE_REPOSITORY_ROOT),
            "all_values_finite": bool(finite_values),
            "one_iteration_completed": int(state.nit) == 1,
            "all_sample_statuses_zero": bool(
                sample_state.size == CERTIFICATION_SAMPLE_PAIRS and np.all(sample_state == 0)
            ),
            "minimizer_succeeded": minimization_success and minimization_status == 0,
            "antithetic_sample_count": len(posterior_parameters) == 2 * CERTIFICATION_SAMPLE_PAIRS,
            "posterior_mean_matches_analytic": posterior_mean_max_error <= 1e-6,
            "sample_covariance_matches_analytic": covariance_relative_error < 0.5,
            "posterior_improves_data_fit": posterior_rmse < initial_rmse,
            "reduced_chi_squared_is_plausible": 0.25 <= reduced_chi_squared <= 4.0,
        }

    report: dict[str, Any] = {
        "schema_version": 2,
        "workflow": "nifty-re-linear-gaussian-fixed-certification",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "validation_passed": all(checks.values()),
        "scientific_conclusion_ready": False,
        "reviewed_provenance": {
            "nifty_release": EXPECTED_NIFTY_VERSION,
            "nifty_release_commit": NIFTY_RELEASE_COMMIT,
            "nifty_pypi_wheel_sha256": NIFTY_WHEEL_SHA256,
            "nifty_wheel_record_payload_sha256": NIFTY_REVIEWED_RECORD_PAYLOAD_SHA256,
            "environment_lock_sha256": REVIEWED_LOCK_SHA256,
            "environment_pyproject_sha256": REVIEWED_PYPROJECT_SHA256,
            "claim": "reviewed upstream identities; observed installation evidence is separate",
        },
        "observed_evidence": {
            "benchmark_script": str(SCRIPT_PATH),
            "benchmark_script_sha256": _sha256_file(SCRIPT_PATH),
            "installation": observed_installation,
        },
        "environment": {
            "python": platform.python_version(),
            "python_executable": str(Path(sys.executable).absolute()),
            "python_executable_resolved": str(Path(sys.executable).resolve()),
            "python_prefix": str(Path(sys.prefix).resolve()),
            "platform": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
            "devices": devices,
            "runtime_boundary": runtime_environment,
            "jax_cache": {
                "path": str(cache_dir),
                "mode": "0700",
                **_cache_inventory(cache_dir),
            },
        },
        "configuration": {
            "fixed_certification_configuration": True,
            "seed": CERTIFICATION_SEED,
            "observations": int(data_array.size),
            "noise_standard_deviation": noise_std,
            "requested_sample_pairs": CERTIFICATION_SAMPLE_PAIRS,
            "actual_antithetic_samples": int(len(posterior_parameters)),
            "iterations": 1,
            "sample_mode": "linear_resample",
        },
        "checks": checks,
        "optimizer": {
            "iteration": int(state.nit),
            "minimization_success": minimization_success,
            "minimization_status": minimization_status,
            "sample_statuses": sample_state.astype(int).tolist(),
        },
        "diagnostics": {
            "removed_ambient_environment_names": removed_environment_names,
            "python_warnings": python_warning_capture.store.report(),
            "nifty_log_records_at_warning_or_higher": log_store.report(),
        },
        "metrics": {
            "initial_data_rmse": initial_rmse,
            "posterior_position_data_rmse": posterior_rmse,
            "reduced_chi_squared": reduced_chi_squared,
            "posterior_mean_max_absolute_error": posterior_mean_max_error,
            "sample_covariance_relative_frobenius_error": covariance_relative_error,
        },
        "analytic_reference": {
            "posterior_mean": exact_mean.tolist(),
            "posterior_covariance": exact_covariance.tolist(),
        },
        "variational_result": {
            "posterior_mean": posterior_mean.tolist(),
            "posterior_covariance": posterior_covariance.tolist(),
            "parameter_samples": posterior_parameters.tolist(),
        },
        "synthetic_data": {
            "x": np.asarray(x, dtype=float).tolist(),
            "observed": data_array.tolist(),
            "truth_parameters": np.asarray(truth, dtype=float).tolist(),
        },
        "interpretation": {
            "proves": [
                "the observed isolated installation matches the reviewed dependency versions",
                "the installed NIFTy files match their RECORD and the reviewed wheel payload",
                "the optimizer and linear-resampling path complete on the recorded CPU backend",
                "the inferred mean and covariance agree with an analytic posterior",
            ],
            "does_not_prove": [
                "that the embedded hashes authenticate the report or establish who ran it",
                "that the reported platform ABI proves native rather than emulated execution",
                "that an arbitrary nonlinear model is identifiable or scientifically valid",
                "that priors, likelihoods, responses, or data in a research project are correct",
                "that posterior predictive checks or sensitivity analyses can be skipped",
            ],
        },
    }
    return _finalize_report(report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fixed CPU NIFTy.re analytic certification or verify an existing "
            "report's canonical payload digest."
        )
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", help="new absolute JSON report path")
    action.add_argument("--verify-report", help="existing absolute JSON report path")
    parser.add_argument(
        "--cache-dir",
        help="new absolute private directory outside the source repository for the JAX cache",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.verify_report is not None:
            if args.cache_dir is not None:
                raise WorkflowError("--cache-dir cannot be used with --verify-report")
            summary = _verify_report(_resolve_existing_report(args.verify_report))
            print(json.dumps(summary, sort_keys=True))
            return 0 if summary["canonical_payload_digest_valid"] else 1

        if args.cache_dir is None:
            raise WorkflowError("--cache-dir is required with --output")
        _require_certification_process()
        output = _resolve_new_output(args.output)
        cache_dir = _create_private_cache(args.cache_dir)
        report = run_benchmark(cache_dir)
        _write_report(output, report)
    except (WorkflowError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "canonical_payload_sha256": report["canonical_payload_sha256"],
                "output": str(output),
                "report_file_sha256": _sha256_file(output),
                "validation_passed": report["validation_passed"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
