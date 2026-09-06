import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/coseecat/scripts/coseecat_demo.py"
spec = importlib.util.spec_from_file_location("coseecat_demo_test", SCRIPT)
demo = importlib.util.module_from_spec(spec)
previous = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    spec.loader.exec_module(demo)
finally:
    sys.dont_write_bytecode = previous

EVENT = "2011171841"


def sample(**kwargs):
    return {
        "event_id": EVENT,
        "epd_tonset": "2020-11-17 18:41:30",
        "epd_tpeak": "2020-11-17 18:48:30",
        "epd_compo": "impulsive",
        **kwargs,
    }


def link(**kwargs):
    return {
        "ID": EVENT,
        "description": "Overview",
        "access_url": demo.BASE + f"/files/dr1/plots/{EVENT}_overview.png",
        "semantics": "#preview-plot",
        "content_type": "application/png",
        "error_message": "",
        **kwargs,
    }


@pytest.mark.parametrize(
    "value", ["", "1", "20111718410", "2011171841 OR 1=1", "../x", "１２３４５６７８９０"]
)
def test_ids_reject_non_decimal_query_input(value):
    with pytest.raises(demo.DemoError):
        demo.event_id(value)


def test_queries_are_scoped_and_bounded():
    assert demo.event_id(2011171841) == EVENT
    assert demo.query_for("event", EVENT).endswith("WHERE event_id=2011171841")
    assert "TOP 1001" in demo.query_for("rise-times", None)
    assert "epd_compo" in demo.query_for("rise-times", None)
    parameters = demo.tap_parameters(demo.query_for("rise-times", None))
    assert "MAXREC" not in parameters
    assert parameters["LANG"] == "ADQL" and "TOP 1001" in parameters["QUERY"]


@pytest.mark.parametrize(
    "url",
    [
        "http://coseecat.aip.de/tap/sync",
        "https://coseecat.aip.de.evil.invalid/tap/sync",
        "https://user:password@coseecat.aip.de/tap/sync",
        "https://coseecat.aip.de:444/tap/sync",
        "https://coseecat.aip.de/tap/sync?redirect=1",
        "https://coseecat.aip.de/tap/sync#x",
        "https://coseecat.aip.de/accounts/login/",
        "https://coseecat.aip.de/files/dr1/plots/2011181438_overview.png",
        "https://coseecat.aip.de/files/dr1/plots/2011171841_../secret.png",
        "https://coseecat.aip.de/files/dr1/plots/2011171841_a%2fb.png",
        "file:///etc/passwd",
    ],
)
def test_network_allowlist_rejects_other_destinations(url):
    with pytest.raises(demo.DemoError):
        demo.permitted_url(url, EVENT)


@pytest.mark.parametrize(
    "url", [demo.TAP + "/sync", demo.BASE + "/datalink/links", link()["access_url"]]
)
def test_network_allowlist_accepts_observed_paths(url):
    demo.permitted_url(url, EVENT)


def fake_session(monkeypatch, status=200, headers=None, chunks=(b"ok",)):
    response = SimpleNamespace(status_code=status, headers=headers or {}, closed=False)
    response.iter_content = lambda size: iter(chunks)
    response.close = lambda: setattr(response, "closed", True)
    calls = []

    class Session:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return response

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=Session))
    return demo.bounded_session(EVENT), response, calls


def test_response_is_bounded_and_buffered_for_pyvo(monkeypatch):
    session, response, calls = fake_session(monkeypatch, chunks=(b"ab", b"cd"))
    result = session.request("GET", demo.TAP + "/sync")
    assert not session.trust_env
    assert calls[0][2] == {"timeout": (10, 45), "allow_redirects": False, "stream": True}
    assert result.raw.read() == b"abcd"
    result.raw.seek(0)
    assert result.raw.read(decode_content=True) == b"abcd"
    assert result._content == b"abcd"
    assert response.closed
    assert session.payloads == [(demo.TAP + "/sync", b"abcd")]


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 404, 500])
def test_http_failures_do_not_follow_redirects(monkeypatch, status):
    session, response, calls = fake_session(monkeypatch, status=status)
    with pytest.raises(demo.DemoError, match="HTTP"):
        session.request("GET", demo.TAP + "/sync")
    assert len(calls) == 1 and response.closed


@pytest.mark.parametrize("headers,chunks", [({"Content-Length": "5"}, ()), ({}, (b"abc", b"de"))])
def test_declared_and_streamed_size_limits(monkeypatch, headers, chunks):
    monkeypatch.setattr(demo, "LIMIT", 4)
    session, response, _ = fake_session(monkeypatch, headers=headers, chunks=chunks)
    with pytest.raises(demo.DemoError, match="limit"):
        session.request("GET", demo.TAP + "/sync")
    assert response.closed


