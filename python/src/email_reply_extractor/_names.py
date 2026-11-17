"""Display names for stage 2: folding, tokens, and lines that name a person.

:func:`~.cleaning.clean_for_scoring` accepts the sender's display name
(``author_name``) and the display names of the people the message answers or
is addressed to (``addressee_names``). This module turns those names into a
:class:`Names` value and answers the questions the sign-off rules
(:mod:`cleaning`) and the greeting rules (:mod:`_greetings`) ask of it. Both
kinds of name go through the same folding and the same parsing.

Folding
-------
:func:`fold` maps a string to the form in which names are compared, in two
steps:

1. ``str.lower`` (the full lowercase mapping: ``İ`` (U+0130) becomes ``i``
   followed by U+0307).
2. Each character is replaced through :data:`_FOLD_TABLE`, in one pass (a
   replacement is not looked up again); a character without an entry is kept.

:data:`_FOLD_TABLE` is a literal table generated once by
``python/tools/gen_fold_table.py``; no Unicode database is consulted at
run time. It holds:

- for every code point ``c`` of Latin-1 Supplement (U+0080–U+00FF), Latin
  Extended-A (U+0100–U+017F), Latin Extended-B (U+0180–U+024F), Latin
  Extended Additional (U+1E00–U+1EFF) and U+2026 (``…``) for which
  ``c.lower() == c``: the compatibility decomposition (NFKD) of ``c`` without
  its combining marks, when that differs from ``c`` (``é`` → ``e``, ``ǆ`` →
  ``dz``, ``ǿ`` → ``ø``, ``…`` → ``...``, U+00A0 → space);
- every combining diacritical mark (U+0300–U+036F) → the empty string;
- the letters without a decomposition: ``ß`` → ``ss``, ``ø`` → ``o``, ``ł``
  → ``l``, ``đ`` → ``d``, ``æ`` → ``ae``, ``œ`` → ``oe``, ``ı`` → ``i``,
  ``ð`` → ``d``, ``þ`` → ``th``.

Characters of other scripts (Greek, Cyrillic, CJK, …) are only lowercased.

Parsing a display name
----------------------
:func:`parse_names` takes one or more display names. Each is reduced in this
order:

1. ``"`` and backquotes become spaces; the edges are stripped of whitespace
   and then of ``'``.
2. A name that contains ``@`` and no space is an address: its local part,
   with ``.``, ``_``, ``+``, ``=`` and ``-`` read as spaces, stands for the
   name. When the local part is a generic mailbox (:data:`_GENERIC_LOCAL_PARTS`)
   and the first label of the domain is alphabetic and at least 6 letters
   long, that label adds tokens ("mail@janedoe.example" → ``janedoe``).
3. The contents of each parenthesis of 1 to 30 characters with no nested
   parenthesis add tokens (a nickname: "Andrew (Andy) Example"); then every
   ``(…)``, ``[…]`` and ``<…>`` group is removed. Any whitespace-separated word
   that still holds ``@`` is replaced by the part before its first ``@``, read
   as in step 2 ("Jane Doe jdoe@example.org" → "Jane Doe jdoe").
4. From the first whitespace-delimited word ``via`` (ASCII letters, any case)
   with whitespace on both sides, the rest of the name is dropped ("Jane Doe
   via Example-List").
5. A name with exactly one comma is read as "Last, First".

The tokens are the folded runs of letters (``[^\\W\\d_]+``) and the folded
runs of letters joined by ``'``, ``’`` or ``-`` ("jean-luc", "o'neill"), of at
least 2 characters, that are not in :data:`_STOP_WORDS`; each token in
:data:`_NICKNAMES` adds its short forms ("robert" adds "bob", "rob", …). The
words (the letter runs, in order, without stop words) of a single name give its
initials: when there are at least two, the first letters of all words, of all
words in reverse order, and of the first and last word in both orders, each of
at least 2 letters, and the first-and-last pair on its own (``first_last``).
A name that yields no token gives ``None``: a name written only in a script
without letters ``\\w`` recognizes, or only of stop words, provides no
evidence.

Name lines
----------
:func:`is_author_name_line` decides whether a line is the sender's own name;
its docstring gives the rule. A word matches the sender
(:func:`_name_word_status`) when, folded, with ``.`` stripped from its edges
and ``.``, ``'``, ``’`` removed inside, it equals a token or an initials
string, is a prefix or a suffix of a token and at least 3 letters long, or is
3 letters whose first and last letter are ``first_last`` ("RjS"). A
hyphenated word matches when every part does. A single letter and a name
particle (:data:`_NAME_PARTICLES`) that is one of the tokens are allowed but
do not count as a match.

Prefix and suffix lookups use sorted token lists and binary search, so every
function here is linear in the length of its input.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass, field

# --- folding --------------------------------------------------------------------

# BEGIN GENERATED FOLD TABLE (python/tools/gen_fold_table.py)
# fmt: off
_FOLD_TABLE = {
    "\u00a0": " ", "\u00a8": " ", "\u00aa": "a", "\u00af": " ", "\u00b2": "2", "\u00b3": "3",
    "\u00b4": " ", "\u00b5": "\u03bc", "\u00b8": " ", "\u00b9": "1", "\u00ba": "o",
    "\u00bc": "1\u20444", "\u00bd": "1\u20442", "\u00be": "3\u20444", "\u00df": "ss",
    "\u00e0": "a", "\u00e1": "a", "\u00e2": "a", "\u00e3": "a", "\u00e4": "a", "\u00e5": "a",
    "\u00e6": "ae", "\u00e7": "c", "\u00e8": "e", "\u00e9": "e", "\u00ea": "e", "\u00eb": "e",
    "\u00ec": "i", "\u00ed": "i", "\u00ee": "i", "\u00ef": "i", "\u00f0": "d", "\u00f1": "n",
    "\u00f2": "o", "\u00f3": "o", "\u00f4": "o", "\u00f5": "o", "\u00f6": "o", "\u00f8": "o",
    "\u00f9": "u", "\u00fa": "u", "\u00fb": "u", "\u00fc": "u", "\u00fd": "y", "\u00fe": "th",
    "\u00ff": "y", "\u0101": "a", "\u0103": "a", "\u0105": "a", "\u0107": "c", "\u0109": "c",
    "\u010b": "c", "\u010d": "c", "\u010f": "d", "\u0111": "d", "\u0113": "e", "\u0115": "e",
    "\u0117": "e", "\u0119": "e", "\u011b": "e", "\u011d": "g", "\u011f": "g", "\u0121": "g",
    "\u0123": "g", "\u0125": "h", "\u0129": "i", "\u012b": "i", "\u012d": "i", "\u012f": "i",
    "\u0131": "i", "\u0133": "ij", "\u0135": "j", "\u0137": "k", "\u013a": "l", "\u013c": "l",
    "\u013e": "l", "\u0140": "l\u00b7", "\u0142": "l", "\u0144": "n", "\u0146": "n", "\u0148": "n",
    "\u0149": "\u02bcn", "\u014d": "o", "\u014f": "o", "\u0151": "o", "\u0153": "oe",
    "\u0155": "r", "\u0157": "r", "\u0159": "r", "\u015b": "s", "\u015d": "s", "\u015f": "s",
    "\u0161": "s", "\u0163": "t", "\u0165": "t", "\u0169": "u", "\u016b": "u", "\u016d": "u",
    "\u016f": "u", "\u0171": "u", "\u0173": "u", "\u0175": "w", "\u0177": "y", "\u017a": "z",
    "\u017c": "z", "\u017e": "z", "\u017f": "s", "\u01a1": "o", "\u01b0": "u", "\u01c6": "dz",
    "\u01c9": "lj", "\u01cc": "nj", "\u01ce": "a", "\u01d0": "i", "\u01d2": "o", "\u01d4": "u",
    "\u01d6": "u", "\u01d8": "u", "\u01da": "u", "\u01dc": "u", "\u01df": "a", "\u01e1": "a",
    "\u01e3": "\u00e6", "\u01e7": "g", "\u01e9": "k", "\u01eb": "o", "\u01ed": "o",
    "\u01ef": "\u0292", "\u01f0": "j", "\u01f3": "dz", "\u01f5": "g", "\u01f9": "n", "\u01fb": "a",
    "\u01fd": "\u00e6", "\u01ff": "\u00f8", "\u0201": "a", "\u0203": "a", "\u0205": "e",
    "\u0207": "e", "\u0209": "i", "\u020b": "i", "\u020d": "o", "\u020f": "o", "\u0211": "r",
    "\u0213": "r", "\u0215": "u", "\u0217": "u", "\u0219": "s", "\u021b": "t", "\u021f": "h",
    "\u0227": "a", "\u0229": "e", "\u022b": "o", "\u022d": "o", "\u022f": "o", "\u0231": "o",
    "\u0233": "y", "\u0300": "", "\u0301": "", "\u0302": "", "\u0303": "", "\u0304": "",
    "\u0305": "", "\u0306": "", "\u0307": "", "\u0308": "", "\u0309": "", "\u030a": "",
    "\u030b": "", "\u030c": "", "\u030d": "", "\u030e": "", "\u030f": "", "\u0310": "",
    "\u0311": "", "\u0312": "", "\u0313": "", "\u0314": "", "\u0315": "", "\u0316": "",
    "\u0317": "", "\u0318": "", "\u0319": "", "\u031a": "", "\u031b": "", "\u031c": "",
    "\u031d": "", "\u031e": "", "\u031f": "", "\u0320": "", "\u0321": "", "\u0322": "",
    "\u0323": "", "\u0324": "", "\u0325": "", "\u0326": "", "\u0327": "", "\u0328": "",
    "\u0329": "", "\u032a": "", "\u032b": "", "\u032c": "", "\u032d": "", "\u032e": "",
    "\u032f": "", "\u0330": "", "\u0331": "", "\u0332": "", "\u0333": "", "\u0334": "",
    "\u0335": "", "\u0336": "", "\u0337": "", "\u0338": "", "\u0339": "", "\u033a": "",
    "\u033b": "", "\u033c": "", "\u033d": "", "\u033e": "", "\u033f": "", "\u0340": "",
    "\u0341": "", "\u0342": "", "\u0343": "", "\u0344": "", "\u0345": "", "\u0346": "",
    "\u0347": "", "\u0348": "", "\u0349": "", "\u034a": "", "\u034b": "", "\u034c": "",
    "\u034d": "", "\u034e": "", "\u034f": "", "\u0350": "", "\u0351": "", "\u0352": "",
    "\u0353": "", "\u0354": "", "\u0355": "", "\u0356": "", "\u0357": "", "\u0358": "",
    "\u0359": "", "\u035a": "", "\u035b": "", "\u035c": "", "\u035d": "", "\u035e": "",
    "\u035f": "", "\u0360": "", "\u0361": "", "\u0362": "", "\u0363": "", "\u0364": "",
    "\u0365": "", "\u0366": "", "\u0367": "", "\u0368": "", "\u0369": "", "\u036a": "",
    "\u036b": "", "\u036c": "", "\u036d": "", "\u036e": "", "\u036f": "", "\u1e01": "a",
    "\u1e03": "b", "\u1e05": "b", "\u1e07": "b", "\u1e09": "c", "\u1e0b": "d", "\u1e0d": "d",
    "\u1e0f": "d", "\u1e11": "d", "\u1e13": "d", "\u1e15": "e", "\u1e17": "e", "\u1e19": "e",
    "\u1e1b": "e", "\u1e1d": "e", "\u1e1f": "f", "\u1e21": "g", "\u1e23": "h", "\u1e25": "h",
    "\u1e27": "h", "\u1e29": "h", "\u1e2b": "h", "\u1e2d": "i", "\u1e2f": "i", "\u1e31": "k",
    "\u1e33": "k", "\u1e35": "k", "\u1e37": "l", "\u1e39": "l", "\u1e3b": "l", "\u1e3d": "l",
    "\u1e3f": "m", "\u1e41": "m", "\u1e43": "m", "\u1e45": "n", "\u1e47": "n", "\u1e49": "n",
    "\u1e4b": "n", "\u1e4d": "o", "\u1e4f": "o", "\u1e51": "o", "\u1e53": "o", "\u1e55": "p",
    "\u1e57": "p", "\u1e59": "r", "\u1e5b": "r", "\u1e5d": "r", "\u1e5f": "r", "\u1e61": "s",
    "\u1e63": "s", "\u1e65": "s", "\u1e67": "s", "\u1e69": "s", "\u1e6b": "t", "\u1e6d": "t",
    "\u1e6f": "t", "\u1e71": "t", "\u1e73": "u", "\u1e75": "u", "\u1e77": "u", "\u1e79": "u",
    "\u1e7b": "u", "\u1e7d": "v", "\u1e7f": "v", "\u1e81": "w", "\u1e83": "w", "\u1e85": "w",
    "\u1e87": "w", "\u1e89": "w", "\u1e8b": "x", "\u1e8d": "x", "\u1e8f": "y", "\u1e91": "z",
    "\u1e93": "z", "\u1e95": "z", "\u1e96": "h", "\u1e97": "t", "\u1e98": "w", "\u1e99": "y",
    "\u1e9a": "a\u02be", "\u1e9b": "s", "\u1ea1": "a", "\u1ea3": "a", "\u1ea5": "a", "\u1ea7": "a",
    "\u1ea9": "a", "\u1eab": "a", "\u1ead": "a", "\u1eaf": "a", "\u1eb1": "a", "\u1eb3": "a",
    "\u1eb5": "a", "\u1eb7": "a", "\u1eb9": "e", "\u1ebb": "e", "\u1ebd": "e", "\u1ebf": "e",
    "\u1ec1": "e", "\u1ec3": "e", "\u1ec5": "e", "\u1ec7": "e", "\u1ec9": "i", "\u1ecb": "i",
    "\u1ecd": "o", "\u1ecf": "o", "\u1ed1": "o", "\u1ed3": "o", "\u1ed5": "o", "\u1ed7": "o",
    "\u1ed9": "o", "\u1edb": "o", "\u1edd": "o", "\u1edf": "o", "\u1ee1": "o", "\u1ee3": "o",
    "\u1ee5": "u", "\u1ee7": "u", "\u1ee9": "u", "\u1eeb": "u", "\u1eed": "u", "\u1eef": "u",
    "\u1ef1": "u", "\u1ef3": "y", "\u1ef5": "y", "\u1ef7": "y", "\u1ef9": "y", "\u2026": "...",
}
# fmt: on
# END GENERATED FOLD TABLE

_FOLD = str.maketrans(_FOLD_TABLE)


def fold(text: str) -> str:
    """Lowercase ``text`` and map every character through :data:`_FOLD_TABLE`."""
    return text.lower().translate(_FOLD)


# --- parsing --------------------------------------------------------------------

#: Words that are never name tokens: titles, generation suffixes, connectors,
#: and the words of list and role display names ("Example-List on behalf of",
#: "IETF Secretariat").
_STOP_WORDS = frozenset(
    "dr prof mr mrs ms mx sir dipl ing phd jr sr ii iii iv the of and via team"
    " on behalf for list mailing ietf iesg iab irtf secretariat".split()
)

#: Local parts of an address that name a mailbox, not a person.
_GENERIC_LOCAL_PARTS = frozenset("mail email me info contact hello ietf post office".split())

#: Common English short forms of given names; a sign-off or a greeting often
#: uses one ("Sue" for Susan).
_NICKNAMES = {
    "robert": ("bob", "rob", "bobby", "robby"),
    "thomas": ("tom", "tommy"),
    "susan": ("sue", "susie"),
    "william": ("bill", "will", "billy"),
    "richard": ("dick", "rich", "rick"),
    "james": ("jim", "jimmy", "jamie"),
    "michael": ("mike", "mick"),
    "andrew": ("andy", "drew"),
    "edward": ("ed", "ted", "eddie"),
    "elizabeth": ("liz", "beth", "betsy"),
    "margaret": ("peggy", "maggie", "meg"),
    "charles": ("chuck", "charlie"),
    "joseph": ("joe",),
    "jonathan": ("jon",),
    "anthony": ("tony",),
    "david": ("dave",),
    "stephen": ("steve",),
    "steven": ("steve",),
    "matthew": ("matt",),
    "benjamin": ("ben",),
    "nicholas": ("nick",),
    "patrick": ("pat",),
    "peter": ("pete",),
    "samuel": ("sam",),
    "alexander": ("alex",),
    "kenneth": ("ken",),
    "donald": ("don",),
    "ronald": ("ron",),
    "lawrence": ("larry",),
    "gregory": ("greg",),
    "timothy": ("tim",),
    "jeffrey": ("jeff",),
    "frederick": ("fred",),
    "theodore": ("ted",),
    "katherine": ("kate", "kathy"),
    "catherine": ("kate", "cathy"),
    "deborah": ("deb",),
    "rebecca": ("becky",),
    "jennifer": ("jen", "jenny"),
    "victoria": ("vicky",),
    "christopher": ("chris",),
    "christine": ("chris",),
    "daniel": ("dan", "danny"),
    "philip": ("phil",),
    "phillip": ("phil",),
    "raymond": ("ray",),
    "gerald": ("gerry",),
    "leonard": ("len",),
}

_LETTER_RUN_RE = re.compile(r"[^\W\d_]+")
_JOINED_RUN_RE = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
_ADDRESS_PUNCT_RE = re.compile(r"[._+=-]")
_NICKNAME_GROUP_RE = re.compile(r"\(([^()]{1,30})\)")
_BRACKETED_RE = re.compile(r"\([^()]*\)|\[[^\[\]]*\]|<[^<>]*>")
_WORD_SPAN_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class Names:
    """The tokens of one or more display names (see the module docstring).

    ``initials`` and ``first_last`` are set only for a single name.
    """

    tokens: frozenset[str]
    initials: frozenset[str] = frozenset()
    first_last: str = ""
    _sorted: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _sorted_reversed: tuple[str, ...] = field(default=(), repr=False, compare=False)

    def has_prefix(self, text: str) -> bool:
        """True when ``text`` is a prefix of some token."""
        pos = bisect_left(self._sorted, text)
        return pos < len(self._sorted) and self._sorted[pos].startswith(text)

    def has_suffix(self, text: str) -> bool:
        """True when ``text`` is a suffix of some token."""
        rev = text[::-1]
        pos = bisect_left(self._sorted_reversed, rev)
        return pos < len(self._sorted_reversed) and self._sorted_reversed[pos].startswith(rev)


def _drop_via(text: str) -> str:
    """``text`` up to the first word ``via`` with whitespace on both sides."""
    for word in _WORD_SPAN_RE.finditer(text):
        if word.start() > 0 and word.end() < len(text) and word.group().lower() == "via":
            return text[: word.start()].rstrip()
    return text


def _reduce(name: str) -> tuple[str, list[str]]:
    """Steps 1-5 of the module docstring: the name text and the extra texts."""
    text = name.replace('"', " ").replace("`", " ").strip().strip("'")
    extra: list[str] = []
    if "@" in text and " " not in text:
        local, _, domain = text.partition("@")
        label = domain.split(".")[0]
        if fold(local) in _GENERIC_LOCAL_PARTS and label.isalpha() and len(label) >= 6:
            extra.append(label)
        text = _ADDRESS_PUNCT_RE.sub(" ", local)
    extra += _NICKNAME_GROUP_RE.findall(text)
    text = _BRACKETED_RE.sub(" ", text)
    if "@" in text:
        text = " ".join(
            _ADDRESS_PUNCT_RE.sub(" ", word.partition("@")[0]) if "@" in word else word
            for word in text.split()
        )
    text = _drop_via(text)
    if text.count(",") == 1:
        last, first = text.split(",")
        text = f"{first} {last}"
    return text, extra


def _tokens(texts: Iterable[str]) -> set[str]:
    tokens: set[str] = set()
    for text in texts:
        folded = fold(text)
        for run in (*_LETTER_RUN_RE.findall(folded), *_JOINED_RUN_RE.findall(folded)):
            if len(run) >= 2 and run not in _STOP_WORDS:
                tokens.add(run)
    for token in list(tokens):
        tokens.update(_NICKNAMES.get(token, ()))
    return tokens


def name_tokens(name: str) -> frozenset[str]:
    """The tokens of one display name."""
    text, extra = _reduce(name)
    return frozenset(_tokens([text, *extra]))


def _make(tokens: Iterable[str], initials: frozenset[str] = frozenset(), first_last: str = ""):
    token_set = frozenset(tokens)
    return Names(
        token_set,
        initials,
        first_last,
        tuple(sorted(token_set)),
        tuple(sorted(token[::-1] for token in token_set)),
    )


def parse_author_name(name: str | None) -> Names | None:
    """The sender's :class:`Names`, with initials, or ``None`` without tokens."""
    if not name:
        return None
    text, extra = _reduce(name)
    tokens = _tokens([text, *extra])
    if not tokens:
        return None
    words = [w for w in _LETTER_RUN_RE.findall(fold(text)) if w not in _STOP_WORDS]
    initials: set[str] = set()
    first_last = ""
    if len(words) >= 2:
        for seq in (words, words[::-1]):
            initials.add("".join(w[0] for w in seq))
            initials.add(seq[0][0] + seq[-1][0])
        first_last = words[0][0] + words[-1][0]
    return _make(tokens, frozenset(i for i in initials if len(i) >= 2), first_last)


