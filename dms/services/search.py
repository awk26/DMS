"""Full-text search across documents, archived emails, their attachments and metadata
(FR-SRC-01 .. FR-SRC-04).

Extracted text lives in ``search_entries`` (title, body = content, meta = metadata/tags/path).
A query is matched in two steps: a SQL prefilter (LIKE conditions that every possible match
satisfies) narrows the candidates, then each candidate is checked exactly against the parsed
query, scored and highlighted. Terms match case-insensitively anywhere in a word.

    budget report            both words (AND is implied)
    budget AND report        the same
    budget OR forecast       either word
    budget -draft            budget but not draft (also: NOT draft)
    "annual budget"          exact phrase
    budg*   b?dget           wildcards: * any letters, ? one letter
    budgte~   budgte~2       fuzzy: within 1 (or 2) typing mistakes
    title:report             only in the name/subject (also body:, meta:, tag:)
    (budget OR cost) report  grouping
"""
import html
import itertools
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import and_, func, or_, select

from ..extensions import db
from ..models import (ArchivedEmail, Document, DocumentVersion, Folder, GmailAccount, MetadataValue, SearchEntry,
                      Tag, document_tags)
from ..validation import TYPE_LABELS

log = logging.getLogger(__name__)

COLUMNS = {"name": "title", "title": "title", "subject": "title", "body": "body", "content": "body",
           "meta": "meta", "tag": "meta", "tags": "meta"}
MAX_CANDIDATES = 5000


# --------------------------------------------------------------------------- indexing

def _upsert(kind, ref_id, title, body, meta):
    entry = db.session.execute(select(SearchEntry).where(SearchEntry.kind == kind, SearchEntry.ref_id == ref_id)
                               ).scalar_one_or_none()
    if entry is None:
        entry = SearchEntry(kind=kind, ref_id=ref_id)
        db.session.add(entry)
    entry.title, entry.body, entry.meta = title or "", body or "", meta or ""


def document_meta_text(doc, with_path=True, with_note=True):
    """Indexed metadata text. ``with_path``/``with_note`` False leave out the folder path and the current
    version's note (used at query time for users who may not see a parent folder or that version)."""
    parts = [doc.extension, TYPE_LABELS.get(doc.extension, ""),
             doc.folder.path if doc.folder and with_path else ""]
    if doc.document_type:
        parts.append(doc.document_type.name)
    if doc.created_by:
        parts.append(doc.created_by.label)
    parts += [t.name for t in doc.tags]
    for mv in doc.metadata_values:
        if mv.value:
            parts.append(f"{mv.field.label} {mv.value}")
    cv = doc.current_version
    if cv and cv.note and with_note:
        parts.append(cv.note)
    return " \n".join(p for p in parts if p)


@dataclass
class _TrimmedEntry:
    """A search entry with the parts the user may not see removed."""
    title: str
    body: str
    meta: str


def _visible_top_folder(folder, resolver):
    """The highest folder above (or at) ``folder`` the user may view, or None."""
    if folder is None:
        return None
    for node in folder.ancestors() + [folder]:
        if "view" in resolver.folder(node):
            return node
    return None


def index_document(doc, body=None):
    """(Re)index a document. ``body`` None re-extracts the current version's text."""
    if doc.deleted_at is not None:
        remove("document", doc.id)
        return
    if body is None:
        from .text_extract import extract_version
        body = extract_version(doc.current_version, current_app.config.get("SEARCH_EXTRACT_MAX_MB", 50))
    _upsert("document", doc.id, doc.name, body, document_meta_text(doc))


def index_document_meta(doc):
    """Refresh name/metadata without re-extracting content."""
    entry = db.session.execute(select(SearchEntry).where(SearchEntry.kind == "document",
                                                         SearchEntry.ref_id == doc.id)).scalar_one_or_none()
    if entry is None:
        index_document(doc)
    elif doc.deleted_at is not None:
        remove("document", doc.id)
    else:
        entry.title, entry.meta = doc.name, document_meta_text(doc)


