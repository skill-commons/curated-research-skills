"""Offline input contracts and independent geometric/recovery checks for CMD fitting.

Science tests also run in a dedicated CI job with the skill's pinned runtime.
No live archive or PARSEC service is contacted by this suite.
"""

import csv
import importlib.util
import io
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/cluster-cmd-isochrone-fit/scripts"


def load_helper(name):
    spec = importlib.util.spec_from_file_location(f"cmd_test_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


fit = load_helper("fit_cmd")
m67 = load_helper("fetch_m67")
parsec = load_helper("fetch_parsec")


@pytest.fixture
def np():
    pytest.importorskip("scipy")
    return pytest.importorskip("numpy")


@pytest.mark.parametrize("script", ["fit_cmd", "fetch_m67", "fetch_parsec"])
def test_help_does_not_require_science_packages(script):
    result = subprocess.run(
        [sys.executable, "-B", "-S", str(SCRIPTS / f"{script}.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def artifact(tmp_path, name, fields, rows, system):
    path = tmp_path / f"{name}.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    path.with_suffix(".json").write_text(
        json.dumps({"schema_version": 1, "photometric_system": system})
    )
    return path


def star(index=0, **changes):
    return {
        "source_id": str(598480504169934336 + index),
        "g": 13.0,
        "bp": 14.0,
        "rp": 13.2,
        "g_error": 0.003,
        "bp_error": 0.004,
        "rp_error": 0.005,
        "member_score": 0.8,
        "selected": "true",
        "exclusion_reason": "",
    } | changes


@pytest.mark.parametrize("system", ["Gaia DR2", "Gaia EDR3 (AB)", "Johnson V"])
def test_passband_mismatch_is_rejected(tmp_path, system):
    path = artifact(tmp_path, "stars", fit.STAR_FIELDS, [star()], system)
    with pytest.raises(ValueError, match="photometric_system"):
        fit.load_stars(path)


@pytest.mark.parametrize(
    "rows",
    [
        [star(), star()],
        [star(g_error=0)],
        [star(bp=float("nan"))],
        [star(selected="maybe")],
        [star(selected="false")],
        [star(member_score=1.2)],
    ],
)
def test_invalid_selected_measurements_and_duplicate_ids_are_rejected(tmp_path, rows):
    path = artifact(tmp_path, "stars", fit.STAR_FIELDS, rows, "Gaia DR3")
    with pytest.raises(ValueError):
        fit.load_stars(path)


def test_excluded_missing_data_and_large_identifiers_are_preserved(tmp_path):
    rows = [star(), star(1, selected="false", bp="", exclusion_reason="missing_bp")]
    path = artifact(tmp_path, "stars", fit.STAR_FIELDS, rows, "Gaia DR3")
    loaded, _ = fit.load_stars(path)
    assert loaded[0]["source_id"] == "598480504169934336"
    assert loaded[1]["source_id"] == "598480504169934337"
    assert math.isnan(loaded[1]["bp"])
    assert not loaded[1]["selected"]


@pytest.mark.parametrize("value", ["9,10,.3", "10,9,.1", "9,10,0", "nan", "inf"])
def test_incomplete_or_invalid_numeric_grid_is_rejected(value):
    with pytest.raises(ValueError):
        fit.values(value)


def test_grid_includes_both_endpoints():
    assert fit.values("9,10,.05") == pytest.approx([9 + 0.05 * i for i in range(21)])


def point(color, g, mass=1.0, phase=1):
    return {"bp_abs": color, "rp_abs": 0.0, "g_abs": g, "mini": mass, "label": phase}


def test_projection_uses_both_axes_and_heterogeneous_errors(np):
    # For line y=x, the weighted projection is an independent analytic solution.
    segments = fit.build_segments([point(0, 0), point(2, 2, mass=2)])
    points = np.array([[0.2, 1.2], [1.8, 0.3], [3, 3]])
    errors = np.array([[0.1, 0.3], [0.5, 0.1], [0.2, 0.2]])
    weights = 1 / errors**2
    expected_t = np.clip((points * weights).sum(axis=1) / weights.sum(axis=1), 0, 2)
    expected = np.column_stack((expected_t, expected_t))
    _, distance, matched = fit.segment_costs(points, errors, segments)
    assert np.allclose(matched, expected, atol=1e-12)
    assert np.allclose(distance, np.linalg.norm((points - expected) / errors, axis=1))


def test_geometry_is_invariant_to_sampling_density(np):
    coarse = [point(0, 0), point(1, 2, mass=2), point(2, 1, mass=3)]
    dense = [point(i / 100, 2 * i / 100, mass=1 + i / 100) for i in range(101)]
    dense += [point(1 + i / 75, 2 - i / 75, mass=2 + i / 75) for i in range(1, 76)]
    rng = np.random.default_rng(814)
    points = rng.uniform(-0.5, 2.5, size=(40, 2))
    errors = rng.uniform(0.02, 0.3, size=(40, 2))
    a = fit.segment_costs(points, errors, fit.build_segments(coarse))
    b = fit.segment_costs(points, errors, fit.build_segments(dense))
    assert np.allclose(a[0], b[0], atol=1e-10)
    assert np.allclose(a[1], b[1], atol=1e-10)


def test_removed_phase_is_not_bridged(np):
    rows = [
        point(0, 0),
        point(1, 0, phase=1),
        point(2, 0, phase=4),
        point(3, 0, phase=1),
        point(4, 0, phase=1),
    ]
    segments = fit.build_segments(rows, phases=(1,))
    _, distance, _ = fit.segment_costs([[2, 0]], [[0.1, 0.1]], segments)
    assert distance[0] == pytest.approx(10)


def synthetic_models():
    # Known curves with distinct turnoff-like bends. These are an algorithm control,
    # not stellar-evolution models and must never be presented as physical isochrones.
    result = []
    for index, log_age in enumerate((8.7, 8.9, 9.1, 9.3, 9.5)):
        rows = []
        for j in range(51):
            color = 0.3 + j / 50
            mag = 2 + 3 * color + 0.25 * index * (color - 0.3) ** 2
            rows.append(point(color, mag, mass=1 + j / 50))
        result.append(
            {"model_id": str(index), "log_age": log_age, "mh": 0.0, "av": 0.0, "rows": rows}
        )
    return result


@pytest.mark.parametrize("true_index", [1, 3])
def test_age_and_distance_recovery_at_two_distinct_ages(np, true_index):
    models = synthetic_models()
    rng = np.random.default_rng(723 + true_index)
    stars = []
    for i, row in enumerate(models[true_index]["rows"][5:-5]):
        # Noise deliberately much smaller than between-model differences, but nonzero.
        stars.append(
            star(
                i,
                bp=row["bp_abs"] + rng.normal(0, 0.001),
                rp=0,
                g=row["g_abs"] + 9.4 + rng.normal(0, 0.001),
            )
        )
    parameters, costs, _, _ = fit.fit_grid(stars, models, [9.2, 9.4, 9.6])
    best = parameters[int(np.argmin(costs.sum(axis=1)))]
    assert best["log_age"] == models[true_index]["log_age"]
    assert best["distance_modulus"] == 9.4
    bootstrap = fit.bootstrap_grid(costs, parameters, repeats=20, seed=918)
    assert bootstrap["quantiles"]["log_age"] == [best["log_age"]] * 3
    assert bootstrap["quantiles"]["age_gyr"][1] == pytest.approx(10 ** best["log_age"] / 1e9)


def raw_member(**changes):
    # Plausible flux ratio exactly on Riello's published x=1 baseline.
    row = dict.fromkeys(m67.COLUMNS, "1")
    row.update(
        Name="NGC_2682",
        GaiaDR3="598480504169934336",
        Prob="0.8",
        RUWE="1.0",
        FG="10000",
        e_FG="10",
        FBP="6000",
        e_FBP="10",
        FRP="6168.44",
        e_FRP="10",
        Gmag="14",
        BPmag="14.6",
        RPmag="13.6",
        NSS="0",
        VarFlag="NOT_AVAILABLE",
    )
    return row | changes


def source_csv(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=m67.COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def test_member_error_propagation_and_quality_selection():
    rows = m67.convert_rows(source_csv([raw_member()]))
    assert rows[0]["selected"] == "true"
    assert rows[0]["g_error"] == pytest.approx(2.5 / math.log(10) * 10 / 10000)
    assert rows[0]["c_star"] == pytest.approx(0, abs=1e-12)
    assert rows[0]["source_id"] == "598480504169934336"


def test_member_missing_flux_is_preserved_with_reason():
    rows = m67.convert_rows(source_csv([raw_member(FBP="nan")]))
    assert len(rows) == 1
    assert rows[0]["selected"] == "false"
    assert rows[0]["bp_error"] is None
    assert "bp_flux_or_error_missing_or_nonpositive" in rows[0]["exclusion_reason"]


@pytest.mark.parametrize("change", [{"Name": "M45"}, {"GaiaDR3": "5.98480504169934336e17"}])
def test_source_identity_change_is_refused(change):
    with pytest.raises(m67.CatalogueError):
        m67.convert_rows(source_csv([raw_member(**change)]))


def test_offline_conversion_never_fetches_and_existing_output_is_preserved(tmp_path, monkeypatch):
    def no_network():
        pytest.fail("Offline conversion or output refusal attempted network access")

    monkeypatch.setattr(m67, "fetch_source", no_network)
    source = tmp_path / "source.csv"
    source.write_bytes(source_csv([raw_member()]))
    out = tmp_path / "out"
    path = m67.run(out, source)
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    metadata = json.loads(path.with_suffix(".json").read_text())
    assert metadata["output_csv_sha256"] == m67.sha256(path.read_bytes())
    assert metadata["provenance"]["retrieved_utc"] is None
    with pytest.raises(m67.CatalogueError, match="already exists"):
        m67.run(out)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def parsec_source(ages=(9.0, 9.1), *, terminator=True):
    lines = [
        "# File generated by CMD 3.9 (synthetic parser fixture)",
        "# isochrones based on PARSEC release v1.2S",
        "# Photometric system: Gaia EDR3 (all Vegamags, Gaia passbands from ESA/Gaia website)",
        "# Using OBC version of bolometric corrections",
        "# Kind of output: isochrone tables",
        "# O-rich circumstellar dust ignored",
        "# C-rich circumstellar dust ignored",
        "# " + " ".join(parsec.SOURCE_COLUMNS),
    ]
    for age in ages:
        for i in range(20):
            mass = 0.1 + 0.05 * i
            lines.append(f"0.0152 0 {age} {mass} 1 {mass} 0 3.7 4.5 1 4 4 4.5 3.8")
    if terminator:
        lines.append("#isochrone terminated")
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.parametrize("raw", [parsec_source((9.0,)), parsec_source(terminator=False)])
def test_parsec_incomplete_age_grid_or_truncated_table_is_rejected(raw):
    with pytest.raises(parsec.GridError):
        parsec.parse_source(raw, {"log_age": [9.0, 9.1], "mh": [0.0], "av": [0.0]}, 0.0)


def test_parsec_preserves_physical_grid_and_refuses_wrong_passbands():
    raw = parsec_source()
    grid = {"log_age": [9.0, 9.1], "mh": [0.0], "av": [0.0]}
    rows = parsec.parse_source(raw, grid, 0.0)
    assert len(rows) == 40
    assert {row[1] for row in rows} == {9.0, 9.1}
    with pytest.raises(parsec.GridError):
        parsec.parse_source(raw.replace(b"Gaia EDR3", b"Gaia DR2"), grid, 0.0)


def test_parsec_bundle_emits_correct_obc_citations(tmp_path, monkeypatch):
    grid = {"log_age": [9.0, 9.1], "mh": [0.0], "av": [0.0]}
    output_url = "https://stev.oapd.inaf.it/tmp/output123.dat"

    def download(url, parameters=None, **kwargs):
        if url == parsec.SOURCE_URL:
            assert parameters == parsec.query_parameters(grid, 0.0)
            return b'<a href="../tmp/output123.dat">data</a>'
        assert url == output_url
        return parsec_source()

    monkeypatch.setattr(parsec, "download", download)
    path = tmp_path / "models"
    parsec.fetch_grid(path, grid)
    manifest_bytes = (path / "model.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    correct = {
        "https://doi.org/10.1051/0004-6361:20078467",
        "https://doi.org/10.1086/588526",
    }
    assert correct <= set(manifest["citations"])
    assert not {
        "https://doi.org/10.1051/0004-6361:20079174",
        "https://doi.org/10.1086/590733",
    } & set(manifest["citations"])
    references = (SCRIPTS.parent / "references/parsec.md").read_text()
    assert all(citation in references for citation in correct)
    assert parsec.fetch_grid(path, grid, offline=True) == manifest
    assert (path / "model.json").read_bytes() == manifest_bytes


def test_parsec_rounding_drift_is_normalized_without_admitting_wrong_ages():
    grid = {"log_age": [9.0, 9.1], "mh": [0.0], "av": [0.0]}
    raw = parsec_source().replace(b" 9.1 ", b" 9.10001 ")
    rows = parsec.parse_source(raw, grid, 0.0)
    assert {row[1] for row in rows} == {9.0, 9.1}
    with pytest.raises(parsec.GridError):
        parsec.parse_source(raw.replace(b"9.10001", b"9.101"), grid, 0.0)


def test_declared_input_digest_is_verified(tmp_path):
    path = artifact(tmp_path, "stars", fit.STAR_FIELDS, [star()], "Gaia DR3")
    sidecar = path.with_suffix(".json")
    metadata = json.loads(sidecar.read_text()) | {"output_csv_sha256": fit.sha256(path)}
    sidecar.write_text(json.dumps(metadata))
    path.write_text(path.read_text().replace("13.0", "13.1"))
    with pytest.raises(ValueError, match="CSV hash"):
        fit.load_stars(path)


def test_complete_offline_fit_records_boundaries_artifacts_and_replay(tmp_path, np, monkeypatch):
    pytest.importorskip("matplotlib")
    import socket

    def no_network(*args, **kwargs):
        pytest.fail("Offline fitting attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", no_network)
    models = synthetic_models()
    rows = []
    for model in models:
        for row in model["rows"]:
            rows.append({key: model[key] for key in ("model_id", "log_age", "mh", "av")} | row)
    model_path = artifact(tmp_path, "models", fit.MODEL_FIELDS, rows, "Gaia EDR3 (Vega)")
    observations = [
        star(i, bp=r["bp_abs"], rp=0, g=r["g_abs"] + 9.4)
        for i, r in enumerate(models[0]["rows"][5:-5])
    ]
    star_path = artifact(tmp_path, "stars", fit.STAR_FIELDS, observations, "Gaia DR3")
    outputs = []
    for name in ("first", "replay"):
        args = fit.parser().parse_args(
            [
                "--stars",
                str(star_path),
                "--models",
                str(model_path),
                "--output",
                str(tmp_path / name),
                "--distance-modulus",
                "9.4",
                "--g-range",
                "0,30",
                "--bootstrap",
                "20",
                "--seed",
                "44",
            ]
        )
        report = fit.run(args)
        assert report["best_fit"]["log_age"] == 8.7
        assert any("log_age" in message and "boundary" in message for message in report["warnings"])
        assert any("grid resolution" in message for message in report["warnings"])
        assert report["runtime"]["helper_sha256"] == fit.sha256(SCRIPTS / "fit_cmd.py")
        for filename, record in report["artifacts"].items():
            assert fit.sha256(tmp_path / name / filename) == record["sha256"]
        outputs.append(report)
    assert outputs[0]["best_fit"] == outputs[1]["best_fit"]
    assert outputs[0]["conditional_bootstrap"] == outputs[1]["conditional_bootstrap"]
    assert (tmp_path / "first/score-grid.csv").read_bytes() == (
        tmp_path / "replay/score-grid.csv"
    ).read_bytes()
    before = (tmp_path / "replay/fit.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        fit.run(args)
    assert (tmp_path / "replay/fit.json").read_bytes() == before


@pytest.mark.parametrize(
    "raw", [b"<p>Server unavailable</p>", b'<a href="https://evil.invalid/grid">data</a>']
)
def test_successful_http_error_or_unexpected_output_link_is_refused(raw):
    with pytest.raises(parsec.GridError):
        parsec.output_link(raw)


def test_truncated_csv_row_is_rejected_before_field_conversion(tmp_path):
    path = artifact(tmp_path, "stars", fit.STAR_FIELDS, [star()], "Gaia DR3")
    lines = path.read_text().splitlines()
    lines[1] = ",".join(lines[1].split(",")[:3])
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="malformed CSV"):
        fit.load_stars(path)
