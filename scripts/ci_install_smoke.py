#!/usr/bin/env python3
"""Install a built distribution artifact into a clean tree and prove it works.

Spec #92's fifth testing seam is a CI smoke test, not a pytest: on the Windows
runner, take the zip the release build just produced, extract it into a
throw-away bootstrap directory, then drive the real installation commands
against separate temporary directories:

    install package  --from <zip> --sha256 <artifact sha256>
    install verify
    console install (bundled Pi Agent)
    install skill    --root <code> --target <temp skills dir>
    <code>\\bin\\horizon.cmd --code <code> --archive <archive> --help
    install report

The wrapper check (``--check-wrapper``) is the real "no system Python" claim:
it executes the installed ``bin\\horizon.cmd`` through ``cmd.exe`` with a
sanitized environment (PATH is only ``%SystemRoot%\\System32;%SystemRoot%`` and
no ``PYTHONHOME``/``PYTHONPATH``/venv variable is inherited) and requires exit
code 0 plus ``usage`` on stdout.  A failure keeps the captured stdout/stderr in
the failure JSON.

The point is the *installed tree*, not the runner's Python: the runner's
interpreter executes the CLI from the source checkout, while the package is
installed into an otherwise empty code directory.  Every command's JSON is
checked, and the final report must describe a package installation whose
``skill`` step is ``ok``.  Other install probes may legitimately be
pending/blocked in CI (no Feishu, no host agent), so they are not asserted.

Exit status is 0 only when every check passes.  A failure prints one JSON object
with ``ok: false``, the failing ``step`` and a human-readable ``error`` on both
stdout and stderr, then exits 1.

The logic lives here (not in the workflow YAML) so it is testable offline:
``tests/test_release_ci_smoke.py`` builds a package with a synthetic runtime and
runs this script against ``tmp_path`` directories.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hostrun import release  # noqa: E402

REQUIRED_ARTIFACT_ENTRIES = (
    release.MANIFEST_NAME,
    f"{release.RUNTIME_DIRNAME}/{release.RUNTIME_PYTHON_NAME}",
    release.WRAPPER_REL,
)
CLI_TIMEOUT_SECONDS = 600
WRAPPER_TIMEOUT_SECONDS = 120
#: The only inherited variables the wrapper check may see.  Everything else --
#: especially PYTHONHOME/PYTHONPATH/PYTHONSTARTUP, VIRTUAL_ENV and CONDA_PREFIX --
#: is dropped so the installed wrapper cannot silently use the runner's Python.
_WRAPPER_ENV_NAMES = ("SystemDrive", "ComSpec", "PATHEXT", "TEMP", "TMP")


class SmokeError(Exception):
    """One smoke step failed with a reason a CI log can act on."""

    def __init__(self, step: str, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.step = step
        self.message = message
        self.details = dict(details or {})


# --------------------------------------------------------------------- helpers
def _emit(payload: dict) -> None:
    """Print the JSON summary as UTF-8 regardless of the console code page.

    Windows CI consoles use a legacy code page (cp1252 on the runner), and the
    summary legitimately contains Chinese notes from the CLI, so a plain
    ``print`` raises ``UnicodeEncodeError`` and hides the real result.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    except (AttributeError, OSError, ValueError):
        print(json.dumps(payload, ensure_ascii=True, indent=2))


def _empty_dir(path: Path, what: str) -> Path:
    """Require an absent or empty directory; never install over user data."""
    if path.exists():
        if not path.is_dir():
            raise SmokeError(what, f"{what} is not a directory: {path}")
        if any(path.iterdir()):
            raise SmokeError(
                what,
                f"{what} must be empty so the smoke test never overwrites an "
                f"existing installation: {path}",
            )
    else:
        try:
            path.mkdir(parents=True)
        except OSError as exc:
            raise SmokeError(what, f"could not create {what} {path}: {exc}") from exc
    return path


