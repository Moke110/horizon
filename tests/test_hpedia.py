import pytest

from hostrun.agent_service import AgentService, CommandError
from hostrun.desktop_bridge import librarian_question_command
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_themes import KnowledgeThemeService
from hostrun.librarian import Librarian
from hostrun.voyager_materials import MaterialsService


TOKEN = "acceptance"


def test_cutover_preserves_terms_and_sources_and_confirms_the_first_question(tmp_path):
    cards = KnowledgeCardService(tmp_path, token=TOKEN)
    term = cards.create({"schema_version": 1, "operation_id": "term",
                         "title": "测试光谱", "content": "测试术语释义。"}, token=TOKEN)
    themes = KnowledgeThemeService(tmp_path, token=TOKEN, cards=cards)
    themes.create({"schema_version": 1, "operation_id": "old",
                   "judgement": "开发阶段命题", "related_cards": [], "evidence": []}, token=TOKEN)
    materials = MaterialsService(tmp_path, token=TOKEN)
    material = materials.register_text({"schema_version": 1, "operation_id": "source",
                                        "title": "测试资料", "text": "原始资料", "source_label": "测试"}, token=TOKEN)

    AgentService(tmp_path, token=TOKEN)
    librarian = Librarian(cards, token=TOKEN)
    assert themes.list(token=TOKEN) == []
    assert librarian.view(term["card_id"], token=TOKEN) == term
    assert materials.list_summaries(token=TOKEN)[0]["material_id"] == material["material_id"]

    def bridge(action, **fields):
        return librarian_question_command(librarian, {"action": action, **fields},
                                          token=TOKEN, origin="gui")

    assert bridge("question_list") == ("question_list", [])
    proposal = {"schema_version": 1, "operation_id": "first", "prompt": "如何定义这个现象？",
                "answer": "这是一个没有引用的回答。"}
    with pytest.raises(CommandError, match="术语"):
        bridge("question_create", **proposal)
    proposal["answer"] = "参见 [[term:" + term["card_id"] + "]]。"
    kind, confirmation = bridge("question_create", **proposal)
    assert kind == "question_confirmation"
    assert confirmation["status"] == "pending"
    assert bridge("question_list") == ("question_list", [])
    _, result = bridge("question_confirm", operation_id="first", choice="confirm")
    question, = result["results"]
    assert question["term_ids"] == [term["card_id"]]
    assert question["prompt"] == proposal["prompt"]
    assert question["answer"] == proposal["answer"]
    AgentService(tmp_path, token=TOKEN)  # A restart does not clear first questions.
    assert bridge("question_open", question_id=question["question_id"])[1] == question
    with pytest.raises(CommandError, match="只有白板"):
        bridge("question_create", **{**proposal, "operation_id": "bad-link",
                                    "prompt": f"[[question:{question['question_id']}]]"})
    term_archive = {"schema_version": 1, "operation_id": "retire-term",
                    "card_id": term["card_id"], "expected_version": 1}
    with pytest.raises(CommandError, match="百科"):
        librarian.request("archive", term_archive, token=TOKEN, origin="gui")
    edit = {"schema_version": 1, "operation_id": "edit", "question_id": question["question_id"],
            "expected_version": 1, "prompt": "改后的题干"}
    bridge("question_update", **edit)
    assert bridge("question_open", question_id=question["question_id"])[1] == question
    with pytest.raises(CommandError, match="确认"):
        bridge("question_update", **{**edit, "operation_id": "overlap"})
    bridge("question_confirm", operation_id="edit", choice="reject")
    bridge("question_delete", **{key: value for key, value in edit.items() if key != "prompt"} |
           {"operation_id": "delete"})
    bridge("question_confirm", operation_id="delete", choice="confirm")
    assert bridge("question_list") == ("question_list", [])
    assert librarian.request("archive", term_archive, token=TOKEN, origin="gui")["status"] == "pending"


def test_agent_hpedia_question_is_confirmed_in_its_own_conversation(tmp_path):
    import queue

    from hostrun.desktop_worker import DesktopWorker
    from hostrun.knowledge_cards import KnowledgeCardService
    from hostrun.librarian import Librarian

    token = "desktop"
    cards = KnowledgeCardService(tmp_path, token=token)
    term = cards.create({"schema_version": 1, "operation_id": "term",
                         "title": "光谱", "content": "光的频率成分。"}, token=token)
    worker = DesktopWorker()
    worker.home = tmp_path
    worker.service_token = token
    worker.task_id = "question-origin"
    worker.events = queue.Queue()
    receipt = worker._term_request({"type": "question_request", "action": "create", "requestId": "question",
        "testData": True, "fields": {"prompt": "解释 [[term:" + term["card_id"] + "]]", "answer": "测试回答"}})
    assert (receipt["entity"], receipt["status"]) == ("question", "pending")
    librarian = Librarian(cards, token=token)
    assert librarian.list_questions(token=token) == []
    assert librarian.confirmations(token=token, origin="agent:question-origin") == [receipt]
    result = librarian.decide("question", "confirm", token=token, origin="agent:question-origin")
    assert result["results"][0]["tags"] == ["测试"]
    assert librarian.list_questions(token=token) == result["results"]
