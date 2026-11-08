"""Distribution build entry: manifest completeness, exclusions and reproducibility.

The build entry is a seam of its own (#93): a source tree plus version/channel
goes in, a package directory, an in-package manifest and an out-of-package
digest file come out.  These tests use a small fixture source tree so the
behaviour is checked without packaging the whole checkout on every case; one
integration test builds the real checkout to keep the packaged inventory honest.
"""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from hostrun import distribution

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "manifest.json"

PACKAGED_FILES = {
    "common.py": "RUNTIME = True\n",
    "requirements.txt": "PyYAML>=6.0\n",
    "README.md": "# fixture\n",
    "LICENSE": "MIT\n",
    "hostrun/__init__.py": "",
    "hostrun/cli.py": "print('fixture')\n",
    "hostrun/base.py": "VALUE = 1\n",
    "skills/horizon-run/SKILL.md": "---\nname: horizon-run\n---\n",
    "contracts/README.md": "contracts\n",
    "docs/adr/0010-distribution.md": "# decision\n",
    # A nested file happens to share the manifest's name; only the package-root
    # manifest is special and must not hide this asset from the index.
    "docs/manifest.json": '{"note": "asset, not the package manifest"}\n',
    # archive/ is runtime code in this checkout (archive.cli / archive.store);
    # runtime archive *data* lives under .horizon/archive and is excluded.
    "archive/store.py": "STORE = 1\n",
}

EXCLUDED_FILES = {
    ".git/HEAD": "ref: refs/heads/main\n",
    ".venv/pyvenv.cfg": "home = /usr/bin\n",
    ".horizon/install/config.json": "{}\n",
    ".horizon/archive/raw/paper.json": "{}\n",
    ".worktrees/other/common.py": "OTHER = True\n",
    "bin/horizon-console.cmd": "@echo off\n",
    "__pycache__/common.cpython-313.pyc": "bytecode",
    "hostrun/__pycache__/cli.cpython-313.pyc": "bytecode",
    ".pytest_cache/v/cache/nodeids": "[]\n",
    "pytest-of-someone/pytest-1/test.log": "log\n",
    "tests/test_thing.py": "def test_thing():\n    pass\n",
    "fixtures/papers/paper.json": "{}\n",
    "drafts/notes.md": "draft\n",
    "outbox/push.json": "{}\n",
    "scantest/evidence.txt": "scratch\n",
    "tmpgap/evidence.txt": "scratch\n",
    ".dev/notes.md": "dev\n",
    ".horizon-archive/originals/paper.pdf": "pdf",
    "scripts/demo.py": "print('demo')\n",
    "deploy/horizon.service.in": "[Unit]\n",
    "AGENTS.md": "# dev guide\n",
    "GLOSSARY.md": "# glossary\n",
    "skills/horizon-run/.DS_Store": "junk",
}


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def source(tmp_path):
    return _write_tree(tmp_path / "src", {**PACKAGED_FILES, **EXCLUDED_FILES})


def _build(source: Path, out: Path, **over):
    options = {
        "source": source,
        "out": out,
        "version": "0.1.0",
        "channel": "stable",
        "commit": "deadbeefcafe",
        "compat": {
            "os": "windows",
            "arch": "x86_64",
            "dsh": "0.2.1",
            "python": "3.13",
        },
    }
    options.update(over)
    return distribution.build(**options)


def _manifest(result: dict) -> dict:
    return json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))


def _digest(result: dict) -> dict:
    return json.loads(Path(result["digest"]).read_text(encoding="utf-8"))


def _paths(manifest: dict) -> list[str]:
    return [row["path"] for row in manifest["files"]]


