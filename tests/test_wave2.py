import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REANA_SCRIPT = (
    ROOT / "skills" / "reana-workflow-authoring" / "scripts" / "reana_workflow_authoring.py"
)
PINNED_IMAGE = (
    "registry.example.org/research/analysis:1.0.0@sha256:"
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)


def _load_reana_authoring():
    spec = importlib.util.spec_from_file_location("crs_reana_workflow_authoring", REANA_SCRIPT)
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


def _write_project(
    project: Path,
    *,
    image: str = PINNED_IMAGE,
    commands: list[str] | None = None,
    input_path: str = "analysis.py",
    output_path: str = "results/summary.json",
    extra_step: dict | None = None,
) -> None:
    project.mkdir()
    (project / "analysis.py").write_text("print('offline fixture')\n", encoding="utf-8")
    step = {
        "name": "analysis",
        "environment": image,
        "commands": commands or ['cd "${REANA_WORKSPACE:?}" && python analysis.py'],
    }
    if extra_step:
        step.update(extra_step)
    document = {
        "inputs": {"files": [input_path]},
        "workflow": {
            "type": "serial",
            "specification": {"steps": [step]},
        },
        "outputs": {"files": [output_path]},
    }
    (project / "reana.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )


def _hashes(project: Path) -> dict[str, str]:
    return {
        path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(project.rglob("*"))
        if path.is_file()
    }


def test_evidence_skill_is_traceability_only() -> None:
    core = (ROOT / "skills" / "research-paper-evidence-workflow" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "claim-evidence ledger" in core
    assert "Do not execute project code, notebooks, builds, or experiments." in core
    assert "Write an artifact only when the user requests one" in core
    for excluded_source_pattern in (
        "delegate_task(",
        "cronjob(",
        "send_message(",
        "git add",
        "git push",
        "rm -",
        "NeurIPS",
        "ICML",
        "AAAI",
        "templates/",
    ):
        assert excluded_source_pattern not in core


def test_reana_skill_is_provider_neutral_and_local_only() -> None:
    core = (ROOT / "skills" / "reana-workflow-authoring" / "SKILL.md").read_text(encoding="utf-8")
    lowered = core.lower()
    assert "local-only boundary" in lowered
    assert "stop before authentication or remote execution" in lowered
    for provider_or_operation in (
        "aip.de",
        "reana.cern.ch",
        "gitlab-p4n",
        "reana_access_token",
        "reana_server_url",
        "reana-client run",
        "reana-client upload",
        "reana-client start",
        "reana-client download",
        "docker run",
        ":latest",
    ):
        assert provider_or_operation not in lowered


def test_reana_helper_has_no_network_or_process_launcher_imports() -> None:
    tree = ast.parse(REANA_SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert imported.isdisjoint(
        {
            "httpx",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
    )
    parser = _load_reana_authoring().build_parser()
    subparser_action = next(
        action for action in parser._actions if action.__class__.__name__ == "_SubParsersAction"
    )
    assert set(subparser_action.choices) == {"scaffold", "validate"}


def test_reana_scaffold_is_deterministic_and_validates_read_only(
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "analysis-project"
    result = module.main(
        [
            "scaffold",
            "--project",
            str(project),
            "--image",
            PINNED_IMAGE,
            "--script",
            "analysis.py",
            "--output",
            "results/summary.json",
        ]
    )
    assert result == 0
    capsys.readouterr()
    assert {
        path.relative_to(project).as_posix() for path in project.rglob("*") if path.is_file()
    } == {
        "analysis.py",
        "reana.yaml",
    }
    document = yaml.safe_load((project / "reana.yaml").read_text(encoding="utf-8"))
    assert document == {
        "inputs": {"files": ["analysis.py"]},
        "workflow": {
            "type": "serial",
            "specification": {
                "steps": [
                    {
                        "name": "analysis",
                        "environment": PINNED_IMAGE,
                        "commands": ['cd "${REANA_WORKSPACE:?}" && python analysis.py'],
                    }
                ]
            },
        },
        "outputs": {"files": ["results/summary.json"]},
    }
    before = _hashes(project)
    report = module.Validator(
        project,
        Path("reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=False,
    ).run()
    after = _hashes(project)
    assert report.errors == 0
    assert report.warnings == 0
    assert before == after


def test_reana_scaffold_refuses_collision_without_overwrite(
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "existing"
    project.mkdir()
    sentinel = project / "keep.txt"
    sentinel.write_text("owned by user\n", encoding="utf-8")
    before = _hashes(project)
    result = module.main(
        [
            "scaffold",
            "--project",
            str(project),
            "--image",
            PINNED_IMAGE,
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "overwrite is never implicit" in captured.err
    assert _hashes(project) == before


def test_reana_validator_rejects_legacy_keys_runtime_installs_and_mutable_images(
    tmp_path: Path,
) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "unsafe"
    _write_project(
        project,
        image="python:latest",
        commands=["pip install pandas && python analysis.py"],
        extra_step={"resources": {"memory": "32gb"}},
    )
    report = module.Validator(
        project,
        Path("reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=False,
    ).run()
    codes = {finding.code for finding in report.findings}
    assert {"IMAGE_LATEST", "RUNTIME_INSTALL", "UNKNOWN_KEY"} <= codes


def test_reana_validator_rejects_malformed_oci_reference(tmp_path: Path) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "bad-image"
    malformed = "bad value@@sha256:" + "a" * 64
    _write_project(project, image=malformed)
    report = module.Validator(
        project,
        Path("reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=False,
    ).run()
    assert "IMAGE_INVALID" in {finding.code for finding in report.findings}
    bad_port = "registry.example.org:99999/research/analysis@sha256:" + "a" * 64
    assert module._validate_image_reference(bad_port, False) == (
        "ERROR",
        "IMAGE_REGISTRY_INVALID",
        "image registry is malformed",
    )
    valid_port = "registry.example.org:5005/research/analysis@sha256:" + "a" * 64
    assert module._validate_image_reference(valid_port, False) is None


def test_reana_validator_rejects_traversal_duplicate_keys_and_symlinks(
    tmp_path: Path,
) -> None:
    module = _load_reana_authoring()

    traversal = tmp_path / "traversal"
    _write_project(traversal, input_path="../outside.py")
    traversal_report = module.Validator(
        traversal,
        Path("reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=False,
    ).run()
    assert "PATH_UNSAFE" in {finding.code for finding in traversal_report.findings}

    duplicate = tmp_path / "duplicate"
    duplicate.mkdir()
    (duplicate / "reana.yaml").write_text(
        "workflow:\n  type: serial\n  type: serial\n",
        encoding="utf-8",
    )
    duplicate_report = module.Validator(
        duplicate,
        Path("reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=False,
    ).run()
    assert "YAML_INVALID" in {finding.code for finding in duplicate_report.findings}

    symlinked = tmp_path / "symlinked"
    symlinked.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    (symlinked / "analysis.py").symlink_to(outside)
    (symlinked / "reana.yaml").write_text(
        yaml.safe_dump(
            {
                "inputs": {"files": ["analysis.py"]},
                "workflow": {
                    "type": "serial",
                    "specification": {
                        "steps": [
                            {
                                "environment": PINNED_IMAGE,
                                "commands": ["python analysis.py"],
                            }
                        ]
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    symlink_report = module.Validator(
        symlinked,
        Path("reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=False,
    ).run()
    symlink_codes = {finding.code for finding in symlink_report.findings}
    assert {"PATH_ESCAPE", "INVENTORY_SYMLINK"} & symlink_codes


def test_reana_validator_handles_symlink_loops_and_excluded_symlinks(
    tmp_path: Path,
) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "loops"
    _write_project(project)
    (project / "loop").symlink_to("loop")
    loop_report = module.Validator(
        project,
        Path("loop/reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=False,
    ).run()
    assert "SPECIFICATION_SYMLINK" in {finding.code for finding in loop_report.findings}

    excluded_target = tmp_path / "excluded-target"
    excluded_target.mkdir()
    (project / ".venv").symlink_to(excluded_target, target_is_directory=True)
    inventory_report = module.Validator(
        project,
        Path("reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=False,
    ).run()
    assert "INVENTORY_SYMLINK" in {finding.code for finding in inventory_report.findings}


def test_reana_declared_excluded_path_fails_without_scanning_secret(
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "excluded-input"
    _write_project(project, input_path="node_modules/secret.txt")
    excluded = project / "node_modules"
    excluded.mkdir()
    candidate = "synthetic_secret_value_123456789"
    (excluded / "secret.txt").write_text(
        f"RESEARCH_API_TOKEN={candidate}\n",
        encoding="utf-8",
    )
    result = module.main(
        [
            "validate",
            "--project",
            str(project),
            "--strict",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert result == 1
    assert "PATH_EXCLUDED_COMPONENT" in {finding["code"] for finding in report["findings"]}
    assert candidate not in captured.out
    assert candidate not in captured.err


def test_reana_declared_symlink_loop_returns_json_report(
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "declared-loop"
    project.mkdir()
    (project / "ordinary.txt").write_text("fixture\n", encoding="utf-8")
    (project / "loop").symlink_to("loop")
    (project / "reana.yaml").write_text(
        yaml.safe_dump(
            {
                "inputs": {"files": ["loop/file.txt"]},
                "workflow": {
                    "type": "serial",
                    "specification": {
                        "steps": [
                            {
                                "environment": PINNED_IMAGE,
                                "commands": ["python loop/file.txt"],
                            }
                        ]
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = module.main(
        [
            "validate",
            "--project",
            str(project),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert result == 1
    assert "PATH_SYMLINK" in {finding["code"] for finding in report["findings"]}
    assert "Traceback" not in captured.err


def test_reana_secret_report_never_echoes_candidate_value(
    tmp_path: Path,
    capsys,
) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "secret"
    _write_project(project)
    candidate = "synthetic_secret_value_123456789"
    (project / ".env").write_text(
        f"RESEARCH_API_TOKEN={candidate}\n",
        encoding="utf-8",
    )
    result = module.main(
        [
            "validate",
            "--project",
            str(project),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    codes = {finding["code"] for finding in report["findings"]}
    assert result == 1
    assert {"POSSIBLE_SECRET", "SENSITIVE_FILENAME"} <= codes
    assert candidate not in captured.out
    assert candidate not in captured.err


def test_reana_yaml_errors_never_echo_source_text(tmp_path: Path, capsys) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "malformed-secret"
    project.mkdir()
    candidate = "synthetic_secret_value_123456789"
    (project / "reana.yaml").write_text(
        f'token: "{candidate}\\q"\n',
        encoding="utf-8",
    )
    result = module.main(
        [
            "validate",
            "--project",
            str(project),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert result == 1
    assert "YAML_INVALID" in {finding["code"] for finding in report["findings"]}
    assert candidate not in captured.out
    assert candidate not in captured.err

    duplicate = tmp_path / "duplicate-secret"
    duplicate.mkdir()
    (duplicate / "reana.yaml").write_text(
        f"{candidate}: 1\n{candidate}: 2\n",
        encoding="utf-8",
    )
    duplicate_result = module.main(
        [
            "validate",
            "--project",
            str(duplicate),
            "--json",
        ]
    )
    duplicate_output = capsys.readouterr()
    assert duplicate_result == 1
    assert candidate not in duplicate_output.out
    assert candidate not in duplicate_output.err


def test_reana_deep_yaml_fails_without_traceback(tmp_path: Path) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "deep-yaml"
    project.mkdir()
    depth = 1_500
    (project / "reana.yaml").write_text(
        "value: " + "[" * depth + "0" + "]" * depth + "\n",
        encoding="utf-8",
    )
    report = module.Validator(
        project,
        Path("reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=False,
    ).run()
    assert "YAML_INVALID" in {finding.code for finding in report.findings}


def test_reana_binary_input_requires_explicit_review_waiver(tmp_path: Path) -> None:
    module = _load_reana_authoring()
    project = tmp_path / "binary"
    _write_project(project, input_path="payload.bin")
    (project / "payload.bin").write_bytes(b"\xff" * 100)
    unreviewed = module.Validator(
        project,
        Path("reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=False,
    ).run()
    assert "BINARY_INPUT_REVIEW" in {finding.code for finding in unreviewed.findings}
    reviewed = module.Validator(
        project,
        Path("reana.yaml"),
        allow_mutable_image=False,
        allow_binary_input=True,
    ).run()
    assert "BINARY_INPUT_WAIVER" in {finding.code for finding in reviewed.findings}
    assert reviewed.errors == 0
    assert reviewed.warnings == 1