def test_unallowed_methods_and_urls_never_reach_network(monkeypatch):
    session, _, calls = fake_session(monkeypatch)
    for method, url in [("DELETE", demo.TAP + "/sync"), ("GET", "https://example.org")]:
        with pytest.raises(demo.DemoError):
            session.request(method, url)
    assert calls == []


def test_datalink_semantics_description_and_mime_are_preserved():
    assert demo.plot_links([link()], EVENT) == {"Overview": link()["access_url"]}
    full = "http://www.ivoa.net/rdf/datalink/core#preview-plot"
    assert demo.plot_links([link(semantics=full, content_type="image/png")], EVENT)
    assert demo.plot_links([link(semantics="#auxiliary")], EVENT) == {}
    assert demo.plot_links([link(description="Future product")], EVENT) == {}


@pytest.mark.parametrize(
    "change",
    [
        {"ID": "2011181438"},
        {"content_type": "text/html"},
        {"error_message": "not available"},
        {"access_url": demo.TAP + "/sync"},
        {"access_url": "https://example.org/a.png"},
    ],
)
def test_invalid_plot_links_fail_closed(change):
    with pytest.raises(demo.DemoError):
        demo.plot_links([link(**change)], EVENT)


def test_duplicate_plot_descriptions_refused():
    with pytest.raises(demo.DemoError, match="Duplicate"):
        demo.plot_links([link(), link()], EVENT)


@pytest.mark.parametrize("value", [None, "", "  ", "--"])
def test_missing_times_remain_missing(value):
    assert demo.timestamp(value) is None


def test_utc_timestamps_handle_offsets_and_cross_midnight():
    assert demo.timestamp("2020-11-18T00:30:00+01:00") == datetime(2020, 11, 17, 23, 30, tzinfo=UTC)
    rows, summary, bins = demo.rise_times(
        [sample(epd_tonset="2020-11-17 23:58:00", epd_tpeak="2020-11-18 00:05:00")]
    )
    assert rows[0]["rise_time_min"] == 7
    assert bins["impulsive"] == [7]
    assert summary["included"] == 1


@pytest.mark.parametrize("value", ["2020-11-18", "tomorrow", "2020-02-31 10:00:00"])
def test_incomplete_invalid_times_are_not_silently_normalized(value):
    with pytest.raises(ValueError):
        demo.timestamp(value)


def test_rise_time_exclusions_zero_tails_and_categories():
    rows = [
        sample(),
        sample(epd_tpeak="2020-11-17 18:41:30"),
        sample(epd_tpeak="2020-11-18 14:41:30", epd_compo="gradual"),
        sample(epd_tpeak="2020-11-17 18:30:00"),
        sample(epd_tpeak=""),
        sample(epd_tpeak="invalid"),
        sample(epd_compo=""),
    ]
    derived, summary, bins = demo.rise_times(rows)
    assert len(derived) == len(rows)
    assert summary["included"] == 4 and summary["excluded"] == 3
    assert bins == {"impulsive": [7, 0], "gradual": [1200], "intermediate": [], "unknown": [7]}
    assert summary["groups"]["impulsive"]["median_min"] == 3.5
    assert [r["exclusion_reason"] for r in derived[3:6]] == [
        "negative rise time",
        "missing timestamp",
        "invalid timestamp",
    ]


def test_unknown_future_category_requires_review():
    with pytest.raises(demo.DemoError, match="Unknown"):
        demo.rise_times([sample(epd_compo="other")])


def test_timeline_preserves_shifted_estimates_and_missing_onset():
    row = {**sample(), **dict.fromkeys(demo.TIMES, "")}
    row.update(epd_tonset="2020-11-17 18:41:30", vda_itime="2020-11-17 18:25:24", vda_time_unc=0.9)
    points = {p["column"]: p for p in demo.timeline(row)}
    assert points["vda_itime"]["minutes_from_epd_onset"] == -16.1
    assert points["vda_itime"]["utc"] == "2020-11-17T18:25:24+00:00"
    assert points["vda_itime"]["standard_uncertainty_min"] == 0.9
    assert points["rpw_t3_time"]["utc"] is None
    assert points["rpw_t3_time"]["minutes_from_epd_onset"] is None


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_vda_uncertainty_refused(value):
    row = {**dict.fromkeys(demo.TIMES, ""), **sample(), "vda_time_unc": value}
    with pytest.raises(demo.DemoError, match="uncertainty"):
        demo.timeline(row)


def test_units_checked_without_guessing():
    table = SimpleNamespace(colnames=["epd_tonset", "epd_tpeak"])

    class Table:
        colnames = table.colnames

        def __getitem__(self, key):
            return SimpleNamespace(unit="UTC" if key == "epd_tonset" else "s")

    with pytest.raises(demo.DemoError, match="epd_tpeak"):
        demo.validate_units(SimpleNamespace(to_table=Table), "rise-times")