def index_email(record, raw_bytes=None):
    from .emails import email_text
    from ..storage import get_storage
    try:
        data = raw_bytes if raw_bytes is not None else get_storage().read_bytes(record.storage_key)
        body = email_text(data)
    except Exception:
        log.info("Could not extract text from email %s", record.id, exc_info=True)
        body = record.snippet
    meta = " \n".join([record.sender, record.recipients, record.cc,
                       " ".join(a.filename for a in record.attachments), record.gmail_state])
    _upsert("email", record.id, record.subject, body, meta)


def remove(kind, ref_id):
    db.session.query(SearchEntry).filter(SearchEntry.kind == kind, SearchEntry.ref_id == ref_id
                                         ).delete(synchronize_session=False)


def reindex_all():
    count = 0
    for doc in db.session.execute(select(Document).where(Document.deleted_at.is_(None))).scalars():
        index_document(doc)
        count += 1
        if count % 50 == 0:
            db.session.commit()
    for record in db.session.execute(select(ArchivedEmail)).scalars():
        index_email(record)
        count += 1
        if count % 50 == 0:
            db.session.commit()
    db.session.commit()
    return count


# --------------------------------------------------------------------------- query parsing

_TOKEN_RE = re.compile(r'\s*(?:(?P<lp>\()|(?P<rp>\))|(?P<field>[A-Za-z]+):(?=\S)|(?P<neg>-)(?=[^\s-])|'
                       r'"(?P<phrase>[^"]*)"?|(?P<word>[^\s()"]+))')


@dataclass
class Node:
    op: str                       # term | phrase | and | or | not
    value: str = ""
    children: list = field(default_factory=list)
    column: str = ""
    fuzzy: int = 0


def _tokenize(q):
    pos, tokens = 0, []
    while pos < len(q):
        m = _TOKEN_RE.match(q, pos)
        if not m or m.end() == pos:
            pos += 1
            continue
        pos = m.end()
        if m.group("lp"):
            tokens.append(("(", ""))
        elif m.group("rp"):
            tokens.append((")", ""))
        elif m.group("field") is not None:
            tokens.append(("field", m.group("field").lower()))
        elif m.group("neg"):
            tokens.append(("NOT", ""))
        elif m.group("phrase") is not None:
            tokens.append(("phrase", m.group("phrase")))
        else:
            w = m.group("word")
            if w in ("AND", "OR", "NOT", "&&", "||"):
                tokens.append(({"&&": "AND", "||": "OR"}.get(w, w), ""))
            else:
                tokens.append(("word", w))
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        node = self.or_expr()
        return node

    def or_expr(self):
        parts = [self.and_expr()]
        while self.peek()[0] == "OR":
            self.take()
            parts.append(self.and_expr())
        parts = [p for p in parts if p is not None]
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else Node("or", children=parts)

    def and_expr(self):
        parts = []
        while True:
            kind, _ = self.peek()
            if kind is None or kind in (")", "OR"):
                break
            if kind == "AND":
                self.take()
                continue
            node = self.unary()
            if node is not None:
                parts.append(node)
        if not parts:
            return None
        return parts[0] if len(parts) == 1 else Node("and", children=parts)

    def unary(self):
        kind, _ = self.peek()
        if kind == "NOT":
            self.take()
            inner = self.unary()
            return Node("not", children=[inner]) if inner is not None else None
        return self.primary()

    def primary(self, column=""):
        kind, value = self.take()
        if kind == "(":
            node = self.or_expr()
            if self.peek()[0] == ")":
                self.take()
            return node
        if kind == "field":
            col = COLUMNS.get(value)
            nxt = self.primary(col or "")
            if col is None and nxt is not None and nxt.op == "term":  # unknown prefix: search it literally
                nxt.value = f"{value} {nxt.value}"
            return nxt
        if kind == "phrase":
            return Node("phrase", value=value, column=column) if value.strip() else None
        if kind == "word":
            fuzzy = 0
            m = re.match(r"^(.*?)~(\d?)$", value)
            if m and m.group(1):
                value, fuzzy = m.group(1), min(int(m.group(2) or 1), 2) or 1
            return Node("term", value=value, column=column, fuzzy=fuzzy)
        return None


