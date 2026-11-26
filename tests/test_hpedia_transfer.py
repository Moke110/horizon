from hostrun.agent_service import AgentService
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.librarian import Librarian

AUTH = {"token": "local"}
GUI = {**AUTH, "origin": "gui"}


def workspace(home):
    AgentService(home, **AUTH)
    return Librarian(KnowledgeCardService(home, **AUTH), **AUTH)


def add_term(lib, op, name):
    lib.request("create", {"schema_version": 1, "operation_id": op, "title": name, "content": "definition"}, **GUI)
    return lib.decide(op, "confirm", **GUI)["results"][0]["card_id"]


def test_confirm_import_retains_ids_and_repeated_import_skips(tmp_path):
    source, dest = [workspace(tmp_path / name) for name in ("source", "dest")]
    term = add_term(source, "first", "共享名称")
    other = add_term(dest, "second", "共享名称")
    package = source.export_entries(**AUTH)
    card = dest.request_import("import", package, **GUI)
    assert card["items"][0]["status"] == "create"
    assert len(dest.list(**AUTH)) == 1
    assert dest.decide_import("import", {}, **GUI)["status"] == "confirmed"
    assert {t["card_id"] for t in dest.list(**AUTH)} == {term, other}
    assert dest.request_import("again", package, **GUI)["items"][0]["status"] == "skip"


def test_select_local_or_incoming_after_reviewing_field_diff(tmp_path):
    source = workspace(tmp_path)
    term = add_term(source, "create", "原名称")
    package = source.export_entries(**AUTH)
    package["entries"][0]["content"]["title"] = "导入名称"
    card = source.request_import("review", package, **GUI)
    item, = card["items"]
    assert item["diff"] == {"title": {"local": "原名称", "incoming": "导入名称"}}
    source.decide_import("review", {term: "local"}, **GUI)
    assert source.view(term, **AUTH)["title"] == "原名称"
    source.request_import("review2", package, **GUI)
    source.decide_import("review2", {term: "incoming"}, **GUI)
    assert source.view(term, **AUTH)["title"] == "导入名称"


def test_conflicting_bodies_hold_dependent_questions_only(tmp_path):
    source, dest = [workspace(tmp_path / name) for name in ("source", "dest")]
    term = add_term(source, "term", "冲突术语")
    independent = add_term(source, "other", "独立术语")
    source.request_question("create", {"operation_id": "question", "schema_version": 1,
        "prompt": "含义？", "answer": f"[[term:{term}]]"}, **GUI)
    question = source.decide("question", "confirm", **GUI)["results"][0]["question_id"]
    package = source.export_entries(**AUTH)
    bad = next(entry for entry in package["entries"] if entry["id"] == term)
    bad["kind"] = "frontier"
    card = dest.request_import("batch", package, **GUI)
    assert {item["id"]: item["status"] for item in card["items"]} == {
        independent: "create", term: "conflict", question: "blocked"}
    dest.decide_import("batch", {}, **GUI)
    assert [card["card_id"] for card in dest.list(**AUTH)] == [independent]


def test_roundtrip_cyclic_term_relations_and_full_question_reference(tmp_path):
    source, dest = [workspace(tmp_path / name) for name in ("source", "dest")]
    a = add_term(source, "one", "甲")
    b = add_term(source, "two", "乙")
    for term, target in [(a,b), (b,a)]:
        op = "link" + term
        source.request("update", {"schema_version": 1, "operation_id": op, "card_id": term,
            "expected_version": 1, "relations": [target]}, **GUI)
        source.decide(op, "confirm", **GUI)
    source.request_question("create", {"operation_id": "ask", "schema_version": 1,
        "prompt": "什么是甲？", "answer": f"[[term:{a}]]"}, **GUI)
    question = source.decide("ask", "confirm", **GUI)["results"][0]["question_id"]
    source.request_frontier("create", {"operation_id":"front", "question":"前沿"}, **GUI)
    frontier = source.decide("front", "confirm", **GUI)["results"][0]["frontier_id"]
    card = dest.request_import("batch", source.export_entries(**AUTH), **GUI)
    assert all(item["status"] == "create" for item in card["items"])
    dest.decide_import("batch", {}, **GUI)
    assert dest.view(a, **AUTH)["relations"] == [b]
    assert dest.view(b, **AUTH)["relations"] == [a]
    assert dest.view_question(question, **AUTH)["term_ids"] == [a]
    assert dest.view_frontier(frontier, **AUTH)["question"] == "前沿"


