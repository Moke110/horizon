"""Read-only search across the local Knowledge Workbench.

The workbench joins the three knowledge owners that already live in the stable
data home: Knowledge Cards, Themes and Frontiers. It never writes them and it
does not add a second store or an index that could drift from the committed
records.

Search is intentionally offline and lexical. Chinese runs are matched as
substrings, so one, two and many-character queries work without segmentation;
Latin words and digits are matched case-insensitively after Unicode NFKC
normalization, which also folds full-width forms. A query may mix Chinese and
English, with or without spaces, and every term must be present in one of the
object's own projected fields. A query made only of punctuation matches
nothing instead of listing the whole database.

Scopes are explicit. ``active`` is the current known set: active known Cards
and Themes, including ``questionable`` and ``source_pending`` ones, plus
following Frontiers. ``inactive`` is archived Cards and Themes plus paused,
resolved and deleted Frontiers. Pending confirmation cards are returned by
Keeper separately from this committed-knowledge query.

Relations are direct, typed and bidirectional: Card/Card and Theme/Card only,
no transitive closure and no Frontier edges. ``related_to`` names one typed
target and keeps the records on the other end of a stored edge; a valid target
with no edge yields an empty page rather than an error.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from hostrun.agent_service import CommandError
from hostrun.frontier import FrontierService
from hostrun.librarian import Librarian
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.knowledge_themes import KnowledgeThemeService
from hostrun.managed_database import database_connection

_KINDS = ("card", "theme", "frontier")
_SCOPES = ("active", "inactive", "pending")
_ID_KEYS = {"card": "card_id", "theme": "theme_id", "frontier": "frontier_id"}
_DEFAULT_LIMIT = 40
_MAX_LIMIT = 100
_MAX_QUERY_BYTES = 4096
_MAX_ID_BYTES = 128
_SUMMARY_CHARS = 240
# Keep Chinese runs whole while separating Latin words/digits, so "光学sensor"
# is two required terms and "单细胞扰动" remains one substring term.
_HAN_RUN = re.compile(
    r"([\u3007\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002ee5f\U0002f800-\U0002fa1f\U00030000-\U000323af]+)")
_WORD = re.compile(r"[^\W_]+(?:[._+-][^\W_]+)*")


def _normalize(value):
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _query_terms(query):
    parts = _HAN_RUN.split(_normalize(query))
    words = [word for part in parts[::2] for word in _WORD.findall(part)]
    return parts[1::2] + words


def _summary(text):
    return " ".join((text or "").split())[:_SUMMARY_CHARS]


def _id(kind, row):
    return row[_ID_KEYS[kind]]


def _title(kind, row):
    if kind == "card":
        return row["title"]
    if kind == "theme":
        return row["judgement"]
    return row["question"]


def _summary_text(kind, row):
    if kind == "card":
        return row.get("content", "")
    if kind == "theme":
        return "；".join(row.get("conditions", []))
    return row["scope"].get("text", "")


def _haystack(kind, row):
    """Projected record text only; pinned source bytes are never searched."""
    if kind == "card":
        fields = [row.get("title", ""), row.get("content", ""),
                  *row.get("aliases", [])]
    elif kind == "theme":
        fields = [row.get("judgement", ""), *row.get("conditions", [])]
    else:
        scope = row.get("scope", {})
        constraints = row.get("hard_constraints", {})
        fields = [row.get("question", ""), scope.get("text", ""),
                  *scope.get("alternatives", []),
                  *constraints.get("must_include", []),
                  *constraints.get("exclude", [])]
    return _normalize(" ".join(field for field in fields if field))


def _in_scope(kind, row, scope):
    if scope == "pending":
        return False
    if scope == "active":
        if kind == "frontier":
            return row["status"] == "following"
        return row["status"] == "active" and bool(row.get("known"))
    if kind == "frontier":
        return row["status"] in {"paused", "resolved", "deleted"}
    return row["status"] == "archived"


def _related_edges(rows):
    """Direct typed edges among existing Cards and Themes.

    Both directions are recorded, so a stored one-way relation answers
    ``related_to`` from either endpoint. Frontiers never take part.
    """

    existing = {(kind, _id(kind, row)) for kind, row in rows}
    edges = {}

    def link(left, right):
        if right not in existing:
            return  # A stale relation to an absent record has no edge.
        edges.setdefault(left, set()).add(right)
        edges.setdefault(right, set()).add(left)

    for kind, row in rows:
        if kind == "card":
            left = ("card", _id(kind, row))
            for related in row.get("relations", []):
                link(left, ("card", related))
        elif kind == "theme":
            left = ("theme", _id(kind, row))
            for related in row.get("related_cards", []):
                link(left, ("card", related["card_id"]))
    return edges


class KnowledgeWorkbenchService:
    """Authenticated, read-only search over Cards, Themes and Frontiers."""

    def __init__(self, home, *, token, cards=None, themes=None, frontiers=None):
        if not isinstance(token, str) or not token:
            raise ValueError("A launch token is required")
        self.home = Path(home)
        self._token = token
        self.cards = cards if cards is not None else KnowledgeCardService(
            home, token=token)
        self.themes = themes if themes is not None else KnowledgeThemeService(
            home, token=token, cards=self.cards)
        self.frontiers = frontiers if frontiers is not None else FrontierService(
            home, token=token)

    def _authorize(self, token):
        import hmac

        if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
            raise PermissionError("Unauthorized desktop command")

    @staticmethod
    def _kind(kind):
        if kind is None:
            return None
        if not isinstance(kind, str) or kind not in _KINDS:
            raise CommandError("invalid_query", "kind must be card, theme or frontier")
        return kind

    @staticmethod
    def _scope(scope):
        if not isinstance(scope, str) or scope not in _SCOPES:
            raise CommandError("invalid_query", "scope must be active, inactive or pending")
        return scope

    @staticmethod
    def _related(related_to):
        if related_to is None:
            return None
        if not isinstance(related_to, dict) \
                or related_to.keys() != {"kind", "id"}:
            raise CommandError("invalid_query",
                               "related_to must contain kind and id")
        kind = related_to["kind"]
        target = related_to["id"]
        if not isinstance(kind, str) or kind not in _KINDS:
            raise CommandError("invalid_query",
                               "related_to.kind must be card, theme or frontier")
        if not isinstance(target, str) or not target.strip() \
                or len(target.encode("utf-8")) > _MAX_ID_BYTES:
            raise CommandError("invalid_query", "related_to.id must be an id")
        return (kind, target)

    @staticmethod
    def _page_bounds(limit, offset):
        if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
            raise CommandError("invalid_query",
                               f"limit must be an integer from 1 to {_MAX_LIMIT}")
        if type(offset) is not int or offset < 0:
            raise CommandError("invalid_query",
                               "offset must be a nonnegative integer")

    def _rows(self):
        """Read every owned record once, inside one read transaction."""
        with database_connection(self.home) as db:
            db.execute("BEGIN")
            cards = self.cards.query_all_in_transaction(db)
            themes = self.themes.query_all_in_transaction(db)
            frontiers = (self.cards.query_frontiers_in_transaction(db)
                         if isinstance(self.cards, Librarian) else self.frontiers.query_all_in_transaction(db))
        return ([("card", card) for card in cards]
                + [("theme", theme) for theme in themes]
                + [("frontier", frontier) for frontier in frontiers])

    @staticmethod
    def _result(kind, row):
        result = {
            "kind": kind,
            "id": _id(kind, row),
            "version": row["version"],
            "title": _title(kind, row),
            "summary": _summary(_summary_text(kind, row)),
            "status": row["status"],
        }
        if kind == "card":
            result["questionable"] = bool(row.get("questionable", False))
            result["source_pending"] = bool(row.get("source_pending", False))
        return result

    def search(self, query="", *, token, kind=None, scope="active",
               related_to=None, limit=_DEFAULT_LIMIT, offset=0, all_terms=False):
        """Search one scope with direct relation, type and paging filters.

        An empty query lists the whole scope in descending update order. A
        nonempty query is split into NFKC/casefolded Han and Latin terms and
        every term must be present; punctuation-only input matches nothing.
        """
        self._authorize(token)
        if not isinstance(query, str):
            raise CommandError("invalid_query", "query must be text")
        if len(query.encode("utf-8")) > _MAX_QUERY_BYTES:
            raise CommandError("invalid_query", "query exceeds its bound")
        selected_kind = self._kind(kind)
        selected_scope = self._scope(scope)
        targets = related_to if isinstance(related_to, list) else [related_to]
        if isinstance(related_to, list) and None in targets:
            raise CommandError("invalid_query", "relation targets must contain kind and id")
        targets = {self._related(item) for item in targets if item is not None}
        self._page_bounds(limit, offset)
        if type(all_terms) is not bool or (all_terms and (selected_kind != "card" or offset != 0)):
            raise CommandError("invalid_query", "all_terms requires a dictionary search from offset zero")
        terms = _query_terms(query)
        if query.strip() and not terms:
            return {"results": [], "total": 0, "offset": offset, "limit": limit}
        if selected_scope == "pending":
            # Pending proposals are read through Keeper, outside known search.
            return {"results": [], "total": 0, "offset": offset, "limit": limit}

        rows = self._rows()
        if targets:
            edges = _related_edges(rows)
            neighbors = {neighbor for target in targets for neighbor in edges.get(target, ())}
        matched = []
        for kind, row in rows:
            if selected_kind is not None and kind != selected_kind:
                continue
            if not _in_scope(kind, row, selected_scope):
                continue
            if targets and (kind, _id(kind, row)) not in neighbors:
                continue
            if terms:
                haystack = _haystack(kind, row)
                if any(term not in haystack for term in terms):
                    continue
            matched.append((kind, row))

        matched.sort(key=lambda item: (-item[1]["updated_at"],
                                       item[0], _id(item[0], item[1])))
        total = len(matched)
        if all_terms:
            limit = total
        page = [self._result(kind, row)
                for kind, row in matched[offset:offset + limit]]
        return {"results": page, "total": total, "offset": offset, "limit": limit}
