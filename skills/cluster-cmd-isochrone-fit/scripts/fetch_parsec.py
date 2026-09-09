#!/usr/bin/env python3
"""Fetch a bounded PARSEC/Gaia grid; validate and replay its local source bundle."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

SOURCE_URL = "https://stev.oapd.inaf.it/cgi-bin/cmd_3.9"
OUTPUT_URL = re.compile(r"https://stev\.oapd\.inaf\.it/tmp/output[0-9]+\.dat")
SOURCE_COLUMNS = [
    "Zini",
    "MH",
    "logAge",
    "Mini",
    "int_IMF",
    "Mass",
    "logL",
    "logTe",
    "logg",
    "label",
    "mbolmag",
    "Gmag",
    "G_BPmag",
    "G_RPmag",
]
CSV_COLUMNS = ["model_id", "log_age", "mh", "av", "mini", "label", "g_abs", "bp_abs", "rp_abs"]
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_MODELS_PER_REQUEST = 600
MAX_MODELS = 3000
COORDINATE_TOLERANCE_DEX = 0.0000201
DEFAULT_AV = "0,0.05,0.10,0.1271,0.15,0.20"
EXTINCTION_MODE = "OBC constant solar coefficients, Cardelli+O'Donnell R_V=3.1"


class GridError(ValueError):
    """An invalid query, service response, or local data bundle."""


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def decimal(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise GridError(f"Invalid numeric value: {value}") from exc
    if not result.is_finite() or abs(result) > Decimal("1000000"):
        raise GridError("Grid values must be finite and bounded")
    return result


def axis(low, high, step, lower, upper):
    low, high, step = map(decimal, (low, high, step))
    if any(value != value.quantize(Decimal("0.00001")) for value in (low, high, step)):
        raise GridError("Axis values may have at most five decimal places")
    if not Decimal(str(lower)) <= low <= high <= Decimal(str(upper)):
        raise GridError(f"Axis must lie within [{lower}, {upper}] in ascending order")
    if low == high:
        return [float(low)]
    if step < Decimal("0.001") or step > 1 or (high - low) % step:
        raise GridError("Step must be 0.001–1 and divide the requested interval exactly")
    count = int((high - low) / step) + 1
    if count > MAX_MODELS_PER_REQUEST:
        raise GridError("Too many values on one axis")
    return [float(low + i * step) for i in range(count)]


def make_grid(args):
    ages = axis(args.log_age_min, args.log_age_max, args.log_age_step, 6.6, 10.1)
    metals = axis(args.mh_min, args.mh_max, args.mh_step, -2.0, 0.4)
    av_decimals = [decimal(value.strip()) for value in args.av.split(",")]
    if any(value != value.quantize(Decimal("0.00001")) for value in av_decimals):
        raise GridError("A_V values may have at most five decimal places")
    avs = [float(value) for value in av_decimals]
    if not avs or len(avs) > 16 or len(set(avs)) != len(avs):
        raise GridError("Supply 1–16 distinct A_V values")
    if any(value < 0 or value > 1 for value in avs):
        raise GridError("This limited constant-extinction adapter accepts only 0 <= A_V <= 1")
    if avs != sorted(avs):
        raise GridError("A_V values must be ascending")
    if len(ages) * len(metals) > MAX_MODELS_PER_REQUEST:
        raise GridError(f"At most {MAX_MODELS_PER_REQUEST} age/metallicity pairs per request")
    if len(ages) * len(metals) * len(avs) > MAX_MODELS:
        raise GridError(f"At most {MAX_MODELS} models per bundle")
    return {"log_age": ages, "mh": metals, "av": avs}


def axis_request(values, upper_limit):
    if len(values) == 1:
        return str(values[0]), str(values[0]), "0"
    step = decimal(values[1]) - decimal(values[0])
    # CMD's floating-point endpoint arithmetic sometimes loses the last age.
    # Padding by half a step requests the same grid; exact output is checked below.
    upper = min(decimal(values[-1]) + step / 2, decimal(upper_limit))
    return str(values[0]), str(upper), str(step)


def query_parameters(grid, av):
    age_low, age_high, age_step = axis_request(grid["log_age"], "10.13")
    mh_low, mh_high, mh_step = axis_request(grid["mh"], "0.5")
    return {
        "submit_form": "Submit",
        "cmd_version": "3.9",
        "track_parsec": "parsec_CAF09_v1.2S",
        "track_colibri": "no",
        "track_postagb": "no",
        "track_omegai": "0.00",
        "n_inTPC": "10",
        "eta_reimers": "0.2",
        "kind_interp": "1",
        "kind_postagb": "-1",
        "photsys_file": "YBC_tab_mag_odfnew/tab_mag_gaiaEDR3.dat",
        "photsys_version": "odfnew",
        "dust_sourceM": "nodustM",
        "dust_sourceC": "nodustC",
        "kind_mag": "2",
        "kind_dust": "0",
        "extinction_av": str(av),
        "extinction_coeff": "constant",
        "extinction_curve": "cardelli",
        "kind_LPV": "4",
        "imf_file": "tab_imf/imf_kroupa_orig.dat",
        "isoc_isagelog": "1",
        "isoc_lagelow": age_low,
        "isoc_lageupp": age_high,
        "isoc_dlage": age_step,
        "isoc_agelow": "1e9",
        "isoc_ageupp": "1e10",
        "isoc_dage": "0",
        "isoc_ismetlog": "1",
        "isoc_metlow": mh_low,
        "isoc_metupp": mh_high,
        "isoc_dmet": mh_step,
        "isoc_zlow": "0.0152",
        "isoc_zupp": "0.03",
        "isoc_dz": "0",
        "output_kind": "0",
        "output_evstage": "1",
        "lf_maginf": "-15",
        "lf_magsup": "20",
        "lf_deltamag": "0.5",
        "sim_mtot": "1e4",
    }


def download(url, parameters=None, max_bytes=MAX_SOURCE_BYTES):
    """Use verified HTTPS, no curl config, cookies, netrc, proxies or redirects."""
    if (parameters is not None and url != SOURCE_URL) or (
        parameters is None and not OUTPUT_URL.fullmatch(url)
    ):
        raise GridError("Only the official CMD endpoint and its exact output URLs are allowed")
    # Apple's native curl uses macOS trust; unrelated Python/Conda CA stores can
    # lack intermediates needed by this service. Certificate checks stay enabled.
    curl = "/usr/bin/curl" if sys.platform == "darwin" else shutil.which("curl")
    if curl is None:
        raise GridError("curl is required for verified HTTPS retrieval")
    command = [
        curl,
        "-q",
        "--noproxy",
        "*",
        "--proto",
        "=https",
        "--fail",
        "--silent",
        "--show-error",
        "--connect-timeout",
        "15",
        "--max-time",
        "180",
        "--max-filesize",
        str(max_bytes),
    ]
    body = None
    if parameters is not None:
        body = urllib.parse.urlencode(parameters).encode("ascii")
        command += [
            "--header",
            "Content-Type: application/x-www-form-urlencoded",
            "--data-binary",
            "@-",
        ]
    command += [url]
    try:
        result = subprocess.run(command, input=body, capture_output=True, timeout=190, check=True)
    except (subprocess.SubprocessError, OSError) as exc:
        raise GridError(
            "Verified HTTPS retrieval failed; preserve the query and retry later"
        ) from exc
    if not result.stdout or len(result.stdout) > max_bytes:
        raise GridError("Empty or oversized service response")
    return result.stdout


def output_link(raw):
    try:
        response = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GridError("Invalid service HTML") from exc
    error = re.search(r'<p class="errorwarning">(.+?)</p>', response, re.S)
    if error:
        message = html.unescape(re.sub(r"<[^>]+>", "", error.group(1)))
        raise GridError(f"CMD rejected the query: {message[:240]}")
    matches = re.findall(r"href=[\"']?(\.\./tmp/output[0-9]+\.dat)(?=[\"'\s>])", response)
    urls = {urllib.parse.urljoin(SOURCE_URL, value) for value in matches}
    if len(urls) != 1:
        raise GridError("Expected exactly one uncompressed official isochrone output link")
    url = urls.pop()
    if not OUTPUT_URL.fullmatch(url):
        raise GridError("Unexpected isochrone output URL")
    return url


def match_axis(value, expected, name):
    matches = [x for x in expected if abs(value - x) <= COORDINATE_TOLERANCE_DEX]
    if len(matches) != 1:
        raise GridError(f"Unexpected or ambiguous {name}: {value}")
    return matches[0]


def parse_source(raw, grid, av):
    """Validate the full unfiltered source; do not silently drop malformed rows."""
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise GridError("Empty or oversized source")
    try:
        source = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise GridError("Expected ASCII isochrone source") from exc
    required = [
        "# File generated by CMD 3.9 ",
        "# isochrones based on PARSEC release v1.2S",
        "# Photometric system: Gaia EDR3 (all Vegamags, Gaia passbands from ESA/Gaia website)",
        "# Using OBC version of bolometric corrections",
        "# Kind of output: isochrone tables",
        "# O-rich circumstellar dust ignored",
        "# C-rich circumstellar dust ignored",
    ]
    if any(marker not in source for marker in required):
        raise GridError("Source model family/version, filter, dust, or output contract changed")
    extinctions = re.findall(
        r"# Attention: photometry includes extinction of Av=([^,]+), "
        r"with coefficients derived for the Sun, for Cardelli et al 89 "
        r"\+ O'Donnell 94 Rv=3.1 extinction curve\.",
        source,
    )
    if av == 0:
        if extinctions and any(float(value) != 0 for value in extinctions):
            raise GridError("Unexpected extinction in zero-extinction source")
    elif len(extinctions) != 1 or abs(float(extinctions[0]) - av) > 0.0000001:
        raise GridError("Source extinction does not match requested A_V or extinction law")
    rows, schema_seen, counts, last_mass, closed = [], False, {}, {}, set()
    previous = None
    for number, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        if line.startswith("#"):
            if line.startswith("# Zini"):
                if line[1:].split() != SOURCE_COLUMNS:
                    raise GridError("Source column schema changed")
                schema_seen = True
            continue
        if not schema_seen or len(line.split()) != len(SOURCE_COLUMNS):
            raise GridError(f"Missing schema or wrong column count at source row {number}")
        try:
            values = list(map(float, line.split()))
        except ValueError as exc:
            raise GridError(f"Non-numeric source row {number}") from exc
        if not all(math.isfinite(value) for value in values):
            raise GridError(f"Non-finite source row {number}")
        z, mh, age, mini, _, mass, _, _, _, label, _, g, bp, rp = values
        age = match_axis(age, grid["log_age"], "log age")
        mh = match_axis(mh, grid["mh"], "[M/H]")
        if not (0 < z < 0.1 and mini > 0 and 0 <= mass <= mini + 0.02):
            raise GridError(f"Invalid stellar mass or composition at row {number}")
        if label != int(label) or not 0 <= label <= 7:
            raise GridError(f"Unexpected phase label for PARSEC without COLIBRI at row {number}")
        if any(abs(value) > 40 for value in (g, bp, rp)):
            raise GridError(f"Implausible or sentinel magnitude at row {number}")
        key = (age, mh)
        if key != previous:
            if key in closed:
                raise GridError("Isochrone rows are not contiguous")
            if previous is not None:
                closed.add(previous)
        if mini < last_mass.get(key, 0):
            raise GridError("Initial masses decrease within an isochrone")
        last_mass[key], previous = mini, key
        counts[key] = counts.get(key, 0) + 1
        model_id = f"age{age:.5f}_mh{mh:+.5f}_av{av:.5f}"
        rows.append((model_id, age, mh, av, mini, int(label), g, bp, rp))
    expected = {(age, mh) for age in grid["log_age"] for mh in grid["mh"]}
    if set(counts) != expected or any(count < 20 for count in counts.values()):
        raise GridError("Incomplete age/metallicity grid or truncated isochrone")
    # CMD terminates a completed table with a comment; catch truncation within its final model.
    if not source.rstrip().endswith("#isochrone terminated"):
        raise GridError("Missing final isochrone terminator; output may be truncated")
    return rows


def csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerows(rows)
    return output.getvalue().encode("ascii")


def safe_output(value):
    path = Path(value).expanduser().absolute()
    if ".." in path.parts:
        raise GridError("Output must not contain '..'")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise GridError("Output path and its parents must not be symlinks; use their real paths")
    if (
        path == Path(__file__).resolve().parents[1]
        or Path(__file__).resolve().parents[1] in path.parents
    ):
        raise GridError("Keep model data outside the installed skill")
    if path.exists() and not path.is_dir():
        raise GridError("Output already exists and is not a directory")
    return path


def expected_names(grid):
    return {"model.csv", "model.json"} | {
        f"{kind}-{index:02d}.{extension}"
        for index in range(len(grid["av"]))
        for kind, extension in (("source", "dat"), ("response", "html"))
    }


def verify_bundle(path, grid):
    expected = expected_names(grid)
    if not path.is_dir() or {child.name for child in path.iterdir()} != expected:
        raise GridError("Bundle inventory differs from the requested grid")
    if any(child.is_symlink() or not child.is_file() for child in path.iterdir()):
        raise GridError("Bundle may contain only regular files")
    if (path / "model.json").stat().st_size > 1024 * 1024:
        raise GridError("Oversized model manifest")
    try:
        manifest = json.loads((path / "model.json").read_text())
    except (ValueError, UnicodeDecodeError) as exc:
        raise GridError("Invalid model manifest") from exc
    contract = {
        "schema_version": 1,
        "photometric_system": "Gaia EDR3 (Vega)",
        "model_family": "PARSEC",
        "model_version": "1.2S",
        "source_url": SOURCE_URL,
        "extinction_mode": EXTINCTION_MODE,
        "grid": grid,
    }
    if any(manifest.get(key) != value for key, value in contract.items()):
        raise GridError("Manifest scientific contract or requested grid differs")
    files = manifest.get("files", {})
    if set(files) != expected - {"model.json"}:
        raise GridError("Manifest file inventory differs")
    total, contents = 0, {}
    for name, record in files.items():
        size = (path / name).stat().st_size
        total += size
        limit = MAX_BUNDLE_BYTES if name == "model.csv" else MAX_SOURCE_BYTES
        if not 0 < size <= limit or total > MAX_BUNDLE_BYTES:
            raise GridError("Oversized bundle artifact")
        raw = (path / name).read_bytes()
        if record != {"bytes": size, "sha256": sha256(raw)}:
            raise GridError(f"Integrity check failed for {name}")
        contents[name] = raw
    queries, rows = manifest.get("queries", []), []
    if len(queries) != len(grid["av"]):
        raise GridError("Manifest query count differs")
    for index, av in enumerate(grid["av"]):
        query = queries[index]
        raw_name, response_name = f"source-{index:02d}.dat", f"response-{index:02d}.html"
        if query.get("parameters") != query_parameters(grid, av):
            raise GridError("Manifest query parameters differ")
        if query.get("output_url") != output_link(contents[response_name]):
            raise GridError("Saved response and output URL differ")
        if query.get("source_file") != raw_name or query.get("response_file") != response_name:
            raise GridError("Manifest source association differs")
        rows.extend(parse_source(contents[raw_name], grid, av))
    if csv_bytes(rows) != contents["model.csv"]:
        raise GridError("Normalized CSV differs from the verified raw sources")
    if manifest.get("output_csv_sha256") != sha256(contents["model.csv"]):
        raise GridError("Manifest output CSV hash differs")
    if manifest.get("row_count") != len(rows) or manifest.get("model_count") != len(
        {row[0] for row in rows}
    ):
        raise GridError("Manifest row/model counts differ")
    return manifest


def fetch_grid(path, grid, offline=False):
    if path.exists() and any(path.iterdir()):
        return verify_bundle(path, grid)
    if offline:
        raise GridError("Offline replay requires an existing complete bundle")
    contents, queries, rows = {}, [], []
    for index, av in enumerate(grid["av"]):
        parameters = query_parameters(grid, av)
        response = download(SOURCE_URL, parameters, max_bytes=2 * 1024 * 1024)
        url = output_link(response)
        raw = download(url)
        parsed = parse_source(raw, grid, av)
        source_name, response_name = f"source-{index:02d}.dat", f"response-{index:02d}.html"
        contents[source_name], contents[response_name] = raw, response
        rows.extend(parsed)
        queries.append(
            {
                "parameters": parameters,
                "output_url": url,
                "source_file": source_name,
                "response_file": response_name,
                "retrieved_utc": datetime.now(UTC).isoformat(),
            }
        )
        print(f"Validated A_V={av:g}: {len(parsed)} source rows", file=sys.stderr)
    contents["model.csv"] = csv_bytes(rows)
    if sum(map(len, contents.values())) > MAX_BUNDLE_BYTES:
        raise GridError("Combined bundle exceeds the size limit")
    manifest = {
        "schema_version": 1,
        "photometric_system": "Gaia EDR3 (Vega)",
        "source_url": SOURCE_URL,
        "model_family": "PARSEC",
        "model_version": "1.2S",
        "cmd_version": "3.9",
        "bolometric_corrections": "OBC",
        "extinction_mode": EXTINCTION_MODE,
        "magnitude_convention": (
            "Absolute Vega magnitudes including model A_V; add distance modulus only"
        ),
        "metallicity_convention": (
            "PARSEC initial scaled-solar [M/H], not necessarily measured surface [Fe/H]"
        ),
        "coordinate_tolerance_dex": COORDINATE_TOLERANCE_DEX,
        "coordinate_normalization": (
            "Match nominal requested age/MH within recorded tolerance; "
            "original printed coordinates remain in source files"
        ),
        "grid": grid,
        "queries": queries,
        "columns": CSV_COLUMNS,
        "row_count": len(rows),
        "output_csv_sha256": sha256(contents["model.csv"]),
        "model_count": len({row[0] for row in rows}),
        "source_columns": SOURCE_COLUMNS,
        "source_rows": (
            "All phases and mass rows preserved in original order; no fitting selection applied"
        ),
        "helper_sha256": sha256(Path(__file__).read_bytes()),
        "transport": "curl with verified HTTPS; no redirects, proxy, netrc, config, or cookies",
        "files": {
            name: {"bytes": len(raw), "sha256": sha256(raw)} for name, raw in contents.items()
        },
        "limitations": [
            "OBC uses constant solar extinction coefficients, not star-by-star YBC extinction.",
            "Applied extinction differs from illustrative G2V coefficients in CMD output HTML; "
            "use model magnitudes directly.",
            "Fixed scaled-solar composition, helium enrichment law, nonrotating PARSEC 1.2S, "
            "RGB Reimers eta 0.2; no COLIBRI.",
            "A fit is conditional on this finite model grid and all external distance, "
            "extinction, metallicity and membership assumptions.",
        ],
        "citations": [
            "https://doi.org/10.1111/j.1365-2966.2012.21948.x",
            "https://doi.org/10.1093/mnras/stu1605",
            "https://doi.org/10.1093/mnras/stv1281",
            "https://doi.org/10.1093/mnras/stu2029",
            "https://doi.org/10.1051/0004-6361:20079174",
            "https://doi.org/10.1086/590733",
            "https://doi.org/10.1051/0004-6361/202039587",
        ],
        "data_rights": (
            "Public research output; no explicit redistribution license found on CMD pages. "
            "Retain source headers/citations; model data are not covered "
            "by the skill's MIT license."
        ),
    }
    contents["model.json"] = json_bytes(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".parsec-stage-", dir=path.parent) as temporary:
        staging = Path(temporary)
        for name, raw in contents.items():
            (staging / name).write_bytes(raw)
        verify_bundle(staging, grid)
        if path.exists():
            if any(path.iterdir()):
                raise GridError("Output changed while the model grid was being retrieved")
            path.rmdir()
        staging.rename(path)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", required=True, help="New/empty directory or verified existing bundle"
    )
    parser.add_argument("--log-age-min", default="9.0")
    parser.add_argument("--log-age-max", default="10.0")
    parser.add_argument("--log-age-step", default="0.05")
    parser.add_argument("--mh-min", default="-0.2")
    parser.add_argument("--mh-max", default="0.2")
    parser.add_argument("--mh-step", default="0.1")
    parser.add_argument(
        "--av", default=DEFAULT_AV, help="Ascending comma-separated A_V values in mag"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Verify hashes and regenerate CSV in memory, without network or writes",
    )
    args = parser.parse_args(argv)
    try:
        grid = make_grid(args)
        path = safe_output(args.out)
        manifest = fetch_grid(path, grid, args.offline)
    except (GridError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{path / 'model.csv'}: {manifest['model_count']} models; {manifest['row_count']} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
