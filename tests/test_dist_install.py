"""Install a distribution package, write the install lock and verify the install.

Ticket #94 is the installation seam: one local artifact plus its digest goes in;
an extracted code directory and ``<code>/.horizon/dist.lock.json`` come out.
Every test builds a real package with :mod:`hostrun.distribution` and installs it
from a local path or ``file://`` URL; nothing reaches the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from conftest import cli_locations
from hostrun import cli, dist, distribution

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


def _write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _host_compat() -> dict[str, str]:
    return {
        "os": distribution.normalize_os(sys.platform),
        "arch": distribution.normalize_arch(platform.machine()),
        "dsh": "*",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _build(
    tmp_path: Path,
    *,
    name: str = "pkg",
    version: str = "0.1.0",
    channel: str = "stable",
    compat: dict | None = None,
) -> dict:
    source = _write_tree(tmp_path / f"{name}-src", PACKAGE_FILES)
    return distribution.build(
        source=source,
        out=tmp_path / f"{name}-out",
        version=version,
        channel=channel,
        commit="deadbeefcafe",
        compat=compat or _host_compat(),
    )


def _digest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _manifest(result: dict) -> dict:
    return json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))


def _refresh_artifact_digest(digest_path: Path, artifact: Path) -> None:
    payload = _digest(digest_path)
    payload["artifact"] = {
        "name": artifact.name,
        "bytes": artifact.stat().st_size,
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }
    digest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _rewrite_zip(
    source: Path,
    *,
    replace: dict[str, bytes] | None = None,
    add: dict[str, bytes] | None = None,
) -> bytes:
    """Return a rewritten zip with selected entries replaced or added."""
    import io

    buffer = io.BytesIO()
    replace = replace or {}
    add = add or {}
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
        buffer, "w", compression=zipfile.ZIP_DEFLATED
    ) as rewritten:
        for info in original.infolist():
            data = replace.get(info.filename, original.read(info.filename))
            copy = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            copy.compress_type = zipfile.ZIP_DEFLATED
            copy.external_attr = info.external_attr
            rewritten.writestr(copy, data)
        for name, data in add.items():
            copy = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            copy.compress_type = zipfile.ZIP_DEFLATED
            copy.external_attr = 0o100644 << 16
            rewritten.writestr(copy, data)
    return buffer.getvalue()


def _install(
    code: Path,
    artifact: Path,
    *,
    digest_path: str | Path | None = None,
    sha256: str | None = None,
    channel: str | None = None,
):
    return dist.install_package(
        code,
        from_source=str(artifact),
        digest_path=str(digest_path) if digest_path else None,
        sha256=sha256,
        channel=channel,
    )


def _cli(capsys, home: Path, *args: str):
    code = cli.main([*cli_locations(home), *args])
    captured = capsys.readouterr()
    raw = captured.out.strip() or captured.err.strip()
    return code, json.loads(raw), captured


# --------------------------------------------------------------------- build
def test_install_lock_schema_declares_the_same_contract():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts" / "install-lock.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is True
    # Fields every installation records, whatever its distribution (#97).
    assert set(schema["required"]) == {
        "schema_version",
        "distribution",
        "source_url",
        "installed_at",
        "requires",
        "skill_digest",
    }
    assert schema["properties"]["distribution"]["enum"] == ["package", "source"]
    assert schema["properties"]["channel"]["enum"] == ["stable", "prerelease"]
    # The package compatibility facts stay declared; the package branch requires
    # them and the source branch keeps requires as an empty object.
    assert set(schema["properties"]["requires"]["properties"]) == {
        "os",
        "arch",
        "dsh",
        "python",
    }
    branches = {branch["if"]["properties"]["distribution"]["const"]: branch for branch in schema["allOf"]}
    package = branches["package"]["then"]
    source = branches["source"]["then"]
    assert set(package["required"]) == {
        "version",
        "channel",
        "artifact_sha256",
        "package_file_count",
        "manifest_sha256",
    }
    assert set(package["properties"]["requires"]["required"]) == {
        "os",
        "arch",
        "dsh",
        "python",
    }
    assert set(source["required"]) == {"commit", "dirty"}


def test_build_writes_a_zip_and_the_digest_records_it(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    digest = _digest(result["digest"])

    assert artifact.is_file()
    assert artifact.name == f"{Path(result['package']).name}.zip"
    assert digest["artifact"]["name"] == result["artifact"]["name"]
    assert digest["artifact"]["bytes"] == result["artifact"]["bytes"]
    assert digest["artifact"]["sha256"] == result["artifact"]["sha256"]
    assert digest["artifact"]["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert digest["manifest_sha256"] == hashlib.sha256(
        Path(result["manifest"]).read_bytes()
    ).hexdigest()


# ------------------------------------------------------------------- install
def test_install_extracts_the_package_and_writes_the_complete_lock(tmp_path):
    result = _build(tmp_path)
    code = tmp_path / "code"

    payload = _install(code, Path(result["artifact"]["path"]))

    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["distribution"] == "package"
    assert payload["version"] == "0.1.0"
    assert payload["channel"] == "stable"
    assert payload["artifact_sha256"] == result["artifact"]["sha256"]
    assert payload["extracted"] is True
    assert payload["code"] == str(code.resolve())
    assert payload["lock"] == str(code / ".horizon" / "dist.lock.json")
    assert payload["manifest"] == str(code / "manifest.json")
    assert payload["files"] == len(_manifest(result)["files"])
    assert payload["bytes"] == sum(row["bytes"] for row in _manifest(result)["files"])
    assert payload["note"]

    lock_path = code / ".horizon" / "dist.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    manifest = _manifest(result)
    source_url = Path(result["artifact"]["path"]).resolve().as_uri()

    assert lock["schema_version"] == 1
    assert lock["distribution"] == "package"
    assert lock["version"] == "0.1.0"
    assert lock["channel"] == "stable"
    assert lock["artifact_sha256"] == result["artifact"]["sha256"]
    assert lock["source_url"] == source_url
    assert lock["requires"] == manifest["compat"]
    assert lock["skill_digest"] == ""
    assert lock["package_file_count"] == len(manifest["files"])
    assert lock["manifest_sha256"] == hashlib.sha256(
        (code / "manifest.json").read_bytes()
    ).hexdigest()
    datetime.fromisoformat(lock["installed_at"])

    assert (code / "manifest.json").read_bytes() == Path(result["manifest"]).read_bytes()
    for row in manifest["files"]:
        target = code / row["path"]
        assert target.is_file(), row["path"]
        assert target.read_bytes() == (Path(result["package"]) / row["path"]).read_bytes()
    assert not (code / Path(result["artifact"]["path"]).name).exists()
    assert not (code / ".horizon" / "staging").exists()


def test_file_url_source_records_a_canonical_source_url(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    code = tmp_path / "code"

    payload = _install(code, artifact)

    assert payload["source_url"] == artifact.resolve().as_uri()
    lock = json.loads((code / ".horizon" / "dist.lock.json").read_text(encoding="utf-8"))
    assert lock["source_url"] == artifact.resolve().as_uri()


def test_install_from_a_file_uri_succeeds(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    code = tmp_path / "code"

    payload = dist.install_package(code, from_source=artifact.as_uri())

    assert payload["ok"] is True
    assert payload["source_url"] == artifact.resolve().as_uri()


def test_https_source_downloads_the_artifact_and_installs_it(tmp_path, monkeypatch):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    code = tmp_path / "code"
    seen: dict[str, object] = {}

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        seen["timeout"] = timeout
        return io.BytesIO(artifact.read_bytes())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    url = f"https://example.invalid/releases/{artifact.name}"

    payload = dist.install_package(
        code, from_source=url, digest_path=str(result["digest"])
    )

    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["version"] == "0.1.0"
    assert payload["channel"] == "stable"
    assert payload["artifact_sha256"] == result["artifact"]["sha256"]
    assert (code / "manifest.json").is_file()
    assert (code / ".horizon" / "dist.lock.json").is_file()
    assert seen["url"] == url
    assert seen["timeout"] is not None


@pytest.mark.parametrize(
    "source",
    [
        "http://example.invalid/horizon-0.1.0-stable.zip",
        "ftp://example.invalid/horizon-0.1.0-stable.zip",
    ],
    ids=["http", "ftp"],
)
def test_insecure_or_unknown_schemes_stay_blocked_without_writes(tmp_path, source):
    code = tmp_path / "code"

    payload = dist.install_package(code, from_source=source)

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "unsupported_source"
    assert "https" in payload["note"].lower()
    assert not code.exists()


def test_https_download_failure_blocks_before_any_write(tmp_path, monkeypatch):
    code = tmp_path / "code"

    def failing_urlopen(url, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)

    payload = dist.install_package(
        code, from_source="https://example.invalid/horizon-0.1.0-stable.zip"
    )

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "unsupported_source"
    assert "download" in payload["note"].lower()
    assert not code.exists()


def test_sibling_digest_is_discovered_for_zip_and_zip_digest_names(tmp_path):
    first = _build(tmp_path, name="first")
    first_artifact = Path(first["artifact"]["path"])
    digest_path = Path(first["digest"])
    assert digest_path.name == first_artifact.name.removesuffix(".zip") + ".digest.json"
    assert _install(tmp_path / "code-a", first_artifact)["ok"] is True

    renamed_dir = tmp_path / "renamed"
    renamed_dir.mkdir()
    renamed_artifact = renamed_dir / first_artifact.name
    shutil.copy2(first_artifact, renamed_artifact)
    shutil.copy2(digest_path, renamed_dir / f"{renamed_artifact.name}.digest.json")
    assert _install(tmp_path / "code-b", renamed_artifact)["ok"] is True


def test_missing_digest_blocks_before_any_write(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    lone = isolated / artifact.name
    shutil.copy2(artifact, lone)
    code = tmp_path / "code"

    payload = _install(code, lone)

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "digest_not_found"
    assert not code.exists()


def test_explicit_sha256_wins_over_the_sibling_digest(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    code = tmp_path / "code"

    payload = _install(code, artifact, sha256="0" * 64)

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "digest_mismatch"
    assert not code.exists()


def test_truncated_artifact_blocks_before_extraction(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    artifact.write_bytes(artifact.read_bytes()[: artifact.stat().st_size // 2])
    code = tmp_path / "code"

    payload = _install(code, artifact)

    assert payload["status"] == "blocked"
    assert payload["reason"] == "digest_mismatch"
    assert not code.exists()


def test_channel_conflict_blocks_before_any_write(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    code = tmp_path / "code"

    payload = _install(code, artifact, channel="prerelease")

    assert payload["status"] == "blocked"
    assert payload["reason"] == "channel_conflict"
    assert not code.exists()


def test_tampered_package_file_blocks_without_a_lock_or_applied_files(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    artifact.write_bytes(
        _rewrite_zip(artifact, replace={"hostrun/cli.py": b"TAMPERED = True\n"})
    )
    _refresh_artifact_digest(Path(result["digest"]), artifact)
    code = tmp_path / "code"

    payload = _install(code, artifact)

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "file_verification_failed"
    assert payload["files"] == ["hostrun/cli.py"]
    assert not (code / "hostrun" / "cli.py").exists()
    assert not (code / "manifest.json").exists()
    assert not (code / ".horizon" / "dist.lock.json").exists()
    staging = code / ".horizon" / "staging"
    assert not staging.exists() or not any(staging.rglob("*"))


@pytest.mark.parametrize(
    "unsafe",
    ["../escape.txt", "/absolute.txt", "C:/drive.txt", "nested/../../escape.txt"],
)
def test_zip_slip_entries_are_refused_before_extraction(tmp_path, unsafe):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    artifact.write_bytes(_rewrite_zip(artifact, add={unsafe: b"escape"}))
    _refresh_artifact_digest(Path(result["digest"]), artifact)
    code = tmp_path / "code"

    payload = _install(code, artifact)

    assert payload["status"] == "blocked"
    assert payload["reason"] == "unsafe_path"
    assert payload["path"] == unsafe
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "absolute.txt").exists()
    assert not (tmp_path / "drive.txt").exists()
    assert not (code / "manifest.json").exists()
    assert not (code / ".horizon" / "dist.lock.json").exists()


def test_manifest_path_escaping_the_package_is_refused(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    manifest = json.loads(zipfile.ZipFile(artifact).read("manifest.json"))
    manifest["files"][0]["path"] = "../escape.py"
    tampered_manifest = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    artifact.write_bytes(
        _rewrite_zip(artifact, replace={"manifest.json": tampered_manifest})
    )
    digest = _digest(result["digest"])
    digest["manifest_sha256"] = hashlib.sha256(tampered_manifest).hexdigest()
    Path(result["digest"]).write_text(
        json.dumps(digest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _refresh_artifact_digest(Path(result["digest"]), artifact)
    code = tmp_path / "code"

    payload = _install(code, artifact)

    assert payload["status"] == "blocked"
    assert payload["reason"] == "unsafe_path"
    assert not (tmp_path / "escape.py").exists()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("os", "linux", "compat_os_mismatch"),
        ("arch", "arm64", "compat_arch_mismatch"),
        ("python", "0.1", "compat_python_mismatch"),
    ],
)
def test_incompatible_os_arch_or_python_blocks_before_writes(tmp_path, field, value, reason):
    compat = _host_compat()
    if compat[field] == value:
        pytest.skip(f"host already reports {field}={value}")
    compat[field] = value
    result = _build(tmp_path, compat=compat)
    artifact = Path(result["artifact"]["path"])
    code = tmp_path / "code"

    payload = _install(code, artifact)

    assert payload["status"] == "blocked"
    assert payload["reason"] == reason
    assert payload["field"] == f"compat.{field}"
    assert not code.exists()


def _recorded_dsh(code: Path, path: Path) -> None:
    config = code / ".horizon" / "install" / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({"schema_version": 1, "paths": {"dsh": str(path)}}),
        encoding="utf-8",
    )


def _version_runner(output: str, *, returncode: int = 0):
    class Proc:
        pass

    proc = Proc()
    proc.returncode = returncode
    proc.stdout = output
    proc.stderr = ""
    return lambda argv, **kwargs: proc


@pytest.mark.parametrize("requirement", [">=0.2.1", "0.2.3"])
def test_dsh_compat_match_is_reported(tmp_path, requirement):
    compat = _host_compat()
    compat["dsh"] = requirement
    result = _build(tmp_path, compat=compat)
    code = tmp_path / "code"
    dsh = tmp_path / "dsh.exe"
    dsh.write_text("", encoding="utf-8")
    _recorded_dsh(code, dsh)

    payload = dist.install_package(
        code,
        from_source=str(result["artifact"]["path"]),
        runner=_version_runner("dsh 0.2.3\n"),
    )

    assert payload["ok"] is True
    assert payload["compat_dsh"]["status"] == "ok"
    assert payload["compat_dsh"]["host"] == "0.2.3"


def test_dsh_compat_mismatch_blocks_before_extraction(tmp_path):
    compat = _host_compat()
    compat["dsh"] = ">=0.2.1"
    result = _build(tmp_path, compat=compat)
    code = tmp_path / "code"
    dsh = tmp_path / "dsh.exe"
    dsh.write_text("", encoding="utf-8")
    _recorded_dsh(code, dsh)

    payload = dist.install_package(
        code,
        from_source=str(result["artifact"]["path"]),
        runner=_version_runner("dsh 0.1.0\n"),
    )

    assert payload["status"] == "blocked"
    assert payload["reason"] == "compat_dsh_mismatch"
    assert payload["field"] == "compat.dsh"
    assert not (code / "manifest.json").exists()
    assert not (code / ".horizon" / "dist.lock.json").exists()
    assert not (code / ".horizon" / "staging").exists()


@pytest.mark.parametrize(
    ("runner", "note_fragment"),
    [
        (_version_runner("not a version"), "无法从"),
        (_version_runner("dsh 0.2.3", returncode=1), "退出码非零"),
    ],
    ids=["unparsable", "runner-failure"],
)
def test_dsh_compat_unavailable_is_unverified_never_silent(
    tmp_path, runner, note_fragment
):
    compat = _host_compat()
    compat["dsh"] = ">=0.2.1"
    result = _build(tmp_path, compat=compat)
    code = tmp_path / "code"
    dsh = tmp_path / "dsh.exe"
    dsh.write_text("", encoding="utf-8")
    _recorded_dsh(code, dsh)

    payload = dist.install_package(
        code,
        from_source=str(result["artifact"]["path"]),
        runner=runner,
    )

    assert payload["ok"] is True
    assert payload["compat_dsh"]["status"] == "unverified"
    assert note_fragment in payload["compat_dsh"]["note"]
    assert "宿主 dsh 兼容性未验证" in payload["note"]


def test_dsh_compat_without_a_recorded_dsh_is_unverified(tmp_path):
    compat = _host_compat()
    compat["dsh"] = ">=0.2.1"
    result = _build(tmp_path, compat=compat)
    code = tmp_path / "code"

    payload = dist.install_package(
        code,
        from_source=str(result["artifact"]["path"]),
        runner=_version_runner("dsh 0.2.3\n"),
    )

    assert payload["ok"] is True
    assert payload["compat_dsh"]["status"] == "unverified"
    assert "未记录 paths.dsh" in payload["compat_dsh"]["note"]


def test_wildcard_compatibility_accepts_every_host(tmp_path):
    result = _build(
        tmp_path,
        compat={"os": "*", "arch": "*", "dsh": "*", "python": "*"},
    )
    payload = _install(tmp_path / "code", Path(result["artifact"]["path"]))

    assert payload["ok"] is True


def test_explicit_digest_path_is_used(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    digest_path = tmp_path / "elsewhere.digest.json"
    shutil.copy2(result["digest"], digest_path)
    code = tmp_path / "code"

    payload = _install(code, artifact, digest_path=digest_path)

    assert payload["ok"] is True
    assert json.loads((code / ".horizon" / "dist.lock.json").read_text(encoding="utf-8"))[
        "artifact_sha256"
    ] == result["artifact"]["sha256"]


def test_explicit_sha256_without_a_digest_uses_the_manifest_channel(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    lone = isolated / artifact.name
    shutil.copy2(artifact, lone)
    code = tmp_path / "code"

    payload = _install(code, lone, sha256=result["artifact"]["sha256"])

    assert payload["ok"] is True
    assert payload["channel"] == "stable"
    assert payload["source_url"] == lone.resolve().as_uri()


def test_invalid_manifest_is_blocked_before_extraction(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    artifact.write_bytes(
        _rewrite_zip(
            artifact,
            replace={
                "manifest.json": b"{}",
            },
        )
    )
    digest = _digest(result["digest"])
    digest["manifest_sha256"] = hashlib.sha256(b"{}").hexdigest()
    Path(result["digest"]).write_text(
        json.dumps(digest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _refresh_artifact_digest(Path(result["digest"]), artifact)
    code = tmp_path / "code"

    payload = _install(code, artifact)

    assert payload["status"] == "blocked"
    assert payload["reason"] == "invalid_manifest"
    assert not code.exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest.__setitem__("unknown_root", True),
        lambda manifest: manifest["compat"].__setitem__("unknown_compat", "1"),
        lambda manifest: manifest["files"][0].__setitem__("unknown_file", "x"),
    ],
    ids=["unknown-root", "unknown-compat", "unknown-file"],
)
def test_manifest_unknown_fields_block_before_any_write(tmp_path, mutate):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    manifest = json.loads(zipfile.ZipFile(artifact).read("manifest.json"))
    mutate(manifest)
    tampered_manifest = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    artifact.write_bytes(
        _rewrite_zip(artifact, replace={"manifest.json": tampered_manifest})
    )
    digest = _digest(result["digest"])
    digest["manifest_sha256"] = hashlib.sha256(tampered_manifest).hexdigest()
    Path(result["digest"]).write_text(
        json.dumps(digest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _refresh_artifact_digest(Path(result["digest"]), artifact)
    code = tmp_path / "code"

    payload = _install(code, artifact)

    assert payload["status"] == "blocked"
    assert payload["reason"] == "invalid_manifest"
    assert any("未知字段" in error or "unknown" in error for error in payload["errors"])
    assert not code.exists()


def test_reinstall_after_a_failed_verify_repairs_the_files(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    code = tmp_path / "code"
    _install(code, artifact)
    (code / "common.py").write_text("TAMPERED\n", encoding="utf-8")
    assert dist.verify_install(code)["status"] == "mismatch"

    payload = _install(code, artifact)

    assert payload["ok"] is True
    assert payload["extracted"] is True
    assert (code / "common.py").read_text(encoding="utf-8") == PACKAGE_FILES["common.py"]
    assert dist.verify_install(code)["status"] == "verified"


def test_foreign_code_directory_is_refused_without_an_install_lock(tmp_path):
    result = _build(tmp_path)
    code = tmp_path / "code"
    code.mkdir()
    (code / "notes.txt").write_text("owner notes\n", encoding="utf-8")

    payload = _install(code, Path(result["artifact"]["path"]))

    assert payload["status"] == "blocked"
    assert payload["reason"] == "foreign_code_dir"
    assert "notes.txt" in payload["conflicts"]
    assert (code / "notes.txt").read_text(encoding="utf-8") == "owner notes\n"
    assert not (code / "manifest.json").exists()
    assert not (code / ".horizon" / "dist.lock.json").exists()


def test_runtime_area_alone_is_not_a_foreign_code_directory(tmp_path):
    result = _build(tmp_path)
    code = tmp_path / "code"
    (code / ".horizon").mkdir(parents=True)
    (code / ".horizon" / "settings.json").write_text("{}\n", encoding="utf-8")

    payload = _install(code, Path(result["artifact"]["path"]))

    assert payload["ok"] is True
    assert (code / ".horizon" / "settings.json").exists()


def test_reinstalling_the_same_artifact_is_idempotent_and_keeps_installed_at(tmp_path):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    code = tmp_path / "code"

    first = _install(code, artifact)
    lock_path = code / ".horizon" / "dist.lock.json"
    lock_bytes = lock_path.read_bytes()
    installed_at = json.loads(lock_bytes)["installed_at"]
    current_path = code / ".horizon" / "current"
    current_bytes = current_path.read_bytes()
    mtime = (code / "hostrun" / "cli.py").stat().st_mtime_ns
    lock_mtime = lock_path.stat().st_mtime_ns

    second = _install(code, artifact)

    assert second["ok"] is True
    assert second["status"] == "ok"
    assert second["extracted"] is False
    assert second["installed_at"] == installed_at
    assert lock_path.read_bytes() == lock_bytes
    assert lock_path.stat().st_mtime_ns == lock_mtime
    assert current_path.read_bytes() == current_bytes
    assert (code / "hostrun" / "cli.py").stat().st_mtime_ns == mtime
    assert first["artifact_sha256"] == second["artifact_sha256"]


def test_a_different_artifact_reinstalls_over_the_existing_package(tmp_path):
    first = _build(tmp_path, name="first", version="0.1.0")
    code = tmp_path / "code"
    assert _install(code, Path(first["artifact"]["path"]))["ok"] is True

    second = _build(tmp_path, name="second", version="0.2.0")
    payload = _install(code, Path(second["artifact"]["path"]))

    assert payload["ok"] is True
    assert payload["extracted"] is True
    assert payload["version"] == "0.2.0"
    lock = json.loads((code / ".horizon" / "dist.lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == "0.2.0"
    assert lock["artifact_sha256"] == second["artifact"]["sha256"]
    assert lock["previous_version"] == "0.1.0"
    assert (code / ".horizon" / "releases" / "0.1.0" / "manifest.json").is_file()
    assert (code / ".horizon" / "current").read_text(encoding="utf-8").strip() == "0.2.0"


# -------------------------------------------------------------------- verify
def test_verify_tolerates_bytecode_caches_written_by_the_installed_cli(tmp_path):
    """Running the installed CLI writes ``__pycache__`` into the code directory.

    A live installation therefore always contains bytecode caches the manifest
    never listed.  ``install verify`` must treat them as runtime state, exactly
    like ``.horizon``, or every real user's first verify would report a false
    mismatch against files the installation itself created.
    """
    result = _build(tmp_path)
    code = tmp_path / "code"
    _install(code, Path(result["artifact"]["path"]))
    cache = code / "hostrun" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "cli.cpython-313.pyc").write_bytes(b"bytecode")
    (code / "stray.pyc").write_bytes(b"bytecode")

    payload = dist.verify_install(code)

    assert payload["ok"] is True
    assert payload["status"] == "verified"
    assert payload["extra_manifest_files"] == []


def test_verify_reports_a_consistent_install(tmp_path):
    result = _build(tmp_path)
    code = tmp_path / "code"
    _install(code, Path(result["artifact"]["path"]))

    payload = dist.verify_install(code)

    assert payload["ok"] is True
    assert payload["status"] == "verified"
    assert payload.get("reason") is None
    assert payload["version"] == "0.1.0"
    assert payload["missing"] == []
    assert payload["mismatch"] == []
    assert payload["extra_manifest_files"] == []
    assert payload["lock_mismatch"] == []


def test_verify_without_a_lock_is_blocked(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    payload = dist.verify_install(code)

    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "no_install_lock"


def test_verify_reports_missing_files(tmp_path):
    result = _build(tmp_path)
    code = tmp_path / "code"
    _install(code, Path(result["artifact"]["path"]))
    (code / "hostrun" / "cli.py").unlink()

    payload = dist.verify_install(code)

    assert payload["status"] == "mismatch"
    assert payload["ok"] is False
    assert payload["missing"] == ["hostrun/cli.py"]


def test_verify_reports_byte_and_digest_mismatches(tmp_path):
    result = _build(tmp_path)
    code = tmp_path / "code"
    _install(code, Path(result["artifact"]["path"]))
    (code / "common.py").write_text("TAMPERED\n", encoding="utf-8")

    payload = dist.verify_install(code)

    assert payload["status"] == "mismatch"
    assert [row["path"] for row in payload["mismatch"]] == ["common.py"]
    row = payload["mismatch"][0]
    assert row["field"] in ("bytes", "sha256", "bytes,sha256")
    assert row["expected"]["bytes"] != row["actual"]["bytes"]


def test_verify_reports_files_that_are_not_in_the_manifest(tmp_path):
    result = _build(tmp_path)
    code = tmp_path / "code"
    _install(code, Path(result["artifact"]["path"]))
    (code / "rogue.txt").write_text("extra\n", encoding="utf-8")

    payload = dist.verify_install(code)

    assert payload["status"] == "mismatch"
    assert payload["extra_manifest_files"] == ["rogue.txt"]


def test_verify_reports_lock_disagreements_with_the_manifest(tmp_path):
    result = _build(tmp_path)
    code = tmp_path / "code"
    _install(code, Path(result["artifact"]["path"]))
    lock_path = code / ".horizon" / "dist.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["version"] = "9.9.9"
    lock["package_file_count"] = 999
    lock["manifest_sha256"] = "0" * 64
    lock_path.write_text(json.dumps(lock), encoding="utf-8")

    payload = dist.verify_install(code)

    assert payload["status"] == "mismatch"
    fields = {row["field"] for row in payload["lock_mismatch"]}
    assert {"version", "package_file_count", "manifest_sha256"} <= fields


def test_verify_detects_a_tampered_manifest_even_when_every_file_matches(tmp_path):
    result = _build(tmp_path)
    code = tmp_path / "code"
    _install(code, Path(result["artifact"]["path"]))
    manifest_path = code / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "9.9.9"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    payload = dist.verify_install(code)

    assert payload["status"] == "mismatch"
    fields = {row["field"] for row in payload["lock_mismatch"]}
    assert "manifest_sha256" in fields
    assert "version" in fields


# ----------------------------------------------------------------------- CLI
def test_cli_install_then_verify_returns_json_envelopes_and_exit_zero(tmp_path, capsys):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    home = tmp_path / "cli-code" / ".horizon"

    code, payload, captured = _cli(
        capsys, home, "install", "package", "--from", str(artifact)
    )

    assert code == 0
    assert captured.err == ""
    assert payload["ok"] is True
    assert payload["status"] == "ok"
    assert payload["distribution"] == "package"
    assert (tmp_path / "cli-code" / ".horizon" / "dist.lock.json").is_file()

    code, payload, captured = _cli(capsys, home, "install", "verify")

    assert code == 0
    assert captured.err == ""
    assert payload["status"] == "verified"


def test_cli_digest_mismatch_exits_one_with_a_blocked_payload(tmp_path, capsys):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    home = tmp_path / "cli-code" / ".horizon"

    code, payload, captured = _cli(
        capsys,
        home,
        "install",
        "package",
        "--from",
        str(artifact),
        "--sha256",
        "0" * 64,
    )

    assert code == 1
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["reason"] == "digest_mismatch"
    assert "Traceback" not in captured.err
    assert not (tmp_path / "cli-code" / ".horizon" / "dist.lock.json").exists()


def test_cli_verify_without_a_lock_exits_one(tmp_path, capsys):
    home = tmp_path / "cli-code" / ".horizon"

    code, payload, captured = _cli(capsys, home, "install", "verify")

    assert code == 1
    assert payload["status"] == "blocked"
    assert payload["reason"] == "no_install_lock"
    assert "Traceback" not in captured.err


def test_cli_verify_mismatch_exits_one_with_specifics(tmp_path, capsys):
    result = _build(tmp_path)
    artifact = Path(result["artifact"]["path"])
    home = tmp_path / "cli-code" / ".horizon"
    code, payload, _ = _cli(
        capsys, home, "install", "package", "--from", str(artifact)
    )
    assert code == 0
    (tmp_path / "cli-code" / "common.py").unlink()

    code, payload, captured = _cli(capsys, home, "install", "verify")

    assert code == 1
    assert payload["ok"] is False
    assert payload["status"] == "mismatch"
    assert payload["missing"] == ["common.py"]
    assert "Traceback" not in captured.err


def test_cli_channel_flag_is_recorded_in_the_lock(tmp_path, capsys):
    result = _build(tmp_path, channel="prerelease")
    artifact = Path(result["artifact"]["path"])
    home = tmp_path / "cli-code" / ".horizon"

    code, payload, _ = _cli(
        capsys,
        home,
        "install",
        "package",
        "--from",
        str(artifact),
        "--channel",
        "prerelease",
    )

    assert code == 0
    assert payload["channel"] == "prerelease"
    lock = json.loads(
        (tmp_path / "cli-code" / ".horizon" / "dist.lock.json").read_text(
            encoding="utf-8"
        )
    )
    assert lock["channel"] == "prerelease"
