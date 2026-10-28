"""Custom metadata, document types, controlled vocabularies and tags (FR-MET-01, FR-MET-02)."""
import re
from datetime import datetime

from sqlalchemy import func, select

from ..extensions import db
from ..models import DocumentType, MetadataField, MetadataValue, Tag, Vocabulary, VocabularyTerm
from ..validation import ValidationError
from . import settings

FIELD_TYPES = {"text": "Text", "number": "Number", "date": "Date", "choice": "Choice (vocabulary)",
               "boolean": "Yes / No"}
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")


def folder_chain_ids(folder):
    ids, node, seen = [], folder, set()
    while node is not None and node.id not in seen:
        seen.add(node.id)
        ids.append(node.id)
        node = node.parent
    return ids


def applicable_fields(folder, document_type_id):
    """Fields that apply to a document of this type in this folder."""
    chain = folder_chain_ids(folder)
    fields = db.session.execute(select(MetadataField).order_by(MetadataField.sort_order, MetadataField.label)
                                ).scalars().all()
    out = []
    for f in fields:
        if f.document_type_id is not None and f.document_type_id != document_type_id:
            continue
        if f.folder_id is not None and f.folder_id not in chain:
            continue
        out.append(f)
    return out


def _coerce(field, raw):
    raw = (raw or "").strip()
    if field.field_type == "boolean":
        return "yes" if raw in ("1", "yes", "true", "on") else ("no" if raw in ("0", "no", "false") else "")
    if not raw:
        return ""
    if field.field_type == "number":
        try:
            float(raw)
        except ValueError:
            raise ValidationError(f"'{field.label}' must be a number.")
        return raw
    if field.field_type == "date":
        try:
            datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            raise ValidationError(f"'{field.label}' must be a date (YYYY-MM-DD).")
        return raw
    if field.field_type == "choice":
        allowed = {t.value for t in field.vocabulary.terms} if field.vocabulary else set()
        if raw not in allowed:
            raise ValidationError(f"'{raw}' is not an allowed value for '{field.label}'.")
        return raw
    return raw[:2000]


def values_from_form(form, folder, document_type_id):
    """Validate posted metadata (fields named meta_<id>). Returns {field_id: value}."""
    values = {}
    for field in applicable_fields(folder, document_type_id):
        value = _coerce(field, form.get(f"meta_{field.id}"))
        if field.required and value in ("",):
            raise ValidationError(f"'{field.label}' is required.")
        values[field.id] = value
    return values


def apply_values(doc, values):
    existing = {v.field_id: v for v in doc.metadata_values}
    for field_id, value in values.items():
        if field_id in existing:
            existing[field_id].value = value
        elif value != "":
            doc.metadata_values.append(MetadataValue(field_id=field_id, value=value))


def document_type_from_form(form):
    raw = form.get("document_type_id")
    if not raw:
        return None
    try:
        dt = db.session.get(DocumentType, int(raw))
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        raise ValidationError("Unknown document type.")
    return dt


def parse_tags(text):
    names = []
    for part in re.split(r"[,;\n]+", text or ""):
        name = re.sub(r"\s+", " ", part).strip()[:80]
        if name and name.lower() not in {n.lower() for n in names}:
            names.append(name)
    return names


def resolve_tags(names):
    """Tag objects for names; creates new tags unless tags are controlled."""
    controlled = settings.get("tags_controlled")
    out = []
    for name in names:
        tag = db.session.execute(select(Tag).where(func.lower(Tag.name) == name.lower())).scalar_one_or_none()
        if tag is None:
            if controlled:
                raise ValidationError(f"'{name}' is not an approved tag. Choose from the tag vocabulary.")
            tag = Tag(name=name)
            db.session.add(tag)
            db.session.flush()
        out.append(tag)
    return out


def all_tags():
    return db.session.execute(select(Tag).order_by(func.lower(Tag.name))).scalars().all()


def schema_json(folder, document_type_id):
    """Field definitions for the upload/metadata forms (rendered by app.js)."""
    out = []
    for f in applicable_fields(folder, document_type_id):
        out.append({"id": f.id, "name": f.name, "label": f.label, "type": f.field_type, "required": f.required,
                    "help": f.help_text,
                    "choices": [t.value for t in f.vocabulary.terms] if f.vocabulary else []})
    return out


# --------------------------------------------------------------------------- administration

def save_field(field, form):
    name = (form.get("name") or "").strip().lower()
    if not _NAME_RE.match(name):
        raise ValidationError("Field keys use lower-case letters, digits and underscores, starting with a letter.")
    label = (form.get("label") or "").strip()[:120]
    if not label:
        raise ValidationError("Field label is required.")
    ftype = form.get("field_type", "text")
    if ftype not in FIELD_TYPES:
        raise ValidationError("Invalid field type.")
    vocab_id = form.get("vocabulary_id", type=int) if hasattr(form, "get") else None
    if ftype == "choice" and not vocab_id:
        raise ValidationError("Choice fields need a vocabulary.")
    field = field or MetadataField()
    field.name, field.label, field.field_type = name, label, ftype
    field.required = form.get("required") == "1"
    field.help_text = (form.get("help_text") or "").strip()[:300]
    field.sort_order = form.get("sort_order", type=int) or 0
    field.vocabulary_id = vocab_id if ftype == "choice" else None
    field.document_type_id = form.get("document_type_id", type=int) or None
    field.folder_id = form.get("folder_id", type=int) or None
    db.session.add(field)
    db.session.commit()
    return field


def save_vocabulary(vocab, name, description, terms_text):
    name = (name or "").strip()[:120]
    if not name:
        raise ValidationError("Vocabulary name is required.")
    clash = db.session.execute(select(Vocabulary).where(func.lower(Vocabulary.name) == name.lower())).scalar_one_or_none()
    if clash is not None and clash is not vocab:
        raise ValidationError(f"A vocabulary named '{name}' already exists.")
    vocab = vocab or Vocabulary()
    vocab.name, vocab.description = name, (description or "").strip()[:500]
    wanted = []
    for line in (terms_text or "").splitlines():
        v = line.strip()[:200]
        if v and v not in wanted:
            wanted.append(v)
    current = {t.value: t for t in vocab.terms}
    vocab.terms = [current.get(v) or VocabularyTerm(value=v) for v in wanted]
    db.session.add(vocab)
    db.session.commit()
    return vocab


def vocabularies():
    return db.session.execute(select(Vocabulary).order_by(Vocabulary.name)).scalars().all()


def document_types():
    return db.session.execute(select(DocumentType).order_by(DocumentType.name)).scalars().all()


def fields():
    return db.session.execute(select(MetadataField).order_by(MetadataField.sort_order, MetadataField.label)
                              ).scalars().all()
