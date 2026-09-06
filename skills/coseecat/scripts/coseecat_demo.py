#!/usr/bin/env python3
"""Bounded CoSEE-Cat DR1 event and catalogue demos with verified offline replay."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import re
import statistics
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

BASE = "https://coseecat.aip.de"
TAP = BASE + "/tap"
PAPER = "https://doi.org/10.1051/0004-6361/202554830"
RELEASE = "https://doi.org/10.17876/coseecat/dr.1"
LIMIT = 8 * 1024 * 1024
ROW_LIMIT = 1001
PLOTS = {
    "Overview": "published-overview.png",
    "EUI-STIX": "published-eui-stix.png",
    "Anisotropy": "published-anisotropy.png",
    "RPW-STIX": "published-rpw-stix.png",
    "Interplanetary Context": "published-ip-context.png",
}
GROUPS = ("impulsive", "gradual", "intermediate", "unknown")
TIMES = {
    "rpw_t3_time": "RPW type III onset (observed)",
    "stix_tpeak": "STIX main peak (observed)",
    "stix_tpeak_epd": "STIX peak associated with electrons (observed)",
    "tsa_itime": "TSA release estimate (light-time shifted)",
    "vda_itime": "VDA release estimate (light-time shifted)",
    "epd_tonset": "EPD electron onset at spacecraft (observed)",
}
TIME_NOTE = (
    "TSA/VDA release estimates already shifted by Sun-to-Solar-Orbiter light time; "
    "no additional correction applied. Associations are not proof of causation."
)


class DemoError(ValueError):
    pass


class BufferedBody(io.BytesIO):
    def read(self, size=-1, decode_content=None):
        # requests.iter_content already decoded transport compression. PyVO's
        # VOTable reader passes the urllib3-specific decode_content argument.
        return super().read(size)


def event_id(value):
    value = str(value)
    if not re.fullmatch(r"[0-9]{10}", value):
        raise DemoError("event ID must contain exactly ten digits")
    return value


def query_for(mode, identifier):
    if mode == "event":
        return f"SELECT TOP 1 * FROM coseecat_dr1.main WHERE event_id={event_id(identifier)}"
    return (
        f"SELECT TOP {ROW_LIMIT} event_id,epd_tonset,epd_tpeak,epd_compo "
        "FROM coseecat_dr1.main ORDER BY event_id"
    )


def tap_parameters(query):
    # This deployment returns HTTP 500 for MAXREC; ADQL TOP is the tested cap.
    return {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable", "QUERY": query}


def permitted_url(url, identifier=None):
    p = urlsplit(url)
    if p.scheme != "https" or p.netloc != "coseecat.aip.de" or p.fragment:
        raise DemoError("Refusing non-catalogue HTTPS URL")
    if p.path in ("/tap/sync", "/datalink/links") and not p.query:
        return
    if (
        identifier
        and not p.query
        and re.fullmatch(
            rf"/files/dr1/plots/{re.escape(event_id(identifier))}_[A-Za-z0-9_-]+\.png", p.path
        )
    ):
        return
    raise DemoError("Refusing unexpected catalogue URL path")


def bounded_session(identifier=None):
    import requests

    class Session(requests.Session):
        def __init__(self):
            super().__init__()
            self.trust_env = False  # no ambient netrc credentials or proxy credentials
            self.payloads = []

        def request(self, method, url, **kwargs):
            permitted_url(url, identifier)
            if method.upper() not in ("GET", "POST"):
                raise DemoError("Only public query/read requests are supported")
            kwargs.update(timeout=(10, 45), allow_redirects=False, stream=True)
            response = super().request(method, url, **kwargs)
            try:
                if response.status_code != 200:
                    raise DemoError(f"HTTP {response.status_code}; redirects are not followed")
                size = response.headers.get("Content-Length")
                if size and int(size) > LIMIT:
                    raise DemoError("Response exceeds 8 MiB limit")
                chunks, total = [], 0
                for chunk in response.iter_content(65536):
                    total += len(chunk)
                    if total > LIMIT:
                        raise DemoError("Response exceeds 8 MiB limit")
                    chunks.append(chunk)
                payload = b"".join(chunks)
            finally:
                response.close()
            # PyVO consumes .raw, while requests callers consume .content.
            response._content = payload
            response._content_consumed = True
            response.raw = BufferedBody(payload)
            self.payloads.append((url, payload))
            return response

    return Session()


def parse_result(raw, session=None, datalink=False):
    import pyvo
    from astropy.io.votable import parse

    if len(raw) > LIMIT:
        raise DemoError("Cached VOTable exceeds size limit")
    votable = parse(io.BytesIO(raw))
    cls = pyvo.dal.adhoc.DatalinkResults if datalink else pyvo.dal.TAPResults
    result = cls(votable, session=session)
    if not datalink and result.query_status != "OK":
        raise DemoError(f"Incomplete TAP response: {result.query_status}")
    return result


def plain(value):
    import numpy as np

    if np.ma.is_masked(value):
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def records(result):
    table = result.to_table()
    return [{c: plain(row[c]) for c in table.colnames} for row in table]


def validate_units(result, mode):
    table = result.to_table()
    expected = {"epd_tonset": "UTC", "epd_tpeak": "UTC"}
    if mode == "event":
        expected.update(dict.fromkeys(TIMES, "UTC"))
        expected["vda_time_unc"] = "min"
    for name, unit in expected.items():
        if name not in table.colnames or str(table[name].unit) != unit:
            raise DemoError(f"Unexpected or missing unit for {name}; expected {unit}")


def validate_rows(rows, mode, identifier):
    required = {"event_id", "epd_tonset", "epd_tpeak", "epd_compo"}
    if mode == "event":
        required |= set(TIMES) | {"vda_time_unc", "stix_epd_qual", "goes_estim"}
    if not rows or len(rows) >= ROW_LIMIT:
        raise DemoError("Empty or potentially truncated catalogue selection")
    if any(not required <= row.keys() for row in rows):
        raise DemoError("Missing required catalogue columns")
    ids = [event_id(row["event_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise DemoError("Duplicate event IDs")
    if mode == "event" and (len(rows) != 1 or ids != [identifier]):
        raise DemoError("Returned event identity does not match request")


def plot_links(links, identifier):
    selected = {}
    for row in links:
        if str(row.get("ID")) != identifier:
            raise DemoError("DataLink event ID mismatch")
        semantics = str(row.get("semantics", ""))
        if semantics not in ("#preview-plot", "http://www.ivoa.net/rdf/datalink/core#preview-plot"):
            continue
        description = row.get("description")
        if description not in PLOTS:
            continue  # preserve unknown products in the inventory; never silently fetch them
        if row.get("error_message"):
            raise DemoError("DataLink reports an error for a selected plot")
        if description in selected:
            raise DemoError("Duplicate DataLink plot description")
        if row.get("content_type") not in ("image/png", "application/png"):
            raise DemoError("Unexpected plot MIME type")
        permitted_url(str(row.get("access_url", "")), identifier)
        if not urlsplit(row["access_url"]).path.startswith("/files/dr1/plots/"):
            raise DemoError("Plot URL is not an archive image")
        selected[description] = row["access_url"]
    return selected


def validate_png(raw):
    if len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise DemoError("Downloaded plot is not a PNG")
    width, height = struct.unpack(">II", raw[16:24])
    if not 0 < width * height <= 40_000_000:
        raise DemoError("Unexpected PNG dimensions")
    from matplotlib.image import imread

    imread(io.BytesIO(raw), format="png")  # decode before admitting a plot to the cache


def timestamp(value):
    if value is None or str(value).strip() in ("", "--"):
        return None
    text = str(value)
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?", text
    ):
        raise ValueError("Expected a complete catalogue timestamp")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    # Catalogue timestamp strings declare UTC in the VOTable metadata.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def timeline(row):
    onset = timestamp(row["epd_tonset"])
    if onset is None:
        raise DemoError("No EPD onset for timeline reference")
    points = []
    for column, label in TIMES.items():
        value = timestamp(row[column])
        uncertainty = None
        if column == "vda_itime" and row.get("vda_time_unc") is not None:
            uncertainty = float(row["vda_time_unc"])
            if not math.isfinite(uncertainty) or uncertainty < 0:
                raise DemoError("Invalid VDA timing uncertainty")
        points.append(
            {
                "column": column,
                "label": label,
                "utc": value.isoformat() if value else None,
                "minutes_from_epd_onset": (value - onset).total_seconds() / 60 if value else None,
                "standard_uncertainty_min": uncertainty if value else None,
            }
        )
    return points


def rise_times(rows):
    derived, bins = [], {group: [] for group in GROUPS}
    for row in rows:
        group = str(row["epd_compo"] or "").strip() or "unknown"
        if group not in GROUPS:
            raise DemoError(f"Unknown ion-composition category: {group!r}")
        reason, delta = None, None
        try:
            onset, peak = timestamp(row["epd_tonset"]), timestamp(row["epd_tpeak"])
            if onset is None or peak is None:
                reason = "missing timestamp"
            else:
                delta = (peak - onset).total_seconds() / 60
                if delta < 0:
                    reason, delta = "negative rise time", None
        except ValueError:
            reason = "invalid timestamp"
        if reason is None:
            bins[group].append(delta)
        derived.append(
            {
                **row,
                "composition_group": group,
                "rise_time_min": delta,
                "included": reason is None,
                "exclusion_reason": reason,
            }
        )
    summary = {
        "total_events": len(rows),
        "included": sum(map(len, bins.values())),
        "excluded": sum(row["included"] is False for row in derived),
        "groups": {
            g: {"n": len(v), "median_min": statistics.median(v) if v else None}
            for g, v in bins.items()
        },
        "definition": "epd_tpeak minus epd_tonset, minutes; zero retained, negatives excluded",
        "classification": "Published ion composition, not inferred from rise time",
    }
    return derived, summary, bins


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def render_timeline(out, row, points):
    plt = pyplot()
    fig, ax = plt.subplots(figsize=(12, 5.5), layout="constrained")
    for i, point in enumerate(points):
        x = point["minutes_from_epd_onset"]
        if x is not None:
            ax.errorbar(
                x,
                i,
                xerr=point["standard_uncertainty_min"],
                fmt="o",
                color="#a45d18" if "itime" in point["column"] else "#17628c",
                capsize=4,
            )
        else:
            ax.text(0.99, i, "not recorded", transform=ax.get_yaxis_transform(), ha="right")
    ax.set_yticks(range(len(points)), [p["label"] for p in points])
    ax.axvline(0, color="grey", ls="--", lw=0.8)
    ax.set_xlabel(f"Minutes relative to EPD onset: {row['epd_tonset']} UTC")
    ax.set_title(
        f"CoSEE-Cat DR1 — event {row['event_id']}\nCatalogue timing summary, not raw time series"
    )
    ax.grid(axis="x", alpha=0.2)
    fig.get_layout_engine().set(rect=(0, 0.14, 1, 0.86))
    fig.text(
        0.02,
        0.065,
        "Release estimates are already light-time shifted. VDA bars: 1σ fit uncertainty.",
        fontsize=9,
    )
    fig.text(
        0.02, 0.02, "Warmuth et al. 2025 · CoSEE-Cat DR1 · Association ≠ causation", fontsize=9
    )
    fig.savefig(out / "timeline.png", dpi=160)
    plt.close(fig)


def render_rises(out, summary, bins):
    plt = pyplot()
    fig, ax = plt.subplots(figsize=(10, 6), layout="constrained")
    colors = ("#17628c", "#b15322", "#5f7f3e", "#777777")
    for (group, values), color in zip(bins.items(), colors, strict=True):
        if values:
            xs = sorted(values)
            ys = [(i + 1) / len(xs) for i in range(len(xs))]
            median = summary["groups"][group]["median_min"]
            ax.step(
                xs,
                ys,
                where="post",
                label=f"{group}: n={len(xs)}, median={median:g} min",
                color=color,
            )
    ax.set_xscale("symlog", linthresh=1)
    ax.set(
        xlabel="Electron rise time (minutes; linear to 1 min, logarithmic beyond)",
        ylabel="Fraction with rise time ≤ x",
        ylim=(0, 1.03),
        title="CoSEE-Cat DR1 — electron rise times by published ion composition",
    )
    ax.legend(loc="lower right")
    ax.grid(alpha=0.2)
    fig.get_layout_engine().set(rect=(0, 0.12, 1, 0.88))
    fig.text(
        0.02,
        0.065,
        f"{summary['included']}/{summary['total_events']} usable events; "
        f"{summary['excluded']} excluded. Zeros and long tails retained; no significance test.",
        fontsize=9,
    )
    fig.text(
        0.02,
        0.02,
        "Warmuth et al. 2025 · CoSEE-Cat DR1 · Composition is not defined by rise time.",
        fontsize=9,
    )
    fig.savefig(out / "rise-times.png", dpi=160)
    plt.close(fig)


def report(out, title, summary, figures, links=()):
    esc = html.escape
    panels = "".join(
        f'<section><h2>{esc(label)}</h2><img src="{esc(name)}" alt="{esc(label)}"></section>'
        for label, name in figures
    )
    external = "".join(
        f'<li><a href="{esc(str(r.get("access_url", "")), quote=True)}">'
        f"{esc(str(r.get('description', '')))}</a> (external; not cached)</li>"
        for r in links
        if urlsplit(str(r.get("access_url", ""))).scheme == "https"
        and r.get("content_type") == "video/mp4"
    )
    doc = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        "<style>body{font:18px system-ui;max-width:1200px;margin:2rem auto;"
        "padding:1rem;color:#17364a}img{max-width:100%;height:auto}section{margin:3rem 0}"
        "pre{white-space:pre-wrap;font-size:14px}</style>"
        f"<title>{esc(title)}</title><h1>{esc(title)}</h1><p>New catalogue-summary figures and "
        "retrieved published diagnostics are labelled separately. "
        "No raw instrument data were reduced.</p>"
        f'<p>Sources: <a href="{PAPER}">Warmuth et al. 2025</a>; '
        f'<a href="{RELEASE}">CoSEE-Cat DR1</a>.</p>{panels}<h2>Summary</h2>'
        f"<pre>{esc(json.dumps(summary, indent=2, ensure_ascii=False))}</pre>"
        f"<h2>External movie links</h2><ul>{external}</ul>"
    )
    (out / "report.html").write_text(doc, encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def output_path(value):
    path = Path(value)
    skill = Path(__file__).resolve().parents[1]
    if path.is_symlink() or path.resolve().is_relative_to(skill):
        raise DemoError("Output must be outside the installed skill and not a symlink")
    if path.exists() and not path.is_dir():
        raise DemoError("Output is not a directory")
    allowed = {
        "cache.json",
        "catalog.xml",
        "datalinks.xml",
        "query.adql",
        "summary.json",
        "timeline.csv",
        "timeline.png",
        "rise-times.csv",
        "rise-times.png",
        "report.html",
        "provenance.json",
        *PLOTS.values(),
    }
    if path.exists() and any(
        p.is_symlink() or not p.is_file() or p.name not in allowed for p in path.iterdir()
    ):
        raise DemoError("Refusing symlinks or unrelated output contents")
    return path


def load_or_fetch(out, mode, identifier, offline):
    query = query_for(mode, identifier)
    cache_path = out / "cache.json"
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
        if (cache.get("format"), cache.get("mode"), cache.get("event_id"), cache.get("query")) != (
            1,
            mode,
            identifier,
            query,
        ):
            raise DemoError("Cache selection mismatch; choose a fresh output directory")
        sources = cache["sources"]
        if not {"catalog.xml", "query.adql"} <= sources.keys():
            raise DemoError("Incomplete source manifest")
        for name, record in sources.items():
            if name not in {"catalog.xml", "datalinks.xml", "query.adql", *PLOTS.values()}:
                raise DemoError("Unsafe source filename in cache")
            if not (out / name).is_file() or digest(out / name) != record["sha256"]:
                raise DemoError(f"Cache hash mismatch: {name}; nothing re-fetched")
        if (out / "query.adql").read_text() != query + "\n":
            raise DemoError("Cached query mismatch")
        return cache
    if offline:
        raise DemoError("Offline mode requires a complete verified cache")
    if out.exists() and any(out.iterdir()):
        raise DemoError("Incomplete/unrecognized bundle; choose a fresh output directory")
    out.mkdir(parents=True, exist_ok=True)
    with bounded_session(identifier) as session:
        response = session.post(TAP + "/sync", data=tap_parameters(query))
        raw = response.content
        result = parse_result(raw, session=session)
        validate_units(result, mode)
        validate_rows(records(result), mode, identifier)
        (out / "catalog.xml").write_bytes(raw)
        (out / "query.adql").write_text(query + "\n")
        urls = {"catalog.xml": TAP + "/sync", "query.adql": None}
        if mode == "event":
            start = len(session.payloads)
            datalinks = list(result.iter_datalinks(preserve_order=True))
            if len(datalinks) != 1 or len(session.payloads) != start + 1:
                raise DemoError("Expected one DataLink response for one event")
            url, raw_links = session.payloads[-1]
            if url != BASE + "/datalink/links":
                raise DemoError("Unexpected DataLink service")
            links = records(datalinks[0])
            selected = plot_links(links, identifier)
            (out / "datalinks.xml").write_bytes(raw_links)
            urls["datalinks.xml"] = url
            for description, url in selected.items():
                payload = session.get(url).content
                validate_png(payload)
                name = PLOTS[description]
                (out / name).write_bytes(payload)
                urls[name] = url
    cache = {
        "format": 1,
        "mode": mode,
        "event_id": identifier,
        "query": query,
        "retrieved_utc": datetime.now(UTC).isoformat(),
        "sources": {name: {"url": url, "sha256": digest(out / name)} for name, url in urls.items()},
    }
    write_json(cache_path, cache)
    return cache


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("event", "rise-times"))
    parser.add_argument(
        "--event-id", help="ten-digit catalogue ID; default 2011171841 in event mode"
    )
    parser.add_argument("--out", required=True, help="dedicated directory outside the skill")
    parser.add_argument(
        "--offline", action="store_true", help="refuse network; require verified cache"
    )
    args = parser.parse_args(argv)
    try:
        if args.mode == "rise-times" and args.event_id:
            raise DemoError("--event-id applies only to event mode")
        identifier = event_id(args.event_id or "2011171841") if args.mode == "event" else None
        out = output_path(args.out)
        cache = load_or_fetch(out, args.mode, identifier, args.offline)
        result = parse_result((out / "catalog.xml").read_bytes())
        validate_units(result, args.mode)
        rows = records(result)
        validate_rows(rows, args.mode, identifier)
        sources = cache["sources"]
        expected = {"query.adql": None, "catalog.xml": TAP + "/sync"}
        if args.mode == "event":
            links = records(parse_result((out / "datalinks.xml").read_bytes(), datalink=True))
            selected = plot_links(links, identifier)
            expected["datalinks.xml"] = BASE + "/datalink/links"
            expected.update({PLOTS[d]: url for d, url in selected.items()})
        if set(expected) != set(sources) or any(
            sources[n]["url"] != u for n, u in expected.items()
        ):
            raise DemoError("Cache source URL or inventory mismatch")
        if args.mode == "event":
            points = timeline(rows[0])
            summary = {
                "event": rows[0],
                "timeline": points,
                "timing_convention": TIME_NOTE,
                "datalinks": links,
                "missing_published_plots": sorted(set(PLOTS) - set(selected)),
            }
            write_csv(out / "timeline.csv", points)
            render_timeline(out, rows[0], points)
            figures = [("New catalogue timeline", "timeline.png")]
            figures += [(f"Published diagnostic — {d}", PLOTS[d]) for d in selected]
            report(out, f"CoSEE-Cat DR1 event {identifier}", summary, figures, links)
        else:
            derived, summary, bins = rise_times(rows)
            if summary["included"] == 0:
                raise DemoError("No usable rise times to plot")
            write_csv(out / "rise-times.csv", derived)
            render_rises(out, summary, bins)
            report(
                out,
                "CoSEE-Cat DR1 rise-time comparison",
                summary,
                [("New analysis from catalogue timestamps", "rise-times.png")],
            )
        write_json(out / "summary.json", summary)
        from importlib.metadata import version

        write_json(
            out / "provenance.json",
            {
                **cache,
                "skill_version": "1.0.0",
                "paper": PAPER,
                "release": RELEASE,
                "helper_sha256": digest(Path(__file__)),
                "python": sys.version.split()[0],
                "packages": {
                    p: version(p) for p in ("pyvo", "requests", "astropy", "numpy", "matplotlib")
                },
                "artifacts": {
                    p.name: digest(p)
                    for p in sorted(out.iterdir())
                    if p.name not in ("provenance.json", "cache.json")
                },
                "scope": "Catalogue summaries and published plots; no raw instrument reduction",
            },
        )
        print(
            json.dumps(
                {
                    "report": str(out / "report.html"),
                    "rows": len(rows),
                    "retrieved_utc": cache["retrieved_utc"],
                }
            )
        )
        return 0
    except Exception as exc:
        print(f"coseecat: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
