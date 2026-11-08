"""CI install smoke: a built artifact installs and reports as a package install.

Spec #92 testing seam 5 is a CI smoke test, not a pytest, but the logic lives in
``scripts/ci_install_smoke.py`` so it can be exercised offline here.  Every test
builds a real package with :mod:`hostrun.distribution` (synthetic source tree and
synthetic runtime) and runs the smoke script in a subprocess against temporary
code/archive/skills directories.  Nothing reaches the network and the workflow's
real-run step is only parsed elsewhere.
"""

from __future__ import annotations

import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from hostrun import distribution

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci_install_smoke.py"

PACKAGE_FILES = {
    "agent/dist/main.js": "// bundled agent fixture\n",
    "common.py": "RUNTIME = True\n",
    "requirements.txt": "PyYAML>=6.0\n",
    "README.md": "# fixture horizon\n",
    "LICENSE": "MIT\n",
    "hostrun/__init__.py": "",
    "hostrun/cli.py": "CLI = 1\n",
    "hostrun/base.py": "VALUE = 1\n",
    "skills/horizon-run/SKILL.md": "---\nname: horizon-run\n---\n",
    "skills/horizon-run/references/installation.md": "# install\n",
    "skills/horizon-run/references/workflow.md": "# workflow\n",
    "contracts/README.md": "contracts\n",
    "docs/adr/0010-distribution.md": "# decision\n",
}


