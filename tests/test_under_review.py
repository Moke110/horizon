"""Under review: optional flag, Base column, read-side filters and pull.

The flag is an optional boolean on cards and propositions (missing = false).  It
is mirrored into the Base 「待复核」 checkbox, but that column is not part of the
required field contract; an old Base without it must keep working and get the
column added when the base is created/attached.  Flagged records keep syncing,
counting and comparing; only retrieval, ranking and writing skip them.  Pulling
an owner's checkbox change clears/sets the local flag without a changelog entry.
"""

import json

import pytest

from archive.store import Archive, ArchiveError, is_under_review, v3_prop_from_v2
from editor import model as editor_model
from editor import tasks as editor_tasks
from horizon import model as horizon_model
from hostrun import kb
from knowledge import bitable
from retriever import cli as retriever_cli
from writer import tasks as writer_tasks

REVIEW_FIELD = "待复核"


class FakeClient:
    def __init__(self):
        self.tables = {}
        self.counter = 0

    def _rows(self, table):
        return self.tables.setdefault(table, [])

    def list(self, table):
        return [dict(row, fields=dict(row["fields"])) for row in self._rows(table)]

    def create(self, table, rows):
        ids = []
        for row in rows:
            self.counter += 1
            rid = f"rec{self.counter:03d}"
            self._rows(table).append({"record_id": rid, "fields": dict(row)})
            ids.append(rid)
        return ids

    def update(self, table, updates):
        by_id = {row["record_id"]: row for row in self._rows(table)}
        for rid, fields in updates.items():
            by_id[rid]["fields"].update(fields)

    def ensure_options(self, table, field, values):
        return None

    def field_names(self, table):
        return list((self._rows(table) and self._rows(table)[0]["fields"]) or [])


def _config(home):
    path = home / "knowledge" / "base.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_token": "fake",
                "base_url": "https://example.feishu.cn/base/fake",
                "tables": {
                    "cards": "cards",
                    "propositions": "props",
                    "evidence": "evidence",
                    "papers": "papers",
                    "logs": "logs",
                },
            }
        ),
        encoding="utf-8",
    )


def _record(client, table, key, value):
    return next(
        row for row in client.list(table) if row["fields"].get(key) == value
    )


def _set_flag(home, kind, record_id, flag=True):
    path = home / "archive" / kind / f"{record_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if flag is None:
        data.pop("under_review", None)
    else:
        data["under_review"] = flag
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _pipeline(home, fixtures_dir):
    plan = horizon_model.plan(home, day="2026-10-20", days=7)
    rows = retriever_cli.run(plan, "fixture", fixtures_dir, check_window=True)
    selection = editor_model.rank(home, rows, day="2026-10-20", limit=4, per_checkpoint=2)
    return rows, selection


def _empty_base(base):
    base.meta = []
    base.fields = {}
    base.tables = {}
    base.save()


def _drop_field(base, table_name, field):
    tables = {row["name"]: row["id"] for row in base.list_tables()}
    base.fields[tables[table_name]].remove(field)
    base.save()


# --------------------------------------------------------------- record field
def test_under_review_is_optional_and_must_be_boolean(kl_home):
    store = Archive(kl_home)
    assert is_under_review(store.get_card("card_hmbs")) is False

    card = store.get_card("card_hmbs")
    card["under_review"] = True
    store.put_card(card)
    assert is_under_review(store.get_card("card_hmbs")) is True
    visible = {row["id"] for row in store.list_cards(include_under_review=False)}
    assert "card_hmbs" not in visible
    assert "card_aip" in visible

    broken = store.get_card("card_aip")
    broken["under_review"] = "yes"
    with pytest.raises(ArchiveError, match="under_review"):
        store.put_card(broken)

    prop = store.get_prop("prop_hmbs_aip")
    prop["under_review"] = True
    store.put_prop(prop)
    assert is_under_review(store.get_prop("prop_hmbs_aip")) is True
    prop_ids = {row["id"] for row in store.list_props(include_under_review=False)}
    assert "prop_hmbs_aip" not in prop_ids

    broken_prop = store.get_prop("prop_hmbs_aip")
    broken_prop["under_review"] = 1
    with pytest.raises(ArchiveError, match="under_review"):
        store.put_prop(broken_prop)

    # The review console (#90) can reuse this narrow write seam directly.
    store.set_under_review("card_aip", True)
    assert store.set_under_review("card_aip", False)["under_review"] is False
    assert is_under_review(store.get_card("card_aip")) is False


