"""Directory model step 2: legacy migration, archive index and the layout record.

Everything runs against temporary copies of a legacy ``--home`` installation; the
live installation and Feishu are never touched.  The private-environment rebuild
is injected because uv/PyMuPDF are not available offline.
"""

import hashlib
import json
from pathlib import Path

from archive.store import Archive
from editor import model as editor_model
from hostrun import archive_index, cli, layout, migration, workspace
from conftest import cli_locations


PAPER = b"paper-one"
TEXT = b"text-one"
PUSH = b"push-one"
ASSET = b"asset-one"
PAPER_MD5 = "cce5a548927176f16788b62dc9695785"
TEXT_MD5 = "9b700c0801557e8c36ce089f420ddf1b"
PUSH_MD5 = "e19819ef382cab2765327950d025b7b8"
ASSET_MD5 = "87f3ddfcb1972c3458369997b2a0c54b"


def _write(path: Path, content) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return path


def build_legacy(tmp_path):
    """A legacy ``--home`` installation shaped like the one on this machine."""
    workspace_root = tmp_path / "workspace"
    legacy = workspace_root / "horizon-archive"
    code = workspace_root / "horizon"
    code.mkdir(parents=True)
    legacy.mkdir(parents=True)
    uv = _write(workspace_root / "uv.exe", b"fake uv")
    old_python = legacy / ".venv" / "Scripts" / "python.exe"

    _write(
        legacy / "install" / "config.json",
        {
            "schema_version": 1,
            "paths": {
                "repo": str(code),
                "home": str(legacy),
                "python": str(old_python),
                "venv": str(legacy / ".venv"),
                "uv": str(uv),
                "lark_cli": str(legacy / "lark-cli" / "bin" / "lark-cli.exe"),
                "dsh": "D:/dsh/dsh.cmd",
            },
            "timezone": "Asia/Shanghai",
            "workspace": {
                "root": str(workspace_root),
                "code_dirname": "horizon",
                "data_dirname": "horizon-archive",
            },
            "listener": {
                "start_command": str(legacy / "bin" / "horizon-listener.cmd"),
                "stop_command": str(legacy / "bin" / "horizon-listener-stop.cmd"),
                "dsh_args": ["--patch", "account.yml"],
            },
        },
    )
    _write(legacy / "install" / "state.json", {"schema_version": 1, "steps": {"knowledge": {"status": "ok"}}})
    _write(legacy / "install" / "initial" / "state.json", {"step": "archive", "status": "ok"})
    _write(legacy / "queue" / "request.json", {"requested": True})
    _write(legacy / "proposals" / "p.json", {"id": "p"})
    _write(legacy / "logs" / "run.log", "log\n")
    _write(legacy / "state" / "checkpoints.yaml", "checkpoints: {}\n")
    _write(legacy / "executor" / "lease.json", {"holder": "workbuddy"})
    _write(legacy / "console" / "state.json", {"service": "paused"})
    _write(legacy / "lark-cli" / "bin" / "lark-cli.exe", b"fake lark")
    _write(legacy / "bin" / "horizon-listener.cmd", "title horizon-listener\n")
    _write(legacy / "bin" / "horizon-listener-stop.cmd", "title horizon-listener\n")
    _write(legacy / "ratings.jsonl", "")
    _write(old_python, b"fake python")

    _write(legacy / "archive" / "raw" / "p1.json", PAPER)
    _write(legacy / "archive" / "raw" / "text" / "p1.txt", TEXT)
    _write(legacy / "archive" / "pushes" / "push1.json", PUSH)
    _write(legacy / "archive" / "pushes" / "assets" / "push1" / "a.png", ASSET)
    _write(legacy / "archive" / "cards" / "card_x.json", {"id": "card_x"})
    _write(legacy / "archive" / "index" / "pushes.jsonl", "")
    _write(legacy / "archive" / "applied" / "group.json", {})

    _write(
        workspace_root / ".horizon" / "install.json",
        {
            "schema_version": 1,
            "root": str(workspace_root),
            "repo": str(code),
            "home": str(legacy),
            "repo_url": "https://github.com/Moke110/horizon.git",
            "uv": str(uv),
            "python": str(old_python),
            "venv": str(legacy / ".venv"),
        },
    )
    return {"workspace": workspace_root, "legacy": legacy, "code": code, "uv": uv}