def test_rows_require_unique_id_columns_and_nontruncation():
    demo.validate_rows([sample()], "rise-times", None)
    for rows in ([], [sample(), sample()], [{}], [sample()] * demo.ROW_LIMIT):
        with pytest.raises(demo.DemoError):
            demo.validate_rows(rows, "rise-times", None)


@pytest.mark.parametrize("kind", ["unrelated", "directory", "symlink", "dangling"])
def test_output_guard_preserves_user_data(tmp_path, kind):
    target = tmp_path / "timeline.png"
    if kind == "unrelated":
        target = tmp_path / "user-notes.txt"
        target.write_text("keep")
    elif kind == "directory":
        target.mkdir()
    else:
        target.symlink_to(tmp_path / "missing")
    with pytest.raises(demo.DemoError):
        demo.output_path(tmp_path)
    assert target.exists() or target.is_symlink()


def test_output_inside_installed_skill_refused():
    with pytest.raises(demo.DemoError, match="outside"):
        demo.output_path(SCRIPT.parent / "outputs")


def make_cache(tmp_path):
    query = demo.query_for("rise-times", None)
    (tmp_path / "catalog.xml").write_bytes(b"test VOTable fixture")
    (tmp_path / "query.adql").write_text(query + "\n")
    cache = {
        "format": 1,
        "mode": "rise-times",
        "event_id": None,
        "query": query,
        "retrieved_utc": "2026-09-06T00:00:00+00:00",
        "sources": {
            n: {"sha256": demo.digest(tmp_path / n), "url": None}
            for n in ("catalog.xml", "query.adql")
        },
    }
    demo.write_json(tmp_path / "cache.json", cache)
    return cache


def test_cache_replay_does_not_contact_network_or_change_retrieval_time(tmp_path, monkeypatch):
    cache = make_cache(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(demo, "bounded_session", forbidden)
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()}
    assert demo.load_or_fetch(tmp_path, "rise-times", None, True) == cache
    assert demo.load_or_fetch(tmp_path, "rise-times", None, False) == cache
    assert {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir()} == before


def test_cache_corruption_refused_without_overwrite(tmp_path):
    make_cache(tmp_path)
    raw = tmp_path / "catalog.xml"
    raw.write_bytes(b"corrupt")
    with pytest.raises(demo.DemoError, match="hash mismatch"):
        demo.load_or_fetch(tmp_path, "rise-times", None, True)
    assert raw.read_bytes() == b"corrupt"


def test_cache_selection_and_path_traversal_refused(tmp_path):
    cache = make_cache(tmp_path)
    with pytest.raises(demo.DemoError, match="selection"):
        demo.load_or_fetch(tmp_path, "event", EVENT, True)
    cache["sources"]["../outside"] = {"sha256": "a"}
    demo.write_json(tmp_path / "cache.json", cache)
    with pytest.raises(demo.DemoError, match="Unsafe"):
        demo.load_or_fetch(tmp_path, "rise-times", None, True)


def test_offline_without_cache_and_incomplete_outputs_refused(tmp_path):
    with pytest.raises(demo.DemoError, match="Offline"):
        demo.load_or_fetch(tmp_path, "rise-times", None, True)
    (tmp_path / "catalog.xml").write_text("unfinished")
    with pytest.raises(demo.DemoError, match="Incomplete"):
        demo.load_or_fetch(tmp_path, "rise-times", None, False)


def test_html_escapes_remote_text_and_never_embeds_external_movies(tmp_path):
    demo.report(
        tmp_path,
        "<script>alert(1)</script>",
        {"x": "<img src=x>"},
        [],
        [
            {
                "access_url": "javascript:alert(1)",
                "description": "unsafe",
                "content_type": "video/mp4",
            },
            {
                "access_url": "https://example.org/a.mp4",
                "description": "<script>x</script>",
                "content_type": "video/mp4",
            },
        ],
    )
    text = (tmp_path / "report.html").read_text()
    assert "<script>" not in text and "javascript:" not in text
    assert "&lt;script&gt;" in text and "external; not cached" in text
    assert "<video" not in text and "<iframe" not in text


def test_help_needs_no_science_dependencies():
    result = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0 and "rise-times" in result.stdout


def test_json_disallows_nonfinite_values(tmp_path):
    with pytest.raises(ValueError):
        demo.write_json(tmp_path / "summary.json", {"x": float("nan")})
    demo.write_json(tmp_path / "summary.json", {"x": None})
    assert json.loads((tmp_path / "summary.json").read_text()) == {"x": None}
