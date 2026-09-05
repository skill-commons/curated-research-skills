import importlib.util
import math
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "gaia-dr3-tap-query"
    / "scripts"
    / "gaia_spectrum_demo.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("crs_gaia_spectrum_demo", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def test_source_id_stays_exact_and_query_is_bounded() -> None:
    module = _load_script()
    source_id = "5722622022989721600"
    assert module.validate_source_id(source_id) == source_id
    assert module.validate_source_id(str(2**63 - 1)) == str(2**63 - 1)
    query = module.build_query(source_id, "rvs")
    assert "SELECT TOP 1 source_id, flux, flux_error" in query
    assert query.endswith(f"WHERE source_id = {source_id}")
    for invalid in (
        float(source_id),
        int(source_id),
        "0",
        "-1",
        "01",
        str(2**63),
        "5722622022989721600.0",
        "5722622022989721600 OR 1=1",
    ):
        with pytest.raises(module.SpectrumError, match="exact positive int64"):
            module.validate_source_id(invalid)
    with pytest.raises(module.SpectrumError, match="product must"):
        module.build_query(source_id, "xp; DROP TABLE")


@pytest.mark.parametrize(
    "product,count,first,last,step",
    [
        ("rvs", 2401, 846, 870, 0.01),
        ("xp", 343, 336, 1020, 2),
    ],
)
def test_published_grids_keep_full_sample_count(product, count, first, last, step) -> None:
    grid = _load_script().wavelength_grid(product)
    assert len(grid) == count
    assert grid[0] == pytest.approx(first)
    assert grid[-1] == pytest.approx(last)
    assert grid[123] == pytest.approx(first + 123 * step)


def test_missing_bins_keep_position_and_errors_are_absolute() -> None:
    module = _load_script()
    flux, error, valid = module.clean_samples(
        [0.25, math.nan, 0.75, 0.4, 0.6], [0.04, 0.02, 0.03, -0.01, math.inf]
    )
    assert valid == [True, False, True, False, False]
    assert len(flux) == len(error) == 5
    assert flux[0] == 0.25
    assert error[0] == 0.04  # Gaia supplies absolute errors, unlike RAVE's fractional ERROR.
    assert math.isnan(flux[1]) and math.isnan(error[1])
    assert flux[2] == 0.75 and error[2] == 0.03
    with pytest.raises(module.SpectrumError, match="matching lengths"):
        module.clean_samples([1.0], [])
    with pytest.raises(module.SpectrumError, match="fewer than two"):
        module.clean_samples([math.nan, 1.0], [0.1, 0.1])


def test_help_is_available_without_science_dependencies() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], check=True, capture_output=True, text=True
    )
    assert "--source-id" in result.stdout
    assert "--product {rvs,xp}" in result.stdout
    assert "--refresh" in result.stdout