def fake_environment(code, runtime):
    python = Path(runtime) / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"new python")
    return {
        "ok": True,
        "status": "ok",
        "python": str(python),
        "venv": str(Path(runtime) / ".venv"),
        "created": True,
    }


def _migrate(env, environment=fake_environment, legacy_home=None):
    return migration.migrate(
        code=str(env["code"]),
        archive=str(env["legacy"]),
        legacy_home=str(legacy_home or env["legacy"]),
        environment=environment,
    )


# --------------------------------------------------------------- runtime move
def test_migration_moves_runtime_state_and_keeps_materials_in_the_archive(tmp_path):
    env = build_legacy(tmp_path)
    legacy, code = env["legacy"], env["code"]
    runtime = code / layout.RUNTIME_DIRNAME

    result = _migrate(env)

    assert result["ok"] is True
    assert result["status"] == "migrated"
    assert result["feishu_touched"] is False
    # Config, state, installation records, init state, lease, queue, logs and the
    # private lark-cli all live under the code directory now.
    for rel in (
        "install/config.json",
        "install/state.json",
        "install/initial/state.json",
        "queue/request.json",
        "proposals/p.json",
        "logs/run.log",
        "state/checkpoints.yaml",
        "executor/lease.json",
        "console/state.json",
        "lark-cli/bin/lark-cli.exe",
    ):
        assert (runtime / rel).is_file(), rel
        assert not (legacy / rel).exists(), rel
    # Materials stay in the archive; the knowledge store is code-directory state.
    assert (legacy / "originals" / "p1.json").read_bytes() == PAPER
    assert (legacy / "originals" / "text" / "p1.txt").read_bytes() == TEXT
    assert (legacy / "pushes" / "push1.json").read_bytes() == PUSH
    assert (legacy / "pushes" / "assets" / "push1" / "a.png").read_bytes() == ASSET
    assert (runtime / "archive" / "cards" / "card_x.json").is_file()
    assert (runtime / "archive" / "index" / "pushes.jsonl").is_file()
    assert not (legacy / "archive" / "cards").exists()

    config = json.loads((runtime / "install" / "config.json").read_text(encoding="utf-8"))
    assert config["paths"]["home"] == str(runtime)
    assert config["paths"]["repo"] == str(code)
    assert config["paths"]["python"] == result["environment"]["python"]
    assert config["paths"]["lark_cli"] == str(runtime / "lark-cli" / "bin" / "lark-cli.exe")
    # The informational workspace record survives the rewrite unredacted.
    assert config["workspace"]["code_dirname"] == "horizon"
    assert config["workspace"]["home"] == str(runtime)
    # The listener section is retired; its Agent-run settings move to agent_run.
    console = code / "bin" / "horizon-console.cmd"
    assert "listener" not in config
    assert "schedules" not in config
    assert config["agent_run"]["dsh_args"] == ["--patch", "account.yml"]
    assert console.is_file()
    text = console.read_text(encoding="utf-8")
    # The shared generator must produce exactly the pre-#95 bytes.
    assert text == (
        "title horizon-console\n"
        f'cd /d "{code}"\n'
        f'"{result["environment"]["python"]}" -m hostrun.cli --code "{code}" '
        f'--archive "{legacy}" console %*\n'
    )
    assert text.splitlines()[0] == "title horizon-console"
    assert "--code" in text and "--archive" in text and "console" in text
    assert text.rstrip().endswith("%*")
    assert not (runtime / "bin" / "horizon-listener.cmd").exists()
    assert not (runtime / "bin" / "horizon-listener-stop.cmd").exists()
    assert not (legacy / "bin" / "horizon-listener.cmd").exists()
    # The retired listener runtime directory is not moved into the new layout.
    assert not (runtime / "listener").exists()

    # The workspace record moves to the code directory and points at it.
    record = workspace.load_record(code)
    assert record["home"] == str(runtime)
    assert record["repo"] == str(code)
    assert record["python"] == result["environment"]["python"]

    # The layout record remembers the archive for later flagless commands.
    assert Path(layout.record_path(code)).is_file()
    assert layout.resolve(code=str(code))["archive"] == str(legacy)


