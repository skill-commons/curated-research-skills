import importlib.util
import io
import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "pepsi-spectra"
    / "scripts"
    / "pepsi_spectrum_demo.py"
)
INDEX_URL = "https://pepsi.aip.de/library/paperII/dwarfs.csv"
SPECTRUM_URL = "https://pepsi.aip.de/library/paperII/cont_v2/pepsib.20150325.001.sxt.awl.all6"
HEADER = "name,simbad,type,snr,basename\n"
CATALOG = HEADER + "18 Sco,HD 146233,G2 V,700,pepsib.20150325.001.sxt\n"


def _load_script():
    spec = importlib.util.spec_from_file_location("crs_pepsi_spectrum_demo", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


@pytest.mark.parametrize(
    "url",
    [
        INDEX_URL,
        "https://pepsi.aip.de/library/paperII/giants.csv",
        SPECTRUM_URL,
        SPECTRUM_URL.replace(".sxt.", ".dxt."),
    ],
)
def test_source_urls_are_exactly_preserved(url) -> None:
    assert _load_script().validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        INDEX_URL.replace("https:", "http:"),
        INDEX_URL.replace("https:", "ftp:"),
        INDEX_URL.replace("pepsi.aip.de", "example.org"),
        INDEX_URL.replace("pepsi.aip.de", "pepsi.aip.de.example.org"),
        INDEX_URL.replace("pepsi.aip.de", "user@pepsi.aip.de"),
        INDEX_URL.replace("pepsi.aip.de", "user:password@pepsi.aip.de"),
        INDEX_URL.replace("pepsi.aip.de", "pepsi.aip.de:443"),
        INDEX_URL.replace("pepsi.aip.de", "pepsi.aip.de:8080"),
        INDEX_URL + "?download=1",
        INDEX_URL + "?",
        INDEX_URL + "#data",
        INDEX_URL + "#",
        " " + INDEX_URL,
        INDEX_URL + " ",
        INDEX_URL.replace("pepsi", "pep\nsi"),
        INDEX_URL.replace("paperII/", "paperII/../paperII/"),
        INDEX_URL.replace("paperII/", "paperII/%2e%2e/paperII/"),
        INDEX_URL.replace("dwarfs", "%64warfs"),
        INDEX_URL.replace("dwarfs.csv", "other.csv"),
        INDEX_URL.replace("paperII", "paperI"),
        SPECTRUM_URL.replace("cont_v2", "cont_v1"),
        SPECTRUM_URL.replace(".sxt.", ".unknown."),
        SPECTRUM_URL.replace("20150325", "2015032"),
        SPECTRUM_URL.replace(".001.", ".01."),
        SPECTRUM_URL + ".fits",
        SPECTRUM_URL + "/",
        "/library/paperII/dwarfs.csv",
    ],
)
def test_source_urls_reject_untrusted_or_unexpected_variants(url) -> None:
    module = _load_script()
    with pytest.raises(module.SpectrumError):
        module.validate_url(url)


def test_catalog_preserves_metadata_and_binds_download_url() -> None:
    module = _load_script()
    rows = module.parse_catalog(CATALOG, INDEX_URL)
    assert rows == [
        {
            "name": "18 Sco",
            "simbad": "HD 146233",
            "type": "G2 V",
            "snr": "700",
            "basename": "pepsib.20150325.001.sxt",
            "index_url": INDEX_URL,
        }
    ]
    assert module.data_url(rows[0]) == SPECTRUM_URL


def test_duplicate_catalog_header_cannot_silently_replace_a_value() -> None:
    module = _load_script()
    duplicated = (
        "name,simbad,type,snr,basename,snr\n18 Sco,HD 146233,G2 V,700,pepsib.20150325.001.sxt,999\n"
    )
    with pytest.raises(module.SpectrumError, match="catalog columns"):
        module.parse_catalog(duplicated, INDEX_URL)


def test_download_url_builder_revalidates_untrusted_row_input() -> None:
    module = _load_script()
    with pytest.raises(module.SpectrumError):
        module.data_url({"basename": "../../elsewhere/spectrum"})


