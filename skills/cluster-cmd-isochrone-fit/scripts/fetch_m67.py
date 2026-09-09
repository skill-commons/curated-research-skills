#!/usr/bin/env python3
"""Fetch published M67 Gaia DR3 members and preserve a declared quality selection.

Uses the Python standard library. No inferred catalogue ages or stellar masses
enter the query. This adapter does not choose an age-sensitive fitting window.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ENDPOINT = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
TABLE = "J/A+A/686/A42/members"
CLUSTER = "NGC_2682"
MAX_BYTES = 3_000_000
MAX_ROWS = 5000
COLUMNS = (
    "Name",
    "GaiaDR3",
    "Prob",
    "RA_ICRS",
    "DE_ICRS",
    "pmRA",
    "e_pmRA",
    "pmDE",
    "e_pmDE",
    "Plx",
    "e_Plx",
    "Solved",
    "ELAT",
    "nueff",
    "pscol",
    "RUWE",
    "FidelityV1",
    "FG",
    "e_FG",
    "FBP",
    "e_FBP",
    "FRP",
    "e_FRP",
    "Gmag",
    "BPmag",
    "RPmag",
    "NSS",
    "VarFlag",
)
QUERY = (
    f"SELECT TOP {MAX_ROWS} "
    + ",".join(f'"{name}"' for name in COLUMNS)
    + f' FROM "{TABLE}" WHERE "Name" = \'{CLUSTER}\' ORDER BY "GaiaDR3"'
)
SOURCE_URL = (
    ENDPOINT
    + "?"
    + urllib.parse.urlencode(
        {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": QUERY}
    )
)
OUTPUT_COLUMNS = (
    "source_id",
    "g",
    "bp",
    "rp",
    "g_error",
    "bp_error",
    "rp_error",
    "member_score",
    "selected",
    "exclusion_reason",
    "plx",
    "plx_error",
    "ruwe",
    "nss",
    "c_star",
    "excess_sigma",
)
SELECTION = {
    "membership_score_min_inclusive": 0.5,
    "flux_snr_min_exclusive_each_band": 50.0,
    "ruwe_max_exclusive": 1.4,
    "nss_required": 0,
    "g_min_exclusive_for_excess_filter": 4.0,
    "corrected_excess_sigma_max_exclusive": 3.0,
    "excess_reference": "https://doi.org/10.1051/0004-6361/202039587",
    "fitting_magnitude_or_colour_window": None,
}


class CatalogueError(ValueError):
    """The source or requested output does not meet the adapter contract."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CatalogueError("The catalogue endpoint redirected; review its new location.")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fetch_source() -> bytes:
    """Read only the fixed public query, without proxy credentials or redirects."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        SOURCE_URL,
        headers={"Accept": "text/csv", "User-Agent": "CRS-M67-CMD/1.0"},
    )
    with opener.open(request, timeout=45) as response:
        if response.status != 200:
            raise CatalogueError(f"Unexpected catalogue status: {response.status}")
        declared = response.headers.get("Content-Length")
        if declared and (not declared.isdecimal() or int(declared) > MAX_BYTES):
            raise CatalogueError("Invalid or excessive catalogue Content-Length.")
        raw = response.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise CatalogueError("Empty catalogue or catalogue exceeds the 3 MB limit.")
    if declared is not None and len(raw) != int(declared):
        raise CatalogueError("Catalogue response was truncated.")
    return raw


def number(value: str, column: str) -> float | None:
    """Missing/nonfinite numeric values become explicit excluded-row blanks."""
    if not value.strip():
        return None
    try:
        result = float(value)
    except ValueError as error:
        raise CatalogueError(f"Invalid numeric value in {column}.") from error
    return result if math.isfinite(result) else None


def corrected_excess(colour: float, g: float, flux_ratio: float) -> tuple[float, float]:
    """Riello et al. (2021), Table 2 and Eq.18; filter is valid for G>4."""
    if g <= 4:
        raise CatalogueError("Corrected-excess filtering requires G > 4 mag.")
    if colour < 0.5:
        baseline = 1.154360 + 0.033772 * colour + 0.032277 * colour**2
    elif colour < 4:
        baseline = 1.162004 + 0.011464 * colour + 0.049255 * colour**2 - 0.005879 * colour**3
    else:
        baseline = 1.057572 + 0.140537 * colour
    sigma = 0.0059898 + 8.817481e-12 * g**7.618399
    return flux_ratio - baseline, sigma


def convert_rows(raw: bytes) -> list[dict]:
    """Validate identity/schema and preserve every source row with reasons."""
    if not raw or len(raw) > MAX_BYTES:
        raise CatalogueError("Source must contain between 1 byte and 3 MB.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CatalogueError("Source is not UTF-8 CSV.") from error
    reader = csv.DictReader(io.StringIO(text), strict=True)
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise CatalogueError("Unexpected catalogue columns; expected the fixed TAP query.")
    results = []
    identifiers = set()
    try:
        for raw_row in reader:
            if len(results) >= MAX_ROWS - 1:
                raise CatalogueError("Catalogue reached TOP 5000; possible truncation.")
            if None in raw_row or any(value is None for value in raw_row.values()):
                raise CatalogueError("Malformed CSV row width.")
            row = {key: value.strip() for key, value in raw_row.items()}
            source_id = row["GaiaDR3"]
            if row["Name"] != CLUSTER:
                raise CatalogueError("Source contains a cluster other than NGC_2682.")
            if not re.fullmatch(r"[1-9][0-9]{0,18}", source_id):
                raise CatalogueError("Gaia source IDs must be exact positive decimal integers.")
            if int(source_id) > 2**63 - 1 or source_id in identifiers:
                raise CatalogueError("Duplicate or out-of-range Gaia source ID.")
            identifiers.add(source_id)
            values = {
                key: number(row[key], key)
                for key in COLUMNS
                if key not in {"Name", "GaiaDR3", "VarFlag"}
            }
            score = values["Prob"]
            if score is None or not 0 <= score <= 1:
                raise CatalogueError("Membership score must be finite and between 0 and 1.")
            nss = values["NSS"]
            if nss is not None and (not nss.is_integer() or not 0 <= nss <= 6):
                raise CatalogueError("Unexpected Gaia non-single-star flag.")
            reasons = []
            if score < 0.5:
                reasons.append("membership_score_below_0.5")
            if values["RUWE"] is None or values["RUWE"] <= 0 or values["RUWE"] >= 1.4:
                reasons.append("ruwe_missing_or_outside_0_to_1.4")
            if nss != 0:
                reasons.append("nss_missing_or_nonzero")
            errors = {}
            good_flux = True
            for band in ("G", "BP", "RP"):
                flux, error = values["F" + band], values["e_F" + band]
                if flux is None or error is None or flux <= 0 or error <= 0:
                    reasons.append(f"{band.lower()}_flux_or_error_missing_or_nonpositive")
                    errors[band] = None
                    good_flux = False
                else:
                    errors[band] = 2.5 / math.log(10) * (error / flux)
                    if not math.isfinite(errors[band]) or errors[band] <= 0:
                        reasons.append(f"{band.lower()}_magnitude_error_invalid")
                        errors[band] = None
                        good_flux = False
                    if flux / error <= 50:
                        reasons.append(f"{band.lower()}_snr_at_or_below_50")
            g, bp, rp = (values[name] for name in ("Gmag", "BPmag", "RPmag"))
            c_star = excess_sigma = None
            if any(value is None for value in (g, bp, rp)):
                reasons.append("photometry_missing_or_nonfinite")
            elif not 4 < g < 25 or not -10 < bp < 35 or not -10 < rp < 35:
                reasons.append("photometry_outside_adapter_validity")
            elif good_flux:
                c_star, excess_sigma = corrected_excess(
                    bp - rp, g, (values["FBP"] + values["FRP"]) / values["FG"]
                )
                if not math.isfinite(c_star):
                    c_star = None
                    reasons.append("corrected_flux_excess_nonfinite")
                elif abs(c_star) >= 3 * excess_sigma:
                    reasons.append("corrected_flux_excess_at_or_above_3sigma")
            results.append(
                {
                    "source_id": source_id,
                    "g": g,
                    "bp": bp,
                    "rp": rp,
                    "g_error": errors["G"],
                    "bp_error": errors["BP"],
                    "rp_error": errors["RP"],
                    "member_score": score,
                    "selected": "false" if reasons else "true",
                    "exclusion_reason": ";".join(reasons),
                    "plx": values["Plx"],
                    "plx_error": values["e_Plx"],
                    "ruwe": values["RUWE"],
                    "nss": int(nss) if nss is not None else None,
                    "c_star": c_star,
                    "excess_sigma": excess_sigma,
                }
            )
    except csv.Error as error:
        raise CatalogueError("Malformed source CSV.") from error
    if not results:
        raise CatalogueError("Catalogue query returned no members.")
    return sorted(results, key=lambda row: int(row["source_id"]))


def csv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=OUTPUT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def run(out: Path, from_raw: Path | None = None) -> Path:
    out = out.expanduser().absolute()
    if out.exists() or out.is_symlink():
        raise CatalogueError("Output directory already exists; choose a new directory.")
    if out.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise CatalogueError("Write outputs outside the installed skill directory.")
    if from_raw is None:
        raw = fetch_source()
    else:
        with from_raw.expanduser().open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
    rows = convert_rows(raw)
    converted = csv_bytes(rows)
    now = datetime.now(UTC).isoformat()
    metadata = {
        "schema_version": 1,
        "photometric_system": "Gaia DR3",
        "magnitude_system": "Vega",
        "source_url": SOURCE_URL,
        "source_table": TABLE,
        "source_query": QUERY,
        "source_citation": "https://doi.org/10.26093/cds/vizier.36860042",
        "cluster": CLUSTER,
        "source_sha256": sha256(raw),
        "source_bytes": len(raw),
        "output_csv_sha256": sha256(converted),
        "selection": SELECTION,
        "n_rows": len(rows),
        "n_selected": sum(row["selected"] == "true" for row in rows),
        "units": {
            "photometry": "mag",
            "photometry_errors": "mag",
            "plx": "mas",
            "plx_error": "mas",
        },
        "parallax_correction": "None: member parallaxes remain as published.",
        "membership_score_interpretation": "HDBSCAN proximity score, not a calibrated probability.",
        "error_model": (
            "First-order mean-flux error propagation; no zero-point or model-scatter floor added."
        ),
        "provenance": {
            "created_utc": now,
            "retrieved_utc": now if from_raw is None else None,
            "source_mode": "network" if from_raw is None else "local_raw_csv",
            "adapter_sha256": sha256(Path(__file__).read_bytes()),
            "python_version": sys.version.split()[0],
        },
    }
    # Validate and build everything before creating the new bundle directory.
    payloads = {
        "source.csv": raw,
        "query.adql": (QUERY + "\n").encode("utf-8"),
        "stars.csv": converted,
        "stars.json": (
            json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8"),
    }
    out.mkdir(parents=True, exist_ok=False)
    for filename, content in payloads.items():
        with (out / filename).open("xb") as target:
            target.write(content)
    return out / "stars.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="New output bundle directory.")
    parser.add_argument(
        "--from-raw", type=Path, help="Convert a saved source.csv without network access."
    )
    args = parser.parse_args(argv)
    try:
        path = run(args.out, args.from_raw)
    except (CatalogueError, OSError, urllib.error.URLError) as error:
        print(f"M67 catalogue error: {error}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