def _package_files(package: Path) -> list[Path]:
    return sorted(
        (path for path in package.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(package).as_posix(),
    )


# ------------------------------------------------------------------ manifest
def test_manifest_covers_generated_runtime_and_wrapper(source, tmp_path):
    """Generated package content is indexed by the same manifest (#95)."""
    runtime = tmp_path / "runtime"
    (runtime / "DLLs").mkdir(parents=True)
    (runtime / "Lib" / "site-packages").mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"MZ fake")
    (runtime / "python313.dll").write_bytes(b"dll")
    (runtime / "python313._pth").write_bytes(b"python313.zip\n")
    (runtime / "DLLs" / "select.pyd").write_bytes(b"pyd")
    (runtime / "Lib" / "site-packages" / "yaml.py").write_bytes(b"YAML = 1\n")
    (runtime / "__pycache__").mkdir()
    (runtime / "__pycache__" / "stale.pyc").write_bytes(b"cache")

    result = _build(source, tmp_path / "out", runtime_dir=runtime)
    package = Path(result["package"])
    manifest = _manifest(result)

    on_disk = {
        path.relative_to(package).as_posix(): path
        for path in _package_files(package)
        if path.relative_to(package).as_posix() != MANIFEST
    }
    listed = {row["path"]: row for row in manifest["files"]}
    assert set(listed) == set(on_disk)
    assert {
        "runtime/python.exe",
        "runtime/python313.dll",
        "runtime/DLLs/select.pyd",
        "runtime/Lib/site-packages/yaml.py",
        "bin/horizon.cmd",
    } <= set(listed)
    assert not any("__pycache__" in path for path in listed)
    for rel, path in on_disk.items():
        assert listed[rel]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest(), rel


def test_manifest_covers_every_packaged_file_with_correct_bytes_and_digest(source, tmp_path):
    result = _build(source, tmp_path / "out")
    manifest = _manifest(result)
    package = Path(result["package"])

    on_disk = {
        path.relative_to(package).as_posix(): path
        for path in _package_files(package)
        if path.relative_to(package).as_posix() != MANIFEST
    }
    listed = {row["path"]: row for row in manifest["files"]}
    assert set(listed) == set(on_disk)

    for rel, path in on_disk.items():
        row = listed[rel]
        data = path.read_bytes()
        assert row["bytes"] == len(data), rel
        assert row["sha256"] == hashlib.sha256(data).hexdigest(), rel
        assert isinstance(row["executable"], bool), rel

    assert manifest["schema_version"] == 1
    assert manifest["version"] == "0.1.0"
    assert manifest["channel"] == "stable"
    assert manifest["built_from_commit"] == "deadbeefcafe"
    assert manifest["compat"] == {
        "os": "windows",
        "arch": "x86_64",
        "dsh": "0.2.1",
        "python": "3.13",
    }

    # Program code and agent-readable assets both ship; dev-only/private content
    # is covered by the exclusion test below.
    assert {"common.py", "hostrun/cli.py", "archive/store.py"} <= set(listed)
    assert "skills/horizon-run/SKILL.md" in listed
    assert "contracts/README.md" in listed
    assert "docs/adr/0010-distribution.md" in listed


def test_files_are_sorted_by_path_and_manifest_is_not_self_digested(source, tmp_path):
    result = _build(source, tmp_path / "out")
    manifest = _manifest(result)
    paths = _paths(manifest)

    assert paths == sorted(paths)
    assert MANIFEST not in paths
    assert Path(result["manifest"]).name == MANIFEST
    assert Path(result["manifest"]).parent == Path(result["package"])


def test_digest_file_summarises_the_manifest_without_volatile_fields(source, tmp_path):
    result = _build(source, tmp_path / "out")
    manifest_path = Path(result["manifest"])
    digest = _digest(result)
    manifest = _manifest(result)
    artifact = Path(result["artifact"]["path"])

    assert digest["schema_version"] == 1
    assert digest["package"] == Path(result["package"]).name
    assert digest["version"] == "0.1.0"
    assert digest["channel"] == "stable"
    assert digest["built_from_commit"] == "deadbeefcafe"
    assert digest["manifest"] == MANIFEST
    assert digest["manifest_sha256"] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert digest["files"] == len(manifest["files"])
    assert digest["bytes"] == sum(row["bytes"] for row in manifest["files"])
    assert digest["artifact"] == {
        "name": artifact.name,
        "bytes": artifact.stat().st_size,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }
    assert "created_at" not in digest
    assert "built_at" not in digest


