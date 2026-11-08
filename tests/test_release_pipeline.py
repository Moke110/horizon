"""Release pipeline seams: tag semantics, release index, runtime assembly, CI workflow.

Ticket #98 adds the tag-triggered release pipeline.  A tag alone names the
version and channel, the release index records what was published, and CI
assembles the bundled runtime from pinned downloads before calling the #93/#95
build entry.  Every test here is offline: tags are strings, indexes are JSON
files, runtime inputs are synthetic zips, and the workflow is parsed, never
executed.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from hostrun import distribution, release

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
INDEX_PATH = ROOT / "releases" / "index.json"
REQUIREMENTS = ROOT / "requirements-release.txt"
FIXED_SHA = "a" * 64
FIXED_SHA_B = "b" * 64


def _load_script(name: str):
    """Import a development script without making ``scripts`` a package."""
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"release_script_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------- tag semantics
@pytest.mark.parametrize(
    ("tag", "version", "channel"),
    [
        ("v1.2.3", "1.2.3", "stable"),
        ("v0.1.0", "0.1.0", "stable"),
        ("v10.20.30", "10.20.30", "stable"),
        ("v0.1.0-rc.1", "0.1.0-rc.1", "prerelease"),
        ("v0.1.0-preview.2", "0.1.0-preview.2", "prerelease"),
        ("v0.1.0-prerelease.1", "0.1.0-prerelease.1", "prerelease"),
        ("v2.0.0-beta", "2.0.0-beta", "prerelease"),
    ],
)
def test_tag_names_version_and_channel(tag, version, channel):
    meta = release.parse_tag(tag)
    assert meta["tag"] == tag
    assert meta["version"] == version
    assert meta["channel"] == channel
    assert meta["prerelease"] is (channel == "prerelease")


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "v",
        "1.2.3",
        "V1.2.3",
        "v1.2",
        "v1.2.3.4",
        "v01.2.3",
        "v1.2.3-",
        "v1.2.3-rc..1",
        "v1.2.3+meta",
        "v1.2.3-rc.01",
        "v1.2.3 ",
        "vv1.2.3",
        "v1..3",
    ],
)
def test_malformed_tags_are_rejected_with_a_reason(tag):
    with pytest.raises(release.ReleaseError) as excinfo:
        release.parse_tag(tag)
    message = str(excinfo.value)
    assert message
    assert "tag" in message.lower()


def test_explicit_channel_must_agree_with_the_tag():
    assert release.parse_tag("v1.2.3", channel="stable")["channel"] == "stable"
    assert (
        release.parse_tag("v1.2.3-rc.1", channel="prerelease")["channel"]
        == "prerelease"
    )
    with pytest.raises(release.ReleaseError):
        release.parse_tag("v1.2.3", channel="prerelease")
    with pytest.raises(release.ReleaseError):
        release.parse_tag("v1.2.3", channel="nightly")


@pytest.mark.parametrize(
    ("version", "channel"),
    [
        ("1.2.3", "stable"),
        ("1.2.3-rc.1", "prerelease"),
        ("1.2.3-preview.2", "prerelease"),
    ],
)
def test_artifact_name_is_derived_from_version_and_channel(version, channel):
    assert (
        release.expected_artifact_name(version, channel)
        == f"horizon-{version}-{channel}.zip"
    )


def test_release_title_and_notes_describe_the_tag_and_assets():
    meta = release.parse_tag("v0.2.0-rc.1")
    assert release.release_title(meta) == "horizon 0.2.0-rc.1 (prerelease)"

    notes = release.release_notes(
        meta,
        artifact_name="horizon-0.2.0-rc.1-prerelease.zip",
        artifact_sha256=FIXED_SHA,
        manifest_sha256=FIXED_SHA_B,
        compat={"os": "windows", "arch": "x86_64", "dsh": "*", "python": "3.13"},
        built_from_commit="deadbeef",
        repository="Moke110/horizon",
    )
    assert "0.2.0-rc.1" in notes
    assert "prerelease" in notes
    assert FIXED_SHA in notes
    assert FIXED_SHA_B in notes
    assert "deadbeef" in notes
    assert "windows" in notes
    assert "3.13" in notes
    assert "horizon-0.2.0-rc.1-prerelease.zip" in notes
    assert "releases/index.json" in notes


# -------------------------------------------------------------- release index
def _entry(**overrides):
    entry = {
        "version": "1.2.3",
        "channel": "stable",
        "artifact_sha256": FIXED_SHA,
        "artifact_name": "horizon-1.2.3-stable.zip",
        "manifest_sha256": FIXED_SHA_B,
        "compat": {"os": "windows", "arch": "x86_64", "dsh": "*", "python": "3.13"},
        "published_at": "2026-10-31T05:00:00+00:00",
        "release_url": "https://github.com/Moke110/horizon/releases/tag/v1.2.3",
    }
    entry.update(overrides)
    if "artifact_name" not in overrides and (
        "version" in overrides or "channel" in overrides
    ):
        entry["artifact_name"] = release.expected_artifact_name(
            entry["version"], entry["channel"]
        )
    return entry


def test_empty_index_starts_at_schema_version_one():
    index = release.empty_index()
    assert index == {"schema_version": 1, "releases": []}
    assert json.loads(release.dump_index(index)) == index


def test_merge_appends_a_release_and_sorts_newest_first():
    index = release.merge_release_index(release.empty_index(), _entry(version="0.1.0"))
    index = release.merge_release_index(
        index, _entry(version="0.2.0", artifact_sha256=FIXED_SHA_B)
    )
    index = release.merge_release_index(
        index,
        _entry(
            version="0.1.0-rc.2",
            channel="prerelease",
            artifact_sha256=FIXED_SHA,
        ),
    )
    assert [row["version"] for row in index["releases"]] == [
        "0.2.0",
        "0.1.0",
        "0.1.0-rc.2",
    ]
    assert index["schema_version"] == 1


def test_merging_the_same_version_replaces_the_entry_in_place():
    index = release.empty_index()
    index = release.merge_release_index(index, _entry(version="0.1.0"))
    merged = release.merge_release_index(
        index,
        _entry(
            version="0.1.0",
            artifact_sha256=FIXED_SHA_B,
            published_at="2026-11-01T05:00:00+00:00",
        ),
    )
    assert len(merged["releases"]) == 1
    assert merged["releases"][0]["artifact_sha256"] == FIXED_SHA_B
    assert merged["releases"][0]["published_at"] == "2026-11-01T05:00:00+00:00"
    # The input index is not mutated by a merge.
    assert index["releases"][0]["artifact_sha256"] == FIXED_SHA


def test_dump_index_is_deterministic_and_canonically_ordered():
    first = release.dump_index(
        release.merge_release_index(release.empty_index(), _entry())
    )
    reordered = {
        "releases": [
            {
                key: value
                for key, value in reversed(list(_entry().items()))
            }
        ],
        "schema_version": 1,
    }
    second = release.dump_index(reordered)
    assert first == second
    assert first.endswith("\n")
    payload = json.loads(first)
    assert list(payload) == ["schema_version", "releases"]
    assert list(payload["releases"][0]) == [
        "version",
        "channel",
        "artifact_sha256",
        "artifact_name",
        "manifest_sha256",
        "compat",
        "published_at",
        "release_url",
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda entry: entry.pop("artifact_sha256"), id="missing-sha"),
        pytest.param(
            lambda entry: entry.update(artifact_sha256="not-a-digest"), id="bad-sha"
        ),
        pytest.param(lambda entry: entry.update(channel="nightly"), id="bad-channel"),
        pytest.param(
            lambda entry: entry.update(
                version="1.2.3-rc.1", channel="stable"
            ),
            id="channel-version-mismatch",
        ),
        pytest.param(
            lambda entry: entry.update(artifact_name="other.zip"), id="wrong-artifact"
        ),
        pytest.param(
            lambda entry: entry.update(published_at="2026-10-31"),
            id="naive-timestamp",
        ),
        pytest.param(
            lambda entry: entry.update(release_url="not-a-url"), id="bad-url"
        ),
        pytest.param(
            lambda entry: entry["compat"].pop("python"), id="incomplete-compat"
        ),
        pytest.param(
            lambda entry: entry.update(extra="unexpected"), id="unknown-field"
        ),
        pytest.param(lambda entry: entry.update(version="1.2"), id="bad-version"),
    ],
)
def test_malformed_release_entries_are_rejected(mutate):
    entry = _entry()
    mutate(entry)
    with pytest.raises(release.ReleaseError):
        release.merge_release_index(release.empty_index(), entry)


def test_index_validation_rejects_wrong_schema_and_duplicate_versions():
    with pytest.raises(release.ReleaseError):
        release.validate_index({"schema_version": 2, "releases": []})
    with pytest.raises(release.ReleaseError):
        release.validate_index({"releases": []})
    duplicate = {
        "schema_version": 1,
        "releases": [_entry(version="0.1.0"), _entry(version="0.1.0")],
    }
    with pytest.raises(release.ReleaseError):
        release.validate_index(duplicate)


def test_release_entry_is_built_from_the_digest_file():
    digest = {
        "schema_version": 1,
        "package": "horizon-0.1.0-stable",
        "version": "0.1.0",
        "channel": "stable",
        "built_from_commit": "deadbeef",
        "compat": {"os": "windows", "arch": "x86_64", "dsh": "*", "python": "3.13"},
        "manifest_sha256": FIXED_SHA_B,
        "artifact": {
            "name": "horizon-0.1.0-stable.zip",
            "bytes": 123,
            "sha256": FIXED_SHA,
        },
    }
    entry = release.entry_from_digest(
        digest,
        release_url="https://github.com/Moke110/horizon/releases/tag/v0.1.0",
        published_at="2026-10-31T05:00:00+00:00",
    )
    assert entry["version"] == "0.1.0"
    assert entry["artifact_name"] == "horizon-0.1.0-stable.zip"
    assert entry["artifact_sha256"] == FIXED_SHA
    assert entry["manifest_sha256"] == FIXED_SHA_B
    assert entry["compat"]["python"] == "3.13"

    bad = json.loads(json.dumps(digest))
    bad["artifact"]["sha256"] = "short"
    with pytest.raises(release.ReleaseError):
        release.entry_from_digest(
            bad,
            release_url="https://github.com/Moke110/horizon/releases/tag/v0.1.0",
            published_at="2026-10-31T05:00:00+00:00",
        )


# ------------------------------------------------------------ build arguments
def test_build_argv_carries_release_inputs_and_explicit_compat():
    argv = release.build_argv(
        version="0.1.0-rc.1",
        channel="prerelease",
        commit="cafebabe",
        runtime_dir=Path("runtime"),
        source=".",
        out="dist",
        compat={"os": "windows", "arch": "x86_64", "dsh": "*", "python": "3.13"},
    )
    assert argv[:3] == ["python", "-m", "hostrun.distribution"]
    # Assert each flag/value pair explicitly; ordering is part of the contract.
    for flag, value in [
        ("--source", "."),
        ("--out", "dist"),
        ("--version", "0.1.0-rc.1"),
        ("--channel", "prerelease"),
        ("--built-from-commit", "cafebabe"),
        ("--runtime-dir", "runtime"),
        ("--os", "windows"),
        ("--arch", "x86_64"),
        ("--python", "3.13"),
        ("--dsh", "*"),
    ]:
        assert flag in argv
        assert argv[argv.index(flag) + 1] == value
    assert release.build_argv(
        version="0.1.0",
        channel="stable",
        commit="cafebabe",
        runtime_dir="runtime",
    )[:2] == ["python", "-m"]


def test_build_argv_rejects_an_invalid_version_or_empty_commit():
    with pytest.raises(release.ReleaseError):
        release.build_argv(
            version="0.1", channel="stable", commit="cafe", runtime_dir="runtime"
        )
    with pytest.raises(release.ReleaseError):
        release.build_argv(
            version="0.1.0", channel="stable", commit="", runtime_dir="runtime"
        )


# ------------------------------------------------------------- runtime inputs
def _write_runtime(root: Path, python_version: str = "3.13") -> Path:
    tag = python_version.replace(".", "")
    (root / "DLLs").mkdir(parents=True)
    (root / "Lib" / "site-packages").mkdir(parents=True)
    (root / "python.exe").write_bytes(b"MZ fake interpreter")
    (root / f"python{tag}.dll").write_bytes(b"dll")
    (root / f"python{tag}._pth").write_text(
        f"python{tag}.zip\r\n.\r\n", encoding="utf-8", newline=""
    )
    (root / f"python{tag}.zip").write_bytes(b"PK fake stdlib")
    (root / "DLLs" / "select.pyd").write_bytes(b"pyd")
    (root / "Lib" / "site-packages" / "yaml").mkdir()
    (root / "Lib" / "site-packages" / "yaml" / "__init__.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    return root


def _write_embeddable_zip(path: Path, python_version: str = "3.13") -> Path:
    tag = python_version.replace(".", "")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("python.exe", b"MZ fake interpreter")
        archive.writestr(f"python{tag}.dll", b"dll")
        archive.writestr(f"python{tag}._pth", f"python{tag}.zip\r\n.\r\n")
        archive.writestr(f"python{tag}.zip", b"PK fake stdlib")
        archive.writestr("select.pyd", b"pyd")  # the real embeddable zip is flat
        archive.writestr("LICENSE.txt", "embed license\n")
    return path


def _write_wheel(
    path: Path,
    *,
    package: str = "samplepkg",
    dist: str = "samplepkg",
    version: str = "1.0.0",
) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{package}/__init__.py", "VALUE = 1\n")
        archive.writestr(
            f"{dist}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n",
        )
    return path


def test_runtime_validation_accepts_the_embeddable_layout(tmp_path):
    runtime = _write_runtime(tmp_path / "runtime")
    summary = release.validate_runtime_dir(
        runtime, python_version="3.13", required_modules=("yaml",)
    )
    assert summary["ok"] is True
    assert summary["python"] == "3.13"
    assert summary["modules"] == ["yaml"]
    assert summary["files"] >= 6


def test_runtime_validation_accepts_flat_extension_modules(tmp_path):
    """The official embeddable zip keeps its ``.pyd`` files next to python.exe.

    The CI runtime is assembled from that zip; requiring a ``DLLs/`` directory
    would reject the real download, so a flat layout must validate too.
    """
    runtime = tmp_path / "runtime-flat"
    (runtime / "Lib" / "site-packages" / "yaml").mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"MZ fake interpreter")
    (runtime / "python313.dll").write_bytes(b"dll")
    (runtime / "python313._pth").write_text(
        "python313.zip\r\n.\r\n", encoding="utf-8", newline=""
    )
    (runtime / "python313.zip").write_bytes(b"PK fake stdlib")
    (runtime / "select.pyd").write_bytes(b"pyd")

    summary = release.validate_runtime_dir(runtime, python_version="3.13")

    assert summary["ok"] is True


@pytest.mark.parametrize(
    ("relative", "reason"),
    [
        ("python.exe", "python.exe"),
        ("python313.dll", "python313.dll"),
        ("python313._pth", "python313._pth"),
        ("DLLs", "extension modules"),
        ("Lib/site-packages", "Lib/site-packages/"),
    ],
)
def test_runtime_validation_names_the_missing_input(tmp_path, relative, reason):
    runtime = _write_runtime(tmp_path / "runtime")
    target = runtime / relative
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    with pytest.raises(release.ReleaseError) as excinfo:
        release.validate_runtime_dir(runtime)
    assert reason in str(excinfo.value)


def test_runtime_validation_reports_missing_required_modules(tmp_path):
    runtime = _write_runtime(tmp_path / "runtime")
    with pytest.raises(release.ReleaseError) as excinfo:
        release.validate_runtime_dir(runtime, required_modules=("yaml", "fitz"))
    assert "fitz" in str(excinfo.value)
    assert "yaml" not in str(excinfo.value).split("modules:", 1)[1]


# ----------------------------------------------------------- prepare_runtime
def test_prepare_runtime_assembles_embeddable_zip_and_wheels(tmp_path):
    script = _load_script("prepare_runtime")
    embed = _write_embeddable_zip(tmp_path / "python-embed.zip")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _write_wheel(wheels / "samplepkg-1.0.0-py3-none-any.whl")
    out = tmp_path / "runtime"

    summary = script.prepare_runtime(
        embed,
        wheels,
        out,
        python_version="3.13",
        required_modules=("samplepkg",),
    )

    assert summary["ok"] is True
    assert Path(summary["runtime"]) == out.resolve()
    assert (out / "python.exe").is_file()
    assert (out / "python313.dll").is_file()
    assert (out / "python313.zip").is_file()
    assert (out / "select.pyd").is_file()
    pth = (out / "python313._pth").read_text(encoding="utf-8")
    assert pth.splitlines()[0] == "python313.zip"
    assert "Lib\\site-packages" in pth.splitlines()
    assert (out / "Lib" / "site-packages" / "samplepkg" / "__init__.py").is_file()
    assert (
        out / "Lib" / "site-packages" / "samplepkg-1.0.0.dist-info" / "METADATA"
    ).is_file()
    assert summary["wheels"] == ["samplepkg-1.0.0-py3-none-any.whl"]


def test_prepare_runtime_pth_exposes_the_code_root_and_site_packages_once(tmp_path):
    """The isolated ``._pth`` must put the code root (``..``) on ``sys.path``.

    The wrapper runs ``runtime\\python.exe -m hostrun.cli`` from the code root,
    but an isolated embeddable interpreter ignores ``sys.path[0]``; without
    ``..`` in ``python3XX._pth``, ``import hostrun``/``common`` fails in a real
    install even though the site-packages wheels are present.
    """
    script = _load_script("prepare_runtime")
    embed = _write_embeddable_zip(tmp_path / "python-embed.zip")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _write_wheel(wheels / "samplepkg-1.0.0-py3-none-any.whl")
    out = tmp_path / "runtime"

    summary = script.prepare_runtime(embed, wheels, out)

    assert summary["ok"] is True
    lines = (out / "python313._pth").read_text(encoding="utf-8").splitlines()
    # The embeddable zip's original entries stay first and unchanged.
    assert lines[:2] == ["python313.zip", "."]
    assert lines.count("..") == 1
    assert lines.count("Lib\\site-packages") == 1


def test_prepare_runtime_maps_wheel_purelib_and_keeps_the_pth_append_idempotent(tmp_path):
    script = _load_script("prepare_runtime")
    embed = _write_embeddable_zip(tmp_path / "python-embed.zip")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = wheels / "extra-2.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("extra-2.0.0.data/purelib/extra/__init__.py", "X = 2\n")
    out = tmp_path / "runtime"

    summary = script.prepare_runtime(embed, wheels, out)

    assert (out / "Lib" / "site-packages" / "extra" / "__init__.py").is_file()
    pth = (out / "python313._pth").read_text(encoding="utf-8")
    assert pth.count("Lib\\site-packages") == 1
    assert summary["ok"] is True


def test_prepare_runtime_fails_with_a_reason_when_inputs_are_missing(tmp_path):
    script = _load_script("prepare_runtime")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _write_wheel(wheels / "samplepkg-1.0.0-py3-none-any.whl")
    with pytest.raises(release.ReleaseError) as excinfo:
        script.prepare_runtime(tmp_path / "absent.zip", wheels, tmp_path / "runtime")
    assert "embeddable" in str(excinfo.value).lower()

    embed = _write_embeddable_zip(tmp_path / "python-embed.zip")
    empty = tmp_path / "empty-wheels"
    empty.mkdir()
    with pytest.raises(release.ReleaseError) as excinfo:
        script.prepare_runtime(embed, empty, tmp_path / "runtime")
    assert ".whl" in str(excinfo.value)

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(release.ReleaseError) as excinfo:
        script.prepare_runtime(bad, wheels, tmp_path / "runtime")
    assert "zip" in str(excinfo.value).lower()


def test_prepare_runtime_refuses_a_non_empty_output_and_reports_cli_errors(tmp_path, capsys):
    script = _load_script("prepare_runtime")
    embed = _write_embeddable_zip(tmp_path / "python-embed.zip")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _write_wheel(wheels / "samplepkg-1.0.0-py3-none-any.whl")
    out = tmp_path / "runtime"
    out.mkdir()
    (out / "stale.txt").write_text("stale\n", encoding="utf-8")

    with pytest.raises(release.ReleaseError) as excinfo:
        script.prepare_runtime(embed, wheels, out)
    assert "not empty" in str(excinfo.value)

    exit_code = script.main(
        [
            "--embed-zip",
            str(tmp_path / "absent.zip"),
            "--wheels",
            str(wheels),
            "--out",
            str(tmp_path / "runtime-2"),
        ]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "embeddable" in captured.out.lower()


# ----------------------------------------------------- update_release_index
def _digest_payload(version: str = "0.1.0", channel: str = "stable") -> dict:
    return {
        "schema_version": 1,
        "package": f"horizon-{version}-{channel}",
        "version": version,
        "channel": channel,
        "built_from_commit": "deadbeef",
        "compat": {"os": "windows", "arch": "x86_64", "dsh": "*", "python": "3.13"},
        "manifest_sha256": FIXED_SHA_B,
        "artifact": {
            "name": f"horizon-{version}-{channel}.zip",
            "bytes": 321,
            "sha256": FIXED_SHA,
        },
    }


def test_update_release_index_writes_a_deterministic_newest_first_index(tmp_path):
    script = _load_script("update_release_index")
    index_path = tmp_path / "releases" / "index.json"
    digest = tmp_path / "digest.json"
    digest.write_text(json.dumps(_digest_payload()), encoding="utf-8")
    url = "https://github.com/Moke110/horizon/releases/tag/v0.1.0"

    exit_code = script.main(
        [
            "--index",
            str(index_path),
            "--digest",
            str(digest),
            "--release-url",
            url,
            "--published-at",
            "2026-10-31T05:00:00+00:00",
        ]
    )

    assert exit_code == 0
    first = json.loads(index_path.read_text(encoding="utf-8"))
    assert first["schema_version"] == 1
    assert [row["version"] for row in first["releases"]] == ["0.1.0"]
    entry = first["releases"][0]
    assert entry["artifact_name"] == "horizon-0.1.0-stable.zip"
    assert entry["artifact_sha256"] == FIXED_SHA
    assert entry["manifest_sha256"] == FIXED_SHA_B
    assert entry["release_url"] == url

    prerelease = _digest_payload(version="0.2.0-rc.1", channel="prerelease")
    digest.write_text(json.dumps(prerelease), encoding="utf-8")
    assert script.main(
        [
            "--index",
            str(index_path),
            "--digest",
            str(digest),
            "--release-url",
            "https://github.com/Moke110/horizon/releases/tag/v0.2.0-rc.1",
            "--published-at",
            "2026-11-01T05:00:00+00:00",
        ]
    ) == 0
    merged = json.loads(index_path.read_text(encoding="utf-8"))
    assert [row["version"] for row in merged["releases"]] == ["0.2.0-rc.1", "0.1.0"]

    # Republishing the same version replaces its row instead of appending.
    replacement = _digest_payload()
    replacement["artifact"]["sha256"] = FIXED_SHA_B
    digest.write_text(json.dumps(replacement), encoding="utf-8")
    assert script.main(
        [
            "--index",
            str(index_path),
            "--digest",
            str(digest),
            "--release-url",
            url,
            "--published-at",
            "2026-11-02T05:00:00+00:00",
        ]
    ) == 0
    replaced = json.loads(index_path.read_text(encoding="utf-8"))
    assert len(replaced["releases"]) == 2
    stable = next(row for row in replaced["releases"] if row["version"] == "0.1.0")
    assert stable["artifact_sha256"] == FIXED_SHA_B


def test_update_release_index_rejects_a_bad_digest_and_leaves_the_index_alone(tmp_path):
    script = _load_script("update_release_index")
    index_path = tmp_path / "releases" / "index.json"
    original = json.dumps(release.empty_index(), indent=2) + "\n"
    index_path.parent.mkdir(parents=True)
    index_path.write_text(original, encoding="utf-8")
    digest = tmp_path / "digest.json"
    digest.write_text(json.dumps({"version": "0.1.0"}), encoding="utf-8")

    exit_code = script.main(
        [
            "--index",
            str(index_path),
            "--digest",
            str(digest),
            "--release-url",
            "https://github.com/Moke110/horizon/releases/tag/v0.1.0",
        ]
    )

    assert exit_code == 1
    assert index_path.read_text(encoding="utf-8") == original


# ------------------------------------------------- artifact checks (real build)
def _fixture_source(root: Path) -> Path:
    files = {
        "common.py": "RUNTIME = True\n",
        "requirements.txt": "PyYAML>=6.0\n",
        "README.md": "# fixture horizon\n",
        "LICENSE": "MIT\n",
        "hostrun/__init__.py": "",
        "hostrun/cli.py": "CLI = 1\n",
        "skills/horizon-run/SKILL.md": "---\nname: horizon-run\n---\n",
        "contracts/README.md": "contracts\n",
        "docs/adr/0010-distribution.md": "# decision\n",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _build_release(tmp_path: Path, out_name: str):
    source = _fixture_source(tmp_path / "src")
    runtime = _write_runtime(tmp_path / f"runtime-{out_name}")
    built = distribution.build(
        source=source,
        out=tmp_path / out_name,
        version="0.1.0",
        channel="stable",
        commit="cafebabe",
        compat={"os": "windows", "arch": "x86_64", "dsh": "*", "python": "3.13"},
        runtime_dir=runtime,
    )
    return source, runtime, built


def test_verify_artifact_requires_runtime_wrapper_and_expected_digest(tmp_path):
    _, _, built = _build_release(tmp_path, "dist-a")

    report = release.verify_artifact(
        built["artifact"]["path"], expected_sha256=built["artifact"]["sha256"]
    )
    assert report["ok"] is True
    assert "runtime/python.exe" in report["required"]
    assert "bin/horizon.cmd" in report["required"]

    with pytest.raises(release.ReleaseError) as excinfo:
        release.verify_artifact(built["artifact"]["path"], expected_sha256="0" * 64)
    assert built["artifact"]["sha256"] in str(excinfo.value)


def test_reproducibility_report_compares_both_builds(tmp_path):
    _, _, first = _build_release(tmp_path, "dist-a")
    _, _, second = _build_release(tmp_path, "dist-b")

    report = release.reproducibility_report(
        Path(first["artifact"]["path"]).parent,
        Path(second["artifact"]["path"]).parent,
        "0.1.0",
        "stable",
    )
    assert report["ok"] is True
    assert report["first_sha256"] == report["second_sha256"]
    assert report["first_manifest_sha256"] == report["second_manifest_sha256"]

    # Touching one byte of the second artifact must be reported with both hashes.
    second_artifact = Path(second["artifact"]["path"])
    second_artifact.write_bytes(second_artifact.read_bytes() + b"tampered")
    broken = release.reproducibility_report(
        Path(first["artifact"]["path"]).parent,
        second_artifact.parent,
        "0.1.0",
        "stable",
    )
    assert broken["ok"] is False
    assert broken["first_sha256"] != broken["second_sha256"]


# --------------------------------------------------------------- CLI outputs
def test_tag_cli_writes_github_actions_outputs(tmp_path, capsys):
    output = tmp_path / "github_output"
    exit_code = release.main(
        [
            "tag",
            "--tag",
            "v0.1.0-rc.1",
            "--github-output",
            str(output),
        ]
    )
    assert exit_code == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert "version=0.1.0-rc.1" in lines
    assert "channel=prerelease" in lines
    assert "prerelease=true" in lines
    assert "tag=v0.1.0-rc.1" in lines
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert release.main(["tag", "--tag", "0.1.0"]) == 1
    assert "not a release tag" in capsys.readouterr().err


def test_reproducibility_cli_fails_and_prints_both_hashes(tmp_path, capsys):
    _, _, first = _build_release(tmp_path, "dist-a")
    _, _, second = _build_release(tmp_path, "dist-b")
    second_artifact = Path(second["artifact"]["path"])
    second_artifact.write_bytes(second_artifact.read_bytes() + b"tampered")

    exit_code = release.main(
        [
            "check-reproducible",
            "--first-dir",
            str(Path(first["artifact"]["path"]).parent),
            "--second-dir",
            str(second_artifact.parent),
            "--version",
            "0.1.0",
            "--channel",
            "stable",
        ]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert release.artifact_sha256(Path(first["artifact"]["path"])) in captured.out
    assert release.artifact_sha256(second_artifact) in captured.out
    assert "reproducibility check failed" in captured.err


# ------------------------------------------------------- CI workflow contract
def _workflow() -> dict:
    import yaml

    assert WORKFLOW.is_file(), "the release workflow is missing"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(workflow: dict) -> list[dict]:
    return workflow["jobs"]["release"]["steps"]


def _run_text(steps: list[dict]) -> str:
    return "\n".join(str(step.get("run") or "") for step in steps)


def test_workflow_parses_and_is_tag_triggered_on_a_windows_runner():
    workflow = _workflow()
    triggers = workflow["on"]
    assert triggers["push"]["tags"] == ["v*"]
    dispatch = triggers["workflow_dispatch"]
    assert "tag" in dispatch["inputs"]
    assert dispatch["inputs"]["dry_run"]["type"] == "boolean"
    assert workflow["permissions"]["contents"] == "write"
    job = workflow["jobs"]["release"]
    assert job["runs-on"] == "windows-latest"

    steps = _steps(workflow)
    checkout = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["fetch-depth"] == 0
    setup = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/setup-python")
    )
    assert str(setup["with"]["python-version"]) == "3.13"


def test_workflow_derives_identity_with_the_tested_module_not_inline_shell():
    text = _run_text(_steps(_workflow()))
    assert "hostrun.release" in text
    assert " tag " in text or '"tag"' in text or "tag --tag" in text


def test_workflow_assembles_the_runtime_from_pinned_downloads():
    text = _run_text(_steps(_workflow()))
    assert "python.org" in text
    assert "embed-amd64.zip" in text
    assert "pip download" in text
    assert "--only-binary=:all:" in text
    assert "requirements-release.txt" in text
    assert "scripts/prepare_runtime.py" in text
    assert "--require-module yaml" in text
    assert "--require-module fitz" in text


def test_workflow_builds_with_explicit_inputs_and_a_bundled_runtime():
    workflow = _workflow()
    text = _run_text(_steps(workflow))
    assert "hostrun.distribution" in text
    assert "--runtime-dir" in text
    assert "--built-from-commit" in text
    assert "github.sha" in text
    for fragment in (
        "--os windows",
        "--arch x86_64",
        "--python",
        "--dsh '*'",
    ):
        assert fragment in text
    assert workflow["env"]["DIST_REPRO_DIR"] == "dist-repro"


def test_workflow_verifies_layout_and_reproducibility():
    text = _run_text(_steps(_workflow()))
    assert "release verify-artifact" in text
    assert "release check-reproducible" in text


def test_workflow_creates_stable_and_prerelease_releases_conditionally():
    steps = _steps(_workflow())
    create_steps = [step for step in steps if "gh release create" in _run_text([step])]
    assert len(create_steps) == 2
    stable = next(
        step for step in create_steps if "stable" in str(step.get("if", ""))
    )
    prerelease = next(
        step for step in create_steps if "prerelease" in str(step.get("if", ""))
    )
    assert "--prerelease" not in _run_text([stable])
    assert "--prerelease" in _run_text([prerelease])
    for step in create_steps:
        assert "inputs.dry_run" in str(step.get("if", ""))
        assert "GH_TOKEN" in step.get("env", {})
        assert ".zip" in _run_text([step])
        assert ".digest.json" in _run_text([step])
        assert "manifest.json" in _run_text([step])


def test_workflow_updates_the_index_and_commits_it_to_main_with_a_retry():
    steps = _steps(_workflow())
    text = _run_text(steps)
    assert "scripts/update_release_index.py" in text
    assert "release upload" in text
    assert "releases/index.json" in text
    assert "github-actions[bot]" in text
    assert "git push origin HEAD:main" in text
    assert "merge-base --is-ancestor" in text
    update = next(
        step
        for step in steps
        if "scripts/update_release_index.py" in _run_text([step])
    )
    assert "inputs.dry_run" in str(update.get("if", ""))


def test_workflow_index_retry_resolves_the_tag_once_and_rebuilds_from_origin_main():
    steps = _steps(_workflow())
    update = next(
        step
        for step in steps
        if "scripts/update_release_index.py" in _run_text([step])
    )
    text = str(update["run"])

    # The tag commit is evidence about the artifact, not about whatever HEAD is
    # after a merge/reset, so resolve it exactly once before the attempt loop.
    assert text.count("$tagCommit =") == 1
    assert text.index("$tagCommit =") < text.index("for ($attempt")

    # Every attempt starts from a clean origin/main, then regenerates the index;
    # a rejected push must never retry on top of the previous local commit.
    reset_at = text.index("git reset --hard origin/main")
    regenerate_at = text.index("scripts/update_release_index.py")
    assert reset_at < regenerate_at
    assert "git merge --ff-only origin/main" not in text
    assert "$head = (git rev-parse HEAD)" not in text

    # Ancestry is checked against the precomputed tag commit, after the reset
    # and regeneration, and before the commit is pushed.
    ancestry = text.index("git merge-base --is-ancestor $tagCommit origin/main")
    commit_at = text.index('git commit -m "chore(release)')
    assert reset_at < regenerate_at < ancestry < commit_at
    assert "git merge-base --is-ancestor $head origin/main" not in text

    # At most one retry, and the final failure still names the Release and the
    # manual index step.
    assert "for ($attempt = 1; $attempt -le 2; $attempt++)" in text
    assert "-le 3" not in text
    assert "$releaseUrl" in text
    assert "update releases/index.json manually" in text


def test_workflow_runs_the_package_install_smoke_after_building_it():
    steps = _steps(_workflow())
    build_at = next(
        index
        for index, step in enumerate(steps)
        if "hostrun.distribution" in _run_text([step])
    )
    smoke_at = next(
        index
        for index, step in enumerate(steps)
        if "scripts/ci_install_smoke.py" in _run_text([step])
    )
    assert smoke_at > build_at, "the install smoke must run on the built artifact"

    assert (ROOT / "scripts" / "ci_install_smoke.py").is_file()
    text = _run_text([steps[smoke_at]])
    for fragment in (
        "--zip",
        "--code",
        "--archive",
        "--skills-target",
        "--check-wrapper",
        "install-smoke",
        "RUNNER_TEMP",
    ):
        assert fragment in text
    # The wrapper check must fail the job, not merely print a reason.
    assert "throw" in text


def test_workflow_dry_run_still_builds_and_smokes_but_publishes_nothing():
    """workflow_dispatch dry_run must build + smoke, and gate every publish step."""
    steps = _steps(_workflow())
    build_at = next(
        index
        for index, step in enumerate(steps)
        if "hostrun.distribution" in _run_text([step])
    )
    smoke_at = next(
        index
        for index, step in enumerate(steps)
        if "scripts/ci_install_smoke.py" in _run_text([step])
    )
    publish_steps = [
        (index, step)
        for index, step in enumerate(steps)
        if any(
            marker in _run_text([step])
            for marker in (
                "gh release create",
                "scripts/update_release_index.py",
                "release upload",
            )
        )
    ]
    assert publish_steps, "the workflow has no publish steps to gate"
    # Build and smoke have no condition: a dry run exercises them.
    assert not steps[build_at].get("if"), "the build must run on a dry run"
    assert not steps[smoke_at].get("if"), "the smoke test must run on a dry run"
    # Every publish step is gated on dry_run (and on non-dispatch tag runs).
    for _, step in publish_steps:
        condition = str(step.get("if") or "")
        assert "inputs.dry_run" in condition
        assert "github.event_name" in condition
    # Publishing happens only after the build + smoke proved the artifact.
    assert max(index for index, _ in publish_steps) > smoke_at > build_at


def test_workflow_pins_actions_by_major_tag_with_a_documented_follow_up():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "SHA-pinning" in text
    # No secret is consumed besides the workflow token.
    assert set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)) == {"GITHUB_TOKEN"}


# ------------------------------------------------------------ repository files
def test_committed_release_index_is_valid():
    """The committed index must validate whether or not releases exist yet."""
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    release.validate_index(payload)
    assert payload["schema_version"] == 1
    assert isinstance(payload["releases"], list)
    versions = [entry["version"] for entry in payload["releases"]]
    assert len(versions) == len(set(versions))


def test_release_requirements_pin_exact_versions():
    text = REQUIREMENTS.read_text(encoding="utf-8")
    pinned = {
        line.split("==", 1)[0].strip().lower(): line.split("==", 1)[1].strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "pyyaml" in pinned
    assert "pymupdf" in pinned
    for version in pinned.values():
        assert version
        assert ">" not in version and "<" not in version


def test_release_contract_documents_tag_channel_index_and_ci():
    contract = (ROOT / "contracts" / "release.md").read_text(encoding="utf-8")
    for fragment in (
        "releases/index.json",
        "vX.Y.Z",
        "prerelease",
        "artifact_sha256",
        "schema_version",
        "runtime",
    ):
        assert fragment in contract


@pytest.mark.parametrize('number', range(1, 7))
def test_historical_frontier_release_records_survive_new_release(number):
    version = f'0.1.0-rc.{number}'
    old = _entry(version=version, channel='prerelease', artifact_name=f'frontier-{version}-prerelease.zip')
    merged = release.merge_release_index({'schema_version': 1, 'releases': [old]}, _entry(version='1.2.3'))
    assert merged['releases'][1]['artifact_name'] == old['artifact_name']
    assert release.expected_artifact_name(version, 'prerelease') == f'horizon-{version}-prerelease.zip'
