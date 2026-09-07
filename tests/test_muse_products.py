"""Offline contracts for the release-specific MUSE helper, without science extras."""

import base64
import csv
import importlib.util
import json
import statistics
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "skills/muse-science-products/scripts/muse_products.py"
)
spec = importlib.util.spec_from_file_location("muse_products_test", SCRIPT)
muse = importlib.util.module_from_spec(spec)
previous = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    spec.loader.exec_module(muse)
finally:
    sys.dont_write_bytecode = previous


def no_network(*args, **kwargs):
    pytest.fail("This operation must not contact the network")


def sample_url():
    return muse.BASE + next(iter(muse.PRODUCTS.values()))["path"]


def stub_requests(monkeypatch, *, status=200, headers=None, chunks=(b"ab", b"cd")):
    calls, states = [], []

    class Response:
        status_code = status

        def __init__(self):
            self.headers = {} if headers is None else headers
            self.closed = False
            self.consumed = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def iter_content(self, size):
            assert size == 65536
            for chunk in chunks:
                self.consumed += 1
                yield chunk

    response = Response()

    class Session:
        def __init__(self):
            self.trust_env = True
            self.closed = False
            states.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def get(self, url, **kwargs):
            calls.append((url, kwargs, self.trust_env))
            return response

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(Session=Session))
    return response, calls, states


@pytest.mark.parametrize("command", [["--help"], ["orion", "--help"], ["list"]])
def test_cli_discovery_works_without_site_packages(command):
    result = subprocess.run(
        [sys.executable, "-B", "-S", str(SCRIPT), *command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if command == ["list"]:
        products = json.loads(result.stdout)
        assert {record["mode"] for record in products.values()} == {"orion", "antennae"}
        assert {record["kind"] for record in products.values()} == {"image", "table"}
        for name, record in products.items():
            assert Path(record["path"]).name == name
            assert 0 < record["bytes"] <= 32 * 1024 * 1024
            assert len(bytes.fromhex(record["sha256"])) == 32
            muse.permitted_url(record["url"])
    else:
        assert "usage:" in result.stdout


@pytest.mark.parametrize(
    "change",
    [
        lambda url: url.replace("https:", "http:"),
        lambda url: url.replace("s3.data.aip.de", "s3.data.aip.de.evil.invalid"),
        lambda url: url.replace("https://", "https://username:password@"),
        lambda url: url.replace(":9000", ":443"),
        lambda url: url + "?download=1",
        lambda url: url + "#fragment",
        lambda url: url.replace("orion/orion_m42/", "orion/../orion/orion_m42/"),
        lambda url: url.rsplit("/", 1)[0] + "/unknown.fits",
        lambda url: "file:///etc/passwd",
    ],
)
def test_unallowlisted_url_variants_never_create_a_session(monkeypatch, change):
    _, calls, states = stub_requests(monkeypatch)
    with pytest.raises(muse.ProductError):
        muse.download(change(sample_url()), 4)
    assert calls == states == []


def test_download_is_exact_bounded_get_without_ambient_credentials(monkeypatch):
    response, calls, states = stub_requests(monkeypatch, headers={"Content-Length": "4"})
    assert muse.download(sample_url(), 4) == b"abcd"
    assert calls == [
        (sample_url(), {"timeout": (10, 45), "stream": True, "allow_redirects": False}, False)
    ]
    assert response.closed and states[0].closed


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 404, 500])
def test_non_200_response_never_reads_body_or_follows_redirect(monkeypatch, status):
    response, calls, states = stub_requests(
        monkeypatch, status=status, headers={"Location": "https://example.invalid/private"}
    )
    with pytest.raises(muse.ProductError):
        muse.download(sample_url(), 4)
    assert len(calls) == 1 and response.consumed == 0
    assert response.closed and states[0].closed


@pytest.mark.parametrize("size", [0, -1, 32 * 1024 * 1024 + 1])
def test_invalid_size_contract_prevents_network(monkeypatch, size):
    _, calls, states = stub_requests(monkeypatch)
    with pytest.raises(muse.ProductError):
        muse.download(sample_url(), size)
    assert calls == states == []


@pytest.mark.parametrize("declared", ["0", "3", "5", "garbage", "-4"])
def test_changed_or_invalid_content_length_refused_before_body(monkeypatch, declared):
    response, _, states = stub_requests(monkeypatch, headers={"Content-Length": declared})
    with pytest.raises(ValueError):
        muse.download(sample_url(), 4)
    assert response.consumed == 0
    assert response.closed and states[0].closed


@pytest.mark.parametrize("chunks", [(), (b"abc",), (b"abc", b"de", b"never read")])
def test_stream_length_is_checked_without_header(monkeypatch, chunks):
    response, _, states = stub_requests(monkeypatch, chunks=chunks)
    with pytest.raises(muse.ProductError):
        muse.download(sample_url(), 4)
    assert response.consumed <= 2
    assert response.closed and states[0].closed