def test_build_emits_a_reproducible_zip_artifact_next_to_the_package(source, tmp_path):
    result = _build(source, tmp_path / "out")
    package = Path(result["package"])
    artifact = Path(result["artifact"]["path"])
    manifest = _manifest(result)

    assert artifact == package.parent / f"{package.name}.zip"
    assert artifact.is_file()
    assert artifact.parent == package.parent
    assert not (package / artifact.name).exists()
    assert result["artifact"]["name"] == artifact.name
    assert result["artifact"]["bytes"] == artifact.stat().st_size
    assert result["artifact"]["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()

    expected_names = sorted(
        ["manifest.json", *(row["path"] for row in manifest["files"])]
    )
    with zipfile.ZipFile(artifact, "r") as archive:
        infos = archive.infolist()
        assert [info.filename for info in infos] == expected_names
        executable = {row["path"]: row["executable"] for row in manifest["files"]}
        for info in infos:
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert info.external_attr != 0
            mode = (info.external_attr >> 16) & 0o777
            assert mode == (0o755 if executable.get(info.filename) else 0o644)
        assert archive.read("manifest.json") == Path(result["manifest"]).read_bytes()
        for row in manifest["files"]:
            assert hashlib.sha256(archive.read(row["path"])).hexdigest() == row["sha256"]


def test_manifest_has_no_timestamp_fields(source, tmp_path):
    result = _build(source, tmp_path / "out")
    text = Path(result["manifest"]).read_text(encoding="utf-8")
    assert "timestamp" not in text
    assert "built_at" not in text


def test_manifest_schema_file_declares_the_same_contract():
    schema = json.loads(
        (ROOT / "contracts" / "distribution-manifest.schema.json").read_text(encoding="utf-8")
    )
    assert schema["type"] == "object"
    assert set(schema["required"]) == {
        "schema_version",
        "version",
        "channel",
        "built_from_commit",
        "compat",
        "files",
    }
    assert schema["properties"]["channel"]["enum"] == ["stable", "prerelease"]
    assert set(schema["properties"]["compat"]["required"]) == {"os", "arch", "dsh", "python"}
    assert set(schema["properties"]["files"]["items"]["required"]) == {
        "path",
        "bytes",
        "sha256",
        "executable",
    }


# --------------------------------------------------------------- exclusions
@pytest.mark.parametrize("rel", sorted(EXCLUDED_FILES))
def test_runtime_state_private_env_caches_and_dev_dirs_are_excluded(source, tmp_path, rel):
    result = _build(source, tmp_path / "out")
    listed = set(_paths(_manifest(result)))
    assert rel not in listed
    assert not (Path(result["package"]) / rel).exists()


def test_ignored_entries_are_not_copied_even_when_their_parent_ships(source, tmp_path):
    result = _build(source, tmp_path / "out")
    package = Path(result["package"])
    assert (package / "hostrun" / "cli.py").is_file()
    assert not (package / "hostrun" / "__pycache__").exists()
    assert not (package / "skills" / "horizon-run" / ".DS_Store").exists()


# ----------------------------------------------------------- reproducibility
def test_building_the_same_source_twice_is_byte_identical(source, tmp_path):
    first = _build(source, tmp_path / "out-a")
    second = _build(source, tmp_path / "out-b")

    assert Path(first["manifest"]).read_bytes() == Path(second["manifest"]).read_bytes()
    assert Path(first["digest"]).read_bytes() == Path(second["digest"]).read_bytes()
    assert Path(first["artifact"]["path"]).read_bytes() == Path(
        second["artifact"]["path"]
    ).read_bytes()

    first_files = {
        path.relative_to(first["package"]).as_posix(): path.read_bytes()
        for path in _package_files(Path(first["package"]))
    }
    second_files = {
        path.relative_to(second["package"]).as_posix(): path.read_bytes()
        for path in _package_files(Path(second["package"]))
    }
    assert first_files == second_files


def test_rebuilding_into_the_same_output_dir_replaces_the_old_package(source, tmp_path):
    out = tmp_path / "out"
    first = _build(source, out)
    stale = Path(first["package"]) / "hostrun" / "stale.py"
    stale.write_text("STALE = True\n", encoding="utf-8")

    second = _build(source, out)
    assert not stale.exists()
    assert Path(first["manifest"]).read_bytes() == Path(second["manifest"]).read_bytes()
    assert Path(first["digest"]).read_bytes() == Path(second["digest"]).read_bytes()


# ------------------------------------------------------------ commit evidence
def test_missing_git_records_an_explicit_unknown_commit_instead_of_crashing(tmp_path):
    source = _write_tree(tmp_path / "src", PACKAGED_FILES)
    result = _build(source, tmp_path / "out", commit=None)
    manifest = _manifest(result)
    assert manifest["built_from_commit"] == distribution.UNKNOWN_COMMIT
    assert _digest(result)["built_from_commit"] == distribution.UNKNOWN_COMMIT


def test_clean_git_source_records_head_commit(tmp_path):
    source = _write_tree(tmp_path / "src", PACKAGED_FILES)
    env = {"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com"}
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "add", "-A"], cwd=source, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
         "commit", "-q", "-m", "fixture"],
        cwd=source,
        check=True,
        env={**__import__("os").environ, **env},
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    result = _build(source, tmp_path / "out", commit=None)
    assert _manifest(result)["built_from_commit"] == head