@pytest.mark.parametrize("missing", ["name", "simbad", "type", "snr", "basename"])
def test_catalog_requires_documented_columns(missing) -> None:
    module = _load_script()
    columns = HEADER.strip().split(",")
    values = CATALOG.splitlines()[1].split(",")
    omitted = columns.index(missing)
    text = ",".join(columns[:omitted] + columns[omitted + 1 :]) + "\n"
    text += ",".join(values[:omitted] + values[omitted + 1 :]) + "\n"
    with pytest.raises(module.SpectrumError):
        module.parse_catalog(text, INDEX_URL)


@pytest.mark.parametrize("snr", ["0", "-1", "nan", "inf", "-inf", "not-a-number", ""])
def test_catalog_rejects_nonpositive_or_nonfinite_snr(snr) -> None:
    module = _load_script()
    with pytest.raises(module.SpectrumError):
        module.parse_catalog(CATALOG.replace(",700,", f",{snr},"), INDEX_URL)


@pytest.mark.parametrize(
    "basename",
    [
        "../pepsib.20150325.001.sxt",
        "pepsib.20150325.001.sxt.awl.all6",
        "pepsib.20150325.001.sxt?download=1",
        "pepsib.20150325.001.xyz",
        "pepsib.2015032.001.sxt",
        "pepsib.20150325.01.sxt",
        "https://example.org/spectrum",
    ],
)
def test_catalog_rejects_unexpected_basenames(basename) -> None:
    module = _load_script()
    with pytest.raises(module.SpectrumError):
        module.parse_catalog(CATALOG.replace("pepsib.20150325.001.sxt", basename), INDEX_URL)


@pytest.mark.parametrize(
    "target", ["18 Sco", "  18   sCo ", "hd 146233", "PEPSIB.20150325.001.SXT"]
)
def test_target_selection_uses_exact_normalized_name_identifier_or_basename(target) -> None:
    module = _load_script()
    rows = module.parse_catalog(CATALOG, INDEX_URL)
    assert module.select_target(rows, target) == rows[0]


@pytest.mark.parametrize("target", ["18", "Sco", "HD 14623", "no such star", ""])
def test_target_selection_does_not_guess_partial_or_missing_matches(target) -> None:
    module = _load_script()
    with pytest.raises(module.SpectrumError):
        module.select_target(module.parse_catalog(CATALOG, INDEX_URL), target)


def test_target_selection_rejects_ambiguous_matches() -> None:
    module = _load_script()
    rows = module.parse_catalog(CATALOG, INDEX_URL)
    duplicate = dict(rows[0], basename="pepsib.20150326.001.sxt")
    with pytest.raises(module.SpectrumError):
        module.select_target([rows[0], duplicate], "18 Sco")


def test_variance_is_absolute_and_invalid_bins_keep_their_positions() -> None:
    module = _load_script()
    wavelength = [5000.0 + index for index in range(7)]
    flux = [0.25, -0.5, math.nan, math.inf, 1.0, 0.75, 0.9]
    variance = [0.04, 0.0, 0.01, 0.01, -0.01, math.inf, math.nan]
    mask = [1] * 7
    originals = tuple(list(values) for values in (wavelength, flux, variance, mask))
    wave, display_flux, sigma, valid = module.clean_samples(wavelength, flux, variance, mask)
    assert wave == wavelength
    assert valid == [True, True, False, False, False, False, False]
    assert len(display_flux) == len(sigma) == len(wavelength)
    assert display_flux[:2] == [0.25, -0.5]
    assert sigma[:2] == pytest.approx([0.2, 0.0])
    for index in range(2, len(wavelength)):
        assert math.isnan(display_flux[index])
        assert math.isnan(sigma[index])
    for actual, original in zip((wavelength, flux, variance, mask), originals, strict=True):
        assert actual == original


def test_observed_uniform_raw_mask_byte_one_is_supported() -> None:
    module = _load_script()
    _, display_flux, sigma, valid = module.clean_samples(
        [5000.0, 5000.1], [0.25, 0.75], [0.01, 0.04], [1, 1]
    )
    assert display_flux == [0.25, 0.75]
    assert sigma == pytest.approx([0.1, 0.2])
    assert valid == [True, True]


