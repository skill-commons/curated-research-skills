from pathlib import Path

from scripts.validate_skills import README, load_skills, render_readme, validate_groupings

ROOT = Path(__file__).resolve().parents[1]


def test_tap_contains_the_eleven_curated_skills() -> None:
    records = load_skills()
    assert len(records) == 11
    assert {record["name"] for record in records} == {
        "arxiv",
        "astro-catalog-plotting-cache",
        "calculator",
        "data-aip-de-s3",
        "gaia-dr3-tap-query",
        "latex-journal-submission-package",
        "latex-research-paper",
        "rave-dr6",
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
