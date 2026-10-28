"""Full-text search across documents, archived emails, their attachments and metadata
(FR-SRC-01 .. FR-SRC-04).

Query syntax
------------
    budget report            both words (AND is implied)
    budget OR forecast       either word
    budget NOT draft         exclude a word (also: -draft)
    "annual budget"          exact phrase
    budg*   b?dget  *port    wildcards (* = any characters, ? = one character)
    budgit~   budgit~2       fuzzy match (edit distance 1 by default, at most 2)
    (a OR b) AND c           grouping
    name:report  body:x  meta:x   restrict a term to the name/title, the content or metadata/tags

On SQLite the index is an FTS5 table kept in sync with ``search_entries`` by triggers;
other databases fall back to substring matching.
"""
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import func, select, text

from ..extensions import db
from ..models import (ArchivedEmail, Document, DocumentVersion, Folder, MetadataValue, SearchEntry, Tag,
                      document_tags)
from ..validation import TYPE_LABELS, ValidationError

log = logging.getLogger(__name__)

FTS_DDL = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(title, body, meta, content='search_entries', "
    "content_rowid='id', tokenize='unicode61 remove_diacritics 2')",
    "CREATE VIRTUAL TABLE IF NOT EXISTS search_vocab USING fts5vocab(search_fts, 'row')",
    "CREATE TRIGGER IF NOT EXISTS search_entries_ai AFTER INSERT ON search_entries BEGIN "
    "INSERT INTO search_fts(rowid, title, body, meta) VALUES (new.id, new.title, new.body, new.meta); END",
    "CREATE TRIGGER IF NOT EXISTS search_entries_ad AFTER DELETE ON search_entries BEGIN "
    "INSERT INTO search_fts(search_fts, rowid, title, body, meta) VALUES ('delete', old.id, old.title, old.body, old.meta); END",
    "CREATE TRIGGER IF NOT EXISTS search_entries_au AFTER UPDATE ON search_entries BEGIN "
    "INSERT INTO search_fts(search_fts, rowid, title, body, meta) VALUES ('delete', old.id, old.title, old.body, old.meta); "
    "INSERT INTO search_fts(rowid, title, body, meta) VALUES (new.id, new.title, new.body, new.meta); END",
]
COLUMNS = {"name": "title", "title": "title", "subject": "title", "body": "body", "content": "body",
           "meta": "meta", "tag": "meta", "tags": "meta"}
MAX_CANDIDATES = 5000
NO_MATCH = '"qqzxjnomatchqqzxj"'  # a wildcard that matched nothing must not match everything


def fts_available():
    return db.engine.dialect.name == "sqlite"


def setup_fts():
    if not fts_available():
        return
    with db.engine.begin() as conn:
        for ddl in FTS_DDL:
            conn.execute(text(ddl))


# --------------------------------------------------------------------------- indexing

def _upsert(kind, ref_id, title, body, meta):
    entry = db.session.execute(select(SearchEntry).where(SearchEntry.kind == kind, SearchEntry.ref_id == ref_id)
                               ).scalar_one_or_none()
    if entry is None:
        entry = SearchEntry(kind=kind, ref_id=ref_id)
        db.session.add(entry)
    entry.title, entry.body, entry.meta = title or "", body or "", meta or ""


def document_meta_text(doc):
    parts = [doc.extension, TYPE_LABELS.get(doc.extension, ""), doc.folder.path if doc.folder else ""]
    if doc.document_type:
        parts.append(doc.document_type.name)
    if doc.created_by:
        parts.append(doc.created_by.label)
    parts += [t.name for t in doc.tags]
    for mv in doc.metadata_values:
        if mv.value:
            parts.append(f"{mv.field.label} {mv.value}")
    cv = doc.current_version
    if cv and cv.note:
        parts.append(cv.note)
    return " \n".join(p for p in parts if p)


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
    if fts_available():
        with db.engine.begin() as conn:
            conn.execute(text("INSERT INTO search_fts(search_fts) VALUES ('rebuild')"))
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