@pytest.mark.parametrize(
    "mask",
    [
        [0, 0],
        [1, 0],
        [2, 2],
        [-1, -1],
        [1, 0.5],
        [1, math.nan],
        [1, math.inf],
        [1.0, 1.0],
        [True, True],
        ["1", "1"],
    ],
)
def test_unknown_mask_states_fail_closed_instead_of_inventing_quality_semantics(mask) -> None:
    module = _load_script()
    with pytest.raises(module.SpectrumError, match="(?i)mask"):
        module.clean_samples([5000.0, 5000.1], [1.0, 0.8], [0.01, 0.02], mask)


@pytest.mark.parametrize(
    "wavelength",
    [
        [0.0, 1.0],
        [-1.0, 1.0],
        [5000.0, 5000.0],
        [5000.1, 5000.0],
        [5000.0, math.nan],
        [5000.0, math.inf],
    ],
)
def test_wavelengths_must_be_finite_positive_and_strictly_increasing(wavelength) -> None:
    module = _load_script()
    with pytest.raises(module.SpectrumError):
        module.clean_samples(wavelength, [1.0, 0.8], [0.01, 0.02], [1, 1])


@pytest.mark.parametrize("column", range(4))
def test_sample_columns_must_have_equal_lengths(column) -> None:
    module = _load_script()
    samples = [[5000.0, 5000.1], [1.0, 0.8], [0.01, 0.02], [1, 1]]
    samples[column] = samples[column][:1]
    with pytest.raises(module.SpectrumError):
        module.clean_samples(*samples)


@pytest.mark.parametrize("column", range(4))
def test_sample_columns_must_be_one_dimensional(column) -> None:
    module = _load_script()
    samples = [[5000.0, 5000.1], [1.0, 0.8], [0.01, 0.02], [1, 1]]
    samples[column] = [[samples[column][0]], [samples[column][1]]]
    with pytest.raises(module.SpectrumError):
        module.clean_samples(*samples)


@pytest.mark.parametrize(
    "samples",
    [
        ([], [], [], []),
        ([5000.0], [1.0], [0.01], [1]),
        ([5000.0, 5000.1], [math.nan, 0.8], [0.01, 0.02], [1, 1]),
        ([5000.0, 5000.1], [1.0, 0.8], [-0.01, -0.02], [1, 1]),
    ],
)
def test_spectrum_requires_at_least_two_numerically_valid_samples(samples) -> None:
    module = _load_script()
    with pytest.raises(module.SpectrumError):
        module.clean_samples(*samples)


def test_large_spectral_gaps_are_split_without_dropping_or_reordering_samples() -> None:
    module = _load_script()
    wave = [1.0, 2.0, 3.0, 30.0, 31.0, 32.0, 100.0, 101.0, 102.0]
    segments = module.segment_slices(wave)
    assert [wave[segment] for segment in segments] == [wave[:3], wave[3:6], wave[6:]]
    covered = [index for segment in segments for index in range(len(wave))[segment]]
    assert covered == list(range(len(wave)))


def test_gap_threshold_is_strict_and_configurable() -> None:
    module = _load_script()
    wave = [1.0, 2.0, 3.0, 13.0, 14.0, 15.0]
    assert [wave[segment] for segment in module.segment_slices(wave)] == [wave]
    assert [wave[segment] for segment in module.segment_slices(wave, gap_factor=5)] == [
        wave[:3],
        wave[3:],
    ]


def test_redirect_handler_never_follows_a_redirect() -> None:
    module = _load_script()
    handler = module.NoRedirect()
    try:
        redirected = handler.redirect_request(
            Request(INDEX_URL), None, 302, "Found", {}, "https://example.org/spectrum"
        )
    except (module.SpectrumError, HTTPError):
        return
    assert redirected is None


class _Response(io.BytesIO):
    def __init__(self, body, headers, url):
        super().__init__(body)
        self.headers = headers
        self.url = url
        self.read_sizes = []

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def _mock_response(monkeypatch, module, body, headers=None, url=INDEX_URL):
    response = _Response(body, headers or {}, url)
    opened = []

    def build_opener(handler):
        assert isinstance(handler, module.NoRedirect)

        def open_response(request, timeout):
            opened.append((request, timeout))
            return response

        return SimpleNamespace(open=open_response)

    monkeypatch.setattr(module.urllib.request, "build_opener", build_opener)
    return response, opened


