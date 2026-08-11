import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_WITH_PORTABLE_ENVIRONMENTS = (
    "astro-catalog-plotting-cache",
    "calculator",
    "data-aip-de-s3",
    "gaia-dr3-tap-query",
    "large-tabular-visualization",
    "rave-dr6",
    "seaborn-paper-plots",
    "starhorse-access",
    "tap-pyvo-adql-access",
)
BASH_BLOCK = re.compile(r"```bash\n(.*?)\n```", re.DOTALL)
DIRECT_PIN = re.compile(r"[\"']?([A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_.-]+\])?==[A-Za-z0-9_.+-]+)[\"']?")


def _pins(block: str) -> set[str]:
    return set(DIRECT_PIN.findall(block))


def test_portable_environments_offer_pip_and_uv_with_the_same_direct_pins() -> None:
    documented = {
        skill_file.parent.name
        for skill_file in (ROOT / "skills").glob("*/SKILL.md")
        if "python3.12 -m venv .venv" in skill_file.read_text(encoding="utf-8")
    }
    assert documented == set(SKILLS_WITH_PORTABLE_ENVIRONMENTS)

    for name in SKILLS_WITH_PORTABLE_ENVIRONMENTS:
        text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        blocks = BASH_BLOCK.findall(text)
        pip_blocks = [block for block in blocks if ".venv/bin/python -m pip install" in block]
        uv_blocks = [
            block for block in blocks if "uv pip install --python .venv/bin/python" in block
        ]

        assert len(pip_blocks) == 1, name
        assert len(uv_blocks) == 1, name
        assert "python3.12 -m venv .venv" in pip_blocks[0], name
        assert "uv venv --python 3.12 .venv" in uv_blocks[0], name
        assert _pins(pip_blocks[0]), name
        assert _pins(pip_blocks[0]) == _pins(uv_blocks[0]), name
        assert "not a complete transitive lock" in text, name
        assert "may download Python 3.12" in text, name
        assert "--no-python-downloads" in text, name
        assert "Both recipes assume `.venv` is a new workspace path" in text, name
        assert ".venv/bin/python -m pip check" in pip_blocks[0], name
        assert "uv pip check --python .venv/bin/python" in uv_blocks[0], name
        assert "pip and uv may resolve transitive dependencies differently" in " ".join(
            text.split()
        ), name


def test_large_tabular_environment_covers_the_exercised_rendering_stack() -> None:
    text = (ROOT / "skills" / "large-tabular-visualization" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    pip_block = next(
        block for block in BASH_BLOCK.findall(text) if ".venv/bin/python -m pip install" in block
    )

    assert _pins(pip_block) == {
        "bokeh==3.9.2",
        "dask[dataframe]==2026.7.1",
        "datashader==0.19.1",
        "hvplot==0.12.2",
        "numpy==2.4.6",
        "pandas==3.0.5",
        "pyarrow==25.0.0",
    }

    smoke = (
        ROOT / "skills" / "large-tabular-visualization" / "scripts" / "hvplot_datashader_smoke.py"
    )
    compile(smoke.read_text(encoding="utf-8"), str(smoke), "exec")
    assert "scripts/hvplot_datashader_smoke.py" in text
    assert "PYTHONDONTWRITEBYTECODE=1" in text