def _fts_tokens(value):
    """Split like the unicode61 tokenizer (letters/digits, plus wildcard characters)."""
    return [t for t in re.split(r"[^\w*?]+", value.lower()) if t]


def _quote(term):
    return '"' + term.replace('"', '""') + '"'


def _levenshtein(a, b, limit):
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            best = min(best, cur[-1])
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


class _Compiler:
    """AST -> FTS5 MATCH expression, expanding wildcards and fuzzy terms via the vocabulary."""

    def __init__(self, conn):
        self.conn = conn
        self.positive_terms = []

    def vocab_glob(self, pattern):
        rows = self.conn.execute(text("SELECT term FROM search_vocab WHERE term GLOB :p ORDER BY cnt DESC LIMIT 60"),
                                 {"p": pattern}).fetchall()
        return [r[0] for r in rows]

    def vocab_fuzzy(self, word, k):
        rows = self.conn.execute(text(
            "SELECT term FROM search_vocab WHERE length(term) BETWEEN :lo AND :hi LIMIT 200000"),
            {"lo": max(1, len(word) - k), "hi": len(word) + k}).fetchall()
        matches = [r[0] for r in rows if _levenshtein(word, r[0], k) <= k]
        return matches[:60]

    def term(self, node):
        tokens = _fts_tokens(node.value)
        if not tokens:
            return None
        parts = []
        for tok in tokens:
            if node.fuzzy:
                options = self.vocab_fuzzy(tok.replace("*", "").replace("?", ""), node.fuzzy) or [tok]
                parts.append("(" + " OR ".join(_quote(o) for o in options) + ")" if len(options) > 1
                             else _quote(options[0]))
            elif "?" in tok or ("*" in tok and not (tok.endswith("*") and "*" not in tok[:-1])):
                options = self.vocab_glob(tok)
                parts.append("(" + " OR ".join(_quote(o) for o in options) + ")" if options else NO_MATCH)
            elif tok.endswith("*"):
                stem = tok.rstrip("*")
                parts.append(_quote(stem) + " *" if stem else NO_MATCH)
            else:
                parts.append(_quote(tok))
        expr = parts[0] if len(parts) == 1 else "(" + " AND ".join(parts) + ")"
        return f"{{{node.column}}} : {expr}" if node.column else expr

    def phrase(self, node):
        tokens = [t for t in _fts_tokens(node.value.replace("*", " ").replace("?", " "))]
        if not tokens:
            return None
        expr = _quote(" ".join(tokens))
        return f"{{{node.column}}} : {expr}" if node.column else expr

    def compile(self, node):
        if node is None:
            return None
        if node.op == "term":
            return self.term(node)
        if node.op == "phrase":
            return self.phrase(node)
        if node.op == "or":
            parts = [self.compile(c) for c in node.children]
            parts = [p for p in parts if p]
            if any(c.op == "not" for c in node.children):
                raise ValidationError("NOT cannot be used on its own inside an OR group.")
            return "(" + " OR ".join(parts) + ")" if parts else None
        if node.op == "and":
            pos = [self.compile(c) for c in node.children if c.op != "not"]
            neg = [self.compile(c.children[0]) for c in node.children if c.op == "not"]
            pos, neg = [p for p in pos if p], [n for n in neg if n]
            if not pos:
                if neg:
                    raise ValidationError("Add at least one word to search for besides the excluded ones.")
                return None
            expr = "(" + " AND ".join(pos) + ")" if len(pos) > 1 else pos[0]
            for n in neg:
                expr = f"({expr} NOT {n})"
            return expr
        if node.op == "not":
            raise ValidationError("Add at least one word to search for besides the excluded ones.")
        return None


def plain_terms(node, out=None):
    """Positive words of the query (for the substring fallback)."""
    out = [] if out is None else out
    if node is None:
        return out
    if node.op in ("term", "phrase"):
        out.append(node.value.replace("*", "").replace("?", ""))
    elif node.op in ("and", "or"):
        for c in node.children:
            plain_terms(c, out)
    return out


def parse(q):
    return _Parser(_tokenize(q or "")).parse()


