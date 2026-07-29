#!/usr/bin/env python3
"""Scaffold and conservatively validate local REANA Serial workflow projects."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

MAX_YAML_BYTES = 1_000_000
MAX_TEXT_SCAN_BYTES = 2_000_000
MAX_INVENTORY_FILES = 10_000
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
IMAGE_NAME_COMPONENT = re.compile(r"^[a-z0-9]+(?:(?:[._]|__|[-]+)[a-z0-9]+)*$")
IMAGE_REGISTRY = re.compile(
    r"^(?:localhost|[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*)"
    r"(?::[1-9][0-9]{0,4})?$"
)
IMAGE_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
RUNTIME_INSTALL = re.compile(
    r"""(?ix)
    (?:^|[;&|]\s*)
    (?:
        (?:python(?:3(?:\.\d+)?)?\s+-m\s+)?pip(?:3)?
        |uv\s+pip
        |conda
        |mamba
        |micromamba
        |apt(?:-get)?
        |apk
        |dnf
        |yum
    )
    \s+install\b
    """
)
HEREDOC = re.compile(r"<<-?\s*['\"]?[A-Za-z_][A-Za-z0-9_]*")
REMOTE_REANA_OPERATION = re.compile(
    r"\breana-client\s+(?:create|delete|download|logs|ping|rm|run|start|status|stop|upload)\b"
)
EXCLUDED_INVENTORY_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
}
SENSITIVE_FILENAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_ecdsa",
    "id_rsa",
}
SECRET_PATTERNS = (
    (
        "private-key",
        re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    ),
    (
        "github-fine-grained-token",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ),
    (
        "github-classic-token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ),
    (
        "aws-access-key",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
    (
        "credential-assignment",
        re.compile(
            r"""(?imx)
            ^\s*(?:export\s+)?
            [A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API_KEY)[A-Z0-9_]*
            \s*[:=]\s*
            ["']?
            (?!\$|<|REPLACE|EXAMPLE|YOUR_)
            [A-Za-z0-9._/+==-]{12,}
            ["']?\s*$
            """
        ),
    ),
    (
        "json-credential",
        re.compile(
            r"""(?ix)
            ["'][^"'\n]*(?:token|password|secret|api[_-]?key)[^"'\n]*["']
            \s*:\s*
            ["']
            (?!\$|<|REPLACE|EXAMPLE|YOUR_)
            [^"'\n]{12,}
            ["']
            """
        ),
    ),
)

ROOT_KEYS = {"version", "inputs", "workflow", "outputs", "workspace"}
INPUT_KEYS = {"files", "directories", "parameters", "options"}
WORKFLOW_KEYS = {"type", "specification", "resources"}
SPECIFICATION_KEYS = {"steps"}
STEP_KEYS = {
    "name",
    "environment",
    "commands",
    "compute_backend",
    "kubernetes_memory_limit",
    "kubernetes_job_timeout",
}
OUTPUT_KEYS = {"files", "directories"}


class DuplicateKeyError(ValueError):
    """Raised when YAML contains a duplicate mapping key."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise DuplicateKeyError(
                f"unhashable mapping key at line {key_node.start_mark.line + 1}"
            ) from exc
        if duplicate:
            raise DuplicateKeyError(f"duplicate mapping key at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    location: str
    message: str

    def render(self) -> str:
        return f"{self.level} {self.code} {self.location}: {self.message}"


@dataclass(frozen=True)
class ValidationReport:
    project: str
    specification: str
    files_scanned: int
    findings: tuple[Finding, ...]

    @property
    def errors(self) -> int:
        return sum(finding.level == "ERROR" for finding in self.findings)

    @property
    def warnings(self) -> int:
        return sum(finding.level == "WARNING" for finding in self.findings)

    def to_json(self) -> str:
        return json.dumps(
            {
                "project": self.project,
                "specification": self.specification,
                "files_scanned": self.files_scanned,
                "errors": self.errors,
                "warnings": self.warnings,
                "findings": [asdict(finding) for finding in self.findings],
            },
            indent=2,
            sort_keys=True,
        )


class Validator:
    def __init__(
        self,
        project: Path,
        specification: Path,
        *,
        allow_mutable_image: bool,
        allow_binary_input: bool,
    ) -> None:
        self.project_arg = project.absolute()
        self.specification_arg = specification
        self.allow_mutable_image = allow_mutable_image
        self.allow_binary_input = allow_binary_input
        self.findings: list[Finding] = []
        self.files_scanned = 0
        self.project = self.project_arg
        if specification.is_absolute():
            self.specification = specification.absolute()
        else:
            self.specification = self.project / specification

    def add(self, level: str, code: str, location: str | Path, message: str) -> None:
        location_text = self.display(location) if isinstance(location, Path) else location
        self.findings.append(Finding(level, code, location_text, message))

    def display(self, path: Path) -> str:
        try:
            return path.absolute().relative_to(self.project_arg).as_posix() or "."
        except ValueError:
            return str(path)

    def run(self) -> ValidationReport:
        if not self._validate_project_root():
            return self._report()
        data, yaml_text = self._load_specification()
        if data is not None and yaml_text is not None:
            self._validate_schema(data)
        self._scan_inventory()
        return self._report()

    def _report(self) -> ValidationReport:
        return ValidationReport(
            project=str(self.project),
            specification=self.display(self.specification),
            files_scanned=self.files_scanned,
            findings=tuple(self.findings),
        )

    def _validate_project_root(self) -> bool:
        symlink = _first_symlink_component(self.project_arg)
        if symlink is not None:
            self.add(
                "ERROR",
                "PROJECT_SYMLINK",
                symlink,
                "project path contains a symlink",
            )
            return False
        if not self.project_arg.exists():
            self.add(
                "ERROR", "PROJECT_MISSING", self.project_arg, "project directory does not exist"
            )
            return False
        if not self.project_arg.is_dir():
            self.add(
                "ERROR", "PROJECT_NOT_DIRECTORY", self.project_arg, "project is not a directory"
            )
            return False
        try:
            self.project = self.project_arg.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            self.add(
                "ERROR",
                "PROJECT_RESOLVE_FAILED",
                self.project_arg,
                _redacted_exception(exc),
            )
            return False
        spec_symlink = _first_symlink_component(self.specification)
        if spec_symlink is not None:
            self.add(
                "ERROR",
                "SPECIFICATION_SYMLINK",
                spec_symlink,
                "specification path contains a symlink",
            )
            return False
        try:
            resolved_specification = self.specification.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            self.add(
                "ERROR",
                "SPECIFICATION_RESOLVE_FAILED",
                self.specification,
                _redacted_exception(exc),
            )
            return False
        if not _is_within(resolved_specification, self.project):
            self.add(
                "ERROR",
                "SPECIFICATION_ESCAPE",
                self.specification,
                "specification must remain inside the project",
            )
            return False
        return True

    def _load_specification(self) -> tuple[dict[str, Any] | None, str | None]:
        if not self.specification.is_file():
            self.add(
                "ERROR",
                "SPECIFICATION_MISSING",
                self.specification,
                "REANA specification is missing or not a regular file",
            )
            return None, None
        size = self.specification.stat().st_size
        if size > MAX_YAML_BYTES:
            self.add(
                "ERROR",
                "SPECIFICATION_TOO_LARGE",
                self.specification,
                f"YAML exceeds the {MAX_YAML_BYTES}-byte validation limit",
            )
            return None, None
        try:
            text = self.specification.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self.add(
                "ERROR",
                "SPECIFICATION_NOT_UTF8",
                self.specification,
                "YAML must be UTF-8 text",
            )
            return None, None
        try:
            value = yaml.load(text, Loader=UniqueKeyLoader)
        except (yaml.YAMLError, DuplicateKeyError, RecursionError) as exc:
            self.add("ERROR", "YAML_INVALID", self.specification, _redacted_yaml_error(exc))
            return None, text
        if not isinstance(value, dict):
            self.add(
                "ERROR",
                "ROOT_NOT_MAPPING",
                self.specification,
                "YAML root must be a mapping",
            )
            return None, text
        return value, text

    def _validate_schema(self, data: dict[str, Any]) -> None:
        self._unknown_keys(data, ROOT_KEYS, "root")
        inputs = data.get("inputs", {})
        if inputs is None:
            inputs = {}
        if not isinstance(inputs, dict):
            self.add("ERROR", "INPUTS_NOT_MAPPING", self.specification, "inputs must be a mapping")
            inputs = {}
        else:
            self._unknown_keys(inputs, INPUT_KEYS, "inputs")

        outputs = data.get("outputs", {})
        if outputs is None:
            outputs = {}
        if not isinstance(outputs, dict):
            self.add(
                "ERROR", "OUTPUTS_NOT_MAPPING", self.specification, "outputs must be a mapping"
            )
            outputs = {}
        else:
            self._unknown_keys(outputs, OUTPUT_KEYS, "outputs")

        self._validate_declared_paths(inputs, outputs)

        workflow = data.get("workflow")
        if not isinstance(workflow, dict):
            self.add(
                "ERROR",
                "WORKFLOW_NOT_MAPPING",
                self.specification,
                "workflow is required and must be a mapping",
            )
            return
        self._unknown_keys(workflow, WORKFLOW_KEYS, "workflow")
        if workflow.get("type") != "serial":
            self.add(
                "ERROR",
                "WORKFLOW_NOT_SERIAL",
                self.specification,
                "this curated authoring workflow accepts only workflow.type: serial",
            )
            return
        specification = workflow.get("specification")
        if not isinstance(specification, dict):
            self.add(
                "ERROR",
                "SERIAL_SPEC_NOT_MAPPING",
                self.specification,
                "workflow.specification must be a mapping containing steps",
            )
            return
        self._unknown_keys(specification, SPECIFICATION_KEYS, "workflow.specification")
        steps = specification.get("steps")
        if not isinstance(steps, list) or not steps:
            self.add(
                "ERROR",
                "STEPS_INVALID",
                self.specification,
                "workflow.specification.steps must be a non-empty list",
            )
            return
        names: set[str] = set()
        for index, step in enumerate(steps):
            location = f"{self.display(self.specification)}#step-{index + 1}"
            if not isinstance(step, dict):
                self.add("ERROR", "STEP_NOT_MAPPING", location, "step must be a mapping")
                continue
            self._unknown_keys(step, STEP_KEYS, f"step {index + 1}")
            name = step.get("name")
            if name is not None and (not isinstance(name, str) or not name.strip()):
                self.add("ERROR", "STEP_NAME_INVALID", location, "name must be a non-empty string")
            elif isinstance(name, str):
                if name in names:
                    self.add(
                        "ERROR",
                        "STEP_NAME_DUPLICATE",
                        location,
                        "step name is duplicated",
                    )
                names.add(name)
            self._validate_environment(step.get("environment"), location)
            self._validate_commands(step.get("commands"), location)

    def _unknown_keys(
        self,
        value: dict[Any, Any],
        allowed: set[str],
        location: str,
    ) -> None:
        unknown_count = sum(key not in allowed for key in value)
        for _ in range(unknown_count):
            self.add(
                "ERROR",
                "UNKNOWN_KEY",
                f"{self.display(self.specification)}#{location}",
                "unsupported or misplaced key; source value withheld",
            )

    def _validate_declared_paths(
        self,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> None:
        seen: dict[str, str] = {}
        for section, mapping, kind, must_exist in (
            ("inputs.files", inputs, "files", True),
            ("inputs.directories", inputs, "directories", True),
            ("outputs.files", outputs, "files", False),
            ("outputs.directories", outputs, "directories", False),
        ):
            values = mapping.get(kind, [])
            if values is None:
                values = []
            if not isinstance(values, list):
                self.add(
                    "ERROR",
                    "PATH_LIST_INVALID",
                    f"{self.display(self.specification)}#{section}",
                    f"{section} must be a list",
                )
                continue
            for index, raw_path in enumerate(values):
                location = f"{self.display(self.specification)}#{section}[{index}]"
                relative = _safe_relative_path(raw_path)
                if relative is None:
                    self.add(
                        "ERROR",
                        "PATH_UNSAFE",
                        location,
                        "path must be a normalized, portable relative path",
                    )
                    continue
                if any(part in EXCLUDED_INVENTORY_DIRS for part in relative.parts):
                    self.add(
                        "ERROR",
                        "PATH_EXCLUDED_COMPONENT",
                        location,
                        "declared paths cannot cross a directory excluded from inventory scanning",
                    )
                    continue
                key = relative.as_posix()
                if key in seen:
                    self.add(
                        "ERROR",
                        "PATH_DUPLICATE",
                        location,
                        f"path is already declared under {seen[key]}",
                    )
                    continue
                seen[key] = section
                candidate = self.project / relative
                symlink = _first_symlink_component(candidate)
                if symlink is not None:
                    self.add("ERROR", "PATH_SYMLINK", symlink, "declared path contains a symlink")
                    continue
                try:
                    resolved = candidate.resolve(strict=False)
                except (OSError, RuntimeError) as exc:
                    self.add("ERROR", "PATH_RESOLVE_FAILED", location, _redacted_exception(exc))
                    continue
                if not _is_within(resolved, self.project):
                    self.add("ERROR", "PATH_ESCAPE", location, "path resolves outside the project")
                    continue
                if must_exist:
                    if not candidate.exists():
                        self.add(
                            "ERROR", "INPUT_MISSING", candidate, "declared input does not exist"
                        )
                        continue
                    mode = candidate.stat().st_mode
                    if kind == "files" and not stat.S_ISREG(mode):
                        self.add(
                            "ERROR", "INPUT_NOT_FILE", candidate, "declared file is not regular"
                        )
                    if kind == "directories" and not stat.S_ISDIR(mode):
                        self.add(
                            "ERROR",
                            "INPUT_NOT_DIRECTORY",
                            candidate,
                            "declared directory is not a directory",
                        )
                elif candidate.exists():
                    mode = candidate.stat().st_mode
                    expected = stat.S_ISREG(mode) if kind == "files" else stat.S_ISDIR(mode)
                    if not expected:
                        self.add(
                            "ERROR",
                            "OUTPUT_TYPE_MISMATCH",
                            candidate,
                            f"existing output does not match declared {kind[:-1]} type",
                        )

    def _validate_environment(self, image: Any, location: str) -> None:
        if not isinstance(image, str):
            self.add(
                "ERROR",
                "IMAGE_INVALID",
                location,
                "environment must be one non-empty OCI reference without whitespace",
            )
            return
        finding = _validate_image_reference(image, self.allow_mutable_image)
        if finding is not None:
            level, code, message = finding
            self.add(level, code, location, message)

    def _validate_commands(self, commands: Any, location: str) -> None:
        if (
            not isinstance(commands, list)
            or not commands
            or not all(isinstance(command, str) and command.strip() for command in commands)
        ):
            self.add(
                "ERROR",
                "COMMANDS_INVALID",
                location,
                "commands must be a non-empty list of non-empty strings",
            )
            return
        for index, command in enumerate(commands):
            command_location = f"{location}.commands[{index}]"
            if RUNTIME_INSTALL.search(command):
                self.add(
                    "ERROR",
                    "RUNTIME_INSTALL",
                    command_location,
                    "bake dependencies into the runtime image instead of installing at runtime",
                )
            if HEREDOC.search(command):
                self.add(
                    "ERROR",
                    "INLINE_HEREDOC",
                    command_location,
                    "move substantial code into a separate declared input file",
                )
            if REMOTE_REANA_OPERATION.search(command):
                self.add(
                    "ERROR",
                    "RECURSIVE_REANA_OPERATION",
                    command_location,
                    "workflow commands must not operate another REANA workflow",
                )

    def _scan_inventory(self) -> None:
        for current_root, dirnames, filenames in os.walk(self.project, followlinks=False):
            current = Path(current_root)
            retained_dirs: list[str] = []
            for dirname in sorted(dirnames):
                path = current / dirname
                if path.is_symlink():
                    self.add("ERROR", "INVENTORY_SYMLINK", path, "project contains a symlink")
                    continue
                if dirname in EXCLUDED_INVENTORY_DIRS:
                    continue
                retained_dirs.append(dirname)
            dirnames[:] = retained_dirs
            for filename in sorted(filenames):
                path = current / filename
                if self.files_scanned >= MAX_INVENTORY_FILES:
                    self.add(
                        "ERROR",
                        "INVENTORY_LIMIT",
                        self.project,
                        f"project exceeds the {MAX_INVENTORY_FILES}-file scan limit",
                    )
                    return
                self.files_scanned += 1
                if path.is_symlink():
                    self.add("ERROR", "INVENTORY_SYMLINK", path, "project contains a symlink")
                    continue
                try:
                    mode = path.stat().st_mode
                except OSError as exc:
                    self.add("ERROR", "INVENTORY_STAT_FAILED", path, _single_line(str(exc)))
                    continue
                if not stat.S_ISREG(mode):
                    self.add(
                        "ERROR",
                        "INVENTORY_SPECIAL_FILE",
                        path,
                        "project contains a non-regular file",
                    )
                    continue
                if _sensitive_filename(path.name):
                    self.add(
                        "ERROR",
                        "SENSITIVE_FILENAME",
                        path,
                        "sensitive filename requires removal or explicit out-of-band handling",
                    )
                size = path.stat().st_size
                declared_input = self._is_declared_input(path)
                if size > MAX_TEXT_SCAN_BYTES:
                    if declared_input and not self.allow_binary_input:
                        self.add(
                            "ERROR",
                            "INPUT_CONTENT_REVIEW",
                            path,
                            "declared input exceeds the content-scan limit and "
                            "requires manual review plus an explicit waiver",
                        )
                    else:
                        self.add(
                            "WARNING",
                            "FILE_NOT_CONTENT_SCANNED",
                            path,
                            f"file exceeds the {MAX_TEXT_SCAN_BYTES}-byte content-scan limit",
                        )
                    continue
                try:
                    raw = path.read_bytes()
                except OSError as exc:
                    self.add("ERROR", "INVENTORY_READ_FAILED", path, _single_line(str(exc)))
                    continue
                if _looks_binary(raw):
                    self._record_binary_review(path, declared_input)
                    continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    if declared_input:
                        self._record_binary_review(path, True)
                    else:
                        self.add(
                            "WARNING",
                            "NON_UTF8_NOT_SCANNED",
                            path,
                            "non-UTF-8 file requires manual secret review",
                        )
                    continue
                self._scan_text(path, text)

    def _record_binary_review(self, path: Path, declared_input: bool) -> None:
        if not declared_input:
            self.add(
                "WARNING",
                "UNDECLARED_BINARY_REVIEW",
                path,
                "undeclared binary project file requires manual review",
            )
            return
        if self.allow_binary_input:
            self.add(
                "WARNING",
                "BINARY_INPUT_WAIVER",
                path,
                "binary declared input accepted after explicit manual-review waiver",
            )
        else:
            self.add(
                "ERROR",
                "BINARY_INPUT_REVIEW",
                path,
                "binary declared input requires manual review and an explicit waiver",
            )

    def _is_declared_input(self, path: Path) -> bool:
        if not self.specification.is_file():
            return False
        try:
            data = yaml.load(
                self.specification.read_text(encoding="utf-8"),
                Loader=UniqueKeyLoader,
            )
        except (
            OSError,
            UnicodeDecodeError,
            yaml.YAMLError,
            DuplicateKeyError,
            RecursionError,
        ):
            return False
        if not isinstance(data, dict) or not isinstance(data.get("inputs"), dict):
            return False
        inputs = data["inputs"]
        for kind in ("files", "directories"):
            values = inputs.get(kind, [])
            if not isinstance(values, list):
                continue
            for raw_path in values:
                relative = _safe_relative_path(raw_path)
                if relative is None:
                    continue
                try:
                    candidate = (self.project / relative).resolve(strict=False)
                    resolved_path = path.resolve(strict=False)
                except (OSError, RuntimeError):
                    continue
                if kind == "files" and candidate == resolved_path:
                    return True
                if kind == "directories" and _is_within(resolved_path, candidate):
                    return True
        return False

    def _scan_text(self, path: Path, text: str) -> None:
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                self.add(
                    "ERROR",
                    "POSSIBLE_SECRET",
                    f"{self.display(path)}:{line}",
                    f"possible {label}; matching value withheld",
                )


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _redacted_exception(exc: BaseException) -> str:
    return f"{type(exc).__name__}; source value withheld"


def _redacted_yaml_error(exc: BaseException) -> str:
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is not None:
        return (
            f"{type(exc).__name__} at line {mark.line + 1}, "
            f"column {mark.column + 1}; source text withheld"
        )
    if isinstance(exc, DuplicateKeyError):
        line_match = re.search(r"\bline (\d+)\b", str(exc))
        if line_match:
            return f"{type(exc).__name__} at line {line_match.group(1)}; source text withheld"
    return f"{type(exc).__name__}; source text withheld"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _first_symlink_component(path: Path) -> Path | None:
    path = path.absolute()
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return current
    return None


def _safe_relative_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value or "\x00" in value or value.startswith("~"):
        return None
    path = Path(value)
    if (
        path.is_absolute()
        or path == Path(".")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return None
    if any(not SAFE_COMPONENT.fullmatch(part) for part in path.parts):
        return None
    return path


def _sensitive_filename(name: str) -> bool:
    lower = name.lower()
    return (
        lower in SENSITIVE_FILENAMES
        or lower.endswith((".key", ".p12", ".pfx", ".pem"))
        or lower.startswith(".env.")
    )


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    sample = raw[:8192]
    if b"\x00" in sample:
        return True
    control_bytes = sum(byte < 32 and byte not in {9, 10, 13} or byte == 127 for byte in sample)
    return control_bytes / len(sample) > 0.10


def _validate_image_reference(
    image: str,
    allow_mutable_image: bool,
) -> tuple[str, str, str] | None:
    if (
        not image
        or len(image) > 512
        or any(character.isspace() or ord(character) < 32 for character in image)
        or "<" in image
        or ">" in image
        or "REPLACE" in image.upper()
        or image.count("@") > 1
    ):
        return (
            "ERROR",
            "IMAGE_INVALID",
            "environment must be one conservative OCI image reference",
        )

    if "@" in image:
        name_and_tag, digest = image.split("@", 1)
        if not IMAGE_DIGEST.fullmatch(digest):
            return (
                "ERROR",
                "IMAGE_DIGEST_INVALID",
                "image digest must be sha256 followed by exactly 64 lowercase hex characters",
            )
    else:
        name_and_tag = image
        digest = None

    if (
        not name_and_tag
        or name_and_tag.startswith("/")
        or name_and_tag.endswith("/")
        or "//" in name_and_tag
    ):
        return ("ERROR", "IMAGE_INVALID", "image repository name is malformed")

    components = name_and_tag.split("/")
    last_component = components[-1]
    if ":" in last_component:
        repository_name, tag = last_component.rsplit(":", 1)
        components[-1] = repository_name
        if not IMAGE_TAG.fullmatch(tag):
            return ("ERROR", "IMAGE_TAG_INVALID", "image tag is malformed")
    else:
        tag = None

    first = components[0]
    if len(components) > 1 and ("." in first or ":" in first or first == "localhost"):
        if not _valid_image_registry(first):
            return ("ERROR", "IMAGE_REGISTRY_INVALID", "image registry is malformed")
        repository_components = components[1:]
    else:
        repository_components = components
    if not repository_components or any(
        not IMAGE_NAME_COMPONENT.fullmatch(component) for component in repository_components
    ):
        return ("ERROR", "IMAGE_REPOSITORY_INVALID", "image repository name is malformed")

    if tag is not None and tag.lower() == "latest":
        return ("ERROR", "IMAGE_LATEST", "latest images are not accepted")
    if digest is not None:
        return None
    if tag is None:
        return (
            "ERROR",
            "IMAGE_UNVERSIONED",
            "image must include a version or build tag",
        )
    if allow_mutable_image:
        return (
            "WARNING",
            "IMAGE_MUTABLE_WAIVER",
            "tag-only image accepted by explicit waiver; record its resolved digest",
        )
    return (
        "ERROR",
        "IMAGE_NOT_DIGEST_PINNED",
        "pin the image by digest or use the explicit mutable-image waiver",
    )


def _valid_image_registry(value: str) -> bool:
    if not IMAGE_REGISTRY.fullmatch(value):
        return False
    if ":" in value:
        host, port_text = value.rsplit(":", 1)
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65_535:
            return False
    else:
        host = value
    if host == "localhost":
        return True
    return len(host) <= 253 and all(1 <= len(label) <= 63 for label in host.split("."))


def scaffold(args: argparse.Namespace) -> int:
    project = Path(args.project).absolute()
    parent = project.parent
    if not parent.is_dir():
        raise ValueError("project parent directory must already exist")
    symlink = _first_symlink_component(parent)
    if symlink is not None:
        raise ValueError(f"project parent contains a symlink: {symlink}")
    script_path = _safe_relative_path(args.script)
    output_path = _safe_relative_path(args.output)
    if script_path is None or output_path is None:
        raise ValueError("script and output must be normalized portable relative paths")
    if script_path.suffix != ".py":
        raise ValueError("the bundled scaffold requires a .py script path")
    if script_path == Path("reana.yaml"):
        raise ValueError("script path cannot replace reana.yaml")
    if output_path == Path("reana.yaml") or output_path == script_path:
        raise ValueError("output path must differ from reana.yaml and the input script")
    image_finding = _validate_image_reference(args.image, args.allow_mutable_image)
    if image_finding is not None and image_finding[0] == "ERROR":
        raise ValueError(image_finding[2])
    if project.exists():
        if project.is_symlink() or not project.is_dir():
            raise ValueError("project must be a real directory")
        if any(project.iterdir()):
            raise ValueError("project directory must be empty; overwrite is never implicit")
    else:
        project.mkdir()

    specification = project / "reana.yaml"
    script = project / script_path
    if specification.exists() or script.exists():
        raise ValueError("refusing to overwrite an existing scaffold file")
    script.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "inputs": {"files": [script_path.as_posix()]},
        "workflow": {
            "type": "serial",
            "specification": {
                "steps": [
                    {
                        "name": "analysis",
                        "environment": args.image,
                        "commands": [
                            f'cd "${{REANA_WORKSPACE:?}}" && python {script_path.as_posix()}'
                        ],
                    }
                ]
            },
        },
        "outputs": {"files": [output_path.as_posix()]},
    }
    specification.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    script.write_text(
        "\n".join(
            [
                '"""Replace this offline placeholder with the declared analysis."""',
                "",
                "from pathlib import Path",
                "",
                f'OUTPUT = Path("{output_path.as_posix()}")',
                "",
                "",
                "def main() -> None:",
                "    OUTPUT.parent.mkdir(parents=True, exist_ok=True)",
                '    OUTPUT.write_text("Replace with reproducible analysis '
                'output.\\n", encoding="utf-8")',
                "",
                "",
                'if __name__ == "__main__":',
                "    main()",
                "",
            ]
        ),
        encoding="utf-8",
    )
    report = Validator(
        project,
        Path("reana.yaml"),
        allow_mutable_image=args.allow_mutable_image,
        allow_binary_input=False,
    ).run()
    _print_report(report, as_json=False, strict=False)
    return 1 if report.errors else 0


def validate(args: argparse.Namespace) -> int:
    report = Validator(
        Path(args.project),
        Path(args.specification),
        allow_mutable_image=args.allow_mutable_image,
        allow_binary_input=args.allow_binary_input,
    ).run()
    return _print_report(report, as_json=args.json, strict=args.strict)


def _print_report(report: ValidationReport, *, as_json: bool, strict: bool) -> int:
    if as_json:
        print(report.to_json())
    else:
        for finding in report.findings:
            print(finding.render())
        print(
            f"SUMMARY files={report.files_scanned} "
            f"errors={report.errors} warnings={report.warnings}"
        )
    if report.errors or (strict and report.warnings):
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scaffold and validate local REANA Serial workflow projects."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold_parser = subparsers.add_parser(
        "scaffold",
        help="create a new local single-step Serial workflow without running it",
    )
    scaffold_parser.add_argument("--project", required=True)
    scaffold_parser.add_argument("--image", required=True)
    scaffold_parser.add_argument("--script", default="analysis.py")
    scaffold_parser.add_argument("--output", default="results/summary.json")
    scaffold_parser.add_argument("--allow-mutable-image", action="store_true")
    scaffold_parser.set_defaults(handler=scaffold)

    validate_parser = subparsers.add_parser(
        "validate",
        help="read-only conservative preflight for a local Serial workflow",
    )
    validate_parser.add_argument("--project", required=True)
    validate_parser.add_argument("--specification", default="reana.yaml")
    validate_parser.add_argument("--allow-mutable-image", action="store_true")
    validate_parser.add_argument("--allow-binary-input", action="store_true")
    validate_parser.add_argument("--strict", action="store_true")
    validate_parser.add_argument("--json", action="store_true")
    validate_parser.set_defaults(handler=validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ValueError as exc:
        print(f"ERROR ARGUMENT: {_single_line(str(exc))}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as exc:
        print(f"ERROR ARGUMENT: {_redacted_exception(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