@pytest.mark.parametrize("fault", ["size", "digest", "unknown_name"])
def test_source_integrity_rejected_before_fits_parser(monkeypatch, fault):
    raw = b"expected exact source bytes"
    name = "tiny.fits"
    monkeypatch.setattr(muse, "PRODUCTS", {name: {"bytes": len(raw), "sha256": muse.sha256(raw)}})

    def unexpected_parse(*args, **kwargs):
        pytest.fail("Unverified source must not reach the FITS parser")

    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules, "astropy.io", SimpleNamespace(fits=SimpleNamespace(open=unexpected_parse))
    )
    monkeypatch.setitem(sys.modules, "astropy.wcs", SimpleNamespace(WCS=unexpected_parse))
    if fault == "size":
        raw += b"extra"
    elif fault == "digest":
        raw = b"X" + raw[1:]
    else:
        name = "unknown.fits"
    with pytest.raises(muse.ProductError):
        muse.validate_product(name, raw)


@pytest.mark.parametrize("kind", ["file", "unrelated", "directory", "symlink"])
def test_output_guard_preserves_existing_user_content(tmp_path, kind):
    root = tmp_path.resolve()
    out = root / "output"
    source = root / "valuable.txt"
    source.write_bytes(b"keep exactly")
    if kind == "file":
        out.write_bytes(b"keep file")
    else:
        out.mkdir()
        if kind == "unrelated":
            (out / "notes.txt").write_bytes(b"keep note")
        elif kind == "directory":
            (out / "subfolder").mkdir()
        else:
            (out / "summary.json").symlink_to(source)
    with pytest.raises(muse.ProductError):
        muse.output_path(out, "orion")
    assert source.read_bytes() == b"keep exactly"
    if kind == "file":
        assert out.read_bytes() == b"keep file"
    elif kind == "unrelated":
        assert (out / "notes.txt").read_bytes() == b"keep note"


def test_output_guard_rejects_traversal_skill_tree_and_symlink_ancestor(tmp_path):
    root = tmp_path.resolve()
    alias = root / "alias"
    alias.symlink_to(root, target_is_directory=True)
    for value in [root / "a" / ".." / "result", SCRIPT.parent / "result", alias / "result"]:
        with pytest.raises(muse.ProductError):
            muse.output_path(value, "orion")
    assert not (root / "result").exists()


@pytest.fixture
def tiny_sources(monkeypatch):
    """Stub FITS/rendering only; exercise actual download/cache/bundle orchestration."""
    payloads = {"first.fits": b"first original payload", "second.fits": b"second payload"}
    products = {
        name: {
            "mode": "orion",
            "path": "test/" + name,
            "kind": "image",
            "bytes": len(raw),
            "sha256": muse.sha256(raw),
        }
        for name, raw in payloads.items()
    }
    monkeypatch.setattr(muse, "PRODUCTS", products)
    monkeypatch.setattr(muse, "environment", lambda: {"test_environment": 1})

    def validate(name, raw):
        if raw != payloads[name]:
            raise muse.ProductError("Unexpected fixture source bytes")
        return {"data": raw}

    def download(url, size):
        raw = payloads[url.rsplit("/", 1)[1]]
        assert size == len(raw)
        return raw

    def render(out, *args):
        (out / "maps.png").write_bytes(b"test PNG payload")

    monkeypatch.setattr(muse, "validate_product", validate)
    monkeypatch.setattr(muse, "download", download)
    monkeypatch.setattr(muse, "map_summary", lambda name, product: ({"source": name}, None))
    monkeypatch.setattr(muse, "render", render)
    return payloads


def snapshot(directory):
    return {path.name: path.read_bytes() for path in directory.iterdir()}


def test_fresh_bundle_preserves_original_sources_and_records_all_artifacts(tmp_path, tiny_sources):
    out = tmp_path.resolve() / "bundle"
    muse.run("orion", out)
    assert set(snapshot(out)) == muse.allowed_files("orion")
    manifest = json.loads((out / "source-manifest.json").read_text())
    provenance = json.loads((out / "provenance.json").read_text())
    for name, raw in tiny_sources.items():
        assert (out / name).read_bytes() == raw
        assert manifest["sources"][name]["sha256"] == muse.sha256(raw)
        assert manifest["sources"][name]["bytes"] == len(raw)
    assert set(provenance["artifacts"]) == set(snapshot(out)) - {"provenance.json"}
    for name, record in provenance["artifacts"].items():
        assert record == {"bytes": (out / name).stat().st_size, "sha256": muse.digest(out / name)}
    assert muse.verify_cache(out, "orion", muse.environment()) == manifest


