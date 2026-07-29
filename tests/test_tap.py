import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_skills import README, load_skills, render_readme, validate_groupings

ROOT = Path(__file__).resolve().parents[1]


def _load_dt4acc_runner():
    path = (
        ROOT
        / "skills"
        / "dt4acc-host-smoke-test"
        / "scripts"
        / "dt4acc_host_smoke_test.py"
    )
    spec = importlib.util.spec_from_file_location("crs_dt4acc_smoke_runner", path)
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


def test_tap_contains_the_fifteen_curated_skills() -> None:
    records = load_skills()
    assert len(records) == 15
    assert {record["name"] for record in records} == {
        "arxiv",
        "astro-catalog-plotting-cache",
        "calculator",
        "data-aip-de-s3",
        "dt4acc-host-smoke-test",
        "gaia-dr3-tap-query",
        "large-tabular-visualization",
        "latex-journal-submission-package",
        "latex-research-paper",
        "python-library-docs-first",
        "rave-dr6",
        "rss-feed-monitor",
        "seaborn-paper-plots",
        "starhorse-access",
        "tap-pyvo-adql-access",
    }


def test_readme_is_generated_from_skill_metadata() -> None:
    assert README.read_text(encoding="utf-8") == render_readme(load_skills())


def test_skills_sh_groupings_match_hermes_categories() -> None:
    validate_groupings(load_skills())


def test_commons_publication_sidecars_are_not_in_the_tap() -> None:
    names = {path.name for path in (ROOT / "skills").rglob("*") if path.is_file()}
    assert "research-skill.yaml" not in names
    assert "research-skill.lock" not in names


def test_docs_first_core_is_provider_neutral() -> None:
    core = (
        ROOT / "skills" / "python-library-docs-first" / "SKILL.md"
    ).read_text(encoding="utf-8")
    for provider_detail in (
        "aip.de",
        "mcp_docs_",
        "mcporter",
        "curl -k",
        "~/.hermes",
    ):
        assert provider_detail not in core


def test_dt4acc_runner_rejects_output_inside_checkouts(tmp_path: Path) -> None:
    runner = _load_dt4acc_runner()
    checkout_root = tmp_path / "checkouts"
    checkout_root.mkdir()
    args = runner.parse_args(
        [
            "--repo-root",
            str(checkout_root),
            "--output",
            str(checkout_root / "result.json"),
        ]
    )
    with pytest.raises(runner.SmokeTestError, match="outside"):
        runner._resolve_config(args)


def test_dt4acc_wrapper_is_syntax_valid_and_install_free() -> None:
    wrapper = (
        ROOT
        / "skills"
        / "dt4acc-host-smoke-test"
        / "scripts"
        / "run_dt4acc_host_smoke_test.sh"
    )
    subprocess.run(["bash", "-n", str(wrapper)], check=True)
    text = wrapper.read_text(encoding="utf-8")
    assert "${BASH_SOURCE[0]}" in text
    assert "pip install" not in text
    assert "/tmp/" not in text