def compile_query(q, conn):
    """Public for tests: the FTS5 MATCH string for a user query (None if empty)."""
    return _Compiler(conn).compile(parse(q))


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


def _fts_matches(q):
    """[(kind, ref_id, rank, title_hl, snippet_hl)] for a query, best first."""
    with db.engine.connect() as conn:
        expr = compile_query(q, conn)
        if not expr:
            return None
        try:
            rows = conn.execute(text(
                "SELECT e.kind, e.ref_id, bm25(search_fts, 8.0, 1.0, 3.0) AS rank, "
                "highlight(search_fts, 0, char(1), char(2)), "
                "snippet(search_fts, 1, char(1), char(2), '…', 28), "
                "snippet(search_fts, 2, char(1), char(2), '…', 12) "
                "FROM search_fts JOIN search_entries e ON e.id = search_fts.rowid "
                "WHERE search_fts MATCH :q ORDER BY rank LIMIT :lim"), {"q": expr, "lim": MAX_CANDIDATES}).fetchall()
        except Exception as exc:
            log.info("FTS query failed for %r -> %r: %s", q, expr, exc)
            raise ValidationError("The search could not be understood. Check quotes and brackets.")
    out = []
    for kind, ref_id, rank, title_hl, body_hl, meta_hl in rows:
        snippet = body_hl if "\x01" in (body_hl or "") else (meta_hl if "\x01" in (meta_hl or "") else body_hl)
        out.append((kind, ref_id, rank, title_hl, snippet))
    return out


def _like_matches(q):
    words = [w for w in plain_terms(parse(q)) if w.strip()]
    if not words:
        return None
    stmt = select(SearchEntry)
    for w in words:
        like = f"%{w}%"
        stmt = stmt.where((SearchEntry.title.ilike(like)) | (SearchEntry.body.ilike(like)) | (SearchEntry.meta.ilike(like)))
    rows = db.session.execute(stmt.limit(MAX_CANDIDATES)).scalars().all()
    out = []
    for e in rows:
        idx = e.body.lower().find(words[0].lower())
        snip = e.body[max(0, idx - 80): idx + 160] if idx >= 0 else e.body[:200]
        out.append((e.kind, e.ref_id, 0.0, e.title, snip))
    return out


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


def _email_query(params, ids=None):
    stmt = select(ArchivedEmail)
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
    if q:
        matches = _fts_matches(q) if fts_available() else _like_matches(q)
    if q and matches is None:
        matches = []
    info = {}
    if matches is not None:
        for kind, ref_id, rank, title_hl, snip in matches:
            info[(kind, ref_id)] = (rank, title_hl, snip)

    hits = []
    if params.kind in ("", "document"):
        ids = [rid for (k, rid) in info if k == "document"] if matches is not None else None
        docs = db.session.execute(_document_query(params, resolver, ids).limit(MAX_CANDIDATES)).scalars().all()
        for d in docs:
            rank, title_hl, snip = info.get(("document", d.id), (0.0, None, None))
            cv = d.current_version
            hits.append(Hit("document", d, rank, _marked(title_hl) if title_hl else html.escape(d.name),
                            _marked(snip) if snip else "", d.updated_at, cv.size if cv else 0,
                            TYPE_LABELS.get(d.extension, d.extension.upper())))
    can_emails = user is not None and user.has("emails")
    if params.kind in ("", "email") and can_emails and not _emails_excluded(params):
        ids = [rid for (k, rid) in info if k == "email"] if matches is not None else None
        for e in db.session.execute(_email_query(params, ids).limit(MAX_CANDIDATES)).scalars().all():
            rank, title_hl, snip = info.get(("email", e.id), (0.0, None, None))
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
    return hits, facets(hits)


def facets(hits):
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
            top = d.folder.ancestors()[0] if d.folder and d.folder.ancestors() else d.folder
            bump("folder", (top.id, top.name) if top else (0, "Root"))
            for t in d.tags:
                bump("tag", t.name)
            if d.document_type:
                bump("doctype", (d.document_type.id, d.document_type.name))
    return {k: sorted(v.items(), key=lambda kv: (-kv[1], str(kv[0]))) for k, v in out.items()}