@pytest.mark.parametrize("offline", [False, True])
def test_complete_replay_preserves_bundle_without_network(
    tmp_path, monkeypatch, tiny_sources, offline
):
    out = tmp_path.resolve() / "bundle"
    muse.run("orion", out)
    before = snapshot(out)
    monkeypatch.setattr(muse, "download", no_network)
    muse.run("orion", out, offline=offline)
    assert snapshot(out) == before


@pytest.mark.parametrize("preexisting", [False, True])
def test_offline_missing_bundle_never_networks_or_creates_files(
    tmp_path, monkeypatch, tiny_sources, preexisting
):
    out = tmp_path.resolve() / "bundle"
    if preexisting:
        out.mkdir()
    monkeypatch.setattr(muse, "download", no_network)
    with pytest.raises(muse.ProductError):
        muse.run("orion", out, offline=True)
    assert not out.exists() or list(out.iterdir()) == []


@pytest.mark.parametrize("preexisting", [False, True])
def test_all_sources_validate_before_any_output_write(
    tmp_path, monkeypatch, tiny_sources, preexisting
):
    out = tmp_path.resolve() / "bundle"
    if preexisting:
        out.mkdir()
    original = muse.validate_product
    seen = []

    def reject_last_source(name, raw):
        seen.append(name)
        if name == "second.fits":
            raise muse.ProductError("Second source changed")
        return original(name, raw)

    monkeypatch.setattr(muse, "validate_product", reject_last_source)
    with pytest.raises(muse.ProductError):
        muse.run("orion", out)
    assert seen == list(tiny_sources)
    assert not out.exists() or list(out.iterdir()) == []


@pytest.mark.parametrize(
    "artifact", ["first.fits", "source-manifest.json", "summary.json", "maps.png", "report.html"]
)
@pytest.mark.parametrize("offline", [False, True])
def test_corrupt_or_missing_artifact_is_never_refetched_or_overwritten(
    tmp_path, monkeypatch, tiny_sources, artifact, offline
):
    out = tmp_path.resolve() / "bundle"
    muse.run("orion", out)
    # Preserve length to require the content digest, rather than only a size check.
    raw = (out / artifact).read_bytes()
    (out / artifact).write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    before = snapshot(out)
    monkeypatch.setattr(muse, "download", no_network)
    with pytest.raises(ValueError):
        muse.run("orion", out, offline=offline)
    assert snapshot(out) == before
    (out / artifact).unlink()
    before = snapshot(out)
    with pytest.raises(muse.ProductError):
        muse.run("orion", out, offline=offline)
    assert snapshot(out) == before


@pytest.mark.parametrize("field", ["url", "bytes", "sha256", "retrieved_utc"])
def test_rehashed_manifest_still_requires_source_identity_and_utc(
    tmp_path, monkeypatch, tiny_sources, field
):
    out = tmp_path.resolve() / "bundle"
    muse.run("orion", out)
    manifest = json.loads((out / "source-manifest.json").read_text())
    changes = {
        "url": "https://example.invalid/first.fits",
        "bytes": 999,
        "sha256": "0" * 64,
        "retrieved_utc": "2026-09-06T12:00:00+02:00",
    }
    manifest["sources"]["first.fits"][field] = changes[field]
    (out / "source-manifest.json").write_bytes(muse.json_bytes(manifest))
    provenance = json.loads((out / "provenance.json").read_text())
    provenance["source_manifest"] = manifest
    provenance["artifacts"].update(muse.artifact_records(out, {"source-manifest.json"}))
    (out / "provenance.json").write_bytes(muse.json_bytes(provenance))
    before = snapshot(out)
    monkeypatch.setattr(muse, "download", no_network)
    with pytest.raises(muse.ProductError):
        muse.run("orion", out, offline=True)
    assert snapshot(out) == before


def test_changed_environment_refuses_replay_without_mutation(tmp_path, monkeypatch, tiny_sources):
    out = tmp_path.resolve() / "bundle"
    muse.run("orion", out)
    before = snapshot(out)
    monkeypatch.setattr(muse, "download", no_network)
    monkeypatch.setattr(muse, "environment", lambda: {"test_environment": 2})
    with pytest.raises(muse.ProductError):
        muse.run("orion", out, offline=True)
    assert snapshot(out) == before


