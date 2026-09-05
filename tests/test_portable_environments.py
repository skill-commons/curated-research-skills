import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS_WITH_PORTABLE_ENVIRONMENTS = (
    "astro-catalog-plotting-cache",
    "calculator",
    "data-aip-de-s3",
    "gaia-dr3-tap-query",
    "large-tabular-visualization",
    "pepsi-spectra",
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


@pytest.mark.parametrize("name", ["gaia-dr3-tap-query", "rave-dr6", "pepsi-spectra"])
@pytest.mark.parametrize("installer", ["pip", "uv"])
@pytest.mark.parametrize("existing", ["directory", "file", "symlink", "dangling-symlink"])
def test_spectrum_setup_refuses_existing_paths_without_mutation(
    name: str, installer: str, existing: str, tmp_path: Path
) -> None:
    text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    marker = ".venv/bin/python -m pip install" if installer == "pip" else "uv pip install"
    block = next(block for block in BASH_BLOCK.findall(text) if marker in block)
    target = tmp_path / ".venv"
    sentinel = tmp_path / "user-owned"
    sentinel.write_text("preserve me", encoding="utf-8")
    if existing == "directory":
        target.mkdir()
        sentinel = target / "user-owned"
        sentinel.write_text("preserve me", encoding="utf-8")
    elif existing == "file":
        target.write_text("preserve me", encoding="utf-8")
    else:
        target.symlink_to(sentinel if existing == "symlink" else tmp_path / "absent")
    before = target.lstat()

    # Empty PATH prevents any package/network operation if the guard regresses.
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", block],
        cwd=tmp_path,
        env={"PATH": ""},
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 1, result.stderr
    assert "Refusing existing .venv" in result.stderr
    assert target.lstat() == before
    assert sentinel.read_text(encoding="utf-8") == "preserve me"
    if existing == "file":
        assert target.read_text(encoding="utf-8") == "preserve me"


@pytest.mark.parametrize("name", ["gaia-dr3-tap-query", "rave-dr6", "pepsi-spectra"])
def test_spectrum_helpers_pin_every_direct_third_party_import(name: str) -> None:
    skill = ROOT / "skills" / name
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    block = next(
        block for block in BASH_BLOCK.findall(text) if ".venv/bin/python -m pip install" in block
    )
    pinned_packages = {pin.split("==")[0] for pin in _pins(block)}
    imports = set()
    for script in (skill / "scripts").glob("*.py"):
        for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])

    assert imports - sys.stdlib_module_names <= pinned_packages
