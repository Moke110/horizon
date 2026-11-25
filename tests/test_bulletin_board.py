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