def _extract_artifact(zip_path: Path, bootstrap: Path) -> dict:
    """Extract the artifact safely and prove the release layout is present."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SmokeError("extract", f"artifact is not a readable zip: {exc}") from exc
    with archive:
        names = set()
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = PurePosixPath(info.filename.replace("\\", "/"))
            if (
                not relative.parts
                or relative.is_absolute()
                or ".." in relative.parts
                or ":" in relative.parts[0]
            ):
                raise SmokeError(
                    "extract", f"artifact contains an unsafe path: {info.filename!r}"
                )
            target = bootstrap.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
            names.add(relative.as_posix())
    missing = [name for name in REQUIRED_ARTIFACT_ENTRIES if name not in names]
    if missing:
        raise SmokeError(
            "extract",
            "artifact is missing required entries: " + ", ".join(missing),
        )
    manifest_path = bootstrap / release.MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SmokeError("extract", f"artifact manifest.json is unreadable: {exc}") from exc
    for field in ("version", "channel"):
        if not manifest.get(field):
            raise SmokeError("extract", f"artifact manifest.json has no {field}")
    return {"entries": len(names), "manifest": manifest}


def _run_cli(
    python: str,
    code: Path,
    archive: Path,
    argv: list[str],
    *,
    name: str,
    accept_nonzero: bool = False,
) -> dict:
    """Run one ``python -m hostrun.cli`` command and return its JSON payload.

    ``accept_nonzero`` is for ``install report``: it exits 1 while any install
    probe is pending/blocked (CI has no Feishu/host agent), but it still prints
    the report this smoke test asserts on.
    """
    command = [
        python,
        "-m",
        "hostrun.cli",
        "--code",
        str(code),
        "--archive",
        str(archive),
        *argv,
    ]
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=CLI_TIMEOUT_SECONDS,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeError(name, f"could not run {argv[0]}: {exc}") from exc
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    payload = None
    if stdout.strip():
        try:
            payload = json.loads(stdout)
        except ValueError:
            payload = None
    if completed.returncode != 0 and not accept_nonzero:
        detail = ""
        if isinstance(payload, dict):
            detail = str(payload.get("reason") or payload.get("error") or "")
        if not detail:
            detail = (stderr.strip() or stdout.strip() or "no output").splitlines()[0]
        raise SmokeError(
            name,
            f"{' '.join(argv)} exited {completed.returncode}: {detail}",
        )
    if not isinstance(payload, dict):
        raise SmokeError(
            name,
            f"{' '.join(argv)} did not print a JSON object: "
            f"{(stdout.strip() or stderr.strip())[:500]}",
        )
    return payload


def _require(payload: dict, *, name: str, **expected) -> None:
    for field, value in expected.items():
        actual = payload.get(field)
        if actual != value:
            raise SmokeError(
                name,
                f"{name} reported {field}={actual!r}, expected {value!r}",
            )


def _sha256(path: Path) -> str:
    return release.artifact_sha256(path)


def wrapper_environment() -> dict[str, str]:
    """A minimal wrapper environment that cannot leak the runner's Python.

    Built from scratch instead of copying ``os.environ``: only the Windows
    system variables needed by ``cmd.exe`` are inherited, ``PATH`` is restricted
    to ``%SystemRoot%\\System32;%SystemRoot%``, and no ``PYTHON*``, virtualenv or
    conda variable exists at all.
    """
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    environment = {
        "SystemRoot": root,
        "PATH": rf"{root}\System32;{root}",
    }
    for name in _WRAPPER_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.setdefault("ComSpec", rf"{root}\System32\cmd.exe")
    return environment


def run_wrapper_check(
    code: str | Path,
    archive: str | Path,
    *,
    timeout: float = WRAPPER_TIMEOUT_SECONDS,
) -> dict:
    """Execute the installed ``bin/horizon.cmd --help`` with a sanitized env.

    Refuses (as a ``wrapper`` step failure) when the installed tree has no
    launcher or no bundled interpreter, so an offline/synthetic runtime can
    never be mistaken for a real one and no system Python is used as a fallback.
    """
    code_dir = Path(code).expanduser().resolve()
    archive_dir = Path(archive).expanduser().resolve()
    wrapper = code_dir / release.WRAPPER_REL
    runtime_python = code_dir / release.RUNTIME_DIRNAME / release.RUNTIME_PYTHON_NAME
    if not wrapper.is_file():
        raise SmokeError(
            "wrapper",
            f"the installed package has no launcher wrapper: {wrapper}",
        )
    if not runtime_python.is_file():
        raise SmokeError(
            "wrapper",
            "the installed package has no bundled interpreter "
            f"({runtime_python}); refusing to fall back to a system Python",
        )
    environment = wrapper_environment()
    comspec = environment.get("ComSpec") or rf"{environment['SystemRoot']}\System32\cmd.exe"
    command = [
        comspec,
        "/d",
        "/c",
        str(wrapper),
        "--code",
        str(code_dir),
        "--archive",
        str(archive_dir),
        "--help",
    ]
    result = {
        "ok": False,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "command": command,
        "environment": {
            "PATH": environment["PATH"],
            "removed_python_environment": True,
        },
    }
    try:
        completed = subprocess.run(
            command,
            cwd=str(code_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["stderr"] = str(exc)
        raise SmokeError(
            "wrapper",
            f"could not run {wrapper} with a sanitized PATH: {exc}",
            details=result,
        ) from exc
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    result.update(
        ok=completed.returncode == 0,
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    if completed.returncode != 0:
        detail = (stderr.strip() or stdout.strip() or "no output").splitlines()[0]
        raise SmokeError(
            "wrapper",
            f"{release.WRAPPER_REL} --help exited {completed.returncode}: {detail}",
            details=result,
        )
    if "usage" not in stdout.lower():
        raise SmokeError(
            "wrapper",
            f"{release.WRAPPER_REL} --help did not print usage on stdout; "
            f"stdout={stdout.strip()[:200]!r}",
            details=result,
        )
    return result


# ------------------------------------------------------------------------ smoke
def run_smoke(
    zip_path: str | Path,
    *,
    code: str | Path,
    archive: str | Path,
    skills_target: str | Path,
    bootstrap: str | Path | None = None,
    python: str | None = None,
    expected_sha256: str | None = None,
    check_wrapper: bool = False,
) -> dict:
    """Run the whole install smoke and return a JSON-serialisable summary."""
    interpreter = str(python or sys.executable)
    artifact = Path(zip_path).expanduser().resolve()
    code_dir = Path(code).expanduser().resolve()
    archive_dir = Path(archive).expanduser().resolve()
    skills_dir = Path(skills_target).expanduser().resolve()

    if not artifact.is_file():
        raise SmokeError("artifact", f"artifact is missing: {artifact}")
    try:
        actual_sha = _sha256(artifact)
    except OSError as exc:
        raise SmokeError("artifact", f"could not read the artifact: {exc}") from exc
    expected = str(expected_sha256 or "").strip().lower()
    if expected and expected != actual_sha:
        raise SmokeError(
            "artifact",
            f"artifact sha256 mismatch: expected {expected}, got {actual_sha}",
        )
    _empty_dir(code_dir, "code")
    _empty_dir(skills_dir, "skills-target")
    if bootstrap is None:
        bootstrap_dir = Path(tempfile.mkdtemp(prefix="horizon-install-smoke-"))
    else:
        bootstrap_dir = Path(bootstrap).expanduser().resolve()
        _empty_dir(bootstrap_dir, "bootstrap")

    extracted = _extract_artifact(artifact, bootstrap_dir)
    manifest = extracted["manifest"]
    version = str(manifest["version"])
    channel = str(manifest["channel"])

    commands: list[dict] = []

    installed = _run_cli(
        interpreter,
        code_dir,
        archive_dir,
        ["install", "package", "--from", str(artifact), "--sha256", actual_sha],
        name="install",
    )
    _require(
        installed,
        name="install",
        ok=True,
        status="ok",
        distribution="package",
        version=version,
        channel=channel,
        artifact_sha256=actual_sha,
    )
    commands.append({"name": "install", **_command_view(installed)})

    verified = _run_cli(
        interpreter, code_dir, archive_dir, ["install", "verify"], name="verify"
    )
    _require(verified, name="verify", ok=True, status="verified", distribution="package")
    commands.append({"name": "verify", **_command_view(verified)})

    console = _run_cli(
        interpreter,
        code_dir,
        archive_dir,
        ["console", "install"],
        name="console",
    )
    _require(console, name="console", ok=True, status="ok", processes_started=False)
    commands.append({"name": "console", **_command_view(console)})

    skill = _run_cli(
        interpreter,
        code_dir,
        archive_dir,
        ["install", "skill", "--root", str(code_dir), "--target", str(skills_dir)],
        name="skill",
    )
    _require(skill, name="skill", ok=True)
    pointer = skills_dir / "SKILL.md"
    if not pointer.is_file():
        raise SmokeError("skill", f"install skill wrote no pointer: {pointer}")
    commands.append({"name": "skill", **_command_view(skill)})

    wrapper_result = None
    if check_wrapper:
        wrapper_result = run_wrapper_check(code_dir, archive_dir)
        commands.append({"name": "wrapper", **wrapper_result})
        # The wrapper just ran the installed CLI from the installed tree, which
        # writes ``__pycache__`` bytecode caches there.  Verify must still pass
        # afterwards: otherwise every real user's first verify would "fail" on
        # files the installation itself created.  This re-verify is what makes
        # that class of bug visible in CI instead of on a user's machine.
        reverified = _run_cli(
            interpreter,
            code_dir,
            archive_dir,
            ["install", "verify"],
            name="verify_after_wrapper",
        )
        _require(
            reverified,
            name="verify_after_wrapper",
            ok=True,
            status="verified",
            distribution="package",
        )
        commands.append(
            {"name": "verify_after_wrapper", **_command_view(reverified)}
        )

    report = _run_cli(
        interpreter,
        code_dir,
        archive_dir,
        ["install", "report"],
        name="report",
        accept_nonzero=True,
    )
    release_block = report.get("release") or {}
    _require(
        release_block,
        name="report.release",
        distribution="package",
        version=version,
        channel=channel,
    )
    skill_step = (report.get("steps") or {}).get("skill") or {}
    _require(skill_step, name="report.steps.skill", status="ok")
    blocked_steps = [
        row.get("step") for row in report.get("blocked") or [] if isinstance(row, dict)
    ]
    if "skill" in blocked_steps:
        raise SmokeError(
            "report", "the install report lists the skill step as blocked"
        )
    commands.append(
        {
            "name": "report",
            "release": release_block,
            "steps": report.get("steps") or {},
            "blocked": blocked_steps,
            **_command_view(report),
        }
    )

    lock = _read_lock(code_dir)
    _require(
        lock,
        name="lock",
        distribution="package",
        version=version,
        channel=channel,
        artifact_sha256=actual_sha,
    )

    summary = {
        "ok": True,
        "zip": str(artifact),
        "sha256": actual_sha,
        "version": version,
        "channel": channel,
        "bootstrap": str(bootstrap_dir),
        "code": str(code_dir),
        "archive": str(archive_dir),
        "skills_target": str(skills_dir),
        "artifact_entries": extracted["entries"],
        "commands": commands,
        "lock": str(_lock_path(code_dir)),
    }
    if wrapper_result is not None:
        summary["wrapper"] = wrapper_result
    return summary


def _lock_path(code: Path) -> Path:
    import hostrun.dist as dist_mod

    return dist_mod.lock_path(code)


def _read_lock(code: Path) -> dict:
    import hostrun.dist as dist_mod

    lock = dist_mod.read_lock(code)
    if not isinstance(lock, dict):
        raise SmokeError("lock", f"no readable install lock under {code}")
    return lock


def _command_view(payload: dict) -> dict:
    """Keep only the fields CI readers need; avoid duplicating huge payloads."""
    return {
        key: payload[key]
        for key in ("ok", "status", "reason", "distribution", "version", "channel")
        if key in payload
    } | {
        "processes_started": payload["processes_started"]
        if "processes_started" in payload
        else None
    }


# -------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/ci_install_smoke.py",
        description=(
            "Install a built distribution artifact into temporary directories "
            "and verify the install commands, console script, skill pointer and report."
        ),
    )
    parser.add_argument("--zip", required=True, help="built distribution artifact (.zip)")
    parser.add_argument("--code", required=True, help="empty code directory to install into")
    parser.add_argument("--archive", required=True, help="archive directory for the install")
    parser.add_argument(
        "--skills-target",
        required=True,
        help="empty host skills directory for the horizon-run pointer",
    )
    parser.add_argument(
        "--bootstrap",
        default=None,
        help="empty directory to extract the artifact into (default: a temp dir)",
    )
    parser.add_argument(
        "--python", default=None, help="interpreter for the CLI (default: this one)"
    )
    parser.add_argument(
        "--sha256",
        default=None,
        dest="expected_sha256",
        help="expected artifact SHA-256; defaults to the artifact's own digest",
    )
    parser.add_argument(
        "--check-wrapper",
        action="store_true",
        help=(
            "execute the installed bin\\horizon.cmd with a sanitized PATH and "
            "assert it prints usage (proves the bundled runtime needs no system Python)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_smoke(
            args.zip,
            code=args.code,
            archive=args.archive,
            skills_target=args.skills_target,
            bootstrap=args.bootstrap,
            python=args.python,
            expected_sha256=args.expected_sha256,
            check_wrapper=args.check_wrapper,
        )
    except SmokeError as exc:
        payload = {"ok": False, "step": exc.step, "error": exc.message, **exc.details}
        _emit(payload)
        print(f"install smoke failed at {exc.step}: {exc.message}", file=sys.stderr)
        return 1
    _emit(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
