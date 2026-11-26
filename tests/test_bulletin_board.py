from hostrun.desktop_bridge import bulletin_board_command
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.librarian import Librarian


def test_board_links_follow_confirmed_convergence_and_open_the_originals(tmp_path):
    token = "desktop"
    cards = KnowledgeCardService(tmp_path, token=token)
    term = cards.create({"schema_version": 1, "operation_id": "legacy-term",
                         "title": "测试术语", "content": "测试释义", "questionable": True}, token=token)
    librarian = Librarian(cards, token=token)

    def board(action, **fields):
        return bulletin_board_command(librarian, {"action": action, **fields}, token=token)

    def links():
        return board("bulletin_list")[1]

    assert [(item["kind"], item["id"], item["convergence"]) for item in links()] == [
        ("term", term["card_id"], "unresolved")]
    assert links()[0]["summary"] == term["content"]
    assert links()[0]["term_ids"] == [term["card_id"]]
    assert board("bulletin_open", kind="term", id=term["card_id"])[1] == term
    question = {"schema_version": 1, "operation_id": "question", "prompt": "测试百科问题",
                "answer": f"[[term:{term['card_id']}]]", "convergence": "conflicted"}
    librarian.request_question("create", question, token=token, origin="gui")
    assert len(links()) == 1
    result, = librarian.decide("question", "confirm", token=token, origin="gui")["results"]
    assert {(item["kind"], item["id"], item["convergence"]) for item in links()} == {
        ("term", term["card_id"], "unresolved"),
        ("question", result["question_id"], "conflicted")}
    assert board("bulletin_open", kind="question", id=result["question_id"])[1] == result

    librarian.request("update", {"schema_version": 1, "operation_id": "term-solve",
                                  "card_id": term["card_id"], "expected_version": 1,
                                  "convergence": "resolved"}, token=token, origin="gui")
    assert len(links()) == 2
    librarian.decide("term-solve", "confirm", token=token, origin="gui")
    assert [item["id"] for item in links()] == [result["question_id"]]
    librarian.request_question("update", {"schema_version": 1, "operation_id": "question-solve",
                                           "question_id": result["question_id"], "expected_version": 1,
                                           "convergence": "resolved"}, token=token, origin="gui")
    librarian.decide("question-solve", "confirm", token=token, origin="gui")
    assert links() == []

def test_validity_and_tracking_are_independent_through_confirmed_edits(tmp_path):
    cards = KnowledgeCardService(tmp_path, token="desktop")
    librarian = Librarian(cards, token="desktop")
    term = cards.create({"schema_version": 1, "operation_id": "term",
                         "title": "示例", "content": "释义"}, token="desktop")
    assert (term["validity"], term["tracked"]) == ("valid", False)
    for version, (changes, expected) in enumerate([
        ({"validity": "questionable", "tracked": False}, ("questionable", True)),
        ({"validity": "valid"}, ("valid", True)),
        ({"tracked": False}, ("valid", False)),
    ], 1):
        operation = f"edit-{version}"
        librarian.request("update", {"schema_version": 1, "operation_id": operation,
                          "card_id": term["card_id"], "expected_version": version, **changes},
                          token="desktop", origin="gui")
        librarian.decide(operation, "confirm", token="desktop", origin="gui")
        current = cards.view(term["card_id"], token="desktop")
        assert (current["validity"], current["tracked"]) == expected
        assert [row["id"] for row in librarian.bulletin_links(token="desktop")] == ([term["card_id"]] if expected[1] else [])


def test_legacy_validity_projection_preserves_term_and_question_references(tmp_path):
    import json
    from hostrun.managed_database import database_connection

    token = "desktop"
    cards = KnowledgeCardService(tmp_path, token=token)
    librarian = Librarian(cards, token=token)
    term = cards.create({"schema_version": 1, "operation_id": "term",
                         "title": "测试", "content": "释义"}, token=token)
    librarian.request_question("create", {"schema_version": 1, "operation_id": "question",
        "prompt": "测试问题", "answer": f"[[term:{term['card_id']}]]"}, token=token, origin="gui")
    question, = librarian.decide("question", "confirm", token=token, origin="gui")["results"]
    for legacy, expected in [
        ({"convergence": "resolved", "questionable": True}, ("valid", False)),
        ({"convergence": "unresolved", "questionable": True}, ("valid", True)),
        ({"convergence": "conflicted", "questionable": False}, ("questionable", True)),
        ({"questionable": True}, ("questionable", True)),
        ({"questionable": False}, ("valid", False)),
    ]:
        with database_connection(tmp_path) as db:
            for table, record in [("knowledge_cards", term), ("hpedia_questions", question)]:
                snapshot = {k: v for k, v in record.items() if k not in ("validity", "tracked")}
                db.execute(f"UPDATE {table} SET result=?", (json.dumps({**snapshot, **legacy}),))
            db.commit()
        for _ in range(2):
            card = cards.view(term["card_id"], token=token)
            entry = librarian.view_question(question["question_id"], token=token)
            assert entry["term_ids"] == [card["card_id"]]
            for record in (card, entry):
                assert (record["validity"], record["tracked"]) == expected
                assert record["version"] == 1

def test_tracked_frontier_links_follow_confirmed_edits(tmp_path):
    librarian = Librarian(KnowledgeCardService(tmp_path, token="s"), token="s")
    def change(action, operation, **fields):
        librarian.request_frontier(action, {"schema_version": 1, "operation_id": operation, **fields},
                                   token="s", origin="gui")
        return librarian.decide(operation, "confirm", token="s", origin="gui")["results"][0]

    item = change("create", "new", question="Frontier [[term:topic]]")
    identity = item["frontier_id"]
    for index, (fields, expected) in enumerate([
        ({}, True), ({"tracked": False}, False), ({"tracked": True}, True), ({"status": "resolved"}, False)
    ]):
        if fields:
            item = change("update", str(index), frontier_id=identity, expected_version=item["version"], **fields)
        links = librarian.bulletin_links(token="s")
        if expected:
            assert links[0]["term_ids"] == ["topic"]
        assert [(link["kind"], link["id"]) for link in links] == ([("frontier", identity)] if expected else [])
        kind, original = bulletin_board_command(librarian, {"action": "bulletin_open", "kind": "frontier",
                                                             "id": identity}, token="s")
        assert kind == "bulletin_frontier" and original == item
