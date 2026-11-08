"""Bundled runtime layout and launcher wrappers (#95).

A distribution package carries its own interpreter and a ``bin/horizon.cmd``
wrapper, so an installation needs no system Python and no PATH lookup.  These
tests pin the wrapper bytes, the deterministic runtime copy, the manifest
coverage and the console's package-mode delegation; a real "no system Python"
run stays acceptance evidence (#100/CI).
"""

from __future__ import annotations

import json
import platform
import re
import sys
from pathlib import Path

import pytest

from hostrun import console, dist, distribution, launcher

PACKAGE_FILES = {
    "common.py": "RUNTIME = True\n",
    "requirements.txt": "PyYAML>=6.0\n",
    "README.md": "# fixture horizon\n",
    "LICENSE": "MIT\n",
    "hostrun/__init__.py": "",
    "hostrun/cli.py": "CLI = 1\n",
    "hostrun/base.py": "VALUE = 1\n",
    "skills/horizon-run/SKILL.md": "---\nname: horizon-run\n---\n",
    "contracts/README.md": "contracts\n",
    "docs/adr/0010-distribution.md": "# decision\n",
}

#: The expected CI-produced layout: interpreter, DLL, path file, stdlib zip,
#: extension DLLs and site-packages.  Cache files must never ship.
RUNTIME_FILES = {
    "python.exe": b"MZ fake interpreter",
    "python313.dll": b"fake core dll",
    "python313._pth": b"python313.zip\n.\nLib\\site-packages\n",
    "python313.zip": b"fake stdlib zip",
    "DLLs/select.pyd": b"fake extension",
    "Lib/site-packages/yaml/__init__.py": b"YAML = 1\n",
    "Lib/site-packages/fitz/__init__.py": b"FITZ = 1\n",
    "__pycache__/stale.cpython-313.pyc": b"cache",
    "Lib/site-packages/yaml/__pycache__/__init__.cpython-313.pyc": b"cache",
    "Lib/site-packages/stray.pyc": b"cache",
}


def _write_tree(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
    return root


@pytest.fixture
def source(tmp_path):
    return _write_tree(tmp_path / "src", PACKAGE_FILES)


@pytest.fixture
def runtime_dir(tmp_path):
    return _write_tree(tmp_path / "runtime", RUNTIME_FILES)


def _host_compat() -> dict[str, str]:
    return {
        "os": distribution.normalize_os(sys.platform),
        "arch": distribution.normalize_arch(platform.machine()),
        "dsh": "*",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _build(source: Path, out: Path, *, runtime_dir: Path | None = None, **over):
    options = {
        "source": source,
        "out": out,
        "version": "0.1.0",
        "channel": "stable",
        "commit": "deadbeefcafe",
        "compat": _host_compat(),
    }
    if runtime_dir is not None:
        options["runtime_dir"] = runtime_dir
    options.update(over)
    return distribution.build(**options)


def _manifest(result: dict) -> dict:
    return json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))


def _listed(result: dict) -> set[str]:
    return {row["path"] for row in _manifest(result)["files"]}


# ------------------------------------------------------------ wrapper bytes
def test_wrapper_text_is_a_fully_quoted_no_path_launcher():
    text = launcher.wrapper_text()

    assert text.splitlines()[0] == "@echo off"
    assert text.splitlines() == [
        "@echo off",
        'cd /d "%~dp0.."',
        '"%~dp0..\\runtime\\python.exe" -m hostrun.cli %*',
    ]
    # The runtime is addressed relative to the wrapper, never through PATH.
    assert "%~dp0..\\runtime\\python.exe" in text
    # A bare ``python``/``py`` token would be resolved through PATH.
    assert not re.search(r'(^|[\s"])python(\.exe)?([\s"]|$)', text)
    assert not re.search(r'(^|[\s"])py([\s"]|$)', text)
    assert text.rstrip().endswith("%*")