def _host_compat() -> dict[str, str]:
    return {
        "os": distribution.normalize_os(sys.platform),
        "arch": distribution.normalize_arch(platform.machine()),
        "dsh": "*",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _write_runtime(root: Path) -> Path:
    """The CPython embeddable layout the builder copies verbatim."""
    (root / "DLLs").mkdir(parents=True)
    (root / "Lib" / "site-packages").mkdir(parents=True)
    (root / "node.exe").write_bytes(b"MZ fixture")
    (root / "python.exe").write_bytes(b"MZ fake interpreter")
    (root / "python313.dll").write_bytes(b"dll")
    (root / "python313._pth").write_text("python313.zip\r\n.\r\n", encoding="utf-8")
    (root / "python313.zip").write_bytes(b"PK fake stdlib")
    (root / "DLLs" / "select.pyd").write_bytes(b"pyd")
    (root / "Lib" / "site-packages" / "yaml").mkdir()
    (root / "Lib" / "site-packages" / "yaml" / "__init__.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    return root


def _build(tmp_path: Path) -> dict:
    source = _write_tree(tmp_path / "src", PACKAGE_FILES)
    runtime = _write_runtime(tmp_path / "runtime")
    return distribution.build(
        source=source,
        out=tmp_path / "dist",
        version="0.1.0",
        channel="stable",
        commit="cafebabe",
        compat=_host_compat(),
        runtime_dir=runtime,
    )


def _smoke_args(built: dict, smoke: Path, **overrides: str) -> list[str]:
    values = {
        "--zip": str(built["artifact"]["path"]),
        "--code": str(smoke / "code"),
        "--archive": str(smoke / "archive"),
        "--skills-target": str(smoke / "host-skills" / "horizon-run"),
        "--bootstrap": str(smoke / "bootstrap"),
    }
    values.update(overrides)
    return [item for pair in values.items() for item in pair]


def _run_smoke(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


def _payload(result: subprocess.CompletedProcess) -> dict:
    assert result.stdout.strip(), result.stderr
    return json.loads(result.stdout)


def test_ci_install_smoke_installs_verifies_and_reports_a_package(tmp_path):
    built = _build(tmp_path)
    smoke = tmp_path / "smoke"

    result = _run_smoke(*_smoke_args(built, smoke))

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    payload = _payload(result)
    assert payload["ok"] is True
    assert payload["sha256"] == built["artifact"]["sha256"]
    assert payload["version"] == "0.1.0"
    assert payload["channel"] == "stable"

    code = smoke / "code"
    lock = json.loads(
        (code / ".horizon" / "dist.lock.json").read_text(encoding="utf-8")
    )
    assert lock["distribution"] == "package"
    assert lock["version"] == "0.1.0"
    assert lock["channel"] == "stable"
    assert lock["artifact_sha256"] == built["artifact"]["sha256"]
    assert (code / "manifest.json").is_file()
    assert (smoke / "bootstrap" / "runtime" / "python.exe").is_file()
    # `install skill` writes the host pointer; `console install` writes its script.
    assert (
        smoke / "host-skills" / "horizon-run" / "SKILL.md"
    ).is_file()
    assert (code / "bin" / "horizon-console.cmd").is_file()

    commands = {row["name"]: row for row in payload["commands"]}
    assert commands["install"]["status"] == "ok"
    assert commands["verify"]["status"] == "verified"
    assert commands["console"]["status"] == "ok"
    assert commands["console"]["processes_started"] is False
    assert commands["skill"]["ok"] is True
    assert commands["report"]["release"]["distribution"] == "package"
    assert commands["report"]["release"]["version"] == "0.1.0"
    assert commands["report"]["steps"]["skill"]["status"] == "ok"
    # The wrapper-execution check is opt-in; without --check-wrapper this smoke
    # stays a pure install check and never executes the synthetic runtime.
    assert "wrapper" not in commands
    assert "wrapper" not in payload


def test_ci_install_smoke_fails_on_a_digest_mismatch_before_installing(tmp_path):
    built = _build(tmp_path)
    smoke = tmp_path / "smoke"

    result = _run_smoke(
        *_smoke_args(built, smoke, **{"--sha256": "0" * 64}),
    )

    assert result.returncode == 1
    payload = _payload(result)
    assert payload["ok"] is False
    assert "sha256" in payload["error"].lower()
    assert not (smoke / "code" / ".horizon" / "dist.lock.json").exists()


def test_ci_install_smoke_refuses_a_non_empty_code_directory(tmp_path):
    built = _build(tmp_path)
    smoke = tmp_path / "smoke"
    code = smoke / "code"
    code.mkdir(parents=True)
    (code / "keep.txt").write_text("user data\n", encoding="utf-8")

    result = _run_smoke(*_smoke_args(built, smoke))

    assert result.returncode == 1
    payload = _payload(result)
    assert payload["ok"] is False
    assert "empty" in payload["error"].lower()
    assert (code / "keep.txt").read_text(encoding="utf-8") == "user data\n"
    assert not (code / ".horizon" / "dist.lock.json").exists()


def _load_smoke_script():
    """Import the smoke script so its helper seams can be tested without a runtime."""
    spec = importlib.util.spec_from_file_location("ci_install_smoke_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wrapper_environment_cannot_leak_the_runner_python(monkeypatch):
    """The wrapper check must not inherit PYTHONHOME/PYTHONPATH or a venv."""
    script = _load_smoke_script()
    monkeypatch.setenv("PYTHONHOME", r"C:\runner\python")
    monkeypatch.setenv("PYTHONPATH", r"C:\runner\python\Lib")
    monkeypatch.setenv("PYTHONSTARTUP", r"C:\runner\startup.py")
    monkeypatch.setenv("VIRTUAL_ENV", r"C:\runner\.venv")
    monkeypatch.setenv("CONDA_PREFIX", r"C:\runner\conda")
    monkeypatch.setenv("PYTHONEXECUTABLE", r"C:\runner\python.exe")

    environment = script.wrapper_environment()

    assert not [name for name in environment if name.upper().startswith("PYTHON")]
    assert "VIRTUAL_ENV" not in environment
    assert "CONDA_PREFIX" not in environment
    root = environment["SystemRoot"]
    assert environment["PATH"] == rf"{root}\System32;{root}"


def test_wrapper_check_refuses_without_a_wrapper_or_bundled_python(tmp_path):
    """No real embedded runtime offline: the helper must refuse, not execute."""
    script = _load_smoke_script()
    code = tmp_path / "code"

    with pytest.raises(script.SmokeError) as excinfo:
        script.run_wrapper_check(code, tmp_path / "archive")
    assert excinfo.value.step == "wrapper"
    assert "horizon.cmd" in excinfo.value.message

    (code / "bin").mkdir(parents=True)
    (code / "bin" / "horizon.cmd").write_text("@echo off\r\n", encoding="ascii")
    with pytest.raises(script.SmokeError) as excinfo:
        script.run_wrapper_check(code, tmp_path / "archive")
    assert excinfo.value.step == "wrapper"
    assert "runtime" in excinfo.value.message.lower()


def test_ci_install_smoke_check_wrapper_fails_clearly_on_a_synthetic_runtime(tmp_path):
    """The synthetic runtime's fake python.exe cannot print usage; the check says so."""
    built = _build(tmp_path)
    smoke = tmp_path / "smoke"

    result = _run_smoke(*_smoke_args(built, smoke), "--check-wrapper")

    assert result.returncode == 1, result.stdout + "\n" + result.stderr
    payload = _payload(result)
    assert payload["ok"] is False
    assert payload["step"] == "wrapper"
    assert "horizon.cmd" in payload["error"]
    # Captured output and the exact command go into the failure JSON so a CI log
    # can explain a broken wrapper without guessing.
    assert "stdout" in payload
    assert "stderr" in payload
    assert "returncode" in payload
    assert any("horizon.cmd" in part for part in payload["command"])
    # The check runs after install/skill, so the package and lock already exist.
    assert (smoke / "code" / "manifest.json").is_file()
    assert (smoke / "code" / ".horizon" / "dist.lock.json").is_file()
    assert (smoke / "host-skills" / "horizon-run" / "SKILL.md").is_file()
