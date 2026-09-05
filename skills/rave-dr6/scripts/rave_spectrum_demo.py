#!/usr/bin/env python3
"""Fetch, validate, cache, and plot one public RAVE DR6 spectrum."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TAP_ENDPOINT = "https://www.rave-survey.org/tap/"
RAVE_FITS_ORIGIN = "https://www.rave-survey.org"
DEFAULT_RAVE_OBS_ID = "20100313_0823m14_113"
MAX_FITS_BYTES = 10 * 1024 * 1024
CA_TRIPLET_ANGSTROM = (8498.018, 8542.089, 8662.140)
RAVE_OBS_ID = re.compile(r"^[0-9]{8}_[0-9]{4}[mp][0-9]{2}_[0-9]{3}$")


class SpectrumError(RuntimeError):
    """A classified failure in the bounded spectrum workflow."""


@dataclass(frozen=True)
class SpectrumData:
    wavelength: Any
    flux: Any
    error: Any
    wavelength_unit: str
    crval1: float
    crpix1: float
    cdelt1: float
    hdu_names: tuple[str, ...]


def validate_rave_obs_id(value: str) -> str:
    """Return one syntactically safe observation ID for an ADQL literal."""
    if not RAVE_OBS_ID.fullmatch(value):
        raise SpectrumError(
            "rave_obs_id must look like 20100313_0823m14_113; arbitrary ADQL is rejected"
        )
    return value


def build_query(rave_obs_id: str) -> str:
    """Build the single-observation metadata query."""
    value = validate_rave_obs_id(rave_obs_id)
    return f"""SELECT TOP 1
    f.rave_obs_id,
    f.doi,
    f.spectrum_fits,
    f.spectrum_png,
    s.hrv_sparv,
    s.hrv_error_sparv,
    s.snr_med_sparv,
    s.correlation_coeff_sparv
FROM ravedr6.dr6_spectra AS f
JOIN ravedr6.dr6_sparv AS s
  ON f.rave_obs_id = s.rave_obs_id
WHERE f.rave_obs_id = '{value}'"""


def validate_fits_url(value: str, rave_obs_id: str) -> str:
    """Allow only the archive's exact public FITS path for this observation."""
    validate_rave_obs_id(rave_obs_id)
    parts = urllib.parse.urlsplit(value)
    expected_origin = urllib.parse.urlsplit(RAVE_FITS_ORIGIN)
    expected_path = f"/files/fits/{rave_obs_id[:8]}/RAVE_{rave_obs_id}.fits"
    if (
        parts.scheme != expected_origin.scheme
        or parts.netloc != expected_origin.netloc
        or parts.username is not None
        or parts.password is not None
        or parts.path != expected_path
        or parts.query
        or parts.fragment
    ):
        raise SpectrumError(
            f"archive returned an unexpected FITS URL for {rave_obs_id}: {value!r}"
        )
    return urllib.parse.urlunsplit(parts)


def linear_wavelength_grid(
    sample_count: int, *, crval1: float, crpix1: float, cdelt1: float
) -> list[float]:
    """Apply one-dimensional FITS linear WCS using FITS' one-based pixels."""
    if sample_count < 2:
        raise SpectrumError("spectrum must contain at least two samples")
    if not all(map(_is_finite, (crval1, crpix1, cdelt1))) or cdelt1 == 0:
        raise SpectrumError("invalid CRVAL1/CRPIX1/CDELT1 wavelength calibration")
    return [crval1 + (index + 1 - crpix1) * cdelt1 for index in range(sample_count)]


def _is_finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def _plain_scalar(value: Any) -> Any:
    """Convert Astropy/NumPy scalar values to JSON-safe Python values."""
    if bool(getattr(value, "mask", False)):
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict").strip()
    if hasattr(value, "item"):
        value = value.item()
    return value.strip() if isinstance(value, str) else value


def normalized_flux_error(normalized_flux: Any, relative_error: Any) -> Any:
    """Convert RAVE's fractional ERROR values to normalized-flux uncertainty."""
    return abs(normalized_flux) * relative_error


def fetch_metadata(query: str) -> dict[str, Any]:
    """Run one bounded anonymous TAP query and return its only row."""
    try:
        import pyvo
    except ImportError as exc:
        raise SpectrumError("pyvo is required; install the versions documented in SKILL.md") from exc

    try:
        result = pyvo.dal.TAPService(TAP_ENDPOINT).run_sync(query, maxrec=1)
        table = result.to_table()
    except Exception as exc:
        raise SpectrumError(f"RAVE TAP query failed: {exc}") from exc
    if len(table) != 1:
        raise SpectrumError(f"expected one released spectrum, received {len(table)} rows")
    row = table[0]
    return {name: _plain_scalar(row[name]) for name in table.colnames}