def test_v2_proposition_conversion_keeps_the_optional_flag():
    raw = {
        "id": "prop_legacy",
        "title": "legacy claim",
        "content": "legacy review text",
        "related_knowledge": ["card_a", "card_b"],
        "history": [{"at": "2026-10-20", "action": "created"}],
        "under_review": True,
    }

    converted = v3_prop_from_v2(raw)

    assert converted["under_review"] is True


# -------------------------------------------------------------- read filters
def test_retrieval_plan_skips_under_review_cards(kl_home):
    _set_flag(kl_home, "cards", "card_hmbs", True)

    plan = horizon_model.plan(kl_home, day="2026-10-20", days=7)

    card_ids = {query["card_id"] for query in plan["queries"]}
    assert "card_hmbs" not in card_ids
    assert "card_aip" in card_ids


def test_ranking_context_skips_under_review_cards_and_propositions(kl_home):
    _set_flag(kl_home, "cards", "card_aip", True)
    _set_flag(kl_home, "propositions", "prop_hmbs_aip", True)

    task = editor_tasks.build_task(kl_home, [], "2026-10-20")

    assert "card_hmbs" in task["checkpoints"]
    assert "card_aip" not in task["checkpoints"]
    props = {p["prop_id"] for p in task["checkpoints"]["card_hmbs"]["propositions"]}
    assert "prop_hmbs_aip" not in props


def test_writing_context_skips_under_review_cards_and_propositions(kl_home, fixtures_dir):
    _set_flag(kl_home, "cards", "card_aip", True)
    _set_flag(kl_home, "propositions", "prop_hmbs_aip", True)
    _, selection = _pipeline(kl_home, fixtures_dir)

    task = writer_tasks.build_task(kl_home, selection, "2026-10-20")
    item = next(i for i in task["items"] if i["checkpoint_id"] == "card_hmbs")

    assert "card_aip" not in {c["id"] for c in item["context"]["cards"]}
    assert "prop_hmbs_aip" not in {p["id"] for p in item["context"]["propositions"]}

    # A flagged selected card is not written with its card context either.
    _set_flag(kl_home, "cards", "card_hmbs", True)
    flagged = writer_tasks.build_task(kl_home, selection, "2026-10-20")
    flagged_item = next(i for i in flagged["items"] if i["checkpoint_id"] == "card_hmbs")
    assert flagged_item["context"]["card"] is None
    assert not flagged_item["card"].get("id")


# ---------------------------------------------------------------- Base column
def test_review_column_is_not_required_and_its_absence_is_not_an_error(
    configured_home, offline_base
):
    _empty_base(offline_base)
    kb.create_tables(configured_home, client=offline_base)

    required = kb.required_fields()
    assert REVIEW_FIELD not in required["cards"]
    assert REVIEW_FIELD not in required["propositions"]

    _drop_field(offline_base, "知识卡片", REVIEW_FIELD)
    _drop_field(offline_base, "命题", REVIEW_FIELD)

    report = kb.verify(configured_home, client=offline_base)

    assert report["ok"] is True
    assert REVIEW_FIELD not in report["missing_fields"].get("cards", [])
    assert REVIEW_FIELD not in report["missing_fields"].get("propositions", [])


def test_attach_adds_the_review_column_to_an_existing_base(offline_home, offline_base):
    assert REVIEW_FIELD not in offline_base.field_names("tblcards")
    offline_base.create("tblcards", [{"卡片 ID": "card_old", "label": "旧卡片"}])

    result = kb.attach(offline_home, "bascnABC123", client=offline_base)

    assert REVIEW_FIELD in offline_base.field_names("tblcards")
    assert result["columns_added"]["cards"] == [REVIEW_FIELD]
    # Adding a column must not touch existing rows.
    assert offline_base.list("tblcards")[0]["fields"]["卡片 ID"] == "card_old"


def test_create_tables_adds_a_missing_review_column_without_recreating_the_table(
    configured_home, offline_base
):
    _empty_base(offline_base)
    kb.create_tables(configured_home, client=offline_base)
    before_tables = {row["name"]: row["id"] for row in offline_base.list_tables()}
    _drop_field(offline_base, "知识卡片", REVIEW_FIELD)

    kb.create_tables(configured_home, client=offline_base)

    after_tables = {row["name"]: row["id"] for row in offline_base.list_tables()}
    assert after_tables == before_tables
    fields = offline_base.field_names(before_tables["知识卡片"])
    assert fields.count(REVIEW_FIELD) == 1