def test_deleted_ids_require_explicit_individual_restoration(tmp_path):
    lib = workspace(tmp_path)
    term = add_term(lib, "term", "术语")
    lib.request_question("create", {"operation_id": "ask", "schema_version": 1,
        "prompt": "问题", "answer": f"[[term:{term}]]"}, **GUI)
    question = lib.decide("ask", "confirm", **GUI)["results"][0]["question_id"]
    lib.request_frontier("create", {"operation_id": "front", "question": "前沿"}, **GUI)
    frontier = lib.decide("front", "confirm", **GUI)["results"][0]["frontier_id"]
    package = lib.export_entries(**AUTH)
    for identity, field, request in [(term,"card_id",lib.request),(question,"question_id",lib.request_question),
                                    (frontier,"frontier_id",lib.request_frontier)]:
        op = "delete" + identity
        request("delete", {"operation_id":op,"schema_version":1,field:identity,"expected_version":1}, **GUI)
        lib.decide(op,"confirm", **GUI)
        assert lib.deleted_entries(**AUTH)[identity] == "条目已删除"
    lib = workspace(tmp_path)
    assert lib.export_entries(**AUTH)["entries"] == []
    card = lib.request_import("restore", package, **GUI)
    assert [item["status"] for item in card["items"]] == ["restore"] * 3
    assert len(lib.deleted_entries(**AUTH)) == 3
    lib.decide_import("restore", {term:"restore",question:"restore",frontier:"skip"}, **GUI)
    assert lib.view_question(question, **AUTH)["term_ids"] == [term]
    assert lib.view(term, **AUTH)["version"] > 1
    assert lib.deleted_entries(**AUTH) == {frontier:"条目已删除"}
    lib.request_import("last", package, **GUI)
    lib.decide_import("last", {frontier:"restore"}, **GUI)
    assert lib.view_frontier(frontier, **AUTH)["version"] > 1
    assert lib.deleted_entries(**AUTH) == {}

def test_import_reconciles_status_and_content_of_archived_terms(tmp_path):
    lib = workspace(tmp_path)
    identity = add_term(lib, "term", "old")
    package = lib.export_entries(**AUTH)
    for op, status in enumerate(("archived", "archived", "active")):
        title = f"new-{op}"
        package["entries"][0]["content"].update(status=status,title=title)
        lib.request_import(str(op), package, **GUI)
        lib.decide_import(str(op), {identity:"incoming"}, **GUI)
        term = lib.view(identity, **AUTH)
        assert (term["status"],term["title"],term["known"]) == (status,title,status=="active")


def test_missing_citation_blocks_only_affected_entries(tmp_path):
    source, dest = [workspace(tmp_path / name) for name in ("source", "dest")]
    bad = add_term(source, "bad", "missing citation")
    good = add_term(source, "good", "independent")
    package = source.export_entries(**AUTH)
    entry = next(item for item in package["entries"] if item["id"] == bad)
    entry["content"]["sources"] = [{"material_id":"missing","locator":"p1","excerpt":"some text"}]
    card = dest.request_import("batch",package,**GUI)
    assert {item["id"]:item["status"] for item in card["items"]} == {bad:"blocked",good:"create"}
    dest.decide_import("batch", {}, **GUI)
    assert [term["card_id"] for term in dest.list(**AUTH)] == [good]
