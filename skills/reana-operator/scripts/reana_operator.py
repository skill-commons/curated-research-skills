#!/usr/bin/env python3
"""Run a fixed, read-only subset of native REANA client commands safely."""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MAX_CAPTURE_BYTES = 262_144
MAX_TOKEN_BYTES = 8_192
MAX_RAW_CAPTURE_BYTES = MAX_CAPTURE_BYTES + MAX_TOKEN_BYTES
WORKFLOW_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
ASSIGNMENT_RE = re.compile(
    r"""(?i)(?P<key_quote>["']?)(?P<key>access[_-]?token|api[_-]?key|"""
    r"""authorization|password|secret)(?P=key_quote)(?P<separator>\s*[:=]\s*)"""
    r"""(?P<value>\[REDACTED_TOKEN\]|"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}\]"']+)"""
)
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
URL_USERINFO_RE = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")


class OperatorError(RuntimeError):
    """A safe user-facing operator error."""


def _origin(value: str, variable_name: str) -> str:
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise OperatorError(f"{variable_name} contains a malformed HTTPS origin")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        port = parsed.port
    except (UnicodeError, ValueError):
        raise OperatorError(f"{variable_name} contains a malformed HTTPS origin") from None
    if parsed.scheme != "https":
        raise OperatorError(f"{variable_name} entries must use https")
    if not hostname or username is not None or password is not None:
        raise OperatorError(f"{variable_name} entries must name a host and contain no credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise OperatorError(f"{variable_name} entries must be HTTPS origins without paths")
    host = hostname.lower()
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host if port in (None, 443) else f"{rendered_host}:{port}"
    return urlunsplit(("https", netloc, "", "", ""))


def _server_url(value: str) -> str:
    return _origin(value, "REANA_SERVER_URL")


def _allowed_origins() -> set[str]:
    raw = os.environ.get("REANA_ALLOWED_SERVER_ORIGINS", "")
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    if not entries:
        raise OperatorError("REANA_ALLOWED_SERVER_ORIGINS is missing or empty")
    if any("*" in entry for entry in entries):
        raise OperatorError("REANA_ALLOWED_SERVER_ORIGINS does not permit wildcards")
    return {_origin(entry, "REANA_ALLOWED_SERVER_ORIGINS") for entry in entries}


def _client_path() -> Path:
    configured = os.environ.get("REANA_CLIENT_BIN")
    candidate = configured if configured else shutil.which("reana-client")
    if not candidate:
        raise OperatorError(
            "native reana-client was not found; provision a reviewed client environment"
        )
    path = Path(candidate).expanduser()
    if configured and not path.is_absolute():
        raise OperatorError("REANA_CLIENT_BIN must be an absolute path")
    path = path.resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise OperatorError("resolved reana-client is not an executable regular file")
    return path


def _credential_state() -> tuple[str | None, bool]:
    raw_server = os.environ.get("REANA_SERVER_URL")
    server = _server_url(raw_server) if raw_server else None
    return server, bool(os.environ.get("REANA_ACCESS_TOKEN"))


def _require_credentials() -> tuple[str, str]:
    server, token_present = _credential_state()
    missing = []
    if server is None:
        missing.append("REANA_SERVER_URL")
    if not token_present:
        missing.append("REANA_ACCESS_TOKEN")
    if not os.environ.get("REANA_ALLOWED_SERVER_ORIGINS"):
        missing.append("REANA_ALLOWED_SERVER_ORIGINS")
    if missing:
        names = ", ".join(missing)
        raise OperatorError(
            f"missing {names}; ask the user to inject it through an approved secret "
            "store or protected process environment, never through chat or a project file"
        )
    if server not in _allowed_origins():
        raise OperatorError("REANA_SERVER_URL is not in REANA_ALLOWED_SERVER_ORIGINS")
    token = os.environ["REANA_ACCESS_TOKEN"]
    try:
        token_bytes = token.encode("ascii")
    except UnicodeEncodeError:
        raise OperatorError("REANA_ACCESS_TOKEN must contain visible ASCII characters") from None
    if (
        not token_bytes
        or len(token_bytes) > MAX_TOKEN_BYTES
        or any(character < 33 or character > 126 for character in token_bytes)
    ):
        raise OperatorError(
            f"REANA_ACCESS_TOKEN must contain 1-{MAX_TOKEN_BYTES} visible ASCII characters"
        )
    return server, token


def _redact(text: str, token: str | None) -> str:
    if token:
        text = text.replace(token, "[REDACTED_TOKEN]")
    text = JWT_RE.sub("[REDACTED_JWT]", text)
    text = BEARER_RE.sub("Bearer [REDACTED]", text)
    text = URL_USERINFO_RE.sub(r"\1[REDACTED]@", text)
    text = ASSIGNMENT_RE.sub(_redact_assignment, text)
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    return text


def _redact_assignment(match: re.Match[str]) -> str:
    raw_value = match.group("value")
    quote = raw_value[0] if raw_value[:1] in {'"', "'"} else ""
    replacement = f"{quote}[REDACTED]{quote}"
    return (
        f"{match.group('key_quote')}{match.group('key')}{match.group('key_quote')}"
        f"{match.group('separator')}{replacement}"
    )


def _bounded(
    data: bytes,
    token: str | None = None,
    *,
    source_truncated: bool = False,
) -> tuple[str, bool]:
    if source_truncated or len(data) > MAX_RAW_CAPTURE_BYTES:
        return "[OUTPUT OMITTED: capture limit exceeded]", True
    decoded = data.decode("utf-8", errors="replace")
    sanitized = _redact(decoded, token)
    encoded = sanitized.encode("utf-8")
    truncated = len(data) > MAX_CAPTURE_BYTES or len(encoded) > MAX_CAPTURE_BYTES
    return encoded[:MAX_CAPTURE_BYTES].decode("utf-8", errors="replace"), truncated


def _containment_error() -> str | None:
    if os.name != "posix" or not hasattr(resource, "RLIMIT_NPROC"):
        return "POSIX RLIMIT_NPROC process containment is unavailable"
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        return "process containment is not enforceable while running as root"
    return None


def _constrain_child_process() -> None:
    # Prevent the credential-bearing native client from forking, cloning, or
    # detaching descendants outside the process group that this wrapper owns.
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def _run_bounded(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: int,
) -> tuple[subprocess.CompletedProcess[bytes], bool, bool]:
    """Run a command while draining, but never retaining, unbounded output."""
    containment_error = _containment_error()
    if containment_error:
        raise OperatorError(containment_error)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
            preexec_fn=_constrain_child_process,
        )
    except (OSError, subprocess.SubprocessError):
        raise OperatorError("failed to start the native client under process containment") from None
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    stdout_overflow = threading.Event()
    stderr_overflow = threading.Event()

    def drain(
        stream: object,
        destination: bytearray,
        overflow: threading.Event,
    ) -> None:
        try:
            while True:
                chunk = stream.read(65_536)  # type: ignore[attr-defined]
                if not chunk:
                    break
                remaining = MAX_RAW_CAPTURE_BYTES - len(destination)
                if remaining > 0:
                    destination.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
        except (OSError, ValueError):
            overflow.set()
        finally:
            with suppress(OSError, ValueError):
                stream.close()  # type: ignore[attr-defined]

    readers = [
        threading.Thread(
            target=drain,
            args=(process.stdout, stdout, stdout_overflow),
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=(process.stderr, stderr, stderr_overflow),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        for reader in readers:
            reader.join(timeout=2)
        raise

    for reader in readers:
        reader.join(timeout=2)
    if any(reader.is_alive() for reader in readers):
        # A background descendant retained a pipe after the client exited.
        stdout_overflow.set()
        stderr_overflow.set()
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        for reader in readers:
            reader.join(timeout=2)
    return (
        subprocess.CompletedProcess(command, return_code, bytes(stdout), bytes(stderr)),
        stdout_overflow.is_set(),
        stderr_overflow.is_set(),
    )


def _child_environment(home: str, server: str, token: str) -> dict[str, str]:
    environment = {
        "HOME": home,
        "PATH": os.environ.get("PATH", os.defpath),
        "REANA_SERVER_URL": server,
        "REANA_ACCESS_TOKEN": token,
    }
    for name in ("LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _version(client: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="crs-reana-version-home-") as temporary_home:
        completed, stdout_overflow, stderr_overflow = _run_bounded(
            [str(client), "version"],
            env={"HOME": temporary_home, "PATH": os.environ.get("PATH", os.defpath)},
            timeout=10,
        )
    output, _ = _bounded(
        completed.stdout + completed.stderr,
        source_truncated=stdout_overflow or stderr_overflow,
    )
    return output.strip() or f"unavailable (exit {completed.returncode})"


def _workflow(value: str) -> str:
    if not WORKFLOW_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "workflow must be 1-128 ASCII letters, digits, dots, underscores, or hyphens"
        )
    return value


def _limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 200:
        raise argparse.ArgumentTypeError("limit must be between 1 and 200")
    return parsed


def _native_arguments(args: argparse.Namespace) -> list[str]:
    if args.operation == "ping":
        return ["ping"]
    if args.operation == "info":
        return ["info", "--json"]
    if args.operation == "list":
        return ["list", "--json", "--page", "1", "--size", str(args.limit)]
    if args.operation == "status":
        return ["status", "--workflow", args.workflow, "--json"]
    if args.operation == "logs":
        return [
            "logs",
            "--workflow",
            args.workflow,
            "--json",
            "--page",
            "1",
            "--size",
            str(args.limit),
        ]
    if args.operation == "files":
        return [
            "ls",
            "--workflow",
            args.workflow,
            "--json",
            "--page",
            "1",
            "--size",
            str(args.limit),
        ]
    if args.operation == "usage":
        return ["du", "--workflow", args.workflow, "--summarize"]
    raise OperatorError(f"unsupported operation: {args.operation}")


def _doctor(json_envelope: bool = False) -> int:
    server, token_present = _credential_state()
    allowed = False
    allowlist_error = None
    try:
        allowed = bool(server and server in _allowed_origins())
    except OperatorError as error:
        allowlist_error = str(error)
    client: Path | None = None
    client_error: str | None = None
    client_version: str | None = None
    containment_error = _containment_error()
    try:
        client = _client_path()
    except OperatorError as error:
        client_error = str(error)
    if client is not None and containment_error is None:
        try:
            client_version = _version(client)
        except OperatorError as error:
            client_error = str(error)
    ready = bool(
        server
        and token_present
        and allowed
        and client is not None
        and client_error is None
        and containment_error is None
    )
    report = {
        "operator": "REANA read-only operator",
        "server": server,
        "server_allowlisted": allowed,
        "allowlist_error": allowlist_error,
        "access_token_present": token_present,
        "native_client": str(client) if client is not None else None,
        "native_client_error": client_error,
        "client_version": client_version,
        "process_containment": (
            "single-process RLIMIT_NPROC=0" if containment_error is None else containment_error
        ),
        "remote_command_policy": "read-only fixed allowlist",
        "ready": ready,
    }
    if json_envelope:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(report["operator"])
        print(f"  server: {server or '<unset>'}")
        print(f"  server allowlisted: {'yes' if allowed else 'no'}")
        if allowlist_error:
            print(f"  allowlist: invalid ({allowlist_error})")
        print(f"  access token present: {'yes' if token_present else 'no'}")
        if client is None:
            print(f"  native client: unavailable ({client_error})")
        else:
            print(f"  native client: {client}")
            print(f"  client version: {client_version}")
        print(
            "  process containment: "
            + (
                "single-process RLIMIT_NPROC=0"
                if containment_error is None
                else f"unavailable ({containment_error})"
            )
        )
        print("  remote command policy: read-only fixed allowlist")
    return 0 if ready else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json-envelope",
        action="store_true",
        help="emit one structured wrapper result instead of human preview lines",
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("doctor", help="inspect local readiness without contacting REANA")
    subparsers.add_parser("ping", help="read authenticated server status")
    subparsers.add_parser("info", help="read cluster information")
    list_parser = subparsers.add_parser("list", help="read workflow inventory")
    list_parser.add_argument("--limit", type=_limit, default=20)
    for name in ("status", "usage"):
        operation = subparsers.add_parser(name)
        operation.add_argument("--workflow", required=True, type=_workflow)
    for name in ("logs", "files"):
        operation = subparsers.add_parser(name)
        operation.add_argument("--workflow", required=True, type=_workflow)
        operation.add_argument("--limit", type=_limit, default=20 if name == "logs" else 50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == "doctor":
            return _doctor(args.json_envelope)
        server, token = _require_credentials()
        client = _client_path()
        native_args = _native_arguments(args)
        workflow = getattr(args, "workflow", None)
        if not args.json_envelope:
            print(f"Server: {server}")
            print(f"Operation: {args.operation}")
            if workflow:
                print(f"Workflow: {workflow}")
            print("Effect: one read-only request; no local or remote mutation")
        with tempfile.TemporaryDirectory(prefix="crs-reana-home-") as temporary_home:
            completed, stdout_overflow, stderr_overflow = _run_bounded(
                [str(client), *native_args],
                env=_child_environment(temporary_home, server, token),
                timeout=60,
            )
        stdout, stdout_truncated = _bounded(
            completed.stdout,
            token,
            source_truncated=stdout_overflow,
        )
        stderr, stderr_truncated = _bounded(
            completed.stderr,
            token,
            source_truncated=stderr_overflow,
        )
        sanitized_stdout = stdout.rstrip()
        sanitized_stderr = stderr.rstrip()
        truncated = stdout_truncated or stderr_truncated
        if args.json_envelope:
            stdout_value: object = sanitized_stdout or None
            if sanitized_stdout:
                with suppress(json.JSONDecodeError):
                    stdout_value = json.loads(sanitized_stdout)
            print(
                json.dumps(
                    {
                        "server": server,
                        "operation": args.operation,
                        "workflow": workflow,
                        "effect": "one read-only request; no local or remote mutation",
                        "native_exit_code": completed.returncode,
                        "stdout": stdout_value,
                        "stderr": sanitized_stderr or None,
                        "truncated": truncated,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            if sanitized_stdout:
                print(sanitized_stdout)
            if sanitized_stderr:
                print(sanitized_stderr, file=sys.stderr)
        if truncated:
            print("[OUTPUT TRUNCATED: narrow the read request]", file=sys.stderr)
            return 3
        return completed.returncode
    except (OperatorError, subprocess.TimeoutExpired) as error:
        safe_error = _redact(str(error), os.environ.get("REANA_ACCESS_TOKEN"))
        if getattr(args, "json_envelope", False):
            print(json.dumps({"error": safe_error}, sort_keys=True), file=sys.stderr)
        else:
            print(f"ERROR: {safe_error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
