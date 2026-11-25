"""Store Hpedia questions and their explicit Dictionary references."""

import json
import re
import uuid

from hostrun.agent_service import CommandError
from hostrun.convergence import validate


_TERM_LINK = re.compile(r"\[\[term:([^\[\]\s]+)\]\]")
_RETIRED_THEME_TABLES = ("knowledge_themes", "knowledge_theme_operations", "knowledge_theme_events")


def initialize_database(db):
    db.execute("CREATE TABLE IF NOT EXISTS hpedia_questions(question_id TEXT PRIMARY KEY, result TEXT NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS hpedia_term_references(
        question_id TEXT NOT NULL, term_id TEXT NOT NULL, PRIMARY KEY(question_id, term_id))""")
    db.execute("""CREATE INDEX IF NOT EXISTS hpedia_references_by_term
        ON hpedia_term_references(term_id)""")


def migrate_legacy_themes(db):
    """Cut over once on the same transaction as the new desktop schema."""
    initialize_database(db)
    db.execute("CREATE TABLE IF NOT EXISTS hpedia_migrations(name TEXT PRIMARY KEY)")
    applied = db.execute("INSERT OR IGNORE INTO hpedia_migrations VALUES('retire_themes')").rowcount
    if not applied:
        return
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    for table in _RETIRED_THEME_TABLES:
        if table in tables:
            db.execute(f"DELETE FROM {table}")
    if "keeper_proposals" in tables:
        db.execute("""UPDATE keeper_proposals SET result=json_set(result,'$.status','cancelled')
            WHERE json_extract(result,'$.status') IN ('pending','revising','returned')
              AND EXISTS(SELECT 1 FROM json_each(result,'$.changes')
                         WHERE json_extract(value,'$.kind')='theme')""")


def check_term_removal(db, term_id):
    exists = db.execute("""SELECT 1 FROM sqlite_schema
        WHERE type='table' AND name='hpedia_term_references'""").fetchone()
    if exists and db.execute("SELECT 1 FROM hpedia_term_references WHERE term_id=? LIMIT 1",
                             (term_id,)).fetchone():
        raise CommandError("term_referenced", "请先移除百科中对该术语的引用")


def read(db, question_id):
    if not isinstance(question_id, str) or not question_id:
        raise CommandError("invalid_question", "需要百科问题标识")
    row = db.execute("SELECT result FROM hpedia_questions WHERE question_id=?", (question_id,)).fetchone()
    if row is None:
        raise CommandError("not_found", "百科问题不存在")
    return json.loads(row[0])


def list_questions(db):
    rows = db.execute("SELECT result FROM hpedia_questions ORDER BY rowid DESC").fetchall()
    return [json.loads(row[0]) for row in rows]


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 24000:
        raise CommandError("invalid_question", f"{label}必填且最多 24000 字节")
    if "[[question:" in value.casefold():
        raise CommandError("invalid_question", "只有白板可以链接百科问题")
    return value.strip()


def apply(db, action, command, *, read_term, check_question_lock, now):
    if type(command.get("schema_version")) is not int or command["schema_version"] != 1:
        raise CommandError("unsupported_schema", "需使用版本 1 的百科问题命令")
    if action == "create":
        question = {"question_id": str(uuid.uuid4()), "version": 0, "created_at": int(now)}
    else:
        question = read(db, command.get("question_id"))
        check_question_lock(question["question_id"])
        if type(command.get("expected_version")) is not int or command["expected_version"] != question["version"]:
            raise CommandError("version_conflict", "百科问题已更新，请重新打开")
    identity = question["question_id"]
    db.execute("DELETE FROM hpedia_term_references WHERE question_id=?", (identity,))
    if action == "delete":
        db.execute("DELETE FROM hpedia_questions WHERE question_id=?", (identity,))
        return {**question, "status": "deleted"}
    prompt = _text(command.get("prompt", question.get("prompt")), "题干")
    answer = _text(command.get("answer", question.get("answer")), "解答")
    term_ids = list(dict.fromkeys(_TERM_LINK.findall(prompt) + _TERM_LINK.findall(answer)))
    if not term_ids:
        raise CommandError("invalid_question", "题干或解答需明确引用至少一个词典术语")
    for term_id in term_ids:
        try:
            term = read_term(term_id)
        except CommandError as error:
            raise CommandError("invalid_question", "引用的词典术语不存在") from error
        if term["status"] != "active":
            raise CommandError("invalid_question", "只可引用有效的词典术语")
    question.update(version=question["version"] + 1, updated_at=int(now),
                    prompt=prompt, answer=answer, term_ids=term_ids)
    if "convergence" in command:
        question["convergence"] = validate(command["convergence"])
    db.execute("""INSERT INTO hpedia_questions VALUES(?,?)
        ON CONFLICT(question_id) DO UPDATE SET result=excluded.result""",
               (identity, json.dumps(question)))
    db.executemany("INSERT INTO hpedia_term_references VALUES(?,?)",
                   [(identity, term_id) for term_id in term_ids])
    return question
