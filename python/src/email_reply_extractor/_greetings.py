"""Opening greetings for stage 2: the word lists and the per-line decision.

:func:`is_opening_greeting` decides whether the first non-blank line of a text
is a greeting; :mod:`cleaning` calls it at most twice per pass and at most
twice per text (its module docstring gives the order). The decision reads one
line, of at most :data:`_MAX_GREETING_LEN` characters after stripping, and the
line below it, so its cost is bounded per call.

The rules, tried in this order on the stripped line:

- **Guard.** An empty line, a line longer than :data:`_MAX_GREETING_LEN`, and
  a line that starts with one of ``+ > | * # [ (`` are not greetings ("++
  Jane and Joe." adds people to a thread).
- **Terminator.** A trailing run of ``-``, ``–``, ``—`` is the terminator
  ``-``; otherwise a trailing ``,``, ``:``, ``!`` or ``.`` is; otherwise there
  is none. The body is the line without it.
- **Opening word.** The body may open with a greeting word
  (:data:`GREETING_WORDS` or :data:`_EXTRA_GREETING_WORDS`, not followed by a
  word character, then optional blanks and one optional ``,``) or, failing
  that, with a thanks phrase (:data:`_THANKS_PREFIX_RE`). The rest of the body
  is split into words at ``,``, ``/``, ``&``, ``+``, ``;``, " and ", " or "
  and whitespace, and each word is classified (:func:`_classify`).
- **Base rule.** A line :data:`GREETING_RE` matches is a greeting unless the
  rest holds a sentence word (:data:`_SENTENCE_WORDS`, or a contraction such
  as "I'm", "don't"); then it is the opening of a sentence ("Hi Jane,
  apologies for the delay.") and the whole line is kept, whatever the rules
  below would say.
- **Addressee names** (only with ``addressee_names``). One to six words, every
  one a name, name prefix, title, initial or group word, at least one a name
  or name prefix: a greeting ("Jane, Joe:" / "Dr. Doe,"). Not when the body
  opens with a thanks phrase and the terminator is not ``,`` or ``:`` ("Thanks
  Jane!" is thanks), and not when there is no opening word, the terminator is
  ``.``, ``!`` or none, and a word is only a name prefix.
- **Greeting word.** An opening greeting word with nothing after it; or with
  one to six words, all name-like (any class but none) and not all acronyms.
- **Group addressees.** No opening word, terminator ``,``, ``:``, ``-`` or
  ``!``, one to six words, all group words, acronyms or addresses, at least
  one a group word or address, and not a single generic group word ("group",
  "members", "people", "community") on its own ("WG chairs,", "All:").
- **Structural.** No opening word, terminator ``,``, ``:`` or ``-``, one to
  four words. With ``:`` or ``-`` there must be at least two items between
  the separators ("Inline:" and "Proposal -" are labels). "V/R" (one letter,
  ``/``, one letter) is a sign-off. Every word is capitalized
  (:func:`_is_cap_word`), none is 7 or more characters ending in "ly", none
  is in :data:`_HEADING_STOP_WORDS`, and the next line is blank, absent, or
  starts with an uppercase letter ("Jane, Joe," above "Please see…").

Word classes (:func:`_classify`), on the folded word (:func:`~._names.fold`)
with trailing dots removed, first match wins: title (:data:`_TITLES`), name (a
token of the addressees), name prefix (an alphabetic word of at least 3
letters that starts some token), initial (one letter), group word
(:data:`_GROUP_WORDS`), filler (:data:`_FILLER_WORDS`), address, acronym (2 to
12 characters, alphanumeric, unchanged by ``str.upper``, with a letter),
capitalized word, none.
"""

from __future__ import annotations

import re

from ._names import Names, fold

# --- word lists ---------------------------------------------------------------------

#: Greeting words, grouped by language; the base list.
GREETING_WORDS = (
    # English
    r"hi|hello|hey|dear|greetings|good[ \t]+(?:morning|afternoon|evening|day)"
    # German
    r"|hallo|guten[ \t]+(?:tag|morgen|abend)|liebe[rs]?|sehr[ \t]+geehrte[rs]?"
    # French / Spanish / Italian / Portuguese
    r"|bonjour|salut|ch(?:è|e)re?s?|hola|estimad[oa]s?|buenos[ \t]+d(?:í|i)as|ciao|ol(?:á|a)"
    # Dutch / Nordic
    r"|beste|geachte|hej|hoi"
    # Māori (common in NZ English mail)
    r"|kia[ \t]+ora|t(?:ē|e)n(?:ā|a)[ \t]+koe"
)
#: Further opening words, accepted only by the rules after the base rule.
_EXTRA_GREETING_WORDS = (
    r"hiya|howdy|moin|re-|good[ \t]+(?:morning|afternoon|evening|day)[ \t]+(?:all|everyone|folks)"
)