@pytest.mark.parametrize("metadata", ["source-manifest.json", "provenance.json"])
def test_oversized_cache_metadata_refused_without_decoding_or_mutation(
    tmp_path, monkeypatch, tiny_sources, metadata
):
    out = tmp_path.resolve() / "bundle"
    muse.run("orion", out)
    (out / metadata).write_bytes(b" " * (1024 * 1024 + 1))
    before = snapshot(out)
    monkeypatch.setattr(muse, "download", no_network)
    with pytest.raises(muse.ProductError):
        muse.run("orion", out, offline=True)
    assert snapshot(out) == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_json_never_serializes_nonfinite_numbers(value):
    with pytest.raises(ValueError):
        muse.json_bytes({"science": {"value": value}})


def test_html_embeds_local_image_and_escapes_summary_and_provenance(tmp_path):
    out = tmp_path.resolve()
    png = b"local image bytes"
    (out / "maps.png").write_bytes(png)
    malicious = '<script>alert("not executable")</script><img src="https://evil.invalid"> &'
    muse.report(out, "orion", {"label": malicious}, {"source": malicious})
    doc = (out / "report.html").read_text()
    assert malicious not in doc
    assert "<script>" not in doc and '<img src="https://evil.invalid">' not in doc
    assert "&lt;script&gt;" in doc and "&amp;" in doc
    assert "data:image/png;base64," + base64.b64encode(png).decode() in doc


@pytest.fixture
def scalar_numpy(monkeypatch):
    """Only scalar coercion/median are dependencies of the catalogue selection."""
    masked = object()

    class Scalar:
        def __init__(self, value):
            self.value = value

        def item(self):
            return self.value

    numpy = SimpleNamespace(
        ma=SimpleNamespace(is_masked=lambda value: value is masked),
        generic=Scalar,
        median=statistics.median,
    )
    monkeypatch.setitem(sys.modules, "numpy", numpy)
    return masked, Scalar


class Rows(list):
    @property
    def names(self):
        return list(self[0])


def region(**changes):
    return {
        "ID": 1,
        "HALPHA": 30.0,
        "E_HALPHA": 10.0,
        "HBETA": 9.0,
        "E_HBETA": 3.0,
        "LHa_cor": 1e38,
        "EXTRA_COLUMN": b"  retained  ",
        **changes,
    }


def table_products(center, south=None):
    result = {"Antennae_Center_measurements.fits": {"table": Rows(center)}}
    if south is not None:
        result["Antennae_South_measurements.fits"] = {"table": Rows(south)}
    return result


def test_region_selection_keeps_rows_columns_threshold_and_field_separation(scalar_numpy, tmp_path):
    masked, scalar = scalar_numpy
    rows = [
        region(ID=scalar(10), LHa_cor=1e38),
        region(ID=20, LHa_cor=3e38),
        region(ID=30, HALPHA=29.999, LHa_cor=8e38),
        region(ID=40, E_HBETA=0, LHa_cor=9e38),
        region(ID=50, LHa_cor=float("nan")),
        region(ID=60, LHa_cor=-1, HBETA=masked),
    ]
    original = [row.copy() for row in rows]
    records, summary, samples = muse.region_records(
        table_products(rows, [region(ID=10, LHa_cor=9e37)])
    )
    assert rows == original
    assert len(records) == 7
    assert [record["source_row_1based"] for record in records] == [1, 2, 3, 4, 5, 6, 1]
    assert [record["included_ecdf"] for record in records] == [
        True,
        True,
        False,
        False,
        False,
        False,
        True,
    ]
    assert records[0]["ID"] == 10 and records[0]["EXTRA_COLUMN"] == "retained"
    assert records[4]["LHa_cor"] is None and records[5]["HBETA"] is None
    assert samples == {"Center": [1e38, 3e38], "South": [9e37]}
    assert summary["Center"]["rows_total"] == 6
    assert summary["Center"]["rows_included"] == 2
    assert summary["Center"]["rows_excluded"] == 4
    assert summary["Center"]["median_LHa_cor_erg_s"] == 2e38
    assert summary["Center"]["exclusion_counts"] == {
        "HALPHA_snr_below_3": 1,
        "HBETA_flux_or_error_not_finite_positive": 2,
        "LHa_cor_not_finite_positive": 2,
    }
    csv_path = tmp_path / "regions.csv"
    muse.write_csv(csv_path, records)
    with csv_path.open(newline="") as stream:
        exported = list(csv.DictReader(stream))
    assert len(exported) == 7 and exported[4]["LHa_cor"] == ""
    assert set(exported[0]) == set(records[0])


@pytest.mark.parametrize(
    "changes",
    [
        {"LHa_cor": 0},
        {"LHa_cor": float("inf")},
        {"HALPHA": float("nan")},
        {"E_HALPHA": -1},
        {"HBETA": 8.999},
        {"E_HBETA": float("inf")},
    ],
)
def test_empty_usable_region_selection_fails_without_inventing_luminosities(scalar_numpy, changes):
    with pytest.raises(muse.ProductError):
        muse.region_records(table_products([region(**changes)]))