def test_fetch_is_bounded_and_returns_original_bytes_and_http_metadata(monkeypatch) -> None:
    module = _load_script()
    body = b"12345678"
    response, opened = _mock_response(
        monkeypatch,
        module,
        body,
        {"Content-Length": "8", "ETag": '"release"', "Content-Type": "text/csv"},
    )
    data, headers = module.fetch_bytes(INDEX_URL, limit=8, timeout=4.0)
    assert data == body
    assert headers == {"Last-Modified": None, "ETag": '"release"', "Content-Type": "text/csv"}
    assert len(opened) == 1
    assert opened[0][0].full_url == INDEX_URL
    assert opened[0][1] == 4.0
    assert response.read_sizes == [9, 1]


@pytest.mark.parametrize("content_length", ["9", "-1", "nan", "1.5", "garbage"])
def test_fetch_rejects_oversized_or_invalid_content_length_before_reading(
    monkeypatch, content_length
) -> None:
    module = _load_script()
    response, _ = _mock_response(
        monkeypatch, module, b"123456789", {"Content-Length": content_length}
    )
    with pytest.raises(module.SpectrumError, match="Content-Length"):
        module.fetch_bytes(INDEX_URL, limit=8, timeout=4.0)
    assert response.read_sizes == []


def test_fetch_detects_oversized_response_without_content_length(monkeypatch) -> None:
    module = _load_script()
    response, _ = _mock_response(monkeypatch, module, b"12345678901234567890")
    with pytest.raises(module.SpectrumError, match="byte limit"):
        module.fetch_bytes(INDEX_URL, limit=8, timeout=4.0)
    assert response.read_sizes == [9]


@pytest.mark.parametrize("body,headers", [(b"", {}), (b"123", {"Content-Length": "8"})])
def test_fetch_rejects_empty_or_truncated_downloads(monkeypatch, body, headers) -> None:
    module = _load_script()
    _mock_response(monkeypatch, module, body, headers)
    with pytest.raises(module.SpectrumError, match="Empty or truncated"):
        module.fetch_bytes(INDEX_URL, limit=8, timeout=4.0)


def test_fetch_rejects_changed_final_url(monkeypatch) -> None:
    module = _load_script()
    response, _ = _mock_response(monkeypatch, module, b"data", url="https://example.org/data")
    with pytest.raises(module.SpectrumError, match="final download URL"):
        module.fetch_bytes(INDEX_URL, limit=8, timeout=4.0)
    assert response.read_sizes == []


def test_fetch_enforces_elapsed_timeout_between_chunks(monkeypatch) -> None:
    module = _load_script()
    _mock_response(monkeypatch, module, b"data")
    ticks = iter([10.0, 12.0])
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(module.SpectrumError, match="elapsed timeout"):
        module.fetch_bytes(INDEX_URL, limit=8, timeout=1.0)


@pytest.mark.parametrize("timeout", [0.0, -1.0, math.nan, math.inf])
def test_fetch_rejects_invalid_timeout_without_network(monkeypatch, timeout) -> None:
    module = _load_script()
    _, opened = _mock_response(monkeypatch, module, b"data")
    with pytest.raises(module.SpectrumError):
        module.fetch_bytes(INDEX_URL, limit=8, timeout=timeout)
    assert opened == []


def _seed_cache(monkeypatch, module, out):
    row = module.parse_catalog(CATALOG, INDEX_URL)[0]
    raw = b"synthetic spectrum bytes; FITS decoding is separately live-tested"
    http = {"ETag": '"test-product"'}
    calls = []

    def fetch(url, limit, timeout):
        calls.append((url, limit, timeout))
        return raw, http

    def read_spectrum(payload, selected):
        assert payload == raw
        assert selected == row
        return {"test_spectrum": True}

    monkeypatch.setattr(
        module, "get_catalog", lambda timeout: ([row], {INDEX_URL: CATALOG.encode()})
    )
    monkeypatch.setattr(module, "fetch_bytes", fetch)
    monkeypatch.setattr(module, "read_spectrum", read_spectrum)
    monkeypatch.setattr(module, "utc_now", lambda: "2026-09-05T12:00:00+00:00")
    spectrum, cache, reused = module.load_or_fetch(out, "18 Sco", refresh=False, timeout=5.0)
    assert spectrum == {"test_spectrum": True}
    assert not reused
    assert calls == [(SPECTRUM_URL, module.MAX_FITS, 5.0)]
    return raw, cache