def test_write_wrapper_uses_crlf_bytes_and_creates_the_bin_directory(tmp_path):
    path = tmp_path / "package" / "bin" / "horizon.cmd"

    written = launcher.write_wrapper(path)

    assert written == path
    data = path.read_bytes()
    assert data == launcher.wrapper_text().encode("utf-8")
    assert b"\r\n" in data
    assert data.replace(b"\r\n", b"").count(b"\n") == 0
    assert data.split(b"\r\n")[0] == b"@echo off"


def test_console_script_source_mode_matches_the_pinned_launcher():
    code = Path("C:/horizon")
    archive = Path("C:/papers")
    python = "C:/horizon/runtime/python.exe"

    text = launcher.console_script_text(code, archive, python)

    assert text == (
        "title horizon-console\n"
        'cd /d "C:\\horizon"\n'
        '"C:/horizon/runtime/python.exe" -m hostrun.cli --code "C:\\horizon" '
        '--archive "C:\\papers" console %*\n'
    )
    assert console.script_text(code, archive, python) == text


# -------------------------------------------------------- bundled runtime copy
def test_build_copies_the_runtime_verbatim_and_generates_the_wrapper(
    source, runtime_dir, tmp_path
):
    result = _build(source, tmp_path / "out", runtime_dir=runtime_dir)
    package = Path(result["package"])

    for rel, data in RUNTIME_FILES.items():
        if "__pycache__" in rel or rel.endswith(".pyc"):
            assert not (package / "runtime" / rel).exists(), rel
            continue
        assert (package / "runtime" / rel).read_bytes() == data, rel

    wrapper = package / "bin" / "horizon.cmd"
    assert wrapper.read_bytes() == launcher.wrapper_text().encode("utf-8")
    assert wrapper.read_bytes().split(b"\r\n")[0] == b"@echo off"

    rows = {row["path"]: row for row in _manifest(result)["files"]}
    assert {
        "runtime/python.exe",
        "runtime/python313.dll",
        "runtime/python313._pth",
        "runtime/python313.zip",
        "runtime/DLLs/select.pyd",
        "runtime/Lib/site-packages/yaml/__init__.py",
        "runtime/Lib/site-packages/fitz/__init__.py",
        "bin/horizon.cmd",
    } <= set(rows)
    assert rows["runtime/python.exe"]["executable"] is True
    assert rows["bin/horizon.cmd"]["executable"] is True
    assert rows["runtime/python313.dll"]["executable"] is False
    assert result["runtime"]["files"] == len(
        [rel for rel in RUNTIME_FILES if "__pycache__" not in rel and not rel.endswith(".pyc")]
    )


def test_build_without_a_runtime_dir_keeps_the_93_layout(source, tmp_path):
    result = _build(source, tmp_path / "out")
    package = Path(result["package"])

    assert not (package / "runtime").exists()
    assert not (package / "bin" / "horizon.cmd").exists()
    assert not any(path.startswith("runtime/") for path in _listed(result))
    assert "bin/horizon.cmd" not in _listed(result)
    assert result["runtime"] is None
    assert result["launcher"] is None


@pytest.mark.parametrize(
    ("broken", "pattern"),
    [("missing_dir", "runtime directory"), ("no_python_exe", "python\\.exe")],
)
def test_build_rejects_an_invalid_runtime_dir(source, tmp_path, broken, pattern):
    runtime_dir = tmp_path / "broken-runtime"
    if broken == "no_python_exe":
        runtime_dir.mkdir()
        (runtime_dir / "python313.dll").write_bytes(b"dll")

    with pytest.raises(distribution.BuildError, match=pattern):
        _build(source, tmp_path / "out", runtime_dir=runtime_dir)

    # Validation happens before the package directory is created.
    assert not (tmp_path / "out" / "horizon-0.1.0-stable").exists()


def test_same_runtime_directory_builds_byte_identical_packages(
    source, runtime_dir, tmp_path
):
    first = _build(source, tmp_path / "out-a", runtime_dir=runtime_dir)
    second = _build(source, tmp_path / "out-b", runtime_dir=runtime_dir)

    assert Path(first["manifest"]).read_bytes() == Path(second["manifest"]).read_bytes()
    assert Path(first["digest"]).read_bytes() == Path(second["digest"]).read_bytes()
    assert Path(first["artifact"]["path"]).read_bytes() == Path(
        second["artifact"]["path"]
    ).read_bytes()