def parse(q):
    return _Parser(_tokenize(q or "")).parse()


# --------------------------------------------------------------------------- searching

@dataclass
class Hit:
    kind: str
    obj: object
    rank: float
    title_html: str
    snippet_html: str
    date: datetime
    size: int
    type_label: str


def _marked(s):
    """Escape text and turn the \\x01 / \\x02 highlight markers into <mark>."""
    return html.escape(s or "").replace("\x01", "<mark>").replace("\x02", "</mark>")


# --------------------------------------------------------------------------- matching

TEXT_COLUMNS = ("title", "body", "meta")
WEIGHTS = {"title": 5.0, "meta": 2.0, "body": 1.0}
_WORD_RE = re.compile(r"\w+")


def _like_escape(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _node_columns(node):
    return [node.column] if node.column else list(TEXT_COLUMNS)


def _any_like(node, pattern):
    return or_(*[getattr(SearchEntry, col).like(pattern, escape="\\") for col in _node_columns(node)])


def _fuzzy_pieces(word, distance):
    """Pieces of ``word``, at least one of which every word within ``distance`` typing mistakes
    contains unchanged (None when no such pieces exist).

    ``word`` is cut into distance + 1 parts, so at least one part has no mistake inside it. A swap
    of two letters across a cut only touches the letters next to the cut, so those are dropped.
    """
    n = distance + 1
    size = len(word) // n
    if size < 1:
        return None
    parts = [word[i * size:(i + 1) * size if i < n - 1 else len(word)] for i in range(n)]
    pieces = [p[(1 if i > 0 else 0):(len(p) - 1 if i < n - 1 else len(p))] for i, p in enumerate(parts)]
    return pieces if all(pieces) else None


def _prefilter(node):
    """SQL condition true for every entry the query can match (None = no restriction)."""
    if node is None:
        return None
    if node.op == "term":
        if node.fuzzy:
            pieces = _fuzzy_pieces(node.value.lower(), node.fuzzy)
            return or_(*[_any_like(node, f"%{_like_escape(p)}%") for p in pieces]) if pieces else None
        pattern = _like_escape(node.value).replace("*", "%").replace("?", "_")
        return _any_like(node, f"%{pattern}%") if pattern.strip("%_") else None
    if node.op == "phrase":
        words = node.value.split()
        return and_(*[_any_like(node, f"%{_like_escape(w)}%") for w in words]) if words else None
    if node.op == "and":
        parts = [p for p in map(_prefilter, node.children) if p is not None]
        return and_(*parts) if parts else None
    if node.op == "or":
        parts = [_prefilter(c) for c in node.children]
        return None if any(p is None for p in parts) else or_(*parts)
    return None  # NOT is decided exactly, after the prefilter


def _typo_distance(a, b, limit):
    """Typing mistakes between a and b (insert, delete, replace, or swap two neighbouring letters),
    or limit + 1 as soon as it is known to exceed ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    before, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            value = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                value = min(value, before[j - 2] + 1)
            cur.append(value)
        if min(cur) > limit:
            return limit + 1
        before, prev = prev, cur
    return prev[-1]


class _Candidate:
    """Lower-cased texts of one search entry; ``with_body`` False hides the content."""

    def __init__(self, entry, with_body=True):
        self.entry = entry
        self.raw = {"title": entry.title or "", "body": (entry.body or "") if with_body else "",
                    "meta": entry.meta or ""}
        self.text = {col: value.lower() for col, value in self.raw.items()}
        self._cache = {}

    def pattern(self, node, col):
        """Compiled regex for a term/phrase in one column (fuzzy terms: the matching words)."""
        key = (id(node), col)
        if key not in self._cache:
            if node.op == "phrase":
                rx = re.compile(r"\s+".join(re.escape(w) for w in node.value.lower().split()))
            elif node.fuzzy:
                word = node.value.lower()
                close = {w for w in set(_WORD_RE.findall(self.text[col]))
                         if _typo_distance(word, w, node.fuzzy) <= node.fuzzy}
                rx = re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, sorted(close, key=len, reverse=True)))) \
                    if close else None
            else:
                rx = re.compile("".join(r"\w*" if ch == "*" else r"\w" if ch == "?" else re.escape(ch)
                                        for ch in node.value.lower()))
            self._cache[key] = rx
        return self._cache[key]

    def spans(self, node, col, limit=50):
        rx = self.pattern(node, col)
        if rx is None:
            return []
        return [m.span() for m in itertools.islice(rx.finditer(self.text[col]), limit) if m.end() > m.start()]

    def matches(self, node):
        if node.op in ("term", "phrase"):
            return any(self.spans(node, col, 1) for col in _node_columns(node))
        if node.op == "and":
            return all(self.matches(c) for c in node.children)
        if node.op == "or":
            return any(self.matches(c) for c in node.children)
        if node.op == "not":
            return not self.matches(node.children[0])
        return False


def _positive_leaves(node, negated=False, out=None):
    """Terms and phrases outside NOT: these are scored and highlighted."""
    out = [] if out is None else out
    if node is None:
        return out
    if node.op in ("term", "phrase"):
        if not negated:
            out.append(node)
    else:
        for child in node.children:
            _positive_leaves(child, negated != (node.op == "not"), out)
    return out


def _mark(raw, lowered, spans):
    """``raw`` with \\x01/\\x02 around the spans (offsets taken from its lower-cased form)."""
    raw = raw.replace("\x01", "").replace("\x02", "")
    if not spans or len(raw) != len(lowered):
        return raw
    out, pos = [], 0
    for start, end in sorted(spans):
        if start < pos:
            start = pos
        if start >= end:
            continue
        out += [raw[pos:start], "\x01", raw[start:end], "\x02"]
        pos = end
    out.append(raw[pos:])
    return "".join(out)


def _evaluate(node, entry, with_body=True):
    """(rank, marked title, marked snippet) when ``entry`` matches the query, else None."""
    cand = _Candidate(entry, with_body)
    if not cand.matches(node):
        return None
    leaves = _positive_leaves(node)
    spans = {col: [s for leaf in leaves for s in cand.spans(leaf, col)] for col in TEXT_COLUMNS}
    score = sum(WEIGHTS[col] * len(found) for col, found in spans.items())
    body, body_spans = cand.raw["body"], spans["body"]
    if body_spans:
        first = min(start for start, _ in body_spans)
        lo = max(0, first - 80)
        hi = min(len(body), first + 160)
        window = [(max(s, lo) - lo, min(e, hi) - lo) for s, e in body_spans if s < hi and e > lo]
        snippet = _mark(body[lo:hi], cand.text["body"][lo:hi], window)
        snippet = ("…" if lo else "") + snippet + ("…" if hi < len(body) else "")
    else:
        snippet = body[:200].replace("\x01", "").replace("\x02", "")
    title = _mark(cand.raw["title"], cand.text["title"], spans["title"])
    return -score, title, snippet  # lower rank sorts first


def _matches(q):
    """(query node, {(kind, ref_id): (rank, title, snippet, entry)}) or None for an empty query."""
    node = parse(q)
    if node is None:
        return None
    stmt = select(SearchEntry)
    condition = _prefilter(node)
    if condition is not None:
        stmt = stmt.where(condition)
    out = {}
    for entry in db.session.execute(stmt.limit(MAX_CANDIDATES)).scalars():
        found = _evaluate(node, entry)
        if found is not None:
            out[(entry.kind, entry.ref_id)] = (*found, entry)
    return node, out


@dataclass
class SearchParams:
    q: str = ""
    kind: str = ""            # "" | document | email
    types: list = field(default_factory=list)   # type labels
    folder_id: int = None
    include_subfolders: bool = True
    tags: list = field(default_factory=list)
    document_type_id: int = None
    date_from: datetime = None
    date_to: datetime = None
    date_field: str = "modified"  # modified | created
    size_min: int = None
    size_max: int = None
    author_id: int = None
    meta_field_id: int = None
    meta_value: str = ""
    sort: str = "relevance"   # relevance | date | name | size


def _descendant_ids(folder_id):
    rows = db.session.execute(select(Folder.id, Folder.parent_id).where(Folder.deleted_at.is_(None))).all()
    children = {}
    for fid, pid in rows:
        children.setdefault(pid, []).append(fid)
    out, stack = set(), [folder_id]
    while stack:
        f = stack.pop()
        if f in out:
            continue
        out.add(f)
        stack.extend(children.get(f, []))
    return out


def _document_query(params, resolver, ids=None):
    stmt = (select(Document).join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
            .where(Document.deleted_at.is_(None), resolver.visible_documents_clause()))
    if ids is not None:
        stmt = stmt.where(Document.id.in_(ids))
    if params.types:
        exts = [e for e, label in TYPE_LABELS.items() if label in params.types]
        stmt = stmt.where(Document.extension.in_(exts or ["-"]))
    if params.folder_id:
        folders = _descendant_ids(params.folder_id) if params.include_subfolders else {params.folder_id}
        stmt = stmt.where(Document.folder_id.in_(folders))
    if params.document_type_id:
        stmt = stmt.where(Document.document_type_id == params.document_type_id)
    if params.author_id:
        stmt = stmt.where(Document.created_by_id == params.author_id)
    col = Document.created_at if params.date_field == "created" else Document.updated_at
    if params.date_from:
        stmt = stmt.where(col >= params.date_from)
    if params.date_to:
        stmt = stmt.where(col < params.date_to + timedelta(days=1))
    if params.size_min is not None:
        stmt = stmt.where(DocumentVersion.size >= params.size_min)
    if params.size_max is not None:
        stmt = stmt.where(DocumentVersion.size <= params.size_max)
    for tag in params.tags:
        stmt = stmt.where(Document.id.in_(select(document_tags.c.document_id).join(Tag, Tag.id == document_tags.c.tag_id)
                                          .where(func.lower(Tag.name) == tag.lower())))
    if params.meta_field_id and params.meta_value:
        stmt = stmt.where(Document.id.in_(select(MetadataValue.document_id).where(
            MetadataValue.field_id == params.meta_field_id, MetadataValue.value.ilike(f"%{params.meta_value}%"))))
    return stmt


def _email_query(params, user, ids=None):
    # Only emails from the user's own mailboxes (same rule as sp_email_list / sp_email_get_owned).
    stmt = (select(ArchivedEmail).join(GmailAccount, GmailAccount.id == ArchivedEmail.account_id)
            .where(GmailAccount.user_id == user.id))
    if ids is not None:
        stmt = stmt.where(ArchivedEmail.id.in_(ids))
    if params.date_from:
        stmt = stmt.where(ArchivedEmail.received_at >= params.date_from)
    if params.date_to:
        stmt = stmt.where(ArchivedEmail.received_at < params.date_to + timedelta(days=1))
    if params.size_min is not None:
        stmt = stmt.where(ArchivedEmail.size >= params.size_min)
    if params.size_max is not None:
        stmt = stmt.where(ArchivedEmail.size <= params.size_max)
    return stmt


def _emails_excluded(params):
    return bool(params.types and "Email" not in params.types) or bool(
        params.folder_id or params.tags or params.document_type_id or params.author_id or params.meta_field_id)


def search(params, resolver, user):
    """Run a search. Returns (hits, facets)."""
    q = (params.q or "").strip()
    matches = None
    node, info = None, {}
    if q:
        found = _matches(q)
        matches = [] if found is None else found
        if found is not None:
            node, info = found

    hits = []
    if params.kind in ("", "document"):
        ids = [rid for (k, rid) in info if k == "document"] if matches is not None else None
        docs = db.session.execute(_document_query(params, resolver, ids).limit(MAX_CANDIDATES)).scalars().all()
        for d in docs:
            rank, title_hl, snip, entry = info.get(("document", d.id), (0.0, None, None, None))
            cv = d.current_version
            version_hidden = cv is not None and "view" not in resolver.version(cv)
            path_hidden = d.folder is not None and any(
                "view" not in resolver.folder(f) for f in d.folder.ancestors() + [d.folder])
            if entry is not None and (version_hidden or path_hidden):
                # Match only what this user may see (FR-SRC-03): never the content or note of a restricted
                # current version, never the names of folders above the document that are hidden from them.
                trimmed = _TrimmedEntry(entry.title, "" if version_hidden else entry.body,
                                        document_meta_text(d, with_path=not path_hidden,
                                                           with_note=not version_hidden))
                found = _evaluate(node, trimmed, with_body=not version_hidden)
                if found is None:
                    continue
                rank, title_hl, snip = found[0], found[1], ("" if version_hidden else found[2])
            hits.append(Hit("document", d, rank, _marked(title_hl) if title_hl else html.escape(d.name),
                            _marked(snip) if snip else "", d.updated_at, cv.size if cv and not version_hidden else 0,
                            TYPE_LABELS.get(d.extension, d.extension.upper())))
    if params.kind in ("", "email") and user is not None and not _emails_excluded(params):
        ids = [rid for (k, rid) in info if k == "email"] if matches is not None else None
        for e in db.session.execute(_email_query(params, user, ids).limit(MAX_CANDIDATES)).scalars().all():
            rank, title_hl, snip, _ = info.get(("email", e.id), (0.0, None, None, None))
            hits.append(Hit("email", e, rank, _marked(title_hl) if title_hl else html.escape(e.subject or "(no subject)"),
                            _marked(snip) if snip else html.escape(e.snippet or ""), e.received_at or e.imported_at,
                            e.size, "Email"))

    if params.sort == "date":
        hits.sort(key=lambda h: h.date or datetime.min, reverse=True)
    elif params.sort == "name":
        hits.sort(key=lambda h: re.sub("<[^>]+>", "", h.title_html).lower())
    elif params.sort == "size":
        hits.sort(key=lambda h: h.size or 0, reverse=True)
    elif q:
        hits.sort(key=lambda h: h.rank)
    else:
        hits.sort(key=lambda h: h.date or datetime.min, reverse=True)
    return hits, facets(hits, resolver)


def facets(hits, resolver=None):
    out = {"kind": {}, "type": {}, "folder": {}, "tag": {}, "year": {}, "doctype": {}}

    def bump(group, key):
        if key:
            out[group][key] = out[group].get(key, 0) + 1

    for h in hits:
        bump("kind", "Documents" if h.kind == "document" else "Emails")
        bump("type", h.type_label)
        if h.date:
            bump("year", str(h.date.year))
        if h.kind == "document":
            d = h.obj
            if resolver is not None:
                top = _visible_top_folder(d.folder, resolver)  # never name a folder the user cannot see
            else:
                top = d.folder.ancestors()[0] if d.folder and d.folder.ancestors() else d.folder
            if top is not None or d.folder is None:
                bump("folder", (top.id, top.name) if top else (0, "Root"))
            for t in d.tags:
                bump("tag", t.name)
            if d.document_type:
                bump("doctype", (d.document_type.id, d.document_type.name))
    return {k: sorted(v.items(), key=lambda kv: (-kv[1], str(kv[0]))) for k, v in out.items()}