def test_migration_remaps_recorded_agent_run_paths_into_the_runtime_area(tmp_path):
    """A stale absolute path in ``dsh_args`` would break every real agent run."""
    env = build_legacy(tmp_path)
    legacy, code = env["legacy"], env["code"]
    runtime = code / layout.RUNTIME_DIRNAME
    patch = legacy / "install" / "dsh-headless-account.patch.yml"
    _write(patch, "patch\n")
    config_path = legacy / "install" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["listener"]["dsh_args"] = ["--patch", str(patch)]
    _write(config_path, config)

    result = _migrate(env)

    assert result["ok"] is True
    migrated = json.loads((runtime / "install" / "config.json").read_text(encoding="utf-8"))
    assert migrated["agent_run"]["dsh_args"] == [
        "--patch",
        str(runtime / "install" / "dsh-headless-account.patch.yml"),
    ]
    assert (runtime / "install" / "dsh-headless-account.patch.yml").is_file()


def test_migration_reports_missing_legacy_files(tmp_path):
    env = build_legacy(tmp_path)
    (env["legacy"] / "install" / "state.json").unlink()

    result = _migrate(env)

    assert result["status"] == "partial"
    assert "install/state.json" in result["missing"]


def test_materials_move_to_a_separate_archive_directory(tmp_path):
    env = build_legacy(tmp_path)
    archive = tmp_path / "papers"

    result = migration.migrate(
        code=str(env["code"]),
        archive=str(archive),
        legacy_home=str(env["legacy"]),
        environment=fake_environment,
    )

    assert result["status"] == "migrated"
    assert (archive / "originals" / "p1.json").read_bytes() == PAPER
    assert (archive / "originals" / "text" / "p1.txt").read_bytes() == TEXT
    assert (archive / "pushes" / "push1.json").read_bytes() == PUSH
    assert (archive / "pushes" / "assets" / "push1" / "a.png").read_bytes() == ASSET
    assert not (env["legacy"] / "archive" / "raw").exists()
    assert (env["code"] / layout.RUNTIME_DIRNAME / "archive" / "cards" / "card_x.json").is_file()
    assert not (env["legacy"] / "install").exists()


def test_a_migrated_install_still_reads_raw_material_and_pushes(tmp_path):
    """B2: the store and its consumers resolve the archive directory after migrate."""
    env = build_legacy(tmp_path)
    legacy = env["legacy"]
    _write(
        legacy / "archive" / "raw" / "p1.json",
        {
            "paper_id": "p1",
            "source": "arxiv",
            "source_id": "p1",
            "title": "Paper one",
            "text_path": "raw/text/p1.txt",
        },
    )
    _write(
        legacy / "archive" / "pushes" / "push1.json",
        {"push_id": "push1", "paper_ids": ["p1"], "checkpoint_id": "card_x"},
    )
    _write(
        legacy / "ratings.jsonl",
        json.dumps({"push_id": "push1", "paper_id": "p1", "verdict": "great"}) + "\n",
    )

    result = _migrate(env)
    runtime = env["code"] / layout.RUNTIME_DIRNAME

    assert result["status"] == "migrated"
    store = Archive(runtime)
    assert [push["push_id"] for push in store.list_pushes()] == ["push1"]
    assert store.get_text("p1") == "text-one"
    assert store.raw == Path(env["legacy"]) / "originals"
    assert store.pushes == Path(env["legacy"]) / "pushes"
    # A consumer (ratings -> pushes -> raw) reads the migrated material too.
    model = editor_model.fit(runtime)
    assert model["checkpoints"]["card_x"]["n"] == 1
    assert model["sources"]["arxiv"]["n"] == 1


def test_migration_infers_the_legacy_home_from_the_workspace_record(tmp_path):
    env = build_legacy(tmp_path)

    result = migration.migrate(
        code=str(env["code"]),
        archive=str(env["legacy"]),
        environment=fake_environment,
    )

    assert result["from"] == str(env["legacy"])
    assert result["status"] == "migrated"


def test_second_migration_is_a_no_op_and_reports_already_migrated(tmp_path):
    env = build_legacy(tmp_path)
    first = _migrate(env)
    rows = archive_index.load(env["code"] / layout.RUNTIME_DIRNAME)

    second = _migrate(env)

    assert first["changed"] > 0
    assert second["status"] == "already-migrated"
    assert second["ok"] is True
    assert second["changed"] == 0
    assert second["missing"] == []
    assert archive_index.load(env["code"] / layout.RUNTIME_DIRNAME) == rows


def test_half_migrated_state_is_reported_without_duplicating_entries(tmp_path):
    env = build_legacy(tmp_path)
    _migrate(env)
    rows = archive_index.load(env["code"] / layout.RUNTIME_DIRNAME)

    result = _migrate(env)

    assert result["status"] == "already-migrated"
    assert result["missing"] == []
    assert archive_index.load(env["code"] / layout.RUNTIME_DIRNAME) == rows


