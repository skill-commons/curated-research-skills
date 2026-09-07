#!/usr/bin/env python3
"""Bounded, provenance-preserving views of selected published AIP MUSE products."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import io
import json
import math
import os
import sys
from datetime import UTC, datetime
from importlib.metadata import distribution
from pathlib import Path

VERSION = "1.0.0"
BASE = "https://s3.data.aip.de:9000/data.aip.de/musesc/"
RELEASE = "https://data.aip.de/projects/musescience.html"
PAPERS = {
    "orion": "https://doi.org/10.1051/0004-6361/201526529",
    "antennae": "https://arxiv.org/abs/1712.04450",
}
PACKAGES = ("astropy", "numpy", "matplotlib", "requests")
# This is a deliberately small release-specific allowlist, never a cube browser.
PRODUCTS = {
    "tem_siii_a_median55.fits": {
        "mode": "orion",
        "path": "orion/orion_m42/tem_siii_a_median55.fits",
        "kind": "image",
        "hdu": 0,
        "shape": [1476, 1766],
        "bunit": None,
        "unit": "K",
        "label": "[S III] electron temperature",
        "positive": True,
        "smoothing": "Published 5 × 5 pixel median filter",
        "bytes": 10434240,
        "sha256": "5496565ec1b2e104066899fcf26deb40810243624c03a85547ba46b1d7aff67b",
    },
    "den_sii_median33.fits": {
        "mode": "orion",
        "path": "orion/orion_m42/den_sii_median33.fits",
        "kind": "image",
        "hdu": 0,
        "shape": [1476, 1766],
        "bunit": None,
        "unit": "cm⁻³",
        "label": "[S II] electron density",
        "positive": True,
        "smoothing": "Published 3 × 3 pixel median filter",
        "bytes": 10431360,
        "sha256": "48c67f1c9e9a2e67962ae8bdaac045c6b6592a52a71790efb54aa34ed5c873c5",
    },
    "emhalpha_velos.fits": {
        "mode": "orion",
        "path": "orion/orion_m42/emhalpha_velos.fits",
        "kind": "image",
        "hdu": 0,
        "shape": [1476, 1766],
        "bunit": "km/s",
        "unit": "km s⁻¹",
        "label": "Hα velocity (barycentric)",
        "positive": False,
        "smoothing": "No local smoothing",
        "exclude_zero": True,
        "bytes": 20856960,
        "sha256": "554a18418be0ba26e7a83bf1bb3fc1e605fdd1d3ca45090e2f70c49cabb50f3f",
    },
    "Antennae_Center_Halpha_gaussfit.fits": {
        "mode": "antennae",
        "path": "antennae/antennae_diffuse_data/Antennae_Center_Halpha_gaussfit.fits",
        "kind": "image",
        "hdu": 0,
        "shape": [973, 950],
        "bunit": "10**(-20)*erg/s/cm**2",
        "unit": "10⁻²⁰ erg s⁻¹ cm⁻² per spaxel",
        "label": "Central field Hα Gaussian-fit flux",
        "positive": True,
        "smoothing": "No local smoothing",
        "bytes": 7398720,
        "sha256": "3c2c68c010bf3e7538d0120682de6352b62915ef951201fc87394e4dbf9125ea",
    },
    "Antennae_Center_Halpha_velo_SN30.fits": {
        "mode": "antennae",
        "path": "antennae/antennae_diffuse_data/Antennae_Center_Halpha_velo_SN30.fits",
        "kind": "image",
        "hdu": 0,
        "shape": [973, 950],
        "bunit": "km/s",
        "unit": "km s⁻¹",
        "label": "Hα barycentric velocity · S/N ≈ 30 bins",
        "positive": False,
        "smoothing": "No local smoothing",
        "bytes": 3700800,
        "sha256": "5ee1a4f25d5dececafa3632089f5f5e936e41f6569434060f7f45cbc4475e5c5",
    },
    "Antennae_Center_measurements.fits": {
        "mode": "antennae",
        "path": "antennae/antennae_diffuse_data/Antennae_Center_measurements.fits",
        "kind": "table",
        "hdu": "HII_REGIONS",
        "rows": 551,
        "field": "Center",
        "bytes": 250560,
        "sha256": "b8b276553d04861b25a2d7248b642fa5c59f5e1bea80aca497028b92eee4f6d1",
    },
    "Antennae_South_measurements.fits": {
        "mode": "antennae",
        "path": "antennae/antennae_diffuse_data/Antennae_South_measurements.fits",
        "kind": "table",
        "hdu": "HII_REGIONS",
        "rows": 55,
        "field": "South",
        "bytes": 43200,
        "sha256": "5a38340d75b73bb99f35ff63b39476adf41bf51c35eac0b1875c265230136b03",
    },
}
TABLE_SCHEMA = [
    ("ID", "K", None),
    ("XPEAK", "K", "pixel"),
    ("YPEAK", "K", "pixel"),
    ("NPIX", "K", None),
    ("HALPHA", "D", "erg/s/cm**2"),
    ("E_HALPHA", "D", "erg/s/cm**2"),
    ("HBETA", "D", "erg/s/cm**2"),
    ("E_HBETA", "D", "erg/s/cm**2"),
    ("OIII5007", "D", "erg/s/cm**2"),
    ("E_OIII5007", "D", "erg/s/cm**2"),
    ("OI6300", "D", "erg/s/cm**2"),
    ("E_OI6300", "D", "erg/s/cm**2"),
    ("SII6716", "D", "erg/s/cm**2"),
    ("E_SII6716", "D", "erg/s/cm**2"),
    ("SII6731", "D", "erg/s/cm**2"),
    ("E_SII6731", "D", "erg/s/cm**2"),
    ("SIII9068", "D", "erg/s/cm**2"),
    ("E_SIII9068", "D", "erg/s/cm**2"),
    ("HA_HB", "D", None),
    ("E_HA_HB", "D", None),
    ("C_HBETA", "D", None),
    ("F_H1r_6563A", "D", "erg/s/cm**2"),
    ("E_H1r_6563A", "D", "erg/s/cm**2"),
    ("F_H1r_4861A", "D", "erg/s/cm**2"),
    ("E_H1r_4861A", "D", "erg/s/cm**2"),
    ("F_O3_5007A", "D", "erg/s/cm**2"),
    ("E_O3_5007A", "D", "erg/s/cm**2"),
    ("F_O1_6300A", "D", "erg/s/cm**2"),
    ("E_O1_6300A", "D", "erg/s/cm**2"),
    ("F_S2_6716A", "D", "erg/s/cm**2"),
    ("E_S2_6716A", "D", "erg/s/cm**2"),
    ("F_S2_6731A", "D", "erg/s/cm**2"),
    ("E_S2_6731A", "D", "erg/s/cm**2"),
    ("F_S3_9069A", "D", "erg/s/cm**2"),
    ("E_S3_9069A", "D", "erg/s/cm**2"),
    ("OIII_SII", "D", None),
    ("E_OIII_SII", "D", None),
    ("SIII_SII", "D", None),
    ("E_SIII_SII", "D", None),
    ("OIII_OI", "D", None),
    ("E_OIII_OI", "D", None),
    ("LHa_obs", "D", "erg/s"),
    ("LHa_cor", "D", "erg/s"),
    ("Q_H0", "D", "1/s"),
    ("E_Q_H0", "D", "1/s"),
    ("NCluster", "K", None),
    ("NLyC_GALEV", "D", "1/s"),
    ("fesc_GALEV", "D", None),
    ("E_fesc_GALEV", "D", None),
    ("NLyC_SB99", "D", "1/s"),
    ("fesc_SB99", "D", None),
    ("E_fesc_SB99", "D", None),
]
ARTIFACTS = {"source-manifest.json", "summary.json", "maps.png", "report.html", "provenance.json"}
SCOPE = "New visualizations of published reduced/inferred science products; no raw-data reduction."
NOTES = {
    "orion": (
        "Temperature and density are the authors' inferred, already median-filtered maps. "
        "No local filtering or physical inference is performed. Stars were not separated or "
        "masked in the published maps: compact features and high temperatures near the "
        "Trapezium/Dark Bay can be artifacts. Display percentiles are visual limits only. "
        "Map medians summarize correlated map pixels, not independent measurements. Exact-zero "
        "Orion velocity pixels are conservatively treated as presumed footprint fill; this is "
        "an explicit display assumption, not a released quality flag. Signed nonzero velocities "
        "are retained, including outliers."
    ),
    "antennae": (
        "The ECDF compares selected catalogued H II regions, not complete or matched galaxy "
        "populations. Published LHa_cor includes internal-extinction correction from the "
        "Balmer decrement and assumes 22 Mpc. No flux-to-luminosity conversion, leakage "
        "estimate, star-formation rate, or significance test is performed. "
        "The velocity image was Voronoi-binned to Hα S/N ≈ 30; this is not a pixel-quality cut. "
        "Velocities are barycentric, without systemic-velocity subtraction. Gaussian-fit fluxes "
        "can be overestimated at low S/N. Native map values are retained per spaxel. "
        "The Hα flux image is not internally extinction-corrected."
    ),
}


class ProductError(ValueError):
    """Invalid input, unexpected public product or unverified bundle."""


def json_bytes(value):
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def selected_products(mode):
    if mode not in PAPERS:
        raise ProductError("Unknown workflow")
    return {name: spec for name, spec in PRODUCTS.items() if spec["mode"] == mode}


def permitted_url(url):
    if url not in {BASE + spec["path"] for spec in PRODUCTS.values()}:
        raise ProductError("Only exact allowlisted public HTTPS product URLs are permitted")


def allowed_files(mode):
    return (
        set(selected_products(mode))
        | ARTIFACTS
        | ({"regions.csv"} if mode == "antennae" else set())
    )


def output_path(value, mode):
    supplied = Path(value).expanduser()
    if ".." in supplied.parts:
        raise ProductError("Output path must not contain parent traversal")
    path = Path(os.path.abspath(supplied))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ProductError("Output path and ancestors must not be symlinks; use the canonical path")
    if path.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ProductError("Output must be outside the installed skill")
    if path.exists():
        if not path.is_dir():
            raise ProductError("Output must be a dedicated directory")
        if any(
            p.is_symlink() or not p.is_file() or p.name not in allowed_files(mode)
            for p in path.iterdir()
        ):
            raise ProductError("Refusing unrelated files, directories or symlinks in output")
    return path


def environment():
    packages = {}
    for name in PACKAGES:
        dist = distribution(name)
        packages[name] = {
            "version": dist.version,
            "metadata_sha256": sha256((dist.read_text("METADATA") or "").encode("utf-8")),
        }
    return {
        "python": sys.version.split()[0],
        "packages": packages,
        "helper_sha256": digest(Path(__file__)),
    }


def download(url, expected_size):
    """One bounded GET, with redirects and ambient credentials disabled."""
    import requests

    permitted_url(url)
    if not 0 < expected_size <= 32 * 1024 * 1024:
        raise ProductError("Invalid public product size contract")
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, timeout=(10, 45), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise ProductError(f"HTTP {response.status_code}; redirects are not followed")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) != expected_size:
                raise ProductError(
                    "Source length changed; inspect release before updating the manifest"
                )
            chunks, count = [], 0
            for chunk in response.iter_content(65536):
                count += len(chunk)
                if count > expected_size:
                    raise ProductError("Response exceeds product-specific size limit")
                chunks.append(chunk)
    if count != expected_size:
        raise ProductError("Incomplete source response")
    return b"".join(chunks)


def validate_product(name, raw):
    """Validate snapshot integrity, FITS structure, scientific units and celestial WCS."""
    import numpy as np
    from astropy.io import fits
    from astropy.wcs import WCS

    if name not in PRODUCTS:
        raise ProductError("Unknown product")
    spec = PRODUCTS[name]
    if len(raw) != spec["bytes"] or sha256(raw) != spec["sha256"]:
        raise ProductError(f"Source bytes/hash mismatch: {name}; no changed source accepted")
    with fits.open(io.BytesIO(raw), memmap=False, checksum=True) as hdus:
        hdus.verify("exception")
        hdu = hdus[spec["hdu"]]
        if spec["kind"] == "table":
            schema = [(c.name, c.format, c.unit) for c in hdu.columns]
            if schema != TABLE_SCHEMA or len(hdu.data) != spec["rows"]:
                raise ProductError(f"Unexpected H II region schema or row count: {name}")
            return {"table": hdu.data.copy(), "schema": schema}
        data, header = hdu.data, hdu.header
        if data is None or list(data.shape) != spec["shape"] or data.ndim != 2:
            raise ProductError(f"Unexpected map shape: {name}")
        if header.get("BUNIT") != spec["bunit"]:
            raise ProductError(f"Unexpected map BUNIT: {name}")
        wcs = WCS(header).celestial
        if not wcs.has_celestial or list(wcs.wcs.ctype) != ["RA---TAN", "DEC--TAN"]:
            raise ProductError(f"Expected RA/Dec TAN WCS: {name}")
        matrix = wcs.pixel_scale_matrix
        if not np.all(np.isfinite(matrix)) or abs(np.linalg.det(matrix)) == 0:
            raise ProductError("Invalid WCS pixel matrix")
        scales = np.sqrt(np.sum(matrix * matrix, axis=0)) * 3600
        if not np.allclose(scales, [0.2, 0.2], atol=1e-5, rtol=0):
            raise ProductError(f"Unexpected pixel scale: {name}")
        world = wcs.all_pix2world([[0, 0], [data.shape[1] - 1, data.shape[0] - 1]], 0)
        if not np.all(np.isfinite(world)) or not np.all(np.abs(world[:, 1]) <= 90):
            raise ProductError("Invalid celestial coordinates")
        if not np.any(np.isfinite(data)):
            raise ProductError("Map contains no finite pixels")
        return {"data": data.copy(), "wcs": wcs, "header": header.copy()}


def verify_cache(out, mode, env):
    """Admit only a complete, unchanged bundle before any replay writes."""
    expected = allowed_files(mode)
    if {p.name for p in out.iterdir()} != expected:
        raise ProductError("Incomplete bundle; choose a fresh output directory")
    # Bound small metadata before decoding untrusted local JSON.
    for name in ("source-manifest.json", "provenance.json"):
        if (out / name).stat().st_size > 1024 * 1024:
            raise ProductError("Oversized bundle metadata")
    manifest = json.loads((out / "source-manifest.json").read_text(encoding="utf-8"))
    provenance = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    if (
        manifest.get("format") != 1
        or manifest.get("mode") != mode
        or manifest.get("skill_version") != VERSION
    ):
        raise ProductError("Cache workflow/version mismatch")
    sources = manifest.get("sources", {})
    if set(sources) != set(selected_products(mode)):
        raise ProductError("Cache source inventory mismatch")
    for name, spec in selected_products(mode).items():
        source = sources[name]
        if any(
            source.get(k) != v
            for k, v in {
                "url": BASE + spec["path"],
                "bytes": spec["bytes"],
                "sha256": spec["sha256"],
            }.items()
        ):
            raise ProductError("Cache source URL/size/hash mismatch")
        stamp = datetime.fromisoformat(source["retrieved_utc"])
        if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
            raise ProductError("Expected UTC source retrieval timestamp")
    if provenance.get("environment") != env or provenance.get("source_manifest") != manifest:
        raise ProductError(
            "Helper, environment or source manifest changed; use a fresh output directory"
        )
    artifacts = provenance.get("artifacts", {})
    if set(artifacts) != expected - {"provenance.json"}:
        raise ProductError("Incomplete artifact inventory")
    for name, record in artifacts.items():
        file = out / name
        if file.stat().st_size > 32 * 1024 * 1024:
            raise ProductError(f"Bundle artifact exceeds size limit: {name}")
        if file.stat().st_size != record.get("bytes") or digest(file) != record.get("sha256"):
            raise ProductError(f"Bundle artifact integrity failure: {name}; nothing re-fetched")
    return manifest


def load_or_fetch(out, mode, offline=False, env=None):
    env = environment() if env is None else env
    if out.exists() and any(out.iterdir()):
        manifest = verify_cache(out, mode, env)
        parsed = {
            name: validate_product(name, (out / name).read_bytes())
            for name in selected_products(mode)
        }
        return manifest, parsed
    if offline:
        raise ProductError("Offline mode requires a complete verified bundle")
    payloads, parsed, sources = {}, {}, {}
    for name, spec in selected_products(mode).items():
        url = BASE + spec["path"]
        raw = download(url, spec["bytes"])
        parsed[name] = validate_product(name, raw)
        payloads[name] = raw
        sources[name] = {
            "url": url,
            "bytes": len(raw),
            "sha256": sha256(raw),
            "retrieved_utc": datetime.now(UTC).isoformat(),
        }
    manifest = {"format": 1, "mode": mode, "skill_version": VERSION, "sources": sources}
    # No output writes occur until every source has passed the complete contract.
    out.mkdir(parents=True, exist_ok=True)
    for name, raw in payloads.items():
        (out / name).write_bytes(raw)
    (out / "source-manifest.json").write_bytes(json_bytes(manifest))
    return manifest, parsed


def map_summary(name, product):
    import numpy as np

    spec, data = PRODUCTS[name], product["data"]
    finite = np.isfinite(data)
    valid = finite & (data > 0) if spec["positive"] else finite
    if spec.get("exclude_zero"):
        valid = valid & (data != 0)
    values = data[valid].astype(float)
    if len(values) < 2:
        raise ProductError(f"No usable map pixels: {name}")
    low, high = map(float, np.percentile(values, [2, 98]))
    if low == high:
        raise ProductError("Degenerate map display range")
    result = {
        "source": name,
        "shape_y_x": list(data.shape),
        "unit": spec["unit"],
        "unit_authority": "FITS BUNIT"
        if spec["bunit"]
        else "Product documentation and paper; BUNIT absent",
        "smoothing": spec["smoothing"],
        "pixels_total": int(data.size),
        "pixels_included": int(valid.sum()),
        "excluded_nonfinite": int((~finite).sum()),
        "excluded_nonpositive": int((finite & (data <= 0)).sum()) if spec["positive"] else 0,
        "excluded_presumed_zero_fill": int((finite & (data == 0)).sum())
        if spec.get("exclude_zero")
        else 0,
        "selection": (
            "finite and strictly positive"
            if spec["positive"]
            else "finite nonzero; exact zeros presumed footprint fill; negative velocities retained"
            if spec.get("exclude_zero")
            else "finite; signed velocities retained"
        ),
        "median": float(np.median(values)),
        "minimum": float(values.min()),
        "maximum": float(values.max()),
        "display_percentiles": [2, 98],
        "display_limits": [low, high],
        "display_clipped_below": int((values < low).sum()),
        "display_clipped_above": int((values > high).sum()),
        "pixel_scale_arcsec": 0.2,
        "coordinate_system": "RA/Dec TAN from source FITS WCS",
    }
    return result, np.ma.masked_where(~valid, data)


def plain(value):
    import numpy as np

    if np.ma.is_masked(value):
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8").strip()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def region_records(parsed):
    """Preserve every table row/column and add explicit selection accounting."""
    import numpy as np

    records, summaries, samples = [], {}, {}
    for name, product in parsed.items():
        spec = PRODUCTS[name]
        if spec["kind"] != "table":
            continue
        field, values, reasons = spec["field"], [], {}
        for index, row in enumerate(product["table"]):
            record = {column: plain(row[column]) for column in product["table"].names}
            luminosity = record["LHa_cor"]
            excluded = []
            if luminosity is None or luminosity <= 0:
                excluded.append("LHa_cor_not_finite_positive")
            for line in ("HALPHA", "HBETA"):
                flux, error = record[line], record["E_" + line]
                if flux is None or error is None or flux <= 0 or error <= 0:
                    excluded.append(line + "_flux_or_error_not_finite_positive")
                elif flux / error < 3:
                    excluded.append(line + "_snr_below_3")
            # No luminosity uncertainties are supplied: never invent such errors.
            for reason in excluded:
                reasons[reason] = reasons.get(reason, 0) + 1
            included = not excluded
            if included:
                values.append(float(luminosity))
            records.append(
                {
                    "source_file": name,
                    "field": field,
                    "source_row_1based": index + 1,
                    **record,
                    "included_ecdf": included,
                    "exclusion_reason": ";".join(excluded),
                }
            )
        samples[field] = values
        summaries[field] = {
            "source": name,
            "rows_total": len(product["table"]),
            "rows_included": len(values),
            "rows_excluded": len(product["table"]) - len(values),
            "exclusion_counts": reasons,
            "median_LHa_cor_erg_s": float(np.median(values)) if values else None,
            "selection": (
                "finite positive LHa_cor, HALPHA, E_HALPHA, HBETA, E_HBETA; "
                "both observed Balmer line S/N >= 3"
            ),
            "selection_authority": (
                "Explicit demonstration quality cut, not the paper's completeness selection"
            ),
            "uncertainty_note": (
                "S/N uses observed line flux/error; no complete LHa_cor uncertainty "
                "supplied or reconstructed"
            ),
            "luminosity_unit": "erg s⁻¹",
        }
        if not values:
            raise ProductError(f"No positive finite region luminosities for {field}")
    return records, summaries, samples


def write_csv(path, records):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def render(out, mode, parsed, summary, maps, samples):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LogNorm, Normalize

    with plt.rc_context(
        {"font.family": "DejaVu Sans", "font.size": 10, "figure.facecolor": "white"}
    ):
        fig = plt.figure(figsize=(17, 6.8), layout="constrained")
        grid = fig.add_gridspec(1, 3)
        for index, (name, masked) in enumerate(maps.items()):
            spec = PRODUCTS[name]
            ax = fig.add_subplot(grid[0, index], projection=parsed[name]["wcs"])
            low, high = summary["maps"][name]["display_limits"]
            log = mode == "antennae" and spec["positive"]
            norm = LogNorm(low, high) if log else Normalize(low, high)
            cmap = plt.get_cmap("magma" if spec["positive"] else "RdBu_r").copy()
            cmap.set_bad("#e5e7eb")
            artist = ax.imshow(
                masked, origin="lower", cmap=cmap, norm=norm, interpolation="nearest"
            )
            ax.coords[0].set_axislabel("Right ascension")
            ax.coords[1].set_axislabel("Declination")
            ax.coords[0].set_major_formatter("hh:mm:ss")
            ax.coords[1].set_major_formatter("dd:mm:ss")
            ax.coords[0].set_ticks(number=3)
            ax.coords[1].set_ticks(number=4)
            ax.coords.grid(color="white", alpha=0.18, linewidth=0.5)
            ax.set_title(spec["label"], fontsize=11, pad=14)
            fig.colorbar(
                artist,
                ax=ax,
                orientation="horizontal",
                pad=0.10,
                shrink=0.88,
                label=spec["unit"] + (" · logarithmic colour scale" if log else ""),
                extend="both",
            )
        if mode == "antennae":
            ax = fig.add_subplot(grid[0, 2])
            for field, color in (("Center", "#17628c"), ("South", "#bd5b20")):
                xs = np.sort(samples[field])
                ys = np.arange(1, len(xs) + 1) / len(xs)
                ax.step(
                    xs,
                    ys,
                    where="post",
                    color=color,
                    linewidth=2,
                    label=f"{field}: {len(xs)} regions",
                )
            ax.set(
                xscale="log",
                ylim=(0, 1.03),
                xlabel="Published extinction-corrected Hα luminosity (erg s⁻¹)",
                ylabel="Fraction with luminosity ≤ x",
                title="Catalogued H II regions",
            )
            ax.legend(loc="lower right", frameon=False)
            ax.grid(alpha=0.2)
        fig.text(
            0.03,
            0.952,
            "MUSE · "
            + (
                "Orion: physical maps of an ionized nebula"
                if mode == "orion"
                else "The Antennae: ionized gas and H II regions"
            ),
            fontsize=18,
        )
        fig.get_layout_engine().set(rect=(0, 0.14, 1, 0.73))
        note = (
            "Published median filtering: temperature 5 × 5; density 3 × 3 pixels. "
            "Stars/artifacts remain."
            if mode == "orion"
            else "Selected catalogue distributions; differing populations/completeness. "
            "Published luminosities assume 22 Mpc."
        )
        fig.text(0.03, 0.071, note, fontsize=10)
        fig.text(
            0.03,
            0.030,
            "Re-rendered published products · 2nd–98th percentile image display limits only · "
            "Grey: excluded pixels",
            fontsize=10,
        )
        fig.savefig(
            out / "maps.png", dpi=150, metadata={"Software": "muse-science-products " + VERSION}
        )
        plt.close(fig)


def report(out, mode, summary, provenance):
    esc = html.escape
    image = base64.b64encode((out / "maps.png").read_bytes()).decode("ascii")
    title = "MUSE science products — " + ("Orion" if mode == "orion" else "The Antennae")
    doc = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        f"<title>{esc(title)}</title>"
        "<style>body{font:17px/1.6 system-ui,sans-serif;margin:0;color:#17364a;"
        "background:#f4f6f8}main{max-width:1350px;margin:auto;padding:2rem}h1{font-size:2.2rem;line-height:1.2}"
        "img{max-width:100%;height:auto;background:white;border-radius:8px}pre{white-space:pre-wrap;"
        "overflow-wrap:anywhere;font:13px/1.5 monospace;background:white;"
        "padding:1.2rem;border-radius:8px}"
        "a{color:#17628c}details{margin:1rem 0}</style><main>"
        f"<h1>{esc(title)}</h1><p>{esc(SCOPE)}</p><p>{esc(NOTES[mode])}</p>"
        f'<p>Sources: <a href="{RELEASE}">AIP MUSE science release</a>; '
        f'<a href="{PAPERS[mode]}">research paper</a>. '
        "Source FITS bytes are preserved in this bundle.</p>"
        '<img alt="Published MUSE maps and, for the Antennae, a catalogue luminosity comparison" '
        f'src="data:image/png;base64,{image}">'
        "<details open><summary>Selections, display limits and summaries</summary>"
        f"<pre>{esc(json_bytes(summary).decode())}</pre></details>"
        "<details><summary>Provenance (embedded for standalone use)</summary>"
        "<p>The separate provenance.json also records this HTML file's hash; "
        "the embedded snapshot omits it to avoid a circular hash.</p>"
        f"<pre>{esc(json_bytes(provenance).decode())}</pre></details></main></html>\n"
    )
    (out / "report.html").write_text(doc, encoding="utf-8")


def artifact_records(out, names):
    return {
        name: {"bytes": (out / name).stat().st_size, "sha256": digest(out / name)}
        for name in sorted(names)
    }


def run(mode, value, offline=False):
    out = output_path(value, mode)
    env = environment()
    manifest, parsed = load_or_fetch(out, mode, offline, env)
    summary = {"mode": mode, "scope": SCOPE, "interpretation": NOTES[mode], "maps": {}}
    maps, samples = {}, {}
    for name, product in parsed.items():
        if PRODUCTS[name]["kind"] == "image":
            summary["maps"][name], maps[name] = map_summary(name, product)
    if mode == "antennae":
        records, summary["regions"], samples = region_records(parsed)
        summary["native_table_TUNIT"] = {name: unit for name, _, unit in TABLE_SCHEMA}
        summary["table_unit_note"] = (
            "Flux and flux-error values need the 1e-20 factor missing from bare cgs TUNIT; "
            "this factor cancels in observed line S/N. Published LHa_cor is already linear erg/s. "
            "CSV retains original values; masked/nonfinite values become blank and "
            "original FITS bytes remain unchanged."
        )
        write_csv(out / "regions.csv", records)
    (out / "summary.json").write_bytes(json_bytes(summary))
    render(out, mode, parsed, summary, maps, samples)
    provenance = {
        "skill_version": VERSION,
        "source_manifest": manifest,
        "environment": env,
        "release": RELEASE,
        "paper": PAPERS[mode],
        "scope": SCOPE,
        "artifacts": artifact_records(
            out, allowed_files(mode) - {"report.html", "provenance.json"}
        ),
    }
    report(out, mode, summary, provenance)
    provenance["artifacts"].update(artifact_records(out, {"report.html"}))
    (out / "provenance.json").write_bytes(json_bytes(provenance))
    print(
        json.dumps(
            {
                "report": str(out / "report.html"),
                "mode": mode,
                "sources": len(manifest["sources"]),
                "source_retrieved_utc": {
                    name: source["retrieved_utc"] for name, source in manifest["sources"].items()
                },
            }
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("list", help="print the fixed small-product manifest; no network/dependencies")
    for mode in PAPERS:
        child = sub.add_parser(mode, help=f"render the published {mode} science products")
        child.add_argument(
            "--out", required=True, help="dedicated canonical directory outside the skill"
        )
        child.add_argument(
            "--offline",
            action="store_true",
            help="refuse network; require a complete verified bundle",
        )
    args = parser.parse_args(argv)
    try:
        if args.mode == "list":
            print(
                json_bytes(
                    {name: {**spec, "url": BASE + spec["path"]} for name, spec in PRODUCTS.items()}
                ).decode(),
                end="",
            )
        else:
            run(args.mode, args.out, args.offline)
        return 0
    except Exception as exc:
        print(f"muse-science-products: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
