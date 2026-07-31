#!/usr/bin/env python3
"""Prove a wheel-installed J-UBIK core with a bounded CPU-only smoke test."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
import re
import stat
import sys
import warnings
import zipfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

EXPECTED_JUBIK_VERSION = "0.3"
EXPECTED_NIFTY_VERSION = "9.2.0"
EXPECTED_WHEEL_NAME = "jubik-0.3-py3-none-any.whl"
EXPECTED_WHEEL_SHA256 = "30953812cbc922aa909e4f7ac5d86cd2c64f69747b8af7e97b00a98f15936b68"
JUBIK_REPOSITORY = "https://github.com/NIFTy-PPL/J-UBIK"
JUBIK_COMMIT = "58a1c7c23477d2099774523278c0ed65f27afde3"
JUBIK_TREE = "675b98f7d0ae9575e5f6081428005063cf942401"
EXPECTED_VERSIONS = {
    "astropy": "8.0.1",
    "astropy-iers-data": "0.2026.7.27.0.56.29",
    "contourpy": "1.3.3",
    "cycler": "0.12.1",
    "ducc0": "0.41.0",
    "fonttools": "4.63.0",
    "jax": "0.11.0",
    "jaxbind": "1.3.1",
    "jaxlib": "0.11.0",
    "jubik": EXPECTED_JUBIK_VERSION,
    "kiwisolver": "1.5.0",
    "matplotlib": "3.11.1",
    "ml-dtypes": "0.5.4",
    "nifty": EXPECTED_NIFTY_VERSION,
    "numpy": "2.5.1",
    "opt-einsum": "3.4.0",
    "packaging": "26.2",
    "pillow": "12.3.0",
    "pyerfa": "2.0.1.5",
    "pyparsing": "3.3.2",
    "python-dateutil": "2.9.0.post0",
    "pyyaml": "6.0.3",
    "scipy": "1.18.0",
    "six": "1.17.0",
}
MAX_REPORT_BYTES = 128 * 1024
MAX_RECORD_BYTES = 256 * 1024
MAX_WARNINGS = 20
MAX_WARNING_CHARS = 500


class SmokeError(RuntimeError):
    """A bounded J-UBIK verification error suitable for a user report."""


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


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _require_owned_private_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise SmokeError(f"{label} must be a regular directory")
    status = path.stat()
    if hasattr(os, "getuid") and status.st_uid != os.getuid():
        raise SmokeError(f"{label} must be owned by the current user")
    if stat.S_IMODE(status.st_mode) & 0o077:
        raise SmokeError(f"{label} must not be accessible by group or other users")


def _resolve_new_output(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise SmokeError("--output must be an absolute path")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise SmokeError("--output parent must be an existing directory") from error
    prefix = Path(sys.prefix).resolve(strict=True)
    if not _is_relative_to(parent, prefix):
        raise SmokeError("--output must be inside the selected virtual environment")
    _require_owned_private_directory(prefix, "virtual environment")
    path = parent / path.name
    if path.exists() or path.is_symlink():
        raise SmokeError("--output must not already exist")
    if not os.access(parent, os.W_OK):
        raise SmokeError("--output parent must be writable")
    return path


def _resolve_wheel(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise SmokeError("--wheel must be an absolute path")
    if path.is_symlink():
        raise SmokeError("--wheel must not be a symlink")
    try:
        path = path.resolve(strict=True)
    except OSError as error:
        raise SmokeError("--wheel does not exist") from error
    if path.name != EXPECTED_WHEEL_NAME or not path.is_file():
        raise SmokeError("--wheel is not the expected J-UBIK 0.3 wheel")
    status = path.stat()
    if hasattr(os, "getuid") and status.st_uid != os.getuid():
        raise SmokeError("--wheel must be owned by the current user")
    if stat.S_IMODE(status.st_mode) & 0o022:
        raise SmokeError("--wheel must not be group/world writable")
    if _sha256(path) != EXPECTED_WHEEL_SHA256:
        raise SmokeError("--wheel SHA-256 does not match the reviewed artifact")
    return path


def _prepare_external_caches(raw: str) -> dict[str, str]:
    cache_root = Path(raw)
    if not cache_root.is_absolute():
        raise SmokeError("--cache-dir must be an absolute path")
    if cache_root.is_symlink():
        raise SmokeError("--cache-dir must not be a symlink")
    try:
        cache_root = cache_root.resolve(strict=True)
    except OSError as error:
        raise SmokeError("--cache-dir must already exist") from error
    prefix = Path(sys.prefix).resolve(strict=True)
    if _is_relative_to(cache_root, prefix):
        raise SmokeError("--cache-dir must be external to the virtual environment")
    _require_owned_private_directory(cache_root, "external cache directory")

    paths = {
        "matplotlib": cache_root / "matplotlib",
        "jax": cache_root / "jax",
        "xdg": cache_root / "xdg",
    }
    for path in paths.values():
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise SmokeError("external cache child is not a regular directory")
        path.mkdir(mode=0o700, exist_ok=True)
        if path.resolve(strict=True).parent != cache_root:
            raise SmokeError("external cache child resolves outside --cache-dir")
        _require_owned_private_directory(path, "external cache child")

    os.environ["MPLCONFIGDIR"] = str(paths["matplotlib"])
    os.environ["JAX_COMPILATION_CACHE_DIR"] = str(paths["jax"])
    os.environ["XDG_CACHE_HOME"] = str(paths["xdg"])
    os.environ["JAX_PLATFORMS"] = "cpu"
    os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"
    return {name: str(path) for name, path in paths.items()}


def _write_report(path: Path, report: dict[str, Any]) -> None:
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if len(payload.encode("utf-8")) > MAX_REPORT_BYTES:
        raise SmokeError("J-UBIK smoke report exceeded the size limit")
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


def _read_limited(path: Path, limit: int, label: str) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise SmokeError(f"{label} exceeded the size limit")
    return raw


def _parse_record(raw: bytes, label: str) -> dict[str, tuple[str, str]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SmokeError(f"{label} is not UTF-8") from error
    rows: dict[str, tuple[str, str]] = {}
    try:
        reader = csv.reader(io.StringIO(text, newline=""))
        for row in reader:
            if len(row) != 3 or not row[0]:
                raise SmokeError(f"{label} contains an invalid row")
            pure = PurePosixPath(row[0])
            if pure.is_absolute() or ".." in pure.parts or str(pure) != row[0]:
                raise SmokeError(f"{label} contains an unsafe path")
            if row[0] in rows:
                raise SmokeError(f"{label} contains a duplicate path")
            rows[row[0]] = (row[1], row[2])
    except csv.Error as error:
        raise SmokeError(f"{label} is invalid CSV") from error
    return rows


def _decode_record_hash(raw: str, label: str) -> str:
    algorithm, separator, encoded = raw.partition("=")
    if separator != "=" or algorithm != "sha256" or not encoded:
        raise SmokeError(f"{label} contains a non-SHA-256 file hash")
    try:
        digest = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, base64.binascii.Error) as error:
        raise SmokeError(f"{label} contains an invalid file hash") from error
    if len(digest) != hashlib.sha256().digest_size:
        raise SmokeError(f"{label} contains an invalid SHA-256 digest")
    return digest.hex()


def _distribution() -> importlib.metadata.Distribution:
    try:
        return importlib.metadata.distribution("jubik")
    except importlib.metadata.PackageNotFoundError as error:
        raise SmokeError("jubik is not installed in the selected environment") from error


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _installed_environment_evidence() -> tuple[dict[str, Any], dict[str, bool]]:
    distributions = list(importlib.metadata.distributions())
    names = sorted(
        _normalized_distribution_name(str(item.metadata["Name"]))
        for item in distributions
        if item.metadata["Name"] is not None
    )
    expected_names = sorted(_normalized_distribution_name(name) for name in EXPECTED_VERSIONS)
    installers: dict[str, str | None] = {}
    direct_url_distributions: list[str] = []
    origin_distributions: list[str] = []
    for name in EXPECTED_VERSIONS:
        item = importlib.metadata.distribution(name)
        installer = item.read_text("INSTALLER")
        installers[name] = installer.strip() if installer is not None else None
        if item.read_text("direct_url.json") is not None:
            direct_url_distributions.append(name)
        if getattr(item, "origin", None) is not None:
            origin_distributions.append(name)
    expected_direct_urls = ["jubik"]
    evidence = {
        "installed_distribution_names": names,
        "installers": installers,
        "direct_url_distributions": sorted(direct_url_distributions),
        "origin_distributions": sorted(origin_distributions),
    }
    checks = {
        "installed_distribution_set_exact": names == expected_names,
        "all_distributions_installed_by_uv": all(value == "uv" for value in installers.values()),
        "only_jubik_has_direct_url": sorted(direct_url_distributions) == expected_direct_urls
        and not origin_distributions,
    }
    return evidence, checks


def _read_direct_url(
    distribution: importlib.metadata.Distribution, expected_wheel: Path
) -> dict[str, Any]:
    raw = distribution.read_text("direct_url.json")
    if raw is None:
        raise SmokeError("jubik has no direct_url.json wheel provenance")
    if len(raw.encode("utf-8")) > 16 * 1024:
        raise SmokeError("jubik direct_url.json exceeded the size limit")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SmokeError("jubik direct_url.json is invalid JSON") from error
    if not isinstance(value, dict) or not isinstance(value.get("archive_info"), dict):
        raise SmokeError("jubik direct_url.json has no archive provenance")
    if "vcs_info" in value or "dir_info" in value:
        raise SmokeError("jubik direct_url.json does not describe a wheel archive")

    url = value.get("url")
    if not isinstance(url, str):
        raise SmokeError("jubik direct_url.json has no archive URL")
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise SmokeError("jubik direct_url.json contains an invalid URL") from error
    if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
        raise SmokeError("jubik must be installed from the planned local wheel")
    observed_path = Path(unquote(parsed.path)).resolve(strict=False)
    if observed_path != expected_wheel:
        raise SmokeError("jubik direct_url.json names an unexpected wheel path")

    archive = value["archive_info"]
    hashes = archive.get("hashes")
    if hashes is not None and (
        not isinstance(hashes, dict) or hashes.get("sha256") != EXPECTED_WHEEL_SHA256
    ):
        raise SmokeError("jubik direct_url.json contains conflicting wheel hashes")
    legacy_hash = archive.get("hash")
    if legacy_hash is not None and legacy_hash != f"sha256={EXPECTED_WHEEL_SHA256}":
        raise SmokeError("jubik direct_url.json contains a conflicting archive hash")
    return {
        "archive_url": url,
        "wheel_filename": expected_wheel.name,
        "wheel_sha256": _sha256(expected_wheel),
        "direct_url_hash_recorded": hashes is not None or legacy_hash is not None,
    }


def _locate_installed_record(distribution: importlib.metadata.Distribution) -> Path:
    files = distribution.files
    if files is None:
        raise SmokeError("jubik installed metadata has no RECORD file list")
    matches = [entry for entry in files if str(entry).endswith(".dist-info/RECORD")]
    if len(matches) != 1:
        raise SmokeError("jubik installed metadata does not identify one RECORD")
    record = Path(distribution.locate_file(matches[0]))
    if record.is_symlink():
        raise SmokeError("jubik installed RECORD must not be a symlink")
    try:
        return record.resolve(strict=True)
    except OSError as error:
        raise SmokeError("jubik installed RECORD is missing") from error


def verify_installed_record(
    distribution: importlib.metadata.Distribution, expected_wheel: Path
) -> dict[str, Any]:
    """Anchor installed files to the reviewed wheel and validate installed RECORD."""

    try:
        with zipfile.ZipFile(expected_wheel) as archive:
            wheel_record_names = [
                name for name in archive.namelist() if name.endswith(".dist-info/RECORD")
            ]
            if len(wheel_record_names) != 1:
                raise SmokeError("reviewed wheel does not contain exactly one RECORD")
            wheel_record_raw = archive.read(wheel_record_names[0])
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise SmokeError("reviewed wheel is not a valid wheel archive") from error
    if len(wheel_record_raw) > MAX_RECORD_BYTES:
        raise SmokeError("reviewed wheel RECORD exceeded the size limit")
    wheel_rows = _parse_record(wheel_record_raw, "reviewed wheel RECORD")

    installed_record = _locate_installed_record(distribution)
    prefix = Path(sys.prefix).resolve(strict=True)
    if not _is_relative_to(installed_record, prefix):
        raise SmokeError("jubik installed RECORD resolves outside the environment")
    distribution_root = Path(distribution.locate_file("")).resolve(strict=True)
    if not _is_relative_to(distribution_root, prefix):
        raise SmokeError("jubik distribution root resolves outside the environment")
    installed_rows = _parse_record(
        _read_limited(installed_record, MAX_RECORD_BYTES, "installed RECORD"),
        "installed RECORD",
    )
    installed_record_key = installed_record.relative_to(distribution_root).as_posix()
    if installed_record_key not in installed_rows:
        raise SmokeError("installed RECORD does not list itself")

    verified = 0
    for relative, (record_hash, recorded_size) in installed_rows.items():
        pure = PurePosixPath(relative)
        candidate = distribution_root.joinpath(*pure.parts)
        if candidate.is_symlink():
            raise SmokeError("installed RECORD names a symlink")
        try:
            candidate = candidate.resolve(strict=True)
        except OSError as error:
            raise SmokeError("installed RECORD names a missing file") from error
        if not _is_relative_to(candidate, prefix) or not candidate.is_file():
            raise SmokeError("installed RECORD names a file outside the environment")
        if relative == installed_record_key:
            if record_hash or recorded_size:
                raise SmokeError("installed RECORD self-entry must not contain a hash or size")
            continue
        if not record_hash or not recorded_size:
            raise SmokeError("installed RECORD contains an unhashed installed file")
        try:
            expected_size = int(recorded_size)
        except ValueError as error:
            raise SmokeError("installed RECORD contains an invalid file size") from error
        if expected_size < 0 or candidate.stat().st_size != expected_size:
            raise SmokeError("installed file size does not match RECORD")
        if _sha256(candidate) != _decode_record_hash(record_hash, "installed RECORD"):
            raise SmokeError("installed file digest does not match RECORD")
        verified += 1

    for relative, expected in wheel_rows.items():
        if relative == wheel_record_names[0]:
            continue
        if installed_rows.get(relative) != expected:
            raise SmokeError("installed RECORD does not preserve the reviewed wheel entry")

    dist_info = wheel_record_names[0].rsplit("/", 1)[0]
    allowed_installer_entries = {
        f"{dist_info}/INSTALLER",
        f"{dist_info}/REQUESTED",
        f"{dist_info}/direct_url.json",
        f"{dist_info}/uv_cache.json",
    }
    unexpected = set(installed_rows) - set(wheel_rows) - allowed_installer_entries
    if unexpected:
        raise SmokeError("installed RECORD contains an unexpected added file")

    wheel_code = {name for name in wheel_rows if name.startswith("jubik/")}
    installed_code_root = distribution_root / "jubik"
    observed_code = {
        path.relative_to(distribution_root).as_posix()
        for path in installed_code_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    if observed_code != wheel_code:
        raise SmokeError("installed J-UBIK package tree differs from the reviewed wheel")

    return {
        "installed_record_path": str(installed_record),
        "installed_record_sha256": _sha256(installed_record),
        "reviewed_wheel_record_sha256": hashlib.sha256(wheel_record_raw).hexdigest(),
        "verified_installed_files": verified,
        "reviewed_wheel_files": len(wheel_rows) - 1,
        "unexpected_installed_files": 0,
    }


def _component(prefix: str, mean: float) -> dict[str, Any]:
    return {
        "offset": {"offset_mean": mean, "offset_std": [0.2, 0.02]},
        "fluctuations": {
            "fluctuations": [0.3, 0.05],
            "loglogavgslope": [-3.0, 0.2],
            "flexibility": [0.2, 0.05],
            "asperity": None,
            "non_parametric_kind": "power",
        },
        "prefix": prefix,
    }


def _bounded_warnings(captured: list[warnings.WarningMessage]) -> dict[str, Any]:
    credential_url = re.compile(r"(?i)(https?://)[^\s/@]+@")
    values = []
    for warning in captured[:MAX_WARNINGS]:
        message = str(warning.message).replace("\x00", "?")
        message = credential_url.sub(r"\1[redacted]@", message)
        values.append(
            {
                "category": warning.category.__name__[:100],
                "message": message[:MAX_WARNING_CHARS],
            }
        )
    return {
        "items": values,
        "observed": len(captured),
        "retained": len(values),
        "truncated": len(captured) > MAX_WARNINGS,
    }


def run_smoke(expected_wheel: Path, cache_paths: dict[str, str]) -> dict[str, Any]:
    """Run one fixed J-UBIK core model in a fresh, bytecode-disabled process."""

    if Path(sys.prefix).resolve() == Path(sys.base_prefix).resolve():
        raise SmokeError("verification must run inside an isolated virtual environment")
    distribution = _distribution()
    environment_evidence, environment_checks = _installed_environment_evidence()
    archive_provenance = _read_direct_url(distribution, expected_wheel)
    record_evidence = verify_installed_record(distribution, expected_wheel)

    with warnings.catch_warnings(record=True) as captured_warnings:
        warnings.simplefilter("always")
        try:
            import jax
            import jubik as ju
            import nifty.re as jft
            import numpy as np
            from jax import random
        except ModuleNotFoundError as error:
            raise SmokeError("the locked J-UBIK core environment is incomplete") from error

        observed_versions = {name: importlib.metadata.version(name) for name in EXPECTED_VERSIONS}
        priors = {
            "diffuse": {
                "spatial": _component("smoke_spatial_", -4.0),
                "plaw": _component("smoke_plaw_", -2.0),
            }
        }
        sky = ju.SkyModel().create_sky_model(
            sdim=8,
            edim=2,
            s_padding_ratio=1.0,
            e_padding_ratio=1.0,
            fov=8.0,
            e_min=[0.5, 1.0],
            e_max=[1.0, 2.0],
            e_ref=1.0,
            priors=priors,
        )
        position = jft.Vector(jft.random_like(random.PRNGKey(20260731), sky.domain))
        compiled_sky = jax.jit(sky)
        output = np.asarray(compiled_sky(position))
        repeated = np.asarray(compiled_sky(position))
        devices = [
            {
                "id": int(device.id),
                "platform": str(device.platform),
                "kind": str(device.device_kind),
            }
            for device in jax.devices()
        ]

    checks = {
        **environment_checks,
        "python_3_12": sys.version_info[:2] == (3, 12),
        "bytecode_writes_disabled": bool(sys.dont_write_bytecode),
        "reviewed_wheel_sha256_exact": _sha256(expected_wheel) == EXPECTED_WHEEL_SHA256,
        "direct_url_wheel_sha256_exact": (
            archive_provenance["wheel_sha256"] == EXPECTED_WHEEL_SHA256
        ),
        "installed_record_verified": record_evidence["verified_installed_files"] > 0,
        "locked_versions_exact": observed_versions == EXPECTED_VERSIONS,
        "jubik_module_version_exact": str(ju.__version__) == EXPECTED_JUBIK_VERSION,
        "cpu_backend": jax.default_backend() == "cpu"
        and all(device["platform"] == "cpu" for device in devices),
        "jax_x64_enabled": bool(jax.config.jax_enable_x64),
        "output_shape": output.shape == (2, 8, 8),
        "all_values_finite": bool(np.all(np.isfinite(output))),
        "all_values_positive": bool(np.all(output > 0)),
        "nonzero_variance": float(np.var(output)) > 0.0,
        "repeat_is_exact": bool(np.array_equal(output, repeated)),
    }
    raw_metrics = {
        "minimum": float(np.min(output)),
        "maximum": float(np.max(output)),
        "mean": float(np.mean(output)),
        "variance": float(np.var(output)),
        "repeat_max_absolute_difference": float(np.max(np.abs(output - repeated))),
    }
    metrics = {name: value if math.isfinite(value) else None for name, value in raw_metrics.items()}

    report: dict[str, Any] = {
        "schema_version": 2,
        "workflow": "jubik-core-wheel-bootstrap-smoke",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "core_ready": all(checks.values()),
        "scientific_research_ready": False,
        "adapters": {
            "jwst_adapter_ready": False,
            "chandra_adapter_ready": False,
            "erosita_adapter_ready": False,
        },
        "adapter_boundaries": {
            "jwst": "packages, PSF inputs, calibration, and data were not verified",
            "chandra": "CIAO, MARX, calibration, setup, and data were not verified",
            "erosita": "Docker/eSASS, CALDB, calibration, and data were not verified",
        },
        "source": {
            "provenance_kind": "reviewed wheel build provenance, not observed Git metadata",
            "jubik_release": "v0.3",
            "upstream_repository": JUBIK_REPOSITORY,
            "upstream_commit": JUBIK_COMMIT,
            "upstream_tree": JUBIK_TREE,
            **archive_provenance,
            "nifty_release": EXPECTED_NIFTY_VERSION,
        },
        "integrity": record_evidence,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "versions": observed_versions,
            "devices": devices,
            "external_caches": cache_paths,
            "bytecode_writes_disabled": bool(sys.dont_write_bytecode),
            **environment_evidence,
        },
        "configuration": {
            "seed": 20260731,
            "spatial_shape": [8, 8],
            "spectral_bins": 2,
            "jit_compiled": True,
        },
        "checks": checks,
        "metrics": metrics,
        "warnings": _bounded_warnings(captured_warnings),
        "serialization": {
            "nonfinite_metrics_replaced_with_null": sum(value is None for value in metrics.values())
        },
        "interpretation": {
            "proves": [
                "the reviewed J-UBIK wheel was installed without package-file drift",
                "the locked J-UBIK core and NIFTy.re dependencies import together",
                "a central correlated sky model evaluates repeatably on CPU",
            ],
            "does_not_prove": [
                "instrument adapter readiness",
                "calibration or observation data availability",
                "scientific validity of any research model or inference",
            ],
        },
    }
    report["content_sha256"] = hashlib.sha256(_canonical_json(report)).hexdigest()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bounded CPU-only J-UBIK wheel readiness smoke test."
    )
    parser.add_argument("--output", required=True, help="new absolute JSON report path")
    parser.add_argument("--wheel", required=True, help="reviewed staged wheel path")
    parser.add_argument(
        "--cache-dir",
        required=True,
        help="existing private cache directory outside the virtual environment",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = _resolve_new_output(args.output)
        wheel = _resolve_wheel(args.wheel)
        cache_paths = _prepare_external_caches(args.cache_dir)
        report = run_smoke(wheel, cache_paths)
        _write_report(output, report)
    except (SmokeError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(output),
                "core_ready": report["core_ready"],
                "content_sha256": report["content_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["core_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