def parse_addressee_names(names: Iterable[str], author_name: str | None = None) -> Names | None:
    """The addressees' :class:`Names`, or ``None`` without tokens.

    A name whose tokens equal the sender's own (``author_name``) is skipped: a
    reply-all that lists the sender among the recipients gives no evidence of
    whom the greeting addresses.
    """
    own = name_tokens(author_name) if author_name else frozenset()
    tokens: set[str] = set()
    for name in names:
        theirs = name_tokens(name)
        if not own or theirs != own:
            tokens |= theirs
    return _make(tokens) if tokens else None


# --- name lines -------------------------------------------------------------------

#: Name particles: allowed in a name line when they are tokens of the name,
#: never the word that matches it.
_NAME_PARTICLES = frozenset("de da di del della der den van von la le du dos das bin al el".split())

#: Characters that decorate a sign-off name ("-Jane", "— Jane", "~jane", "/J").
NAME_DECORATION = "-–—~/*_ \t"

#: A role or proxy tail after a name ("Jane on behalf of the chairs", "Jane
#: (as chair)" is handled separately, "Jane // as an individual", "Jane, for
#: the authors"). The ``(?<![ \t])`` lookbehinds keep a search from restarting
#: inside a blank run, which is quadratic; they do not change the match.
_NAME_LINE_TAIL_RE = re.compile(
    r"(?:(?<![ \t])[ \t]+(?:on[ \t]+behalf[ \t]+of|for[ \t]+the)\b.{0,60}"
    r"|(?<![ \t])[ \t]*(?://|,)[ \t]*(?:as|for|on|speaking|with|wearing|no|contributor|individual)\b.{0,40})$",
    re.IGNORECASE,
)
#: One word of a name line after folding.
_FOLDED_NAME_WORD_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[.'’\-])*$")
#: A capitalized word that is not the sender's (a co-signer, a middle name).
LOOSE_NAME_TOKEN_RE = re.compile(r"^[^\W\d_][\w'’\-]{0,24}$")
#: Words that join co-signers in a group sign-off ("Jane and the co-authors").
_GROUP_SIGNOFF_WORDS = frozenset(
    "and & co-authors coauthors authors chairs co-chairs editors team".split()
)


def _name_word_status(word: str, author: Names) -> int:
    """2: ``word`` matches the sender; 1: an initial or particle; 0: neither."""
    folded = fold(word).strip(".")
    if not folded or not _FOLDED_NAME_WORD_RE.match(folded):
        return 0
    if "-" in folded:
        parts = [part for part in folded.split("-") if part]
        return 2 if parts and all(_name_word_status(p, author) == 2 for p in parts) else 0
    core = folded.replace(".", "").replace("'", "").replace("’", "")
    if not core:
        return 0
    if core in _NAME_PARTICLES:
        return 1 if core in author.tokens else 0
    if core in author.tokens or core in author.initials:
        return 2
    if len(core) >= 3 and (author.has_prefix(core) or author.has_suffix(core)):
        return 2
    if len(core) == 3 and author.first_last and core[0] + core[2] == author.first_last:
        return 2
    if len(core) == 1:
        return 1
    return 0


def matches_author(word: str, author: Names) -> bool:
    """True when ``word`` matches the sender's name (status 2 above)."""
    return _name_word_status(word, author) == 2


def matches_addressee(word: str, addressees: Names | None) -> bool:
    """True when a folded letter run of ``word`` is an addressee token."""
    if addressees is None:
        return False
    return any(run in addressees.tokens for run in _LETTER_RUN_RE.findall(fold(word)))


def is_author_name_line(line: str, author: Names | None) -> bool:
    """True when ``line`` is the sender's own name.

    The line is stripped; a leading parenthesis of at most 30 characters
    ("(personal) Jane") is dropped, then leading :data:`NAME_DECORATION`, a
    role tail (:data:`_NAME_LINE_TAIL_RE`), trailing `` .,;:!`` and a trailing
    parenthesis that starts after the first character and is at most 40
    characters long ("Jane (as chair)"). What remains must be 1 to 40
    characters and 1 to 4 words. Each word matches the sender, is an initial
    or particle, is a group word (:data:`_GROUP_SIGNOFF_WORDS`, only when the
    line has an "and" or "&"), or is a capitalized word
    (:data:`LOOSE_NAME_TOKEN_RE`); at least one word matches the sender, and
    at most one capitalized word is unmatched, and only in a group line or
    beside two matching words ("Jane Q. Middle Doe").
    """
    if author is None:
        return False
    s = line.strip()
    if s.startswith("(") and 0 < s.find(")") <= 30:
        s = s[s.find(")") + 1 :]
    s = s.lstrip(NAME_DECORATION)
    s = _NAME_LINE_TAIL_RE.sub("", s)
    s = s.rstrip(" \t.,;:!")
    open_paren = s.rfind("(")
    if s.endswith(")") and open_paren > 0 and len(s) - open_paren <= 40:
        s = s[:open_paren].rstrip(" \t.,;:!")
    if not s or len(s) > 40:
        return False
    words = s.split()
    if len(words) > 4:
        return False
    group = any(w.lower() in ("and", "&") for w in words)
    statuses = []
    for word in words:
        if group and word.lower() in _GROUP_SIGNOFF_WORDS:
            statuses.append(1)
            continue
        status = _name_word_status(word, author)
        if status == 0 and not (word[:1].isupper() and LOOSE_NAME_TOKEN_RE.match(word)):
            return False
        statuses.append(status)
    unknown = statuses.count(0)
    matched = statuses.count(2)
    if not matched:
        return False
    return unknown == 0 or (unknown == 1 and (group or matched >= 2))


def is_author_initial_line(line: str, author: Names | None) -> bool:
    """A decorated one-letter sign-off ("/J", "-J.") that is the sender's first initial."""
    if author is None:
        return False
    s = line.strip()
    core = s.lstrip(NAME_DECORATION).rstrip(" .")
    if len(core) != 1 or not core.isalpha() or core == s:
        return False
    folded = fold(core)
    return any(initials[0] == folded for initials in author.initials)
