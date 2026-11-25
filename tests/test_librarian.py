import pytest

from hostrun.agent_service import CommandError
from hostrun.librarian import Librarian
from hostrun.knowledge_cards import KnowledgeCardService


def test_existing_terms_remain_readable_until_the_origin_confirms(tmp_path):
    token = "desktop"
    cards = KnowledgeCardService(tmp_path, token=token)
    term = cards.create({"schema_version": 1, "operation_id": "legacy",
                         "title": "光谱", "content": "旧释义"}, token=token)
    librarian = Librarian(cards, token=token)
    edit = {"schema_version": 1, "operation_id": "edit", "card_id": term["card_id"],
            "expected_version": 1, "content": "新释义"}
    card = librarian.request("update", edit, token=token, origin="gui")
    assert card["status"] == "pending"
    assert librarian.view(term["card_id"], token=token) == term
    with pytest.raises(CommandError, match="确认"):
        librarian.request("update", {**edit, "operation_id": "other"},
                          token=token, origin="agent")
    result = librarian.decide("edit", "confirm", token=token, origin="gui")
    assert result["status"] == "confirmed"
    assert librarian.view(term["card_id"], token=token)["content"] == "新释义"
    assert librarian.decide("edit", "confirm", token=token, origin="gui") == result


def test_expired_request_cannot_execute_and_releases_the_term(tmp_path):
    clock = [1000]
    token = "desktop"
    cards = KnowledgeCardService(tmp_path, token=token, clock=lambda: clock[0])
    term = cards.create({"schema_version": 1, "operation_id": "legacy",
                         "title": "光谱", "content": "旧释义"}, token=token)
    librarian = Librarian(cards, token=token, clock=lambda: clock[0])
    edit = {"schema_version": 1, "operation_id": "edit", "card_id": term["card_id"],
            "expected_version": 1, "content": "新释义"}
    librarian.request("update", edit, token=token, origin="agent")
    with pytest.raises(CommandError, match="确认"):
        cards.update({**edit, "operation_id": "bypass"}, token=token)
    clock[0] += 72 * 60 * 60
    assert librarian.decide("edit", "confirm", token=token, origin="agent")["status"] == "expired"
    assert librarian.view(term["card_id"], token=token) == term
    assert librarian.request("update", {**edit, "operation_id": "new-edit"},
                             token=token, origin="gui")["status"] == "pending"


def test_bridge_requires_the_origin_and_applies_an_import_as_one_batch(tmp_path):
    from hostrun.desktop_bridge import librarian_card_command

    token = "desktop"
    librarian = Librarian(KnowledgeCardService(tmp_path, token=token), token=token)
    first = {"schema_version": 1, "operation_id": "first", "title": "光谱", "content": "定义"}
    second = {**first, "operation_id": "second", "title": "波段"}
    kind, pending = librarian_card_command(librarian, {
        "action": "card_batch", "operation_id": "import",
        "changes": [["create", first], ["create", second]],
    }, token=token, origin="agent:session")
    assert kind == "term_confirmation"
    assert pending["status"] == "pending"
    assert librarian.confirmations(token=token, origin="gui") == []
    assert librarian.list(token=token) == []
    with pytest.raises(CommandError, match="本会话"):
        librarian_card_command(librarian, {"action": "card_confirm",
                               "operation_id": "import", "choice": "confirm"},
                               token=token, origin="gui")
    _, confirmed = librarian_card_command(librarian, {"action": "card_confirm",
                                          "operation_id": "import", "choice": "confirm"},
                                          token=token, origin="agent:session")
    assert confirmed["status"] == "confirmed"
    assert {row["title"] for row in librarian.list(token=token)} == {"光谱", "波段"}
