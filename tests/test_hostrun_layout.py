"""Directory model: ``--code`` / ``--archive`` and no legacy ``--home``.

These tests describe the two top-level locations and the unified runtime area
under the code directory.  The retired single data root is refused with a
migration pointer.  Everything runs against temporary directories; no command
touches a live installation.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from common import runtime_root
from hostrun import cli, layout
from hostrun.base import HostRunError

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ defaults
def test_defaults_are_the_repo_root_and_a_sibling_archive():
    resolved = layout.resolve()

    assert Path(resolved["code"]) == ROOT
    assert Path(resolved["archive"]) == ROOT.parent / layout.ARCHIVE_DIRNAME
    assert Path(resolved["runtime"]) == ROOT / layout.RUNTIME_DIRNAME


def test_explicit_code_and_archive_are_used_verbatim(tmp_path):
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"

    resolved = layout.resolve(code=str(code), archive=str(archive))

    assert Path(resolved["code"]) == code.resolve()
    assert Path(resolved["archive"]) == archive.resolve()
    assert Path(resolved["runtime"]) == code.resolve() / layout.RUNTIME_DIRNAME


def test_archive_defaults_to_the_code_sibling_when_only_code_is_given(tmp_path):
    code = tmp_path / "checkout"

    resolved = layout.resolve(code=str(code))

    assert Path(resolved["archive"]) == code.resolve().parent / layout.ARCHIVE_DIRNAME


# ------------------------------------------------------------- retired --home
def test_the_resolver_has_no_legacy_home_switch():
    with pytest.raises(TypeError):
        layout.resolve(home="legacy")


def test_the_hostrun_cli_rejects_home_before_the_subcommand(tmp_path, capsys):
    rc = cli.main(["--home", str(tmp_path / "home"), "status"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert "--home" in payload["error"]
    assert "install migrate" in payload["error"]


def test_the_hostrun_cli_rejects_home_after_the_subcommand(tmp_path, capsys):
    rc = cli.main(["status", "--home", str(tmp_path / "home")])

    assert rc == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert "install migrate" in payload["error"]


def test_the_hostrun_cli_rejects_the_equals_form(tmp_path, capsys):
    rc = cli.main(["--home=" + str(tmp_path / "home"), "status"])

    assert rc == 1
    assert "install migrate" in json.loads(capsys.readouterr().err)["error"]


def test_a_module_cli_rejects_home_with_the_same_guidance(tmp_path, capsys):
    from knowledge import cli as knowledge_cli

    rc = knowledge_cli.main(["--home", str(tmp_path / "home"), "config"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert "install migrate" in payload["error"]


# ------------------------------------------------------- out-of-bounds layout
def test_archive_inside_the_code_directory_is_rejected(tmp_path):
    code = tmp_path / "checkout"

    with pytest.raises(HostRunError):
        layout.resolve(code=str(code), archive=str(code / "horizon-archive"))


def test_code_inside_the_archive_is_rejected(tmp_path):
    archive = tmp_path / "papers"

    with pytest.raises(HostRunError):
        layout.resolve(code=str(archive / "checkout"), archive=str(archive))


def test_archive_equal_to_code_is_rejected(tmp_path):
    same = tmp_path / "same"

    with pytest.raises(HostRunError):
        layout.resolve(code=str(same), archive=str(same))


# ------------------------------------------------------------- category table
def test_runtime_categories_cover_the_required_kinds():
    assert set(layout.CATEGORY_RELS) == {
        "config",
        "state",
        "queue",
        "proposals",
        "logs",
        "lease",
        "console",
        "install",
        "init",
        "lark_cli",
    }
    assert set(layout.ARCHIVE_RELS) == {"originals", "pushes"}


# ------------------------------------------------------ establishing the area
def test_ensure_creates_the_runtime_area_and_the_archive_skeleton(tmp_path):
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"
    resolved = layout.resolve(code=str(code), archive=str(archive))

    layout.ensure(resolved)

    for key, rel in layout.CATEGORY_RELS.items():
        parent = Path(resolved["paths"][key]).parent
        assert parent.is_dir(), key
    for key in layout.ARCHIVE_RELS:
        assert Path(resolved["archive_paths"][key]).is_dir()
    assert sorted(p.name for p in archive.iterdir()) == ["originals", "pushes"]


def test_ensure_writes_no_file_into_the_archive(tmp_path):
    resolved = layout.resolve(
        code=str(tmp_path / "checkout"), archive=str(tmp_path / "papers")
    )

    layout.ensure(resolved)

    files = [p for p in Path(resolved["archive"]).rglob("*") if p.is_file()]
    assert files == []


def test_ensure_is_idempotent_and_keeps_existing_files(tmp_path):
    resolved = layout.resolve(
        code=str(tmp_path / "checkout"), archive=str(tmp_path / "papers")
    )
    layout.ensure(resolved)
    kept = Path(resolved["paths"]["logs"]) / "run.log"
    kept.write_text("kept", encoding="utf-8")

    layout.ensure(resolved)

    assert kept.read_text(encoding="utf-8") == "kept"


# ------------------------------------------------------ bounded write helpers
def test_entry_joins_only_inside_its_area(tmp_path):
    resolved = layout.resolve(
        code=str(tmp_path / "checkout"), archive=str(tmp_path / "papers")
    )

    allowed = layout.entry(resolved, "archive", "pushes", "push_1.html")
    assert allowed == Path(resolved["archive_paths"]["pushes"]) / "push_1.html"

    with pytest.raises(HostRunError):
        layout.entry(resolved, "archive", "..", "escape.html")
    with pytest.raises(HostRunError):
        layout.entry(resolved, "runtime", "..", "outside.json")
    with pytest.raises(HostRunError):
        layout.entry(resolved, "elsewhere", "x")


# ----------------------------------------------------------- git hygiene
def test_the_runtime_area_is_gitignored():
    probe = ".horizon/logs/run.log"
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "--quiet", probe],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{probe} is not gitignored: {result.stderr}"


# ---------------------------------------------------- resolved-location handoff
def test_every_module_cli_uses_the_same_resolved_runtime_area(tmp_path, capsys):
    from types import SimpleNamespace

    from archive import cli as archive_cli

    code = tmp_path / "checkout"
    archive = tmp_path / "papers"

    rc = archive_cli.main(
        ["--code", str(code), "--archive", str(archive), "init"]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    runtime = code / layout.RUNTIME_DIRNAME
    assert (runtime / "archive" / "cards").is_dir()
    # A module command writes no material into the archive by itself.
    assert [p for p in archive.rglob("*") if p.is_file()] == []


def test_the_child_handoff_resolves_the_same_locations(tmp_path):
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"
    resolved = layout.resolve(code=str(code), archive=str(archive))

    layout.export(resolved)
    child = layout.resolve_for_cli(SimpleNamespace(code=None, archive=None))

    assert child == resolved
    assert runtime_root() == Path(resolved["runtime"])


def test_an_explicit_flag_wins_over_the_child_handoff(tmp_path):
    layout.export(
        layout.resolve(code=str(tmp_path / "parent"), archive=str(tmp_path / "papers"))
    )
    child = layout.resolve_for_cli(
        SimpleNamespace(code=str(tmp_path / "other"), archive=None)
    )

    assert Path(child["code"]) == (tmp_path / "other").resolve()
    assert Path(child["archive"]) == (tmp_path / "other").resolve().parent / layout.ARCHIVE_DIRNAME


# --------------------------------------------------------------- CLI surface
def test_normalize_accepts_the_new_flags_after_the_subcommand():
    parser = cli.build_parser()

    args = parser.parse_args(
        cli._normalize(["install", "status", "--code", "C", "--archive", "A"])
    )

    assert args.code == "C"
    assert args.archive == "A"
    assert parser.parse_args(
        cli._normalize(["--code", "C", "--archive", "A", "install", "status"])
    ).code == "C"


def test_normalize_keeps_the_last_location_flag():
    parser = cli.build_parser()

    args = parser.parse_args(
        cli._normalize(["status", "--code", "first", "--code", "second"])
    )

    assert args.code == "second"


def test_cli_with_code_and_archive_lands_in_the_runtime_area(tmp_path, capsys):
    code = tmp_path / "checkout"
    archive = tmp_path / "papers"
    code.mkdir()

    rc = cli.main(
        [
            "--code",
            str(code),
            "--archive",
            str(archive),
            "install",
            "record",
            "--step",
            "workspace",
            "--status",
            "ok",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["steps"]["workspace"] == "ok"
    runtime = code / layout.RUNTIME_DIRNAME
    assert (runtime / "install" / "state.json").is_file()
    for rel in layout.RUNTIME_DIRS:
        assert (runtime / rel).is_dir(), rel
    assert sorted(p.name for p in archive.iterdir()) == ["originals", "pushes"]
    assert [p for p in archive.rglob("*") if p.is_file()] == []
    # No run state leaks next to the checkout either.
    assert not (code / "install").exists()


def test_cli_archive_defaults_next_to_the_code_directory(tmp_path, capsys):
    code = tmp_path / "checkout"
    code.mkdir()

    rc = cli.main(
        [
            "--code",
            str(code),
            "install",
            "record",
            "--step",
            "workspace",
            "--status",
            "ok",
        ]
    )

    assert rc == 0
    capsys.readouterr()
    default_archive = tmp_path / layout.ARCHIVE_DIRNAME
    assert sorted(p.name for p in default_archive.iterdir()) == ["originals", "pushes"]


def test_cli_reports_an_unusable_archive_path_as_json(tmp_path, capsys):
    blocker = tmp_path / "papers"
    blocker.write_text("not a directory", encoding="utf-8")

    rc = cli.main(
        ["--code", str(tmp_path / "checkout"), "--archive", str(blocker), "status"]
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False
    assert "资料目录" in payload["error"]


def test_cli_rejects_an_archive_inside_the_code_directory(tmp_path, capsys):
    rc = cli.main(
        [
            "--code",
            str(tmp_path / "checkout"),
            "--archive",
            str(tmp_path / "checkout" / "papers"),
            "status",
        ]
    )

    assert rc == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload["ok"] is False