#: The base greeting line: a greeting word, then either a short run of names
#: ending in "," / "!" / "." or nothing else. The mandatory trailing
#: punctuation on the name-run form keeps prose like "Hey that reminds me of X"
#: intact. The bare form's tail is ``(?:[ \t]*[,!.])?[ \t]*$``, which accepts
#: the same strings as ``[ \t]*[,!.]?[ \t]*$``: written that way, two adjacent
#: ``[ \t]*`` runs split a long blank run in quadratically many ways before a
#: non-blank character fails the line.
GREETING_RE = re.compile(
    rf"^[ \t]*(?:{GREETING_WORDS})\b(?:[^.!?;:]{{0,60}}[,!.]|(?:[ \t]*[,!.])?)[ \t]*$",
    re.IGNORECASE,
)

_MAX_GREETING_LEN = 120

_GREETING_PREFIX_RE = re.compile(
    rf"^(?:{GREETING_WORDS}|{_EXTRA_GREETING_WORDS})(?![\w])[ \t]*,?[ \t]*", re.IGNORECASE
)
_THANKS_PREFIX_RE = re.compile(
    r"^(?:many[ \t]+thanks|thanks[ \t]+(?:a[ \t]+lot|again)|thanks|thank[ \t]+you)(?![\w])[ \t]*,?[ \t]*",
    re.IGNORECASE,
)
_ITEM_SPLIT_RE = re.compile(r"[ \t]*(?:,|/|&|\+|;)[ \t]*|[ \t]+(?:and|or)[ \t]+", re.IGNORECASE)
_ADDRESS_RE = re.compile(r"^<?[\w.+=-]+@[\w-]+(?:\.[\w-]+)+>?$")
_SIGNOFF_ABBREVIATION_RE = re.compile(r"[A-Za-z]/[A-Za-z]")
_CONTRACTION_RE = re.compile(r"(?:n['’]t|['’](?:s|m|re|ve|ll|d))$")


def _folded_set(words: str) -> frozenset[str]:
    return frozenset(fold(word) for word in words.split())


_TITLES = _folded_set("dr dr. prof prof. professor mr mr. ms ms. mrs mrs. mx sir madam doctor")
#: Group addressees ("all", "chairs", "WG").
_GROUP_WORDS = _folded_set(
    "all everyone everybody folks colleagues team chairs chair cochairs co-chairs authors author"
    " author(s) co-authors coauthors contributors wg wgs rg working group members iana iesg iab"
    " irsg ads reviewers editors experts shepherd community people friends gentlemen ladies"
)
#: Words that are name-like only after a greeting word ("Hi there,").
_FILLER_WORDS = _folded_set("there again both guys y'all you")
#: First words that make a short capitalized line a discourse marker or a
#: heading rather than an addressee ("FYI,", "Inline:", "Actually,").
_HEADING_STOP_WORDS = _folded_set(
    "thanks thank thx regards best cheers sincerely hi hello dear yes no ok okay agreed great"
    " note notes update hmm well so also fyi inline re regarding sorry first second third finally"
    " like minor major correction corrections idea ideas off per management done ack template"
    " specifically question questions addition additionally perhaps maybe sure excellent old new"
    " right actually errata reminder see target ps p.s btw imo psa pr tl proposed proposal thoughts"
    " indeed personally instead apparently exactly correct interesting noted approved agree just"
    " considering summary comment comments issue issues review evaluation subject replace below"
    " above attention however anyway meanwhile otherwise similarly likewise basically honestly"
    " frankly oh ah wow cool fine nice good he she they it this that we i you suggestion"
    " suggestions aside current text section background example examples answer response reply"
    " nit nits typo support strawman disclaimer ref almost to from as if fwiw iirc afaik afaict"
    " imho brilliant fantastic awesome wonderful perfect splendid absolutely definitely hooray"
    " congratulations congrats welcome bingo bravo oops whoops ouch yay yikes wfm lgtm nack ptal"
    " iiuc afair br ta tx tks tnx rgds kr viele grüße gruesse gruß mfg vg lg salutations"
)
#: Words that turn a greeting line into a sentence (compared lowercased,
#: with ``.,!?:;()*`` stripped from the edges).
_SENTENCE_WORDS = frozenset(
    "i we my our this that these those it is are was were be have has had do does did can could"
    " would should thanks thank apologies sorry please nice good great just see let here glad"
    " welcome for some feedback an with about as if not no yes".split()
)
_GENERIC_GROUP_WORDS = ("group", "members", "people", "community")

