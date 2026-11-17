"""Generate the literal folding table of python/src/email_reply_extractor/_names.py.

The table replaces a runtime NFKD step. For every code point ``c`` in RANGES with
``c.lower() == c`` (the table is applied after ``str.lower``), the entry is ``c`` ->
NFKD(c) with every code point of non-zero canonical combining class removed, kept only when
it differs from ``c``. The combining diacritical marks U+0300-U+036F map to "". EXTRA (the
letters without a decomposition, and "ß" that ``str.casefold`` expanded) overrides.

The block between the BEGIN/END markers in _names.py is rewritten in place.

usage: python python/tools/gen_fold_table.py [--check]
"""

import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "python" / "src" / "email_reply_extractor" / "_names.py"
BEGIN = "# BEGIN GENERATED FOLD TABLE (python/tools/gen_fold_table.py)\n"
END = "# END GENERATED FOLD TABLE\n"

RANGES = [
    (0x0080, 0x00FF),  # Latin-1 Supplement
    (0x0100, 0x017F),  # Latin Extended-A
    (0x0180, 0x024F),  # Latin Extended-B
    (0x1E00, 0x1EFF),  # Latin Extended Additional
    (0x2026, 0x2026),  # HORIZONTAL ELLIPSIS
]
MARKS = (0x0300, 0x036F)  # Combining Diacritical Marks
EXTRA = {
    "ß": "ss",
    "ø": "o",
    "ł": "l",
    "đ": "d",
    "æ": "ae",
    "œ": "oe",
    "ı": "i",
    "ð": "d",
    "þ": "th",
}


def build():
    table = {}
    for lo, hi in RANGES:
        for cp in range(lo, hi + 1):
            c = chr(cp)
            if unicodedata.category(c) == "Cn" or c.lower() != c:
                continue
            v = "".join(
                ch for ch in unicodedata.normalize("NFKD", c) if not unicodedata.combining(ch)
            )
            if v != c:
                table[c] = v
    for cp in range(MARKS[0], MARKS[1] + 1):
        table[chr(cp)] = ""
    table.update(EXTRA)
    for k, v in table.items():
        assert v.lower() == v, (k, v)
    return dict(sorted(table.items()))


def _lit(s):
    out = []
    for ch in s:
        if 0x20 <= ord(ch) < 0x7F and ch not in '"\\':
            out.append(ch)
        elif ord(ch) <= 0xFFFF:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(f"\\U{ord(ch):08x}")
    return '"' + "".join(out) + '"'


def render(table):
    lines = [BEGIN, "# fmt: off\n", "_FOLD_TABLE = {\n"]
    items = [f"{_lit(k)}: {_lit(v)}," for k, v in table.items()]
    row = "   "
    for it in items:
        if len(row) + 1 + len(it) > 99:
            lines.append(row + "\n")
            row = "   "
        row += " " + it
    lines.append(row + "\n")
    lines.append("}\n")
    lines.append("# fmt: on\n")
    lines.append(END)
    return "".join(lines)


def main():
    table = build()
    src = TARGET.read_text()
    start = src.index(BEGIN)
    end = src.index(END) + len(END)
    new = src[:start] + render(table) + src[end:]
    print(f"{len(table)} entries; unicodedata {unicodedata.unidata_version}", file=sys.stderr)
    if "--check" in sys.argv:
        sys.exit(0 if new == src else 1)
    TARGET.write_text(new)


if __name__ == "__main__":
    main()