def test_cache_persists_exact_sources_hashes_and_original_retrieval_time(
    monkeypatch, tmp_path
) -> None:
    module = _load_script()
    raw, cache = _seed_cache(monkeypatch, module, tmp_path)
    assert (tmp_path / "catalog.csv").read_bytes() == CATALOG.encode()
    assert (tmp_path / "spectrum.fits").read_bytes() == raw
    assert json.loads((tmp_path / "cache.json").read_bytes()) == cache
    assert cache["source_url"] == SPECTRUM_URL
    assert cache["catalog_url"] == INDEX_URL
    assert cache["catalog_sha256"] == module.digest(CATALOG.encode())
    assert cache["fits_sha256"] == module.digest(raw)
    assert cache["retrieved_utc"] == "2026-09-05T12:00:00+00:00"

    def no_network(*args, **kwargs):
        pytest.fail("A valid cached replay must not access the network")

    monkeypatch.setattr(module, "get_catalog", no_network)
    monkeypatch.setattr(module, "fetch_bytes", no_network)
    monkeypatch.setattr(module, "utc_now", lambda: "2099-01-01T00:00:00+00:00")
    spectrum, reused_cache, reused = module.load_or_fetch(
        tmp_path, "HD 146233", refresh=False, timeout=5.0
    )
    assert reused
    assert spectrum == {"test_spectrum": True}
    assert reused_cache == cache
    assert reused_cache["retrieved_utc"] == "2026-09-05T12:00:00+00:00"


@pytest.mark.parametrize("filename", ["catalog.csv", "spectrum.fits"])
def test_cache_corruption_is_rejected_without_network_or_overwrite(
    monkeypatch, tmp_path, filename
) -> None:
    module = _load_script()
    _seed_cache(monkeypatch, module, tmp_path)
    damaged = b"corrupted local bytes"
    (tmp_path / filename).write_bytes(damaged)

    def no_network(*args, **kwargs):
        pytest.fail("Cache corruption must fail before fetching replacement data")

    monkeypatch.setattr(module, "get_catalog", no_network)
    monkeypatch.setattr(module, "fetch_bytes", no_network)
    with pytest.raises(module.SpectrumError, match="hash mismatch"):
        module.load_or_fetch(tmp_path, "18 Sco", refresh=False, timeout=5.0)
    assert (tmp_path / filename).read_bytes() == damaged


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_url", "https://example.org/data"),
        ("catalog_url", "https://pepsi.aip.de/library/paperII/giants.csv"),
    ],
)
def test_cache_manifest_must_match_the_catalog_source_identity(
    monkeypatch, tmp_path, field, value
) -> None:
    module = _load_script()
    _, cache = _seed_cache(monkeypatch, module, tmp_path)
    cache[field] = value
    (tmp_path / "cache.json").write_text(json.dumps(cache))
    with pytest.raises(module.SpectrumError, match="source identity"):
        module.load_or_fetch(tmp_path, "18 Sco", refresh=False, timeout=5.0)


def test_cache_catalog_row_must_match_manifest_even_when_hashes_match(
    monkeypatch, tmp_path
) -> None:
    module = _load_script()
    _, cache = _seed_cache(monkeypatch, module, tmp_path)
    altered_catalog = CATALOG.replace(",700,", ",900,").encode()
    (tmp_path / "catalog.csv").write_bytes(altered_catalog)
    cache["catalog_sha256"] = module.digest(altered_catalog)
    (tmp_path / "cache.json").write_text(json.dumps(cache))
    with pytest.raises(module.SpectrumError, match="catalog row differs"):
        module.load_or_fetch(tmp_path, "18 Sco", refresh=False, timeout=5.0)


def test_help_is_available_without_optional_science_dependencies() -> None:
    result = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    for option in ("--list", "--target", "--range", "--out", "--refresh"):
        assert option in result.stdout
