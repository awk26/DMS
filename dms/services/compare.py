"""Compare two versions of a document: side-by-side and redline views (FR-VER-03)."""
import difflib
import html
import re

from ..validation import ValidationError
from .text_extract import EXTRACTABLE, extract_version

MAX_LINES = 20000
CONTEXT = 3
_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def supported(ext):
    return ext in EXTRACTABLE


def _words(a, b):
    """Word-level diff of two lines: (left_html, right_html, redline_html)."""
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    left, right, red = [], [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        sa, sb = html.escape("".join(ta[i1:i2])), html.escape("".join(tb[j1:j2]))
        if op == "equal":
            left.append(sa)
            right.append(sb)
            red.append(sa)
        else:
            if sa:
                left.append(f"<del>{sa}</del>")
                red.append(f"<del>{sa}</del>")
            if sb:
                right.append(f"<ins>{sb}</ins>")
                red.append(f"<ins>{sb}</ins>")
    return "".join(left), "".join(right), "".join(red)


def compare_versions(doc, old, new):
    if not supported(doc.extension):
        raise ValidationError(f"Comparison is available for text, PDF and Office documents, not .{doc.extension} files.")
    a = extract_version(old, max_mb=100).splitlines()[:MAX_LINES]
    b = extract_version(new, max_mb=100).splitlines()[:MAX_LINES]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    rows, redline = [], []
    stats = {"added": 0, "removed": 0, "changed": 0}
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            block = list(range(i2 - i1))
            if len(block) > 2 * CONTEXT + 1:
                keep = block[:CONTEXT] + [None] + block[-CONTEXT:]
            else:
                keep = block
            for k in keep:
                if k is None:
                    skipped = len(block) - 2 * CONTEXT
                    rows.append({"kind": "skip", "count": skipped})
                    redline.append({"kind": "skip", "count": skipped})
                    continue
                text = html.escape(a[i1 + k])
                rows.append({"kind": "equal", "ln": i1 + k + 1, "rn": j1 + k + 1, "left": text, "right": text})
                redline.append({"kind": "equal", "html": text})
        elif op == "replace":
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                la = a[i1 + k] if i1 + k < i2 else None
                lb = b[j1 + k] if j1 + k < j2 else None
                if la is not None and lb is not None:
                    left, right, red = _words(la, lb)
                    rows.append({"kind": "change", "ln": i1 + k + 1, "rn": j1 + k + 1, "left": left, "right": right})
                    redline.append({"kind": "change", "html": red})
                    stats["changed"] += 1
                elif la is not None:
                    rows.append({"kind": "delete", "ln": i1 + k + 1, "rn": "", "left": html.escape(la), "right": ""})
                    redline.append({"kind": "delete", "html": f"<del>{html.escape(la)}</del>"})
                    stats["removed"] += 1
                else:
                    rows.append({"kind": "insert", "ln": "", "rn": j1 + k + 1, "left": "", "right": html.escape(lb)})
                    redline.append({"kind": "insert", "html": f"<ins>{html.escape(lb)}</ins>"})
                    stats["added"] += 1
        elif op == "delete":
            for k in range(i1, i2):
                rows.append({"kind": "delete", "ln": k + 1, "rn": "", "left": html.escape(a[k]), "right": ""})
                redline.append({"kind": "delete", "html": f"<del>{html.escape(a[k])}</del>"})
                stats["removed"] += 1
        else:
            for k in range(j1, j2):
                rows.append({"kind": "insert", "ln": "", "rn": k + 1, "left": "", "right": html.escape(b[k])})
                redline.append({"kind": "insert", "html": f"<ins>{html.escape(b[k])}</ins>"})
                stats["added"] += 1
    identical = stats == {"added": 0, "removed": 0, "changed": 0}
    return {"rows": rows, "redline": redline, "stats": stats, "identical": identical,
            "empty": not a and not b}
