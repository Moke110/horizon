import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _clean_location_handoff(monkeypatch):
    """Commands export the resolved locations for child runs; keep tests isolated."""
    for name in ("HORIZON_CODE", "HORIZON_ARCHIVE", "HORIZON_RUNTIME", "KL_HOME"):
        monkeypatch.delenv(name, raising=False)


def cli_locations(home) -> list[str]:
    """Location flags for a CLI call, derived from a test's runtime directory.

    Tests treat the runtime area as their working "home": ``<code>/.horizon``.
    """
    code = Path(home).parent
    return ["--code", str(code)]


@pytest.fixture
def kl_home(tmp_path):
    home = tmp_path / "code" / ".horizon"
    shutil.copytree(ROOT / "fixtures" / "home", home)
    return home


@pytest.fixture
def fixtures_dir():
    return ROOT / "fixtures" / "papers"


@pytest.fixture
def offline_home(tmp_path):
    """A private runtime area with no Bitable configured yet."""
    home = tmp_path / "code" / ".horizon"
    (home / "knowledge").mkdir(parents=True)
    return home


def configure_base(home: Path, token: str = "basetest0001") -> Path:
    """Point a home at a (fake) Base; tests never reach a live tenant."""
    path = Path(home) / "knowledge" / "base.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_token": token,
                "base_url": f"https://example.feishu.cn/base/{token}",
                "tables": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def configured_home(offline_home):
    """A home already pointing at a Base (feedback/library tests start here)."""
    configure_base(offline_home)
    return offline_home


@pytest.fixture
def offline_base(offline_home):
    from library.offline import JsonBase

    return JsonBase(offline_home / "library" / "offline-base.json")


def seed_queue(home: Path, items: list[dict]) -> None:
    """Write queue state directly: tests describe the queue, not its history."""
    root = Path(home) / "queue"
    root.mkdir(parents=True, exist_ok=True)
    active = next((row["id"] for row in items if row.get("status") == "active"), None)
    (root / "state.json").write_text(
        json.dumps({"schema_version": 1, "active_id": active, "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "events.jsonl").touch()
