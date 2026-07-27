#!/usr/bin/env python3
"""Validate the Hermes tap and generate its human-facing README."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "skills"
README = ROOT / "README.md"
SKILLS_SH = ROOT / "skills.sh.json"

CATEGORIES = {
    "general": (
        "General",
        "Literature discovery, monitoring, and scientific calculation.",
    ),
    "latex": (
        "LaTeX",
        "Research-manuscript authoring, revision, compilation, and submission packaging.",
    ),
    "astronomy": (
        "Astronomy",
        "Astronomy catalog access, survey workflows, and scientific visualization.",
    ),
    "data": (
        "Data",
        "Reproducible access to large research datasets and object storage.",
    ),
    "visualization": (
        "Visualization",
        "General-purpose publication and report graphics.",
    ),
}

REQUIRED_FIELDS = ("name", "description", "version", "author", "license")
FORBIDDEN_PACKAGE_FILES = {"research-skill.yaml", "research-skill.lock"}
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path}: missing YAML frontmatter")
    try:
        raw, _body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise ValueError(f"{path}: unterminated YAML frontmatter") from exc
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: frontmatter must be a mapping")
    return value


def _validate_links(skill_dir: Path) -> None:
    for markdown in skill_dir.rglob("*.md"):
        for target in MARKDOWN_LINK_RE.findall(markdown.read_text(encoding="utf-8")):
            target = target.strip().strip("<>")
            if not target or target.startswith(("#", "/", "mailto:")) or "://" in target:
                continue
            relative = target.split("#", 1)[0]
            if relative and not (markdown.parent / relative).resolve().is_file():
                raise ValueError(f"{markdown}: broken relative link {target!r}")


def load_skills(root: Path = SKILLS_ROOT) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise ValueError(f"skills directory does not exist: {root}")
    records: list[dict[str, Any]] = []
    names: set[str] = set()
    for skill_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            raise ValueError(f"{skill_dir}: missing SKILL.md")
        metadata = _frontmatter(skill_file)
        for field in REQUIRED_FIELDS:
            if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                raise ValueError(f"{skill_file}: {field} must be a non-empty string")
        if metadata["name"] != skill_dir.name:
            raise ValueError(f"{skill_file}: name {metadata['name']!r} does not match directory")
        if metadata["name"] in names:
            raise ValueError(f"duplicate skill name: {metadata['name']}")
        names.add(metadata["name"])
        hermes = metadata.get("metadata", {}).get("hermes", {})
        if not isinstance(hermes, dict):
            raise ValueError(f"{skill_file}: metadata.hermes must be a mapping")
        category = hermes.get("category")
        if category not in CATEGORIES:
            raise ValueError(f"{skill_file}: unknown Hermes category {category!r}")
        tags = hermes.get("tags")
        if (
            not isinstance(tags, list)
            or not tags
            or not all(isinstance(tag, str) and tag for tag in tags)
        ):
            raise ValueError(f"{skill_file}: metadata.hermes.tags must be a string list")
        forbidden = FORBIDDEN_PACKAGE_FILES.intersection(
            path.name for path in skill_dir.rglob("*") if path.is_file()
        )
        if forbidden:
            raise ValueError(
                f"{skill_dir}: Commons publication files are not Hermes package files: "
                f"{', '.join(sorted(forbidden))}"
            )
        _validate_links(skill_dir)
        records.append(
            {
                "name": metadata["name"],
                "description": " ".join(metadata["description"].split()),
                "version": metadata["version"],
                "category": category,
            }
        )
    return records


def validate_groupings(records: list[dict[str, Any]]) -> None:
    value = json.loads(SKILLS_SH.read_text(encoding="utf-8"))
    groupings = value.get("groupings")
    if not isinstance(groupings, list):
        raise ValueError("skills.sh.json: groupings must be a list")
    expected = {
        CATEGORIES[category][0]: {
            record["name"] for record in records if record["category"] == category
        }
        for category in CATEGORIES
    }
    actual: dict[str, set[str]] = {}
    for grouping in groupings:
        if not isinstance(grouping, dict):
            raise ValueError("skills.sh.json: each grouping must be an object")
        title = grouping.get("title")
        skills = grouping.get("skills")
        if not isinstance(title, str) or not isinstance(skills, list):
            raise ValueError("skills.sh.json: grouping title/skills are invalid")
        actual[title] = set(skills)
    if actual != expected:
        raise ValueError("skills.sh.json groupings differ from SKILL.md categories")


def render_readme(records: list[dict[str, Any]]) -> str:
    lines = [
        "# Curated Research Skills",
        "",
        "Canonical Hermes skills consolidated and maintained by "
        "[Skill Commons](https://github.com/skill-commons). This repository is a "
        "content source; the federated discovery index lives in "
        "[`skill-commons/skill-commons`](https://github.com/skill-commons/skill-commons).",
        "",
        "Each directory under [`skills/`](skills/) is a complete installable unit. "
        "Supporting `references/`, `scripts/`, `templates/`, and assets stay with its "
        "`SKILL.md`.",
        "",
        "## Use with Hermes",
        "",
        "Subscribe to the complete tap:",
        "",
        "```bash",
        "hermes skills tap add skill-commons/curated-research-skills",
        "hermes skills search astronomy",
        "hermes skills install skill-commons/curated-research-skills/tap-pyvo-adql-access",
        "```",
        "",
        "Or install one skill directly without subscribing:",
        "",
        "```bash",
        "hermes skills install skill-commons/curated-research-skills/skills/tap-pyvo-adql-access",
        "```",
        "",
        "## Skills",
    ]
    by_category = {category: [] for category in CATEGORIES}
    for record in records:
        by_category[record["category"]].append(record)
    for category, (name, description) in CATEGORIES.items():
        lines.extend(
            [
                "",
                f"### {name}",
                "",
                description,
                "",
                "| Skill | Version | Description |",
                "|---|---:|---|",
            ]
        )
        for record in by_category[category]:
            escaped = record["description"].replace("|", "\\|")
            lines.append(
                f"| [`{record['name']}`](skills/{record['name']}/) "
                f"| `{record['version']}` | {escaped} |"
            )
    lines.extend(
        [
            "",
            "Attribution and consolidation history are recorded in "
            "[`PROVENANCE.md`](PROVENANCE.md).",
            "",
            "The inventory is generated from Hermes `SKILL.md` metadata. After changing "
            "a skill, run:",
            "",
            "```bash",
            "uv run python scripts/validate_skills.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when README.md differs from the generated inventory",
    )
    args = parser.parse_args()
    records = load_skills()
    validate_groupings(records)
    expected = render_readme(records)
    if args.check:
        if not README.is_file() or README.read_text(encoding="utf-8") != expected:
            raise SystemExit("README.md is stale; run scripts/validate_skills.py")
    else:
        README.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