# --------------------------------------------------------------- archive index
def test_archive_index_registers_every_material_file_by_business_id_and_md5(tmp_path):
    env = build_legacy(tmp_path)
    runtime = env["code"] / layout.RUNTIME_DIRNAME
    _migrate(env)

    rows = archive_index.load(runtime)
    by_path = {row["path"]: row for row in rows}

    assert set(by_path) == {
        "originals/p1.json",
        "originals/text/p1.txt",
        "pushes/push1.json",
        "pushes/assets/push1/a.png",
    }
    assert by_path["originals/p1.json"]["md5"] == PAPER_MD5
    assert by_path["originals/p1.json"]["business_id"] == "p1"
    assert by_path["originals/text/p1.txt"]["md5"] == TEXT_MD5
    assert by_path["originals/text/p1.txt"]["business_id"] == "p1"
    assert by_path["pushes/push1.json"]["md5"] == PUSH_MD5
    assert by_path["pushes/push1.json"]["business_id"] == "push1"
    assert by_path["pushes/assets/push1/a.png"]["md5"] == ASSET_MD5
    assert by_path["pushes/assets/push1/a.png"]["business_id"] == "push1"
    assert all(row["size"] == Path(env["legacy"] / row["path"]).stat().st_size for row in rows)


def test_duplicate_content_is_registered_once(tmp_path):
    runtime = tmp_path / "runtime"
    archive = tmp_path / "archive"
    _write(archive / "originals" / "a.pdf", b"same")
    _write(archive / "originals" / "copy.pdf", b"same")

    first = archive_index.register(runtime, archive)
    second = archive_index.register(runtime, archive)

    assert first["registered"] == 1
    assert first["duplicates"] == 1
    assert second["registered"] == 0
    assert len(archive_index.load(runtime)) == 1
    assert hashlib.md5(b"same").hexdigest() == archive_index.load(runtime)[0]["md5"]


# ------------------------------------------------------------ layout record
def test_layout_record_survives_later_flagless_commands(tmp_path):
    code = tmp_path / "checkout"
    code.mkdir()
    archive = tmp_path / "papers"

    resolved = layout.resolve(code=str(code), archive=str(archive))
    layout.remember(resolved)

    later = layout.resolve(code=str(code))
    assert Path(later["archive"]) == archive
    assert Path(later["runtime"]) == code / layout.RUNTIME_DIRNAME
    # A different code directory never inherits someone else's remembered archive.
    other = layout.resolve(code=str(tmp_path / "other"))
    assert Path(other["archive"]) == (tmp_path / "other").parent / layout.ARCHIVE_DIRNAME


def test_cli_persists_a_custom_archive(tmp_path, capsys):
    code = tmp_path / "checkout"
    code.mkdir()
    archive = tmp_path / "papers"

    assert cli.main(["--code", str(code), "--archive", str(archive), "install", "status"]) == 0
    capsys.readouterr()
    record = json.loads(Path(layout.record_path(code)).read_text(encoding="utf-8"))

    assert Path(record["archive"]) == archive
    assert layout.resolve(code=str(code))["archive"] == str(archive)


# ------------------------------------------------------------------- CLI seam
def test_cli_install_migrate_reports_a_missing_legacy_install(tmp_path, capsys):
    home = tmp_path / "code" / ".horizon"

    rc = cli.main([*cli_locations(home), "install", "migrate"])

    assert rc == 1
    assert "--from" in json.loads(capsys.readouterr().err)["error"]


def test_cli_install_migrate_reports_its_result(tmp_path, capsys, monkeypatch):
    env = build_legacy(tmp_path)
    monkeypatch.setattr(
        migration,
        "_rebuild_environment",
        lambda code, runtime, **kwargs: fake_environment(code=code, runtime=runtime),
    )

    rc = cli.main(
        [
            "--code",
            str(env["code"]),
            "--archive",
            str(env["legacy"]),
            "install",
            "migrate",
            "--from",
            str(env["legacy"]),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "migrated"
    assert Path(payload["runtime"]) == env["code"] / layout.RUNTIME_DIRNAME
    assert (env["legacy"] / "originals" / "p1.json").is_file()
    assert not (env["legacy"] / "install" / "config.json").exists()
