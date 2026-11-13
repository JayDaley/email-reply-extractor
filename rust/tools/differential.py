"""Differential test: compare the Rust port with the Python reference.

Usage (from the repository root)::

    .venv/bin/python rust/tools/differential.py [--cases N] [--seed S] [--keep DIR]

Generates inputs beyond the conformance vectors — truncated, sliced and
mutated fixture bodies, generated lines built from the reference's keyword
lists, Unicode edge strings, and HTML fragments — runs each through the
Python reference (``python/src``) and through the Rust port
(``cargo run --release --example difftest``), and reports every case whose
outputs differ. Exit status 1 when any case differs or the Rust driver
fails (a panic included).

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python" / "src"))

import email_reply_extractor as ere
from email_reply_extractor import _fragments, cleaning, extraction

# --- reference calls --------------------------------------------------------------


def _lines(a):
    return a["lines"]


REFERENCE = {
    "extract_new_text": lambda a: ere.extract_new_text(
        a.get("body"), a.get("parent_body"), a.get("html_body")
    ),
    "clean_for_scoring": lambda a: ere.clean_for_scoring(
        a["extracted_text"], a.get("html_signature_text")
    ),
    "strip_parent_content": lambda a: ere.strip_parent_content(
        a["text"], a["parent_body"], continuation=a.get("continuation", False)
    ),
    "html_to_text": lambda a: ere.html_to_text(a["html"]),
    "split_html_parts": lambda a: ere.split_html_parts(a["html"]),
    "tolerant_lines": lambda a: ere.tolerant_lines(a["text"]),
    "texts_equivalent": lambda a: ere.texts_equivalent(a["a"], a["b"]),
    "read_fragments": lambda a: _fragments.read_fragments(a["text"]),
    "is_attribution_line": lambda a: extraction.is_attribution_line(a["line"]),
    "is_quote_line": lambda a: extraction.is_quote_line(a["line"]),
    "is_signoff_name_line": lambda a: extraction.is_signoff_name_line(a["text"]),
    "is_mobile_tagline": lambda a: cleaning.is_mobile_tagline(a["line"]),
    "is_signature_delimiter": lambda a: cleaning.is_signature_delimiter(a["line"]),
    "normalize_body": lambda a: extraction.normalize_body(a["body"]),
    "custom_clean": lambda a: extraction.custom_clean(
        a["text"], localized=a.get("localized", True)
    ),
    "count_unquoted_content_lines": lambda a: extraction.count_unquoted_content_lines(
        a["body"], localized=a.get("localized", True)
    ),
    "find_quote_header_block": lambda a: extraction.find_quote_header_block(
        _lines(a), localized=a.get("localized", True)
    ),
    "find_signoff_boundary": lambda a: extraction.find_signoff_boundary(_lines(a)),
    "strip_attribution_lines": lambda a: extraction.strip_attribution_lines(_lines(a)),
    "strip_after_original_message_divider": lambda a: (
        extraction.strip_after_original_message_divider(
            _lines(a), localized=a.get("localized", True)
        )
    ),
}


def encode(value):
    if hasattr(value, "__dataclass_fields__"):
        return {k: encode(getattr(value, k)) for k in value.__dataclass_fields__}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def reference(case):
    try:
        return {"result": encode(REFERENCE[case["fn"]](case["args"]))}
    except Exception:  # noqa: BLE001 - only internal helpers raise
        return {"error": True}


# --- input generation ---------------------------------------------------------------

EDGE_CHARS = [
    "\u0130",
    "\u0131",
    "\u017f",
    "\u212a",
    "\u0301",
    "\u00b2",
    "\u00bd",
    "\u2160",
    "\x1c",
    "\x1f",
    "\x85",
    "\xa0",
    "\u202f",
    "\u2028",
    "\u2029",
    "\u3000",
    "\u180e",
    "\ufeff",
    "\u200b",
    "\u200d",
    "\u2060",
    "\r",
    "\t",
    "\x0b",
    "\x0c",
    "\x00",
    "\u1e9e",
    "\u00df",
    "\u0345",
    "\u01c5",
    "\u00e9",
    "e\u0301",
    "\u5e74",
    "\u306e",
    "\uff1a",
    "\U0001f600",
    "\u0663",
    "_",
    "-",
    "\\",
    "$",
    "&",
    ";",
    "#",
    "<",
    ">",
    "'",
    '"',
]

KEYWORDS = [
    "On",
    "Am",
    "Le",
    "El",
    "El d\xeda",
    "wrote:",
    "schrieb",
    "a \xe9crit :",
    "escribi\xf3:",
    "\u306e\u30e1\u30fc\u30eb:",
    "2026\u5e747\u670822\u65e5",
    "2026/07/22",
    "From:",
    "Sent:",
    "To:",
    "Cc:",
    "Subject:",
    "Date:",
    "Reply-To:",
    "Message-ID:",
    "Received:",
    "Resent-From:",
    "De :",
    "De\xa0:",
    "Da:",
    "Van:",
    "Von:",
    "Envoy\xe9 :",
    "Objet :",
    "Asunto:",
    "Betreff:",
    "Onderwerp:",
    "Oggetto:",
    "\u53d1\u4ef6\u4eba\uff1a",
    "\u4e3b\u3000\u9898\uff1a",
    "\u6536\u4ef6\u4eba:",
    "-----Original Message-----",
    "-------- Message d'origine --------",
    "---- Mensaje original ----",
    "-- Urspr\xfcngliche Nachricht --",
    "--",
    "-- ",
    "______",
    "========",
    "~ Ann",
    "Best,",
    "Best regards,",
    "Cheers, Peter",
    "Thanks",
    "Thx.",
    "Gr\xfc\xdfe",
    "Mit freundlichen Gr\xfc\xdfen",
    "Ng\u0101 mihi",
    "Cordialement",
    "Hi all,",
    "Hello Bob!",
    "Dear Ms. Smith,",
    "Hola",
    "Kia ora",
    "Sent from my iPhone",
    "Get Outlook for iOS",
    "Tel: +1 905.502.7000",
    "ORCID: 0009-0007-4602-5624",
    "LinkedIn <https://x.example/in/a>",
    "Tokyo Office: 1-2-3 Shibuya 150-0021",
    "www.example.org",
    "https://example.org/a",
    "a.b@example.org",
    "Dr. Jane Doe <jane@example.org>",
    "CONFIDENTIALITY NOTICE:",
    "This email and any attachments are confidential",
    "If you are not the intended recipient",
    "To unsubscribe",
    "mailing list -- foo",
    "-----BEGIN PGP SIGNATURE-----",
    "[Med]",
    "[RFC8174]",
    "[Linda3]",
    "> ",
    ">> ",
    "...>",
    "  > ",
    "Songbo",
    "Thi Nguyen-Huu | CEO",
    "O'Brien",
    "Louis Navarre, UCLouvain",
    "123 Main Street | Suite 4",
    "M9W 4Y8",
    "Ottawa, ON 12345",
    "C:\\Users\\x",
    "\\1",
    "\\g<2>",
    "\\n",
    "\\101",
    "Jos\xe9",
    "\u0130stanbul",
    "Lea",
]


BOUNDARY_TOKENS = [
    "On",
    "Am",
    "Le",
    "El",
    "El d\xeda",
    "schrieb",
    "wrote:",
    "a \xe9crit :",
    "escribi\xf3:",
    "mailing list",
    "--",
    "To unsubscribe",
    "PGP key-id",
    "PGP key-fingerprint",
    "www.",
    "https://",
    "x",
    "\xe9",
    "\u0301",
    "\u0345",
    "\xb2",
    "_",
    " ",
    ":",
    "\n",
    "1",
    "2026",
    "\u5e74",
    "\u0130",
    "\xa0",
    "-",
    "MAILING LIST",
]

NEIGHBORS = ["", "", " ", "x", "\u0345", "\u0301", "\xb2", "_", "\n", ":", "\xe9", "1", "-", "\t"]


def boundary_line(rng: random.Random) -> str:
    if rng.random() < 0.3:
        return "".join(rng.choice(BOUNDARY_TOKENS) for _ in range(rng.randint(1, 8)))
    first = rng.choice(
        ["On", "Am", "Le", "El", "El d\xeda", "To unsubscribe", "PGP key-id", "", "x mailing list"]
    )
    last = rng.choice(
        [
            "wrote:",
            "schrieb",
            "schrieb:",
            "a \xe9crit :",
            "escribi\xf3:",
            "mailing list",
            "--",
            "www.x",
        ]
    )
    filler = "".join(
        rng.choice(BOUNDARY_TOKENS + ["Bob", " ", " "]) for _ in range(rng.randint(0, 3))
    )
    if rng.random() < 0.5:
        first = rng.choice(["On", "Am", "On", "Am", "Le", "El d\xeda"])
        last = rng.choice(["wrote:", "schrieb", "schrieb:", "a \xe9crit :", "escribi\xf3:"])
        tail = rng.choice(["", " ", "\n", ":", " :", "x:", "\u0345:"])
    else:
        tail = (
            rng.choice(NEIGHBORS)
            + rng.choice(["", ":", " x:", "--x", ""])
            + rng.choice(["", " ", "\n", "\r"])
        )
    parts = [
        rng.choice(["", " ", "\t"]),
        first,
        rng.choice(NEIGHBORS),
        filler,
        rng.choice(NEIGHBORS),
        last,
        tail,
    ]
    return "".join(parts)


TEMPLATE_TOKENS = [
    "\\U",
    "\\n",
    "\\t",
    "\\1",
    "\\2",
    "\\3",
    "\\12",
    "\\g<1>",
    "\\g<0>",
    "\\g<2>",
    "\\g<x>",
    "\\g<",
    "\\g<>",
    "\\g<007>",
    "\\0",
    "\\012",
    "\\177",
    "\\400",
    "\\\\",
    "\\.",
    "\\-",
    "\\a",
    "\\b",
    "\\x41",
    "\\$",
    "$1",
    "$&",
    "a",
    " ",
    "\\",
    "\\é",
    "\\8",
]


def random_code_point(rng: random.Random) -> str:
    while True:
        cp = rng.choice([rng.randint(0, 0x7F), rng.randint(0x80, 0x3000), rng.randint(0, 0x10FFFF)])
        if not 0xD800 <= cp <= 0xDFFF:
            return chr(cp)


def edge_string(rng: random.Random, n: int) -> str:
    parts = []
    for _ in range(n):
        r = rng.random()
        if r < 0.05:
            parts.append(random_code_point(rng))
        elif r < 0.35:
            parts.append(rng.choice(EDGE_CHARS))
        elif r < 0.75:
            parts.append(rng.choice(KEYWORDS))
        elif r < 0.85:
            parts.append(rng.choice([" ", "  ", "\n", "\n\n", ", ", ". "]))
        else:
            parts.append(rng.choice(["Name", "text", "a", "Bob Smith", "x" * rng.randint(1, 50)]))
    return "".join(parts)


def generated_line(rng: random.Random) -> str:
    pre = rng.choice(["", " ", "\t", "  ", "> ", "*"])
    body = " ".join(
        rng.choice(KEYWORDS + ["Bob", "Ann Lee", "some text", "2026"])
        for _ in range(rng.randint(1, 4))
    )
    post = rng.choice(["", " ", ",", ":", "!", ".", "\r", "\xa0", "\u2028", "\n"])
    if rng.random() < 0.3:
        body = body.upper() if rng.random() < 0.5 else body.lower()
    if rng.random() < 0.2:
        i = rng.randint(0, len(body))
        body = body[:i] + rng.choice(EDGE_CHARS) + body[i:]
    return pre + body + post


def generated_body(rng: random.Random) -> str:
    return "\n".join(
        generated_line(rng) if rng.random() < 0.7 else "" for _ in range(rng.randint(1, 25))
    )


HTML_TOKENS = [
    "<",
    "</",
    "<!--",
    "-->",
    "--!>",
    "<!-->",
    "<![CDATA[",
    "]]>",
    "<!DOCTYPE html>",
    "<!doctype",
    "<?xml ?>",
    "<?",
    ">",
    "/>",
    " ",
    "\n",
    "\t",
    "\r",
    "=",
    "'",
    '"',
    "&",
    "&amp;",
    "&amp",
    "&#",
    "&#x",
    "&#38;",
    "&#x26;",
    "&#128;",
    "&#0;",
    "&#xD800;",
    "&#1114112;",
    "&noti;",
    "&notin;",
    "&nbsp",
    "&nbsp;",
    "&copy",
    "&ampx",
    "&lt",
    "&#65",
    "&#x41g",
    "&bogus;",
    "&;",
    "&#;",
    "div",
    "p",
    "br",
    "BR",
    "blockquote",
    "script",
    "style",
    "head",
    "title",
    "textarea",
    "xmp",
    "plaintext",
    "iframe",
    "DIV",
    "Div",
    "li",
    "table",
    "tr",
    "span",
    "a",
    "img",
    "hr",
    "b",
    ' class="gmail_quote"',
    " class='gmail_quote_container'",
    " class=gmail_attr",
    ' class="gmail_signature"',
    ' id="Signature"',
    " id=divRplyFwdMsg",
    ' class="moz-cite-prefix"',
    ' class="OutlookMessageHeader"',
    ' class="a b Signature"',
    " id=appendonsend",
    ' class="x" class="gmail_signature"',
    " data-x=&amp;",
    ' title="&notin;&#65;"',
    " a= 'b",
    ' a="b',
    " checked",
    " /",
    "/ ",
    "Hello",
    "world",
    "On Mon, Bob wrote:",
    "Thanks,",
    "Ann",
    "\xa0",
    "\u2028",
    "\x1c",
    "\u0130",
    "K",
    "\u212a",
    "&#" + "1" * 4300 + ";",
    "&#" + "0" * 4301,
    "&#x" + "0" * 5000 + "41;",
    "<a title='&#" + "9" * 4301 + "'>",
]

HTML_SHAPES = [
    "<div>{a}</div>",
    "<p>{a}<br>{b}</p>",
    "<blockquote>{a}</blockquote>{b}",
    '<div class="gmail_quote">{a}<blockquote>{b}</blockquote>{c}</div>',
    '<div class="gmail_quote">{a}</div>',
    '<div class="gmail_signature">{a}</div>{b}',
    "<html><head><title>{a}</title><style>{b}</style></head><body>{c}</body></html>",
    "<script>{a}</script>{b}",
    "<textarea>{a}</textarea>{b}",
    "<xmp>{a}</xmp>{b}",
    "<plaintext>{a}",
    "<!-- {a} -->{b}",
    "<table><tr><td>{a}</td><td>{b}</td></tr></table>",
    '<div id="divRplyFwdMsg">{a}</div><div>{b}</div>',
    "<ul><li>{a}<li>{b}</ul>",
    "{a}</div>{b}",
    "<div>{a}<blockquote>{b}</div>{c}</blockquote>",
]


def html_fragment(rng: random.Random) -> str:
    if rng.random() < 0.5:
        return "".join(rng.choice(HTML_TOKENS) for _ in range(rng.randint(1, 30)))
    shape = rng.choice(HTML_SHAPES)
    fill = {
        k: html_fragment(rng) if rng.random() < 0.3 else edge_string(rng, rng.randint(0, 5))
        for k in "abc"
    }
    return shape.format(**fill)


def mutate(rng: random.Random, text: str) -> str:
    if not text:
        return text
    choice = rng.random()
    if choice < 0.25:
        i = rng.randint(0, len(text))
        return text[:i]
    if choice < 0.5:
        i = rng.randint(0, len(text))
        j = rng.randint(i, min(len(text), i + rng.randint(1, 3000)))
        return text[i:j]
    if choice < 0.7:
        lines = text.split("\n")
        i = rng.randint(0, len(lines))
        j = rng.randint(i, len(lines))
        return "\n".join(lines[i:j])
    if choice < 0.85:
        out = text
        for _ in range(rng.randint(1, 8)):
            i = rng.randint(0, len(out))
            out = out[:i] + rng.choice(EDGE_CHARS + KEYWORDS + ["\n"]) + out[i:]
        return out
    lines = text.split("\n")
    rng.shuffle(lines)
    return "\n".join(lines)


def cases(rng: random.Random, count: int, corpus: list[dict]) -> list[dict]:
    bodies = [e["body"] for e in corpus if e["body"]]
    htmls = [e["html_body"] for e in corpus if e["html_body"]]
    stage1 = [e["extract_new_text"]["text"] for e in corpus]
    out: list[dict] = []

    def text_source() -> str:
        r = rng.random()
        if r < 0.45:
            return mutate(rng, rng.choice(bodies))
        if r < 0.75:
            return generated_body(rng)
        return edge_string(rng, rng.randint(0, 40))

    def html_source() -> str:
        r = rng.random()
        if r < 0.4:
            return mutate(rng, rng.choice(htmls))
        return html_fragment(rng)

    pairs = [e for e in corpus if e["body"] and e["html_body"]]

    def light(text: str) -> str:
        return mutate(rng, text) if rng.random() < 0.5 else text

    while len(out) < count:
        r = rng.random()
        if r < 0.15:
            args = {"body": text_source() if rng.random() < 0.9 else None}
            if rng.random() < 0.3:
                args["parent_body"] = text_source()
            if rng.random() < 0.3:
                args["html_body"] = html_source() if rng.random() < 0.9 else None
            out.append({"fn": "extract_new_text", "args": args})
        elif r < 0.20:
            # Plain and HTML parts of the same message (oracle paths).
            e = rng.choice(pairs)
            out.append(
                {
                    "fn": "extract_new_text",
                    "args": {"body": light(e["body"]), "html_body": light(e["html_body"])},
                }
            )
        elif r < 0.25:
            # A top-posted reply above an unmarked copy of its parent.
            parent = light(rng.choice(bodies))
            new = generated_body(rng) if rng.random() < 0.5 else edge_string(rng, 10)
            quoted = parent if rng.random() < 0.5 else mutate(rng, parent)
            args = {"body": new + "\n\n" + quoted, "parent_body": parent}
            if rng.random() < 0.3:
                args["body"] = new + "\n\n" + rng.choice(KEYWORDS) + "\n" + quoted
            out.append({"fn": "extract_new_text", "args": args})
        elif r < 0.27:
            # Word-boundary patterns: keywords glued to word and non-word
            # neighbors (including U+0345, combining marks, No digits, \n).
            line = boundary_line(rng)
            fn = rng.choice(["is_attribution_line", "strip_attribution_lines", "clean_for_scoring"])
            if fn == "is_attribution_line":
                out.append({"fn": fn, "args": {"line": line}})
            elif fn == "strip_attribution_lines":
                extra = "".join(rng.choice(BOUNDARY_TOKENS) for _ in range(rng.randint(1, 8)))
                out.append({"fn": fn, "args": {"lines": [line, extra, "x"]}})
            else:
                args = {"extracted_text": "Body text here.\n\nBest,\nAnn\n" + line + "\n" + line}
                if rng.random() < 0.5:
                    args["html_signature_text"] = line
                out.append({"fn": fn, "args": args})
        elif r < 0.30:
            # The attribution collapse's replacement-template rules.
            esc = "".join(rng.choice(TEMPLATE_TOKENS) for _ in range(rng.randint(1, 4)))
            text = f"Reply.\n\nOn Mon, Jan 1, 2026{rng.choice([' ', chr(10)])}{esc} X{rng.choice(['', chr(10)])} wrote:\n> q\n"
            if rng.random() < 0.3:
                text = "On " + text
            fn = "read_fragments" if rng.random() < 0.5 else "extract_new_text"
            out.append(
                {"fn": fn, "args": {"text": text} if fn == "read_fragments" else {"body": text}}
            )
        elif r < 0.42:
            src = mutate(rng, rng.choice(stage1)) if rng.random() < 0.5 else text_source()
            args = {"extracted_text": src}
            if rng.random() < 0.3:
                args["html_signature_text"] = text_source()
            out.append({"fn": "clean_for_scoring", "args": args})
        elif r < 0.50:
            base = rng.choice(bodies)
            text = mutate(rng, base) if rng.random() < 0.7 else text_source()
            parent = mutate(rng, base) if rng.random() < 0.7 else text_source()
            out.append(
                {
                    "fn": "strip_parent_content",
                    "args": {
                        "text": text,
                        "parent_body": parent,
                        "continuation": rng.random() < 0.5,
                    },
                }
            )
        elif r < 0.62:
            out.append({"fn": "html_to_text", "args": {"html": html_source()}})
        elif r < 0.74:
            out.append({"fn": "split_html_parts", "args": {"html": html_source()}})
        elif r < 0.77:
            out.append(
                {"fn": "tolerant_lines", "args": {"text": edge_string(rng, rng.randint(0, 20))}}
            )
        elif r < 0.79:
            a = edge_string(rng, rng.randint(0, 10))
            out.append(
                {
                    "fn": "texts_equivalent",
                    "args": {"a": a, "b": mutate(rng, a) if rng.random() < 0.5 else a + " \xa0\n"},
                }
            )
        elif r < 0.86:
            out.append({"fn": "read_fragments", "args": {"text": text_source()}})
        elif r < 0.93:
            fn = rng.choice(
                [
                    "is_attribution_line",
                    "is_quote_line",
                    "is_signoff_name_line",
                    "is_mobile_tagline",
                    "is_signature_delimiter",
                ]
            )
            key = "text" if fn == "is_signoff_name_line" else "line"
            out.append({"fn": fn, "args": {key: generated_line(rng).rstrip("\n")}})
        else:
            fn = rng.choice(
                [
                    "custom_clean",
                    "count_unquoted_content_lines",
                    "find_quote_header_block",
                    "find_signoff_boundary",
                    "strip_attribution_lines",
                    "strip_after_original_message_divider",
                ]
            )
            body = generated_body(rng) if rng.random() < 0.6 else mutate(rng, rng.choice(bodies))
            if fn in ("custom_clean",):
                args = {"text": body}
            elif fn == "count_unquoted_content_lines":
                args = {"body": body}
            else:
                args = {"lines": body.split("\n")}
            if fn in (
                "custom_clean",
                "count_unquoted_content_lines",
                "find_quote_header_block",
                "strip_after_original_message_divider",
            ):
                args["localized"] = rng.random() < 0.7
            out.append({"fn": fn, "args": args})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument(
        "--keep", type=pathlib.Path, help="write cases and results to this directory"
    )
    opts = parser.parse_args()

    rng = random.Random(opts.seed)
    corpus = json.loads((ROOT / "conformance" / "corpus.json").read_text(encoding="utf-8"))[
        "entries"
    ]
    all_cases = cases(rng, opts.cases, corpus)
    expected = [reference(c) for c in all_cases]

    payload = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in all_cases)
    proc = subprocess.run(
        ["cargo", "run", "--quiet", "--release", "--example", "difftest"],
        cwd=ROOT / "rust",
        input=payload.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        print("rust driver failed", file=sys.stderr)
        return 1
    got = [json.loads(line) for line in proc.stdout.decode("utf-8").split("\n") if line.strip()]
    if len(got) != len(all_cases):
        print(
            f"rust driver returned {len(got)} results for {len(all_cases)} cases", file=sys.stderr
        )
        return 1

    diffs = [(c, e, g) for c, e, g in zip(all_cases, expected, got) if e != g]
    if opts.keep:
        opts.keep.mkdir(parents=True, exist_ok=True)
        (opts.keep / "cases.jsonl").write_text(payload, encoding="utf-8")
        with (opts.keep / "diffs.jsonl").open("w", encoding="utf-8") as fh:
            for c, e, g in diffs:
                fh.write(json.dumps({"case": c, "python": e, "rust": g}, ensure_ascii=False) + "\n")
    by_fn: dict[str, int] = {}
    for c in all_cases:
        by_fn[c["fn"]] = by_fn.get(c["fn"], 0) + 1
    print(f"{len(all_cases)} cases (seed {opts.seed}): {len(diffs)} differ")
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(by_fn.items())))
    for c, e, g in diffs[:5]:
        print(json.dumps({"case": c, "python": e, "rust": g}, ensure_ascii=False)[:2000])
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