_NAME_KINDS = frozenset({"name", "prefix", "title", "init", "group"})
_NAME_LIKE_KINDS = frozenset(
    {"name", "prefix", "title", "init", "group", "filler", "email", "acro", "cap"}
)


# --- classification ---------------------------------------------------------------


def _is_sentence(words: list[str]) -> bool:
    for word in words:
        folded = word.lower().strip(".,!?:;()*")
        if folded in _SENTENCE_WORDS:
            return True
        if _CONTRACTION_RE.search(folded) and folded not in _FILLER_WORDS:
            return True
    return False


def _split_terminator(s: str) -> tuple[str, str]:
    s = s.rstrip()
    if s.endswith(("--", "-", "—", "–")):
        return s.rstrip("-—–").rstrip(), "-"
    if s and s[-1] in ",:!.":
        return s[:-1].rstrip(), s[-1]
    return s, ""


def _is_cap_word(word: str) -> bool:
    if not word or not word[0].isupper() or word == "I":
        return False
    return all(ch.isalpha() or ch in ".-'’" for ch in word)


def _classify(words: list[str], addressees: Names | None) -> list[str | None]:
    kinds: list[str | None] = []
    for word in words:
        folded = fold(word).rstrip(".")
        if folded in _TITLES or folded + "." in _TITLES:
            kinds.append("title")
        elif addressees is not None and folded in addressees.tokens:
            kinds.append("name")
        elif (
            addressees is not None
            and len(folded) >= 3
            and folded.isalpha()
            and addressees.has_prefix(folded)
        ):
            kinds.append("prefix")
        elif len(folded) == 1 and folded.isalpha():
            kinds.append("init")
        elif folded in _GROUP_WORDS:
            kinds.append("group")
        elif folded in _FILLER_WORDS:
            kinds.append("filler")
        elif _ADDRESS_RE.match(word):
            kinds.append("email")
        elif (
            2 <= len(word) <= 12
            and word.isalnum()
            and word.upper() == word
            and any(ch.isalpha() for ch in word)
        ):
            kinds.append("acro")
        elif _is_cap_word(word):
            kinds.append("cap")
        else:
            kinds.append(None)
    return kinds


def is_opening_greeting(line: str, next_line: str | None, addressees: Names | None) -> bool:
    """True when ``line``, the first non-blank line, is a greeting (module docstring)."""
    s = line.strip()
    if not s or len(s) > _MAX_GREETING_LEN or s[0] in "+>|*#[(":
        return False
    body, terminator = _split_terminator(s)
    greeting = _GREETING_PREFIX_RE.match(body)
    thanks = None if greeting else _THANKS_PREFIX_RE.match(body)
    opening = greeting or thanks
    rest = body[opening.end() :] if opening else body
    words = [w for item in _ITEM_SPLIT_RE.split(rest) for w in item.split() if w]
    kinds = _classify(words, addressees)
    n = len(words)

    if GREETING_RE.match(line):
        return not _is_sentence(rest.split())

    if addressees is not None and 0 < n <= 6 and all(k in _NAME_KINDS for k in kinds):
        if any(k in ("name", "prefix") for k in kinds):
            thanks_only = thanks is not None and terminator not in (",", ":")
            bare_prefix = not opening and terminator in (".", "!", "") and "prefix" in kinds
            if not thanks_only and not bare_prefix:
                return True

    if greeting is not None:
        if n == 0:
            return True
        if n <= 6 and all(k in _NAME_LIKE_KINDS for k in kinds) and any(k != "acro" for k in kinds):
            return True
    if (
        not opening
        and terminator in (",", ":", "-", "!")
        and 0 < n <= 6
        and all(k in ("group", "acro", "email") for k in kinds)
        and any(k in ("group", "email") for k in kinds)
    ):
        lone_generic_group = (
            kinds.count("group") == 1
            and "acro" not in kinds
            and fold(words[kinds.index("group")]) in _GENERIC_GROUP_WORDS
        )
        if not lone_generic_group:
            return True

    if not opening and terminator in (",", ":", "-") and 0 < n <= 4:
        items = [item for item in _ITEM_SPLIT_RE.split(rest) if item.strip()]
        if terminator != "," and len(items) < 2:
            return False
        if _SIGNOFF_ABBREVIATION_RE.fullmatch(rest.strip()):
            return False
        if all(
            _is_cap_word(w) and not (len(w) >= 7 and w.lower().endswith("ly")) for w in words
        ) and not any(fold(w).rstrip(".") in _HEADING_STOP_WORDS for w in words):
            return next_line is None or not next_line.strip() or next_line.lstrip()[:1].isupper()
    return False
