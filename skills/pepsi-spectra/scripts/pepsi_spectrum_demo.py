#!/usr/bin/env python3
"""Retrieve, validate, cache, and plot one public PEPSI Paper II normalized spectrum."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import importlib.metadata
import io
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BASE = "https://pepsi.aip.de/library/paperII/"
INDEX_URLS = (BASE + "dwarfs.csv", BASE + "giants.csv")
BASENAME = re.compile(r"pepsib\.[0-9]{8}\.[0-9]{3}\.(?:sxt|dxt)\Z")
MAX_INDEX = 256 * 1024
MAX_FITS = 32 * 1024 * 1024
MASK_NOTE = "Release mask polarity undocumented; mask not applied (raw 0x01 retained)."
FILES = (
    "catalog.csv",
    "spectrum.fits",
    "cache.json",
    "spectrum.csv",
    "spectrum.png",
    "provenance.json",
)


class SpectrumError(ValueError):
    pass


def utc_now():
    return dt.datetime.now(dt.UTC).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_url(url):
    if url in INDEX_URLS:
        return url
    prefix, suffix = BASE + "cont_v2/", ".awl.all6"
    if (
        isinstance(url, str)
        and url.startswith(prefix)
        and url.endswith(suffix)
        and BASENAME.fullmatch(url[len(prefix) : -len(suffix)])
    ):
        return url
    raise SpectrumError("URL must be an exact official Paper II index or cont_v2 product")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SpectrumError("Redirect refused; recheck the official PEPSI release links")


def fetch_bytes(url, limit, timeout):
    validate_url(url)
    if not math.isfinite(timeout) or timeout <= 0 or limit <= 0:
        raise SpectrumError("Download timeout and byte limit must be positive")
    request = urllib.request.Request(url, headers={"User-Agent": "Skill-Commons-pepsi-spectra/1.0"})
    start = time.monotonic()
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        if response.geturl() != url:
            raise SpectrumError("Unexpected final download URL")
        declared = response.headers.get("Content-Length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            raise SpectrumError("Download exceeds byte limit or has invalid Content-Length")
        chunks, size = [], 0
        while True:
            chunk = response.read(min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                raise SpectrumError("Download exceeds byte limit")
            if time.monotonic() - start > timeout:
                raise SpectrumError("Download exceeded elapsed timeout")
        data = b"".join(chunks)
        if not data or (declared is not None and len(data) != int(declared)):
            raise SpectrumError("Empty or truncated download")
        return data, {
            key: response.headers.get(key) for key in ("Last-Modified", "ETag", "Content-Type")
        }


def parse_catalog(text, index_url):
    if index_url not in INDEX_URLS:
        raise SpectrumError("Unknown catalog URL")
    reader = csv.DictReader(io.StringIO(text))
    fields = {"name", "simbad", "type", "snr", "basename"}
    if (
        reader.fieldnames is None
        or len(reader.fieldnames) != len(fields)
        or set(reader.fieldnames) != fields
    ):
        raise SpectrumError("Unexpected PEPSI catalog columns")
    rows = []
    for row in reader:
        if set(row) != fields or any(not isinstance(v, str) or not v.strip() for v in row.values()):
            raise SpectrumError("Incomplete PEPSI catalog row")
        row = {k: v.strip() for k, v in row.items()}
        if not BASENAME.fullmatch(row["basename"]):
            raise SpectrumError("Invalid catalog basename")
        try:
            snr = float(row["snr"])
        except ValueError as exc:
            raise SpectrumError("Invalid catalog SNR") from exc
        if not math.isfinite(snr) or snr <= 0:
            raise SpectrumError("Invalid catalog SNR")
        rows.append({**row, "index_url": index_url})
    if not rows:
        raise SpectrumError("Empty PEPSI catalog")
    return rows


def normalized_name(value):
    return " ".join(value.split()).casefold()


def select_target(rows, target):
    key = normalized_name(target)
    matches = [
        row
        for row in rows
        if key in {normalized_name(row[k]) for k in ("name", "simbad", "basename")}
    ]
    if len(matches) != 1:
        raise SpectrumError("Target is missing or ambiguous; use --list and an exact catalog label")
    return matches[0]


def data_url(row):
    return validate_url(BASE + "cont_v2/" + row["basename"] + ".awl.all6")


def get_catalog(timeout):
    rows, originals = [], {}
    for url in INDEX_URLS:
        raw, _ = fetch_bytes(url, MAX_INDEX, timeout)
        originals[url] = raw
        rows.extend(parse_catalog(raw.decode("utf-8-sig"), url))
    return rows, originals


def clean_samples(wavelength, flux, variance, mask):
    if len(wavelength) < 2 or not (len(wavelength) == len(flux) == len(variance) == len(mask)):
        raise SpectrumError("Spectrum arrays require matching lengths of at least two")
    # The release does not define mask polarity. Only the observed uniform state
    # is supported; neither a good-pixel claim nor a bad-pixel exclusion is made.
    if any(type(m) is not int or m != 1 for m in mask):
        raise SpectrumError(
            "Unsupported mask bytes: mask semantics are undocumented; refusing to guess"
        )
    try:
        wave = [float(w) for w in wavelength]
        values = [float(f) for f in flux]
        variances = [float(v) for v in variance]
    except (ValueError, TypeError) as exc:
        raise SpectrumError("Spectrum columns must be scalar numeric arrays") from exc
    if any(not math.isfinite(w) or w <= 0 for w in wave) or any(
        b <= a for a, b in zip(wave, wave[1:], strict=False)
    ):
        raise SpectrumError("Wavelengths must be finite, positive, and strictly increasing")
    valid = [
        math.isfinite(f) and math.isfinite(v) and v >= 0
        for f, v in zip(values, variances, strict=True)
    ]
    if sum(valid) < 2:
        raise SpectrumError("Spectrum has fewer than two numerically valid samples")
    display = [f if ok else math.nan for f, ok in zip(values, valid, strict=True)]
    sigma = [math.sqrt(v) if ok else math.nan for v, ok in zip(variances, valid, strict=True)]
    return wave, display, sigma, valid


def segment_slices(wave, gap_factor=10.0):
    if len(wave) < 2 or not math.isfinite(gap_factor) or gap_factor <= 1:
        raise SpectrumError(
            "Gap detection requires two samples and a finite factor greater than one"
        )
    steps = [b - a for a, b in zip(wave, wave[1:], strict=False)]
    if any(not math.isfinite(step) or step <= 0 for step in steps):
        raise SpectrumError("Gap detection requires increasing finite wavelengths")
    threshold = gap_factor * statistics.median(steps)
    starts = [0] + [i + 1 for i, step in enumerate(steps) if step > threshold] + [len(wave)]
    return [slice(a, b) for a, b in zip(starts, starts[1:], strict=False)]


def read_spectrum(raw, row):
    import numpy as np
    from astropy.io import fits

    if len(raw) > MAX_FITS or not raw.startswith(b"SIMPLE  ="):
        raise SpectrumError("Expected a bounded uncompressed FITS product, not HTML or gzip")
    # This release's CHECKSUM is labelled Adler32, not a standard FITS checksum.
    with fits.open(io.BytesIO(raw), memmap=False, logical_as_bytes=True, checksum=False) as hdus:
        if len(hdus) != 2 or not isinstance(hdus[1], fits.BinTableHDU):
            raise SpectrumError("Expected PRIMARY plus one DataVector binary table")
        primary, table = hdus[0].header, hdus[1]
        if primary.get("INSTRUME") != "PEPSI" or not str(primary.get("OBJECT", "")).strip():
            raise SpectrumError("Missing PEPSI instrument/target identity")
        if not str(primary.get("FILE0", "")).startswith(row["basename"] + "."):
            raise SpectrumError("FITS FILE0 does not match the selected catalog basename")
        if table.name.casefold() != "datavector" or table.columns.names != [
            "Arg",
            "Fun",
            "Var",
            "Mask",
        ]:
            raise SpectrumError("Unexpected spectrum table or columns")
        if [str(c.format) for c in table.columns] != ["1D", "1D", "1D", "1L"]:
            raise SpectrumError("Unsupported FITS column shapes or mask storage")
        if any(
            table.header.get(f"TSCAL{i}", 1) != 1 or table.header.get(f"TZERO{i}", 0) != 0
            for i in range(1, 5)
        ):
            raise SpectrumError("Scaled FITS columns are outside the verified release contract")
        if table.columns[0].unit not in (None, "Angstrom", "angstrom", "AA"):
            raise SpectrumError("Unexpected wavelength unit; do not guess a conversion")
        if any(c.unit not in (None, "", "1") for c in table.columns[1:3]):
            raise SpectrumError("Expected dimensionless normalized intensity and variance")
        if not 2 <= table.header.get("NAXIS2", 0) <= 1_000_000:
            raise SpectrumError("Unsupported spectrum row count")
        data = table.data
        raw_mask = np.asarray(data["Mask"])
        if raw_mask.dtype != np.dtype("S1") or raw_mask.ndim != 1:
            raise SpectrumError(
                "Raw logical-byte access unavailable; use the pinned Astropy environment"
            )
        mask = raw_mask.view(np.uint8).tolist()
        wave, display, sigma, valid = clean_samples(data["Arg"], data["Fun"], data["Var"], mask)
        metadata = {
            key: primary.get(key)
            for key in (
                "INSTRUME",
                "OBJECT",
                "FILE0",
                "NUMFILES",
                "DATE-OBS",
                "UT-OBS",
                "EXPTIME",
                "DATE",
                "RADVEL",
                "CCFOFF",
                "CCFERR",
            )
        }
        metadata["HISTORY"] = list(primary.get("HISTORY", []))
        return {
            "wave": wave,
            "flux": data["Fun"].tolist(),
            "variance": data["Var"].tolist(),
            "mask": mask,
            "display": display,
            "sigma": sigma,
            "valid": valid,
            "header": metadata,
        }


def atomic_write(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pepsi-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def bounded_local(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise SpectrumError("Empty or oversized cached file")
    return raw


def workspace_output(path):
    out = path.expanduser().resolve()
    skill = Path(__file__).resolve().parents[1]
    if out == skill or skill in out.parents:
        raise SpectrumError("Outputs must be outside the installed skill directory")
    if any((out / name).is_symlink() for name in FILES):
        raise SpectrumError("Refusing symlinked cache/output files")
    if out.exists() and not (out / "cache.json").is_file() and any(out.iterdir()):
        raise SpectrumError(
            "Use an empty output directory; unrelated existing files will not be overwritten"
        )
    out.mkdir(parents=True, exist_ok=True)
    return out


def load_or_fetch(out, target, refresh, timeout):
    cache_path = out / "cache.json"
    if cache_path.exists():
        cache = json.loads(bounded_local(cache_path, MAX_INDEX))
        if not isinstance(cache, dict) or cache.get("schema") != 1:
            raise SpectrumError("Unsupported cache manifest; use a new output directory")
        row = select_target([cache["target"]], target)
        if cache["source_url"] != data_url(row) or cache["catalog_url"] != row["index_url"]:
            raise SpectrumError("Cache source identity mismatch")
        catalog = bounded_local(out / "catalog.csv", MAX_INDEX)
        raw = bounded_local(out / "spectrum.fits", MAX_FITS)
        if digest(catalog) != cache["catalog_sha256"] or digest(raw) != cache["fits_sha256"]:
            raise SpectrumError("Cache hash mismatch; use a new output directory")
        if (
            select_target(parse_catalog(catalog.decode("utf-8-sig"), row["index_url"]), target)
            != row
        ):
            raise SpectrumError("Cached catalog row differs from provenance")
        spectrum = read_spectrum(raw, row)
        if not refresh:
            return spectrum, cache, True
    rows, originals = get_catalog(timeout)
    row = select_target(rows, target)
    raw, http = fetch_bytes(data_url(row), MAX_FITS, timeout)
    spectrum = read_spectrum(raw, row)
    catalog = originals[row["index_url"]]
    cache = {
        "schema": 1,
        "target": row,
        "source_url": data_url(row),
        "catalog_url": row["index_url"],
        "retrieved_utc": utc_now(),
        "fits_sha256": digest(raw),
        "catalog_sha256": digest(catalog),
        "http": http,
    }
    atomic_write(out / "catalog.csv", catalog)
    atomic_write(out / "spectrum.fits", raw)
    atomic_write(cache_path, (json.dumps(cache, ensure_ascii=False, indent=2) + "\n").encode())
    return spectrum, cache, False


def write_csv(path, spectrum):
    # Retain native rows and original flux/variance, even where numeric validity fails.
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(
        [
            "wavelength_air_angstrom",
            "normalized_intensity",
            "variance",
            "sigma",
            "mask_raw_byte",
            "numeric_valid",
        ]
    )
    writer.writerows(
        zip(
            *(spectrum[key] for key in ("wave", "flux", "variance", "sigma", "mask", "valid")),
            strict=True,
        )
    )
    atomic_write(path, text.getvalue().encode())


def plot_spectrum(path, spectrum, row, bounds):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    low, high = bounds
    wave = np.asarray(spectrum["wave"])
    flux, sigma = np.asarray(spectrum["display"]), np.asarray(spectrum["sigma"])
    in_window = (wave >= low) & (wave <= high)
    if np.count_nonzero(in_window & np.asarray(spectrum["valid"])) < 2:
        raise SpectrumError(
            "Requested wavelength window has fewer than two numerically valid samples"
        )
    fig, (ax, err) = plt.subplots(
        2,
        1,
        figsize=(13, 6),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
        layout="constrained",
    )
    for segment in segment_slices(spectrum["wave"]):
        w, f, s = wave[segment], flux[segment], sigma[segment]
        select = (w >= low) & (w <= high)
        ax.plot(w[select], f[select], color="#174f78", lw=0.9)
        ax.fill_between(w[select], (f - s)[select], (f + s)[select], color="#469cbc", alpha=0.3)
        err.plot(w[select], s[select], color="#986642", lw=0.8)
    ax.set_title(f"PEPSI · {row['name']} · {row['type']} · catalog S/N {row['snr']}")
    ax.set_ylabel("Continuum-normalized intensity")
    err.set_ylabel("σ = √Var")
    err.set_xlabel("Stellar-rest-frame air wavelength [Å]")
    ax.set_xlim(low, high)
    for panel in (ax, err):
        panel.grid(alpha=0.15)
        panel.spines[["top", "right"]].set_visible(False)
    fig.supxlabel(MASK_NOTE, fontsize=9, color="#805322")
    stream = io.BytesIO()
    fig.savefig(stream, format="png", dpi=180)
    plt.close(fig)
    atomic_write(path, stream.getvalue())
    return int(in_window.sum())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="List the live Paper II catalogs; downloads no spectra"
    )
    parser.add_argument(
        "--target", default="18 Sco", help="Exact catalog name, SIMBAD label, or basename"
    )
    parser.add_argument(
        "--range", nargs=2, type=float, default=(5160.0, 5190.0), metavar=("LOW_A", "HIGH_A")
    )
    parser.add_argument("--out", type=Path, help="Dedicated workspace evidence-bundle directory")
    parser.add_argument(
        "--refresh", action="store_true", help="Deliberately refresh an existing verified cache"
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="Per-download socket/elapsed timeout in seconds"
    )
    args = parser.parse_args(argv)
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise SpectrumError("Timeout must be positive and finite")
        if args.list:
            rows, _ = get_catalog(args.timeout)
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if args.out is None:
            raise SpectrumError("--out is required unless --list is used")
        if (
            not all(math.isfinite(v) and v > 0 for v in args.range)
            or args.range[0] >= args.range[1]
        ):
            raise SpectrumError("Range must contain positive finite increasing Angstrom bounds")
        out = workspace_output(args.out)
        spectrum, cache, reused = load_or_fetch(out, args.target, args.refresh, args.timeout)
        window_count = plot_spectrum(out / "spectrum.png", spectrum, cache["target"], args.range)
        write_csv(out / "spectrum.csv", spectrum)
        provenance = {
            **cache,
            "generated_utc": utc_now(),
            "cache_reused": reused,
            "sample_count": len(spectrum["wave"]),
            "numeric_valid_count": sum(spectrum["valid"]),
            "wavelength_first_angstrom": spectrum["wave"][0],
            "wavelength_last_angstrom": spectrum["wave"][-1],
            "wavelength_frame": (
                "stellar rest frame, air Angstrom; release/paper convention, "
                "not inferred from absent TUNIT"
            ),
            "flux": "continuum-normalized intensity",
            "uncertainty": (
                "sqrt(Var); correlated pipeline errors, not total systematic uncertainty"
            ),
            "mask_policy": MASK_NOTE,
            "mask_raw_counts": {"1": len(spectrum["mask"])},
            "gap_policy": (
                "plot segments split at spacing >10 times full-grid median; no resampling"
            ),
            "plot_segments": len(segment_slices(spectrum["wave"])),
            "plot_range_angstrom": args.range,
            "plot_window_samples": window_count,
            "fits_header": spectrum["header"],
            "time_caveat": (
                "combined-product header dates are retained as strings, "
                "not a single physical observing epoch"
            ),
            "citation": (
                "Strassmeier, Ilyin & Weber 2018, A&A 612 A45, doi:10.1051/0004-6361/201731633"
            ),
            "software": {
                name: importlib.metadata.version(name)
                for name in ("astropy", "numpy", "matplotlib")
            },
            "python": sys.version.split()[0],
            "artifact_sha256": {
                name: digest((out / name).read_bytes())
                for name in FILES
                if name != "provenance.json"
            },
        }
        atomic_write(
            out / "provenance.json",
            (json.dumps(provenance, ensure_ascii=False, indent=2) + "\n").encode(),
        )
        print(MASK_NOTE, file=sys.stderr)
        print(
            json.dumps(
                {
                    "output_directory": str(out),
                    "target": cache["target"]["name"],
                    "sample_count": len(spectrum["wave"]),
                    "window_samples": window_count,
                    "cache_reused": reused,
                },
                indent=2,
            )
        )
        return 0
    except (SpectrumError, OSError, ImportError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