def download_fits(
    url: str,
    destination: Path,
    *,
    rave_obs_id: str,
    refresh: bool,
    timeout: float,
) -> bool:
    """Atomically cache a bounded FITS response; return True when cache was reused."""
    url = validate_fits_url(url, rave_obs_id)
    if destination.is_file() and not refresh:
        return True
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "CRS-rave-dr6-spectrum/2.1"})
    temporary: Path | None = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            validate_fits_url(response.geturl(), rave_obs_id)
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > MAX_FITS_BYTES:
                raise SpectrumError("RAVE FITS response exceeds the 10 MiB safety bound")
            with tempfile.NamedTemporaryFile(
                prefix=f".{rave_obs_id}.", suffix=".fits", dir=destination.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                total = 0
                while chunk := response.read(64 * 1024):
                    total += len(chunk)
                    if total > MAX_FITS_BYTES:
                        raise SpectrumError("RAVE FITS response exceeds the 10 MiB safety bound")
                    handle.write(chunk)
        if temporary is None or temporary.stat().st_size == 0:
            raise SpectrumError("RAVE FITS response was empty")
        os.replace(temporary, destination)
        temporary = None
    except SpectrumError:
        raise
    except Exception as exc:
        raise SpectrumError(f"RAVE FITS download failed: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return False


def read_spectrum(path: Path, rave_obs_id: str) -> SpectrumData:
    """Validate the released FITS structure and return spectrum arrays."""
    try:
        import numpy as np
        from astropy.io import fits
    except ImportError as exc:
        raise SpectrumError(
            "astropy and numpy are required; install the versions documented in SKILL.md"
        ) from exc

    try:
        with fits.open(path, memmap=False) as hdus:
            roi = str(hdus[0].header.get("ROI", "")).strip()
            if roi != rave_obs_id:
                raise SpectrumError(
                    f"cached FITS ROI {roi!r} does not match requested {rave_obs_id!r}"
                )
            if "SPECTRUM" not in hdus or "ERROR" not in hdus:
                raise SpectrumError("FITS must contain SPECTRUM and ERROR image extensions")
            spectrum_hdu = hdus["SPECTRUM"]
            error_hdu = hdus["ERROR"]
            flux = np.asarray(spectrum_hdu.data, dtype=float).copy()
            error = np.asarray(error_hdu.data, dtype=float).copy()
            header = spectrum_hdu.header.copy()
            hdu_names = tuple(hdu.name for hdu in hdus)
    except SpectrumError:
        raise
    except Exception as exc:
        raise SpectrumError(f"invalid RAVE FITS product: {exc}") from exc

    if flux.ndim != 1 or error.ndim != 1 or flux.shape != error.shape:
        raise SpectrumError("SPECTRUM and ERROR must be matching one-dimensional arrays")
    if not np.all(np.isfinite(flux)) or not np.all(np.isfinite(error)):
        raise SpectrumError("SPECTRUM and ERROR contain non-finite values")
    if np.any(error < 0):
        raise SpectrumError("ERROR contains negative values")

    try:
        crval1 = float(header["CRVAL1"])
        crpix1 = float(header["CRPIX1"])
        cdelt1 = float(header["CDELT1"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SpectrumError("SPECTRUM lacks a valid linear wavelength calibration") from exc
    wavelength_unit = str(header.get("CUNIT1", "")).strip()
    if wavelength_unit.lower() not in {"angstrom", "angstroms"}:
        raise SpectrumError(f"unexpected wavelength unit: {wavelength_unit!r}")
    wavelength = np.asarray(
        linear_wavelength_grid(
            len(flux), crval1=crval1, crpix1=crpix1, cdelt1=cdelt1
        ),
        dtype=float,
    )
    return SpectrumData(
        wavelength=wavelength,
        flux=flux,
        error=error,
        wavelength_unit=wavelength_unit,
        crval1=crval1,
        crpix1=crpix1,
        cdelt1=cdelt1,
        hdu_names=hdu_names,
    )


def write_csv(path: Path, spectrum: SpectrumData) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "wavelength_angstrom",
                "normalized_flux",
                "relative_error",
                "normalized_flux_error",
            )
        )
        writer.writerows(
            zip(
                spectrum.wavelength,
                spectrum.flux,
                spectrum.error,
                normalized_flux_error(spectrum.flux, spectrum.error),
                strict=True,
            )
        )


def plot_spectrum(
    path: Path, spectrum: SpectrumData, metadata: dict[str, Any], rave_obs_id: str
) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SpectrumError(
            "matplotlib is required; install the versions documented in SKILL.md"
        ) from exc

    fig, (ax, error_ax) = plt.subplots(
        2,
        1,
        figsize=(10.5, 5.8),
        sharex=True,
        gridspec_kw={"height_ratios": (4, 1), "hspace": 0.06},
        facecolor="white",
    )
    flux_error = normalized_flux_error(spectrum.flux, spectrum.error)
    ax.plot(spectrum.wavelength, spectrum.flux, color="#163d73", linewidth=0.9)
    ax.fill_between(
        spectrum.wavelength,
        spectrum.flux - flux_error,
        spectrum.flux + flux_error,
        color="#66a5d9",
        alpha=0.24,
        linewidth=0,
        label="± normalized flux × relative error",
    )
    for wavelength in CA_TRIPLET_ANGSTROM:
        ax.axvline(wavelength, color="#c44e52", linestyle="--", linewidth=0.9, alpha=0.8)
        ax.text(
            wavelength,
            0.03,
            f"Ca II {wavelength:.0f}",
            rotation=90,
            color="#9d3236",
            fontsize=8,
            ha="right",
            va="bottom",
            transform=ax.get_xaxis_transform(),
        )
    snr = metadata.get("snr_med_sparv")
    rv = metadata.get("hrv_sparv")
    detail = []
    if snr is not None:
        detail.append(f"median S/N={float(snr):.1f}")
    if rv is not None:
        detail.append(f"catalog HRV={float(rv):.2f} km/s")
    suffix = f" · {' · '.join(detail)}" if detail else ""
    ax.set_title(f"RAVE DR6 {rave_obs_id}{suffix}")
    ax.set_ylabel("Continuum-normalized flux")
    ax.legend(loc="lower right", frameon=False, fontsize=8)
    ax.grid(alpha=0.15)

    error_ax.plot(spectrum.wavelength, spectrum.error, color="#c44e52", linewidth=0.8)
    error_ax.set_xlabel("Rest-frame air wavelength [Å]")
    error_ax.set_ylabel("Relative\nerror")
    error_ax.grid(alpha=0.15)
    fig.align_ylabels((ax, error_ax))
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def software_versions() -> dict[str, str]:
    versions = {"python": sys.version.split()[0]}
    for name in ("astropy", "matplotlib", "numpy", "pyvo"):
        module = sys.modules.get(name)
        if module is None:
            try:
                module = __import__(name)
            except ImportError:
                continue
        versions[name] = str(getattr(module, "__version__", "unknown"))
    return versions


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and plot one bounded public RAVE DR6 spectrum."
    )
    parser.add_argument("--rave-obs-id", default=DEFAULT_RAVE_OBS_ID)
    parser.add_argument("--out", required=True, help="Workspace output directory")
    parser.add_argument(
        "--refresh", action="store_true", help="Replace the validated local FITS cache"
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="FITS download timeout")
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be greater than 0 and at most 300 seconds")
    return args


def run(args: argparse.Namespace) -> dict[str, Any]:
    rave_obs_id = validate_rave_obs_id(args.rave_obs_id)
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    query = build_query(rave_obs_id)
    metadata = fetch_metadata(query)
    returned_id = str(metadata.get("rave_obs_id", "")).strip()
    if returned_id != rave_obs_id:
        raise SpectrumError(
            f"TAP returned observation {returned_id!r}, expected {rave_obs_id!r}"
        )
    source_url = validate_fits_url(str(metadata.get("spectrum_fits", "")), rave_obs_id)

    query_path = out / "query.adql"
    fits_path = out / f"{rave_obs_id}.fits"
    csv_path = out / f"{rave_obs_id}.csv"
    figure_path = out / f"{rave_obs_id}.png"
    provenance_path = out / "provenance.json"
    query_path.write_text(query.rstrip() + "\n", encoding="utf-8")
    cache_reused = download_fits(
        source_url,
        fits_path,
        rave_obs_id=rave_obs_id,
        refresh=args.refresh,
        timeout=args.timeout,
    )
    spectrum = read_spectrum(fits_path, rave_obs_id)
    write_csv(csv_path, spectrum)
    plot_spectrum(figure_path, spectrum, metadata, rave_obs_id)

    provenance = {
        "access_method": "anonymous TAP metadata plus bounded HTTPS FITS download",
        "tap_endpoint": TAP_ENDPOINT,
        "query_file": query_path.name,
        "rave_obs_id": rave_obs_id,
        "doi": metadata.get("doi"),
        "source_fits_url": source_url,
        "retrieved_utc": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "cache_reused": cache_reused,
        "files": {
            "fits": fits_path.name,
            "table": csv_path.name,
            "figure": figure_path.name,
        },
        "spectrum": {
            "sample_count": int(len(spectrum.wavelength)),
            "wavelength_first_angstrom": float(spectrum.wavelength[0]),
            "wavelength_last_angstrom": float(spectrum.wavelength[-1]),
            "wavelength_formula": "CRVAL1 + (zero_based_index + 1 - CRPIX1) * CDELT1",
            "crval1": spectrum.crval1,
            "crpix1": spectrum.crpix1,
            "cdelt1": spectrum.cdelt1,
            "wavelength_unit": spectrum.wavelength_unit,
            "wavelength_frame": (
                "pipeline zero-velocity/rest frame; air wavelengths; "
                "SPECSYS not declared in FITS"
            ),
            "flux": "continuum-normalized",
            "error": (
                "reported relative error; not inverse variance; normalized-flux "
                "uncertainty = abs(normalized flux) * relative error"
            ),
            "hdu_names": list(spectrum.hdu_names),
        },
        "catalog_metadata": metadata,
        "software": software_versions(),
    }
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

    for path in (fits_path, csv_path, figure_path, provenance_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise SpectrumError(f"expected non-empty output is missing: {path}")
    return provenance


def main(argv: list[str] | None = None) -> int:
    try:
        provenance = run(parse_args(argv))
    except SpectrumError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(provenance, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
