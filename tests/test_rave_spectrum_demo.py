import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "rave-dr6" / "scripts" / "rave_spectrum_demo.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("crs_rave_spectrum_demo", SCRIPT)
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


def test_observation_id_and_query_are_bounded() -> None:
    module = _load_script()
    query = module.build_query("20100313_0823m14_113")
    assert "SELECT TOP 1" in query
    assert "WHERE f.rave_obs_id = '20100313_0823m14_113'" in query
    with pytest.raises(module.SpectrumError, match="arbitrary ADQL"):
        module.build_query("20100313_0823m14_113' OR 1=1")


def test_fits_url_is_bound_to_exact_https_archive_path() -> None:
    module = _load_script()
    obs_id = "20100313_0823m14_113"
    expected = "https://www.rave-survey.org/files/fits/20100313/RAVE_20100313_0823m14_113.fits"
    assert module.validate_fits_url(expected, obs_id) == expected
    for unsafe in (
        expected.replace("https://", "http://"),
        expected.replace("www.rave-survey.org", "example.org"),
        expected.replace("20100313/RAVE", "20100314/RAVE"),
        expected + "?download=1",
    ):
        with pytest.raises(module.SpectrumError, match="unexpected FITS URL"):
            module.validate_fits_url(unsafe, obs_id)


def test_linear_wavelength_grid_honors_fits_one_based_reference_pixel() -> None:
    module = _load_script()
    grid = module.linear_wavelength_grid(4, crval1=8410.0, crpix1=2.0, cdelt1=0.5)
    assert grid == [8409.5, 8410.0, 8410.5, 8411.0]
    with pytest.raises(module.SpectrumError, match="at least two"):
        module.linear_wavelength_grid(1, crval1=1.0, crpix1=1.0, cdelt1=1.0)


def test_relative_error_is_converted_to_positive_flux_uncertainty() -> None:
    module = _load_script()
    assert module.normalized_flux_error(0.25, 0.04) == pytest.approx(0.01)
    assert module.normalized_flux_error(-0.25, 0.04) == pytest.approx(0.01)


def test_help_is_available_without_optional_science_dependencies() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--rave-obs-id" in result.stdout
    assert "--refresh" in result.stdout