def test_cli_builds_with_a_runtime_dir(source, runtime_dir, tmp_path):
    import subprocess

    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "hostrun.distribution",
            "--source",
            str(source),
            "--out",
            str(tmp_path / "out"),
            "--version",
            "0.1.0",
            "--channel",
            "stable",
            "--built-from-commit",
            "deadbeefcafe",
            "--runtime-dir",
            str(runtime_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["ok"] is True
    assert summary["runtime"]["files"] > 0
    assert Path(summary["launcher"]).is_file()


def test_cli_reports_an_invalid_runtime_dir_before_writing(source, tmp_path):
    import subprocess

    broken = tmp_path / "broken-runtime"
    broken.mkdir()
    (broken / "python313.dll").write_bytes(b"dll")

    completed = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "hostrun.distribution",
            "--source",
            str(source),
            "--out",
            str(tmp_path / "out"),
            "--version",
            "0.1.0",
            "--channel",
            "stable",
            "--built-from-commit",
            "deadbeefcafe",
            "--runtime-dir",
            str(broken),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert "python.exe" in payload["error"]
    assert not (tmp_path / "out" / "horizon-0.1.0-stable").exists()


# ------------------------------------------------- console package delegation
def test_console_package_mode_delegates_to_the_wrapper(tmp_path):
    code = tmp_path / "code"
    archive = tmp_path / "papers"
    (code / "bin").mkdir(parents=True)
    (code / "bin" / "horizon.cmd").write_bytes(
        launcher.wrapper_text().encode("utf-8")
    )
    (code / ".horizon").mkdir()
    (code / ".horizon" / "dist.lock.json").write_text(
        json.dumps({"schema_version": 1, "distribution": "package"}),
        encoding="utf-8",
    )

    first = console.ensure_script(code, archive, "C:/system/python.exe")
    path = code / "bin" / "horizon-console.cmd"
    text = path.read_text(encoding="utf-8")

    assert first["written"] is True
    assert text == (
        "title horizon-console\n"
        f'call "%~dp0horizon.cmd" --code "{code}" '
        f'--archive "{archive}" console %*\n'
    )
    assert text.startswith("title horizon-console\n")
    assert text.rstrip().endswith("%*")
    assert "python.exe" not in text

    assert console.ensure_script(code, archive, "C:/system/python.exe")["written"] is False


def test_console_source_mode_bytes_are_unchanged(tmp_path):
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"
    python = "C:/python/python.exe"

    assert console.ensure_script(code, archive, python)["written"] is True
    text = (code / "bin" / "horizon-console.cmd").read_text(encoding="utf-8")

    assert text == (
        "title horizon-console\n"
        f'cd /d "{code}"\n'
        f'"{python}" -m hostrun.cli --code "{code}" --archive "{archive}" console %*\n'
    )


# ------------------------------------------- verify tolerates the generated file
def test_verify_covers_the_bundled_runtime_files(source, runtime_dir, tmp_path):
    result = _build(source, tmp_path / "out", runtime_dir=runtime_dir)
    code = tmp_path / "code"
    dist.install_package(code, from_source=Path(result["artifact"]["path"]))
    (code / "runtime" / "python313.dll").write_bytes(b"tampered")

    payload = dist.verify_install(code)

    assert payload["status"] == "mismatch"
    assert [row["path"] for row in payload["mismatch"]] == ["runtime/python313.dll"]


def test_verify_tolerates_the_generated_console_launcher(
    source, runtime_dir, tmp_path
):
    result = _build(source, tmp_path / "out", runtime_dir=runtime_dir)
    code = tmp_path / "code"
    dist.install_package(code, from_source=Path(result["artifact"]["path"]))

    console.ensure_script(code, tmp_path / "papers", "C:/unused/python.exe")
    generated = code / "bin" / "horizon-console.cmd"
    assert generated.is_file()

    payload = dist.verify_install(code)

    assert payload["ok"] is True
    assert payload["status"] == "verified"
    assert payload["extra_manifest_files"] == []