def test_explicit_commit_wins_over_git(source, tmp_path):
    result = _build(source, tmp_path / "out", commit="explicit-sha")
    assert _manifest(result)["built_from_commit"] == "explicit-sha"


# ------------------------------------------------------------------- package
def test_version_and_channel_name_the_package_and_are_validated(source, tmp_path):
    result = _build(source, tmp_path / "out", version="1.2.3", channel="prerelease")
    assert Path(result["package"]).name == "horizon-1.2.3-prerelease"
    assert _manifest(result)["channel"] == "prerelease"

    with pytest.raises(distribution.BuildError):
        _build(source, tmp_path / "out-bad", channel="nightly")
    with pytest.raises(distribution.BuildError):
        _build(source, tmp_path / "out-bad", version="../escape")


def test_version_strips_a_leading_v_before_naming_the_package(source, tmp_path):
    result = _build(source, tmp_path / "out", version="v1.2.3", channel="stable")

    assert Path(result["package"]).name == "horizon-1.2.3-stable"
    assert Path(result["artifact"]["path"]).name == "horizon-1.2.3-stable.zip"
    assert _manifest(result)["version"] == "1.2.3"
    assert result["version"] == "1.2.3"
    assert result["requested_version"] == "v1.2.3"
    assert result["version_normalized"] is True


def test_version_help_does_not_advertise_a_v_prefixed_input():
    from hostrun import distribution

    help_text = distribution.build_parser().format_help()
    assert "v0.1.0" not in help_text
    assert "不带 v" in help_text or "leading 'v'" in help_text


def test_cli_normalizes_a_v_prefixed_version(source, tmp_path):
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
            "v0.1.0",
            "--channel",
            "stable",
            "--built-from-commit",
            "deadbeefcafe",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["version"] == "0.1.0"
    assert Path(summary["artifact"]["path"]).name == "horizon-0.1.0-stable.zip"
    assert summary["version_normalized"] is True


def test_cli_prints_a_json_summary_and_exits_zero(source, tmp_path):
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
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["ok"] is True
    assert Path(summary["manifest"]).is_file()
    assert Path(summary["digest"]).is_file()


# ------------------------------------------------------------- real checkout
def test_real_checkout_build_packages_program_code_and_agent_assets(tmp_path):
    result = _build(ROOT, tmp_path / "out", version="0.0.0", channel="prerelease")
    manifest = _manifest(result)
    paths = set(_paths(manifest))

    assert "hostrun/distribution.py" in paths
    assert "hostrun/cli.py" in paths
    assert "common.py" in paths
    assert "skills/horizon-run/SKILL.md" in paths
    assert "contracts/distribution-manifest.schema.json" in paths
    assert any(path.startswith("docs/") for path in paths)
    assert len(paths) > 40

    for prefix in (".git/", ".venv/", ".horizon/", "tests/", "fixtures/", "drafts/",
                   "outbox/", "scantest/", "tmpgap/", "scripts/", "deploy/",
                   "__pycache__/"):
        assert not any(path.startswith(prefix) for path in paths), prefix
