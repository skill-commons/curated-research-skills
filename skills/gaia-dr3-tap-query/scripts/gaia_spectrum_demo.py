#!/usr/bin/env python3
"""Retrieve, validate, cache, and plot one Gaia DR3 spectrum from AIP TAP."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

TAP_ENDPOINT = "https://gaia.aip.de/tap/"
DEFAULT_SOURCE_ID = "5722622022989721600"
PRODUCTS = {
    "rvs": {
        "table": "gaiadr3.rvs_mean_spectrum",
        "count": 2401,
        "start": 846.0,
        "step": 0.01,
        "unit": "dimensionless (continuum normalized)",
    },
    "xp": {
        "table": "gaiadr3.xp_sampled_mean_spectrum",
        "count": 343,
        "start": 336.0,
        "step": 2.0,
        "unit": "W m^-2 nm^-1",
    },
}
CA_TRIPLET_VACUUM_NM = (850.035, 854.444, 866.452)


class SpectrumError(RuntimeError):
    """An actionable failure in the bounded spectrum workflow."""


def validate_source_id(value: str) -> str:
    """Accept only an exact positive decimal signed-int64 identifier."""
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[1-9][0-9]{0,18}", value)
        or int(value) > 2**63 - 1
    ):
        raise SpectrumError("source_id must be an exact positive int64 decimal string, never float")
    return value


def product_spec(product: str) -> dict[str, Any]:
    if product not in PRODUCTS:
        raise SpectrumError("product must be rvs or xp")
    return PRODUCTS[product]


def build_query(source_id: str, product: str) -> str:
    spec = product_spec(product)
    return (
        f"SELECT TOP 1 source_id, flux, flux_error\nFROM {spec['table']}\n"
        f"WHERE source_id = {validate_source_id(source_id)}"
    )


def wavelength_grid(product: str) -> list[float]:
    spec = product_spec(product)
    return [spec["start"] + spec["step"] * index for index in range(spec["count"])]


def clean_samples(flux: list[float], error: list[float]) -> tuple[list, list, list]:
    """Keep every bin, break plots at bad bins, and retain absolute flux errors."""
    if len(flux) != len(error):
        raise SpectrumError("flux and flux_error must have matching lengths")
    valid = [
        math.isfinite(f) and math.isfinite(e) and e >= 0 for f, e in zip(flux, error, strict=True)
    ]
    if sum(valid) < 2:
        raise SpectrumError("spectrum has fewer than two valid flux/error samples")
    return (
        [f if ok else math.nan for f, ok in zip(flux, valid, strict=True)],
        [e if ok else math.nan for e, ok in zip(error, valid, strict=True)],
        valid,
    )


def read_samples(table: Any, source_id: str, product: str) -> tuple[list, list, list]:
    import numpy as np
    from astropy import units as u

    if len(table) != 1 or not {"source_id", "flux", "flux_error"} <= set(table.colnames):
        raise SpectrumError("expected exactly one row with source_id, flux, and flux_error")
    returned_id = table["source_id"][0]
    if (
        np.ma.is_masked(returned_id)
        or not isinstance(returned_id, (int, np.integer))
        or str(returned_id) != source_id
    ):
        raise SpectrumError("returned source_id is not the exact requested integer")
    values = []
    for name in ("flux", "flux_error"):
        unit = table[name].unit
        # AIP advertises this exact XP spelling, which PyVO/Astropy can preserve
        # as UnrecognizedUnit. Normalize its syntax, without rescaling any data.
        if str(unit) == "W.m**-2.nm**-1":
            unit = u.W / u.m**2 / u.nm
        expected_unit = u.W / u.m**2 / u.nm if product == "xp" else u.dimensionless_unscaled
        try:
            correct_unit = (unit is None and product == "rvs") or (
                unit is not None and u.Unit(unit) == expected_unit
            )
        except (TypeError, ValueError):
            correct_unit = False
        if not correct_unit:
            raise SpectrumError(
                f"unexpected {name} unit {unit!r}; expected {product_spec(product)['unit']}"
            )
        cell = np.ma.asarray(table[name][0])
        if (
            cell.ndim != 1
            or cell.shape != (product_spec(product)["count"],)
            or cell.dtype.kind not in "fiu"
        ):
            raise SpectrumError(
                f"{name} is not a complete numeric {product_spec(product)['count']}-bin array; "
                "do not parse truncated strings. See references/spectra.md for the SJS fallback."
            )
        values.append(np.ma.asarray(cell, dtype=float).filled(np.nan).tolist())
    return clean_samples(*values)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def atomic_text(path: Path, content: str) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def retrieve_table(query: str, timeout: float) -> Any:
    import pyvo
    import requests

    class TimedSession(requests.Session):
        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            kwargs["timeout"] = timeout
            return super().request(method, url, **kwargs)

    try:
        with TimedSession() as session:
            return (
                pyvo.dal.TAPService(TAP_ENDPOINT, session=session)
                .run_sync(query, maxrec=1)
                .to_table()
            )
    except Exception as exc:
        raise SpectrumError(f"Gaia AIP TAP request failed: {exc}") from exc


def load_or_fetch(
    out: Path, source_id: str, product: str, *, refresh: bool, timeout: float
) -> tuple[Any, dict, bool]:
    from astropy.table import Table

    query = build_query(source_id, product)
    raw_path, query_path, cache_path = out / "spectrum.ecsv", out / "query.adql", out / "cache.json"
    identity = {
        "source_id": source_id,
        "product": product,
        "endpoint": TAP_ENDPOINT,
        "table": product_spec(product)["table"],
        "query_sha256": query_hash(query),
    }
    if not refresh and any(path.exists() for path in (raw_path, query_path, cache_path)):
        try:
            metadata = json.loads(cache_path.read_text(encoding="utf-8"))
            if any(metadata.get(key) != value for key, value in identity.items()):
                raise ValueError("cache identity differs from the requested source/product/query")
            if query_path.read_text(encoding="utf-8") != query or metadata["ecsv_sha256"] != sha256(
                raw_path
            ):
                raise ValueError("cached query or spectrum checksum does not match")
            dt.datetime.fromisoformat(metadata["retrieved_utc"])
            table = Table.read(raw_path, format="ascii.ecsv")
            read_samples(table, source_id, product)
            return table, metadata, True
        except Exception as exc:
            raise SpectrumError(
                f"invalid/incomplete cache: {exc}; inspect it, then use --refresh"
            ) from exc
    table = retrieve_table(query, timeout)
    read_samples(table, source_id, product)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=out, prefix=".spectrum.", suffix=".ecsv", delete=False
        ) as handle:
            temporary = Path(handle.name)
        table.write(temporary, format="ascii.ecsv", overwrite=True)
        os.replace(temporary, raw_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    metadata = {
        **identity,
        "retrieved_utc": utc_now(),
        "ecsv_sha256": sha256(raw_path),
        "tap_column_units": {
            name: str(table[name].unit) if table[name].unit else None for name in table.colnames
        },
    }
    atomic_text(query_path, query)
    atomic_text(cache_path, json.dumps(metadata, indent=2) + "\n")
    return table, metadata, False


def write_csv(path: Path, wave: list, flux: list, error: list, valid: list) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("wavelength_nm", "flux", "flux_error", "valid"))
        writer.writerows(zip(wave, flux, error, valid, strict=True))


def plot_spectrum(
    path: Path, wave: list, flux: list, error: list, source_id: str, product: str
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    f, e = np.asarray(flux), np.asarray(error)
    fig, (ax, err_ax) = plt.subplots(
        2,
        1,
        figsize=(13, 6),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1]},
        layout="constrained",
    )
    ax.plot(wave, f, color="#174f79", lw=1.1, label="Mean spectrum")
    ax.fill_between(wave, f - e, f + e, color="#78b6d1", alpha=0.35, label="± flux_error")
    err_ax.plot(wave, e, color="#947045", lw=0.8)
    ax.set_title(f"Gaia DR3 {product.upper()} · source {source_id}", fontsize=16)
    if product == "rvs":
        ax.set_ylabel("Continuum-normalized flux")
        ax.set_ylim(
            bottom=min(-0.05, float(np.nanmin(f - e))), top=max(1.16, float(np.nanmax(f + e)))
        )
        for center in CA_TRIPLET_VACUUM_NM:
            ax.axvline(center, color="#97643f", lw=0.8, ls="--", alpha=0.6)
            ax.text(
                center,
                0.98,
                f"Ca II\n{center:.3f}",
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="top",
                fontsize=9,
                color="#80502f",
            )
        err_ax.set_xlabel("Stellar rest-frame vacuum wavelength [nm]")
    else:
        ax.set_ylabel("Flux [W m$^{-2}$ nm$^{-1}$]")
        err_ax.set_xlabel("Wavelength [nm]")
    err_ax.set_ylabel("Flux error")
    ax.legend(loc="lower right", frameon=False)
    for panel in (ax, err_ax):
        panel.grid(alpha=0.15)
        panel.spines[["top", "right"]].set_visible(False)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", default=DEFAULT_SOURCE_ID)
    parser.add_argument("--product", choices=tuple(PRODUCTS), default="rvs")
    parser.add_argument("--out", type=Path, required=True, help="Workspace output parent directory")
    parser.add_argument("--refresh", action="store_true", help="Replace cached retrieval from AIP")
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="Per-request timeout in seconds"
    )
    args = parser.parse_args(argv)
    try:
        source_id = validate_source_id(args.source_id)
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise SpectrumError("timeout must be positive and finite")
        out = args.out.expanduser().resolve() / f"{source_id}-{args.product}"
        out.mkdir(parents=True, exist_ok=True)
        table, metadata, reused = load_or_fetch(
            out, source_id, args.product, refresh=args.refresh, timeout=args.timeout
        )
        flux, error, valid = read_samples(table, source_id, args.product)
        wave = wavelength_grid(args.product)
        write_csv(out / "spectrum.csv", wave, flux, error, valid)
        plot_spectrum(out / "spectrum.png", wave, flux, error, source_id, args.product)
        spec = product_spec(args.product)
        provenance = {
            **metadata,
            "generated_utc": utc_now(),
            "cache_reused": reused,
            "query": build_query(source_id, args.product),
            "table_doi": "10.17876/gaia/dr.3/" + ("54" if args.product == "rvs" else "52"),
            "wavelength_grid": {
                "start_nm": spec["start"],
                "step_nm": spec["step"],
                "count": spec["count"],
                "last_nm": wave[-1],
            },
            "flux_unit": spec["unit"],
            "flux_error_unit": spec["unit"],
            "flux_error_semantics": "absolute standard uncertainty; never multiply by flux",
            "wavelength_frame": "stellar rest-frame vacuum"
            if args.product == "rvs"
            else "as published for externally calibrated BP/RP",
            "valid_samples": sum(valid),
            "invalid_samples": len(valid) - sum(valid),
            "mask_policy": "preserve original bins; invalid flux/error pairs become NaN",
            "software_versions": {
                name: importlib.metadata.version(name)
                for name in ("astropy", "numpy", "pyvo", "matplotlib")
            },
            "python_version": platform.python_version(),
            "artifacts": {
                name: {"sha256": sha256(out / name)}
                for name in ("spectrum.ecsv", "spectrum.csv", "spectrum.png", "query.adql")
            },
        }
        atomic_text(out / "provenance.json", json.dumps(provenance, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "output_directory": str(out),
                    "cache_reused": reused,
                    "samples": len(valid),
                    "valid_samples": sum(valid),
                },
                indent=2,
            )
        )
        return 0
    except ImportError as exc:
        print(
            f"ERROR: missing science dependency ({exc}); install the pins in SKILL.md",
            file=sys.stderr,
        )
    except (SpectrumError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
