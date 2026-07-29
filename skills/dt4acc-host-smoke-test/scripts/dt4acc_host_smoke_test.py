#!/usr/bin/env python3
"""Bounded, simulation-only host smoke test for three local dt4acc checkouts."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.metadata
import json
import math
import os
import platform
import subprocess
import sys
import sysconfig
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHECKOUT_LAYOUT = {
    "dt4acc": ("pyproject.toml", "src/dt4acc/__init__.py"),
    "dt4acc-lib": ("pyproject.toml", "src/dt4acc_lib/__init__.py"),
    "lat2db": ("pyproject.toml", "lat2db/__init__.py"),
}
IMPORT_LAYOUT = {
    "dt4acc": ("dt4acc", "dt4acc/src"),
    "dt4acc-lib": ("dt4acc_lib", "dt4acc-lib/src"),
    "lat2db": ("lat2db", "lat2db"),
}
LATTICE_RELATIVE = Path(
    "dt4acc/src/dt4acc/custom_facility/bessyii/resources/storage_ring/"
    "input/bessy2_storage_ring_reflat.json"
)


class SmokeTestError(RuntimeError):
    """A precondition or runtime error with a user-facing message."""


@dataclass(frozen=True)
class Config:
    repo_root: Path
    output: Path
    energy_ev: float
    quadrupole: str
    device_id: str | None
    current_delta_a: float
    max_current_delta_a: float
    strength_atol: float
    tune_atol: float
    minimum_tune_change: float
    minimum_points: int
    allow_dirty: bool
    force: bool
    expected_commits: dict[str, str | None]


@dataclass(frozen=True)
class Runtime:
    at: Any
    factory: Any
    simulator_backend: Any
    pyat_simulator: Any
    command_rewriter: Any
    translating_engine: Any
    load_managers: Any
    command: Any
    behaviour_on_error: Any


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("must be finite")
    return parsed


def _positive_float(value: str) -> float:
    parsed = _finite_float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--energy-ev", type=_positive_float, default=1.7185e9)
    parser.add_argument("--quadrupole", default="Q1M2D1R")
    parser.add_argument("--device-id")
    parser.add_argument("--current-delta", type=_finite_float, default=0.01)
    parser.add_argument("--max-current-delta", type=_positive_float, default=0.1)
    parser.add_argument("--strength-atol", type=_positive_float, default=1e-9)
    parser.add_argument("--tune-atol", type=_positive_float, default=1e-9)
    parser.add_argument("--minimum-tune-change", type=_positive_float, default=1e-7)
    parser.add_argument("--minimum-points", type=_positive_int, default=100)
    parser.add_argument("--expected-dt4acc-commit")
    parser.add_argument("--expected-dt4acc-lib-commit")
    parser.add_argument("--expected-lat2db-commit")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def _resolve_config(args: argparse.Namespace) -> Config:
    repo_root = args.repo_root.expanduser().resolve()
    raw_output = args.output.expanduser()
    if raw_output.is_symlink():
        raise SmokeTestError("refusing a symlink as the output target")
    output = raw_output.resolve()
    if not repo_root.is_dir():
        raise SmokeTestError("checkout root does not exist or is not a directory")
    if not output.parent.is_dir():
        raise SmokeTestError("output parent does not exist or is not a directory")
    if output.is_relative_to(repo_root):
        raise SmokeTestError("output must be outside the checkout root")
    if output.exists() and not args.force:
        raise SmokeTestError(
            "output already exists; choose another path or pass --force"
        )
    if not args.quadrupole.strip():
        raise SmokeTestError("quadrupole must not be empty")
    if args.device_id is not None and not args.device_id.strip():
        raise SmokeTestError("device ID must not be empty")
    if args.current_delta == 0:
        raise SmokeTestError("current delta must be non-zero")
    if abs(args.current_delta) > args.max_current_delta:
        raise SmokeTestError(
            "absolute current delta exceeds the configured maximum current delta"
        )
    return Config(
        repo_root=repo_root,
        output=output,
        energy_ev=args.energy_ev,
        quadrupole=args.quadrupole,
        device_id=args.device_id,
        current_delta_a=args.current_delta,
        max_current_delta_a=args.max_current_delta,
        strength_atol=args.strength_atol,
        tune_atol=args.tune_atol,
        minimum_tune_change=args.minimum_tune_change,
        minimum_points=args.minimum_points,
        allow_dirty=args.allow_dirty,
        force=args.force,
        expected_commits={
            "dt4acc": args.expected_dt4acc_commit,
            "dt4acc-lib": args.expected_dt4acc_lib_commit,
            "lat2db": args.expected_lat2db_commit,
        },
    )


def _git(checkout: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        raise SmokeTestError(f"{checkout.name} is not a readable Git checkout")
    return result.stdout.strip()


def _validate_checkouts(config: Config) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for name, required_paths in CHECKOUT_LAYOUT.items():
        checkout = config.repo_root / name
        if not checkout.is_dir():
            raise SmokeTestError(f"missing checkout directory: {name}")
        missing = [
            relative
            for relative in required_paths
            if not (checkout / relative).is_file()
        ]
        if missing:
            raise SmokeTestError(
                f"{name} is missing required paths: {', '.join(missing)}"
            )
        commit = _git(checkout, "rev-parse", "HEAD")
        dirty = bool(_git(checkout, "status", "--porcelain"))
        expected = config.expected_commits[name]
        if expected is not None and commit != expected:
            raise SmokeTestError(f"{name} HEAD does not match its expected commit")
        if dirty and not config.allow_dirty:
            raise SmokeTestError(
                f"{name} has uncommitted changes; pass --allow-dirty to override"
            )
        records[name] = {"commit": commit, "dirty": dirty}
    if not (config.repo_root / LATTICE_RELATIVE).is_file():
        raise SmokeTestError(f"missing packaged lattice: {LATTICE_RELATIVE.as_posix()}")
    return records


def _assert_import_origin(module: Any, expected_root: Path, label: str) -> str:
    raw_origin = getattr(module, "__file__", None)
    if raw_origin is None:
        raise SmokeTestError(f"cannot determine import origin for {label}")
    origin = Path(raw_origin).resolve()
    if not origin.is_relative_to(expected_root):
        raise SmokeTestError(
            f"{label} import did not originate in the supplied checkout"
        )
    return origin.relative_to(expected_root).as_posix()


def _load_runtime(config: Config) -> tuple[Runtime, dict[str, str]]:
    origins: dict[str, str] = {}
    for module_name, relative_root in IMPORT_LAYOUT.values():
        module = importlib.import_module(module_name)
        expected_root = (config.repo_root / relative_root).resolve()
        origins[module_name] = _assert_import_origin(module, expected_root, module_name)

    uuid_module = importlib.import_module("uuid")
    uuid_origin = Path(uuid_module.__file__).resolve()
    stdlib_root = Path(sysconfig.get_paths()["stdlib"]).resolve()
    if not uuid_origin.is_relative_to(stdlib_root):
        raise SmokeTestError(
            "uuid import did not originate in the Python standard library"
        )
    origins["uuid"] = uuid_origin.relative_to(stdlib_root).as_posix()

    at = importlib.import_module("at")
    factories = importlib.import_module("lat2db.tools.factories.pyat")
    backend_module = importlib.import_module(
        "dt4acc_lib.pyat_simulator.simulator_backend"
    )
    simulator_module = importlib.import_module(
        "dt4acc_lib.pyat_simulator.accelerator_simulator"
    )
    rewriter_module = importlib.import_module("dt4acc_lib.bl.command_rewritter")
    engine_module = importlib.import_module(
        "dt4acc.core.bl.translating_command_execution_engine"
    )
    manager_module = importlib.import_module(
        "dt4acc.custom_facility.bessyii.liasion_translator_setup"
    )
    command_module = importlib.import_module("dt4acc_lib.model.utils.command")
    runtime = Runtime(
        at=at,
        factory=factories.factory,
        simulator_backend=backend_module.SimulatorBackend,
        pyat_simulator=simulator_module.PyATAcceleratorSimulator,
        command_rewriter=rewriter_module.CommandRewriter,
        translating_engine=engine_module.TranslatingCommandExecutionEngine,
        load_managers=manager_module.load_managers,
        command=command_module.Command,
        behaviour_on_error=command_module.BehaviourOnError,
    )
    return runtime, origins


def _distribution_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in ("dt4acc", "dt4acc-lib", "lat2db", "accelerator-toolbox"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "not-installed-as-distribution"
    return versions


def _build_ring(config: Config, runtime: Runtime) -> tuple[Any, Path]:
    lattice_path = config.repo_root / LATTICE_RELATIVE
    with lattice_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    sequence = runtime.factory(payload, energy=config.energy_ev)
    ring = runtime.at.Lattice(
        sequence,
        name="BESSY II storage ring",
        energy=config.energy_ev,
    )
    ring.enable_6d()
    ring.cavpts = "CAV*"
    ring.set_cavity_phase(cavpts=ring.cavpts)
    return ring, lattice_path


def _tune_pair(tune: Any) -> tuple[float, float]:
    pair = (float(tune.x), float(tune.y))
    if not all(math.isfinite(value) for value in pair):
        raise SmokeTestError("simulator returned a non-finite tune")
    return pair


def _command_key(command: Any) -> str:
    return f"{command.id}.{command.property}"


async def _read_targets(backend: Any, commands: Sequence[Any]) -> dict[str, float]:
    values: dict[str, float] = {}
    for command in commands:
        key = _command_key(command)
        if key in values:
            raise SmokeTestError(f"translation produced a duplicate target: {key}")
        value = float(await backend.read(command.id, command.property))
        if not math.isfinite(value):
            raise SmokeTestError(f"simulator returned a non-finite value for {key}")
        values[key] = value
    return values


def _max_delta(left: dict[str, float], right: dict[str, float]) -> float:
    if set(left) != set(right):
        raise SmokeTestError("translated target sets changed during the smoke test")
    return max((abs(left[key] - right[key]) for key in left), default=0.0)


def _select_device_command(
    translated: Sequence[Any],
    requested_device: str | None,
) -> Any:
    candidates = [
        command for command in translated if command.property == "set_current"
    ]
    if requested_device is not None:
        candidates = [
            command for command in candidates if command.id == requested_device
        ]
    if len(candidates) != 1:
        identifiers = sorted({_command_key(command) for command in candidates})
        detail = ", ".join(identifiers) if identifiers else "none"
        raise SmokeTestError(
            "expected exactly one translated set_current command; "
            f"candidates: {detail}; select one with --device-id"
        )
    return candidates[0]


async def _run(config: Config, runtime: Runtime) -> dict[str, object]:
    ring, _lattice_path = _build_ring(config, runtime)
    backend = runtime.simulator_backend(
        name="dt4acc-host-smoke-test",
        acc=runtime.pyat_simulator(at_lattice=ring),
    )

    baseline_tune = _tune_pair(await backend.read("tune", "transversal"))
    track = await backend.read("track", "pos")
    twiss = await backend.read("twiss", "parameters")

    _yellow_pages, liaison_manager, translation_service = runtime.load_managers()
    rewriter = runtime.command_rewriter(
        liaison_manager=liaison_manager,
        translation_service=translation_service,
    )
    engine = runtime.translating_engine(
        backend=backend,
        cmd_rewriter=rewriter,
        expected_view_for_output="device",
        num_readings=1,
    )

    baseline_strength = float(await backend.read(config.quadrupole, "main_strength"))
    if not math.isfinite(baseline_strength):
        raise SmokeTestError("simulator returned a non-finite baseline strength")
    design_command = runtime.command(
        id=config.quadrupole,
        property="main_strength",
        value=baseline_strength,
        behaviour_on_error=runtime.behaviour_on_error.stop,
    )
    device_command = _select_device_command(
        rewriter.forward(design_command),
        config.device_id,
    )
    baseline_targets = tuple(rewriter.inverse(device_command))
    if not baseline_targets:
        raise SmokeTestError("device translation produced no inverse lattice targets")
    baseline_target_values = await _read_targets(backend, baseline_targets)

    perturbed_device_command = runtime.command(
        id=device_command.id,
        property=device_command.property,
        value=float(device_command.value) + config.current_delta_a,
        behaviour_on_error=runtime.behaviour_on_error.stop,
    )
    perturbed_targets = tuple(rewriter.inverse(perturbed_device_command))
    if {_command_key(item) for item in perturbed_targets} != set(
        baseline_target_values
    ):
        raise SmokeTestError("perturbed translation changed the inverse target set")

    operation_error: Exception | None = None
    restore_error: Exception | None = None
    perturbed_tune: tuple[float, float] | None = None
    perturbed_strength: float | None = None
    perturbed_target_values: dict[str, float] | None = None
    try:
        await engine.set([perturbed_device_command])
        perturbed_tune = _tune_pair(await backend.read("tune", "transversal"))
        perturbed_strength = float(
            await backend.read(config.quadrupole, "main_strength")
        )
        perturbed_target_values = await _read_targets(backend, perturbed_targets)
    except Exception as exc:  # restoration still has priority
        operation_error = exc
    finally:
        try:
            await engine.set([device_command])
        except Exception as exc:
            restore_error = exc

    if restore_error is not None:
        raise SmokeTestError(
            f"failed to restore simulation state: {type(restore_error).__name__}: "
            f"{restore_error}"
        ) from restore_error
    if operation_error is not None:
        raise SmokeTestError(
            f"perturbation failed after restoration: {type(operation_error).__name__}: "
            f"{operation_error}"
        ) from operation_error
    if (
        perturbed_tune is None
        or perturbed_strength is None
        or perturbed_target_values is None
    ):
        raise SmokeTestError("perturbation did not produce complete measurements")

    restored_tune = _tune_pair(await backend.read("tune", "transversal"))
    restored_strength = float(await backend.read(config.quadrupole, "main_strength"))
    restored_target_values = await _read_targets(backend, baseline_targets)
    if not all(
        math.isfinite(value) for value in (perturbed_strength, restored_strength)
    ):
        raise SmokeTestError("simulator returned a non-finite strength")

    tune_delta = (
        perturbed_tune[0] - baseline_tune[0],
        perturbed_tune[1] - baseline_tune[1],
    )
    tune_restore_delta = (
        restored_tune[0] - baseline_tune[0],
        restored_tune[1] - baseline_tune[1],
    )
    target_perturbation = _max_delta(
        perturbed_target_values,
        baseline_target_values,
    )
    target_restore_error = _max_delta(
        restored_target_values,
        baseline_target_values,
    )
    checks = {
        "lattice_size": len(ring) > config.minimum_points,
        "track_size": len(track.track) > config.minimum_points,
        "twiss_size": len(twiss.twiss) > config.minimum_points,
        "translation_targets": len(baseline_targets) >= 1,
        "strength_changed": abs(perturbed_strength - baseline_strength)
        > config.strength_atol,
        "targets_changed": target_perturbation > config.strength_atol,
        "tune_changed": max(abs(tune_delta[0]), abs(tune_delta[1]))
        > config.minimum_tune_change,
        "strength_restored": abs(restored_strength - baseline_strength)
        <= config.strength_atol,
        "all_targets_restored": target_restore_error <= config.strength_atol,
        "tune_restored": max(
            abs(tune_restore_delta[0]),
            abs(tune_restore_delta[1]),
        )
        <= config.tune_atol,
    }
    success = all(checks.values())
    return {
        "schema_version": 1,
        "status": "passed" if success else "failed",
        "success": success,
        "parameters": {
            "lattice": LATTICE_RELATIVE.as_posix(),
            "energy_ev": config.energy_ev,
            "quadrupole": config.quadrupole,
            "translated_device": str(device_command.id),
            "current_delta_a": config.current_delta_a,
            "max_current_delta_a": config.max_current_delta_a,
            "strength_atol": config.strength_atol,
            "tune_atol": config.tune_atol,
            "minimum_tune_change": config.minimum_tune_change,
            "minimum_points": config.minimum_points,
        },
        "measurements": {
            "lattice_elements": len(ring),
            "track_points": len(track.track),
            "twiss_points": len(twiss.twiss),
            "translated_targets": len(baseline_targets),
            "baseline_strength": baseline_strength,
            "perturbed_strength": perturbed_strength,
            "restored_strength": restored_strength,
            "baseline_tune": list(baseline_tune),
            "perturbed_tune": list(perturbed_tune),
            "restored_tune": list(restored_tune),
            "tune_delta": list(tune_delta),
            "tune_restore_delta": list(tune_restore_delta),
            "maximum_target_perturbation": target_perturbation,
            "maximum_target_restore_error": target_restore_error,
        },
        "checks": checks,
    }


def _environment(origins: dict[str, str] | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "distributions": _distribution_versions(),
    }
    if origins is not None:
        value["import_origins"] = origins
    return value


def _sanitized_error(exc: Exception, config: Config) -> dict[str, str]:
    message = str(exc).replace(str(config.repo_root), "<checkout-root>")
    message = message.replace(str(config.output.parent), "<output-directory>")
    return {"type": type(exc).__name__, "message": message}


def _write_json(path: Path, payload: dict[str, object], *, overwrite: bool) -> None:
    serialized = (
        json.dumps(
            payload,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
            temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = _resolve_config(args)
    except SmokeTestError as exc:
        print(f"preflight error: {exc}", file=sys.stderr)
        return 2

    checkouts: dict[str, dict[str, object]] = {}
    origins: dict[str, str] | None = None
    try:
        checkouts = _validate_checkouts(config)
        runtime, origins = _load_runtime(config)
        payload = asyncio.run(_run(config, runtime))
        payload["checkouts"] = checkouts
        payload["environment"] = _environment(origins)
    except Exception as exc:
        payload = {
            "schema_version": 1,
            "status": "error",
            "success": False,
            "checkouts": checkouts,
            "environment": _environment(origins),
            "error": _sanitized_error(exc, config),
        }

    try:
        _write_json(config.output, payload, overwrite=config.force)
    except (OSError, TypeError, ValueError) as exc:
        print(f"could not write strict JSON result: {exc}", file=sys.stderr)
        return 2

    print(f"status: {payload['status']}")
    print(f"results_json: {config.output}")
    if payload["status"] == "passed":
        return 0
    if payload["status"] == "failed":
        return 1
    print(f"error: {payload['error']['message']}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