# ------------------------------------------------------------ sync / compare
def test_sync_and_compare_include_the_review_flag(kl_home):
    _config(kl_home)
    store = Archive(kl_home)
    card = store.get_card("card_hmbs")
    card["under_review"] = True
    store.put_card(card)
    prop = store.get_prop("prop_hmbs_aip")
    prop["under_review"] = True
    store.put_prop(prop)

    client = FakeClient()
    first = bitable.sync(kl_home, client)

    assert first["created"]["cards"] == len(store.list_cards())
    assert first["created"]["propositions"] == len(store.list_props())
    card_row = _record(client, "cards", "卡片 ID", "card_hmbs")
    assert card_row["fields"][REVIEW_FIELD] is True
    prop_row = _record(client, "props", "命题 ID", "prop_hmbs_aip")
    assert prop_row["fields"][REVIEW_FIELD] is True

    again = bitable.sync(kl_home, client)
    assert again["created"]["cards"] == 0
    assert again["updated"]["cards"] == 0
    assert again["updated"]["propositions"] == 0

    card = store.get_card("card_hmbs")
    card["under_review"] = False
    store.put_card(card)
    drift = bitable.compare(kl_home, client)
    assert any(d["id"] == "card_hmbs" and d["field"] == "under_review" for d in drift["diffs"])

    bitable.sync(kl_home, client)
    card_row = _record(client, "cards", "卡片 ID", "card_hmbs")
    assert card_row["fields"][REVIEW_FIELD] is False


def test_flagged_records_still_count_and_appear_in_the_ledger(kl_home):
    store = Archive(kl_home)
    before = store.stats()
    _set_flag(kl_home, "cards", "card_hmbs", True)
    _set_flag(kl_home, "propositions", "prop_hmbs_aip", True)

    after = Archive(kl_home).stats()
    assert after["cards"] == before["cards"]
    assert after["propositions"] == before["propositions"]

    ledger = bitable.current_ledger(kl_home)
    assert ledger["cards"]["card_hmbs"]["under_review"] is True
    assert ledger["propositions"]["prop_hmbs_aip"]["under_review"] is True


def test_sync_tolerates_a_base_whose_rows_lack_the_review_column(kl_home):
    _config(kl_home)
    store = Archive(kl_home)
    card = store.get_card("card_hmbs")
    row = bitable.card_row(card)
    row.pop(REVIEW_FIELD)
    client = FakeClient()
    client.create("cards", [row])

    report = bitable.sync(kl_home, client)

    assert report["updated"]["cards"] == 0


# --------------------------------------------------------------------- pull
def test_pull_accepts_unchecking_and_writes_no_changelog(kl_home):
    _config(kl_home)
    store = Archive(kl_home)
    card = store.get_card("card_hmbs")
    card["under_review"] = True
    store.put_card(card)
    prop = store.get_prop("prop_hmbs_aip")
    prop["under_review"] = True
    store.put_prop(prop)
    changelog = store.get_prop("prop_hmbs_aip")["changelog"]

    client = FakeClient()
    bitable.sync(kl_home, client)  # ledger baseline = true
    card_row = _record(client, "cards", "卡片 ID", "card_hmbs")
    prop_row = _record(client, "props", "命题 ID", "prop_hmbs_aip")
    client.update("cards", {card_row["record_id"]: {REVIEW_FIELD: False}})
    client.update("props", {prop_row["record_id"]: {REVIEW_FIELD: False}})

    result = bitable.pull(kl_home, client)

    applied = {(row["kind"], row.get("field")) for row in result["applied"]}
    assert ("card", "under_review") in applied
    assert ("proposition", "under_review") in applied
    store = Archive(kl_home)
    assert is_under_review(store.get_card("card_hmbs")) is False
    assert is_under_review(store.get_prop("prop_hmbs_aip")) is False
    assert store.get_prop("prop_hmbs_aip")["changelog"] == changelog

    # The new baseline is false, so a repeated poll changes nothing.
    assert bitable.pull(kl_home, client)["applied"] == []

    # Checking the box in Base brings the record back into the flagged set.
    client.update("cards", {card_row["record_id"]: {REVIEW_FIELD: True}})
    bitable.pull(kl_home, client)
    assert is_under_review(Archive(kl_home).get_card("card_hmbs")) is True


def test_pull_ignores_the_review_flag_when_the_column_is_absent(kl_home):
    _config(kl_home)
    store = Archive(kl_home)
    card = store.get_card("card_hmbs")
    card["under_review"] = True
    store.put_card(card)
    bitable.write_ledger(kl_home)

    client = FakeClient()
    client.create("cards", [{"卡片 ID": "card_hmbs", "annotation": card["annotation"]}])

    result = bitable.pull(kl_home, client)

    assert not [row for row in result["applied"] if row.get("field") == "under_review"]
    assert is_under_review(Archive(kl_home).get_card("card_hmbs")) is True
