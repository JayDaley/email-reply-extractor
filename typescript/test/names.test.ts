// Stage-2 names and greetings (generation 10): src/names.ts and src/greetings.ts.
//
// Nothing calls these modules before clean_for_scoring is ported, and no
// conformance vectors exercise them directly, so these tests restate, through
// the helpers, the cases of python/tests/test_port_hazards.py (name folding
// through str.lower and the literal table, Unicode letter tokens, the
// str.upper acronym test) and the name and greeting cases of
// python/tests/test_cleaning.py. Expected values were checked against the
// Python helpers. research/work/port-typescript/names_reference.py and
// names_replay.mjs compare the port with Python on a larger input set.

import assert from "node:assert/strict";
import { test } from "node:test";

import { FOLD_TABLE } from "../src/foldTable.js";
import { classify, isOpeningGreeting } from "../src/greetings.js";
import {
  fold,
  isAuthorInitialLine,
  isAuthorNameLine,
  matchesAddressee,
  matchesAuthor,
  nameWordStatus,
  parseAddresseeNames,
  parseAuthorName,
} from "../src/names.js";
import { compareCodePoints, isUpperInvariant, pyLower } from "../src/pyCompat.js";

const AUTHOR = "Jane Example";
const NAMES = ["Jane Doe", "Joseph Other"];

function senderLine(author: string, line: string): boolean {
  return isAuthorNameLine(line, parseAuthorName(author));
}

function greeting(line: string, addressees: string[] | null, nextLine: string | null = ""): boolean {
  return isOpeningGreeting(line, nextLine, addressees === null ? null : parseAddresseeNames(addressees));
}

// --- str.lower ----------------------------------------------------------------------

test("pyLower: full mapping and the final sigma", () => {
  assert.equal(pyLower("İ"), "i̇");
  assert.equal(pyLower("ΑΣ"), "ας");
  assert.equal(pyLower("ΑΣΑ"), "ασα");
  assert.equal(pyLower("Σ"), "σ");
  assert.equal(pyLower("ΑΣ'"), "ας'");
  assert.equal(pyLower("ΑΣ'Α"), "ασ'α");
  assert.equal(pyLower("K"), "k");
  assert.equal(pyLower("ß"), "ß");
  // Characters newer than Python's Unicode 16 are left alone (Node may know them).
  assert.equal(pyLower("꟎"), "꟎");
});

test("isUpperInvariant: str.upper() == s", () => {
  assert.equal(isUpperInvariant("IETF"), true);
  assert.equal(isUpperInvariant("STRAßE"), false);
  assert.equal(isUpperInvariant("A1"), true);
  assert.equal(isUpperInvariant("ǅ"), false);
});

test("compareCodePoints orders astral characters above the BMP", () => {
  assert.equal(compareCodePoints("\u{1d400}", "￿") > 0, true);
  assert.equal(compareCodePoints("ab", "abc") < 0, true);
  assert.equal(compareCodePoints("b", "ab") > 0, true);
});

// --- folding (test_port_hazards.py) -------------------------------------------------------

test("fold: the literal table after str.lower", () => {
  assert.equal(FOLD_TABLE.size, 396);
  assert.equal(fold("ß"), "ss");
  assert.equal(fold("İ"), "i");
  assert.equal(fold("ı"), "i");
  assert.equal(fold("é"), "e");
  assert.equal(fold("é"), "e");
  assert.equal(fold("Ǆ"), "dz");
  assert.equal(fold("ſ"), "s");
  assert.equal(fold("…"), "...");
  assert.equal(fold(" x"), " x");
  // Outside the table: only lowercased.
  assert.equal(fold("Ｊ"), "ｊ");
  assert.equal(fold("ΑΣ"), "ας");
  assert.equal(fold("ΑΣΑ"), "ασα");
});

test("name folding: sharp s", () => {
  assert.equal(senderLine("John Preuß", "Preuss"), true);
  assert.equal(senderLine("John Preuss", "PREUß"), true);
});

test("name folding: dotted and dotless i", () => {
  assert.equal(senderLine("Ayşe Yıldız", "Yildiz"), true);
  assert.equal(senderLine("İsmail Example", "Ismail"), true);
});

test("name folding: combining marks are dropped", () => {
  assert.equal(senderLine("José Example", "José"), true);
  assert.equal(senderLine("José Example", "José"), true);
});

test("name folding: explicit letters and compatibility forms", () => {
  assert.equal(senderLine("Bjørn Æsir", "Bjorn"), true);
  assert.equal(senderLine("Bjørn Æsir", "Aesir"), true);
  assert.equal(senderLine("Ǆoe Example", "ǆoe"), true);
  assert.equal(senderLine("Ǆoe Example", "Dzoe"), true);
  assert.equal(senderLine("Ross Example", "Roſſ"), true);
});

test("name folding is limited to the table", () => {
  assert.equal(senderLine("Jane Example", "Ｊａｎｅ"), false);
  const alexis = "Αλέξης";
  assert.equal(senderLine(`${alexis} Example`, alexis), true);
  assert.equal(senderLine(`${alexis} Example`, "Αλεξης"), false);
  assert.equal(senderLine(`${alexis} Example`, "Αλέξησ"), false);
  assert.equal(senderLine("Roy Example", "…Roy"), true);
});

test("name tokens use Unicode letters", () => {
  assert.equal(senderLine("山田太郎", "山田太郎"), true);
  // A Devanagari vowel sign (Mc) is not a letter: no token, no evidence.
  assert.equal(parseAuthorName("राम"), null);
  assert.equal(senderLine("राम", "राम"), false);
});

test("addressee folding in greetings", () => {
  assert.equal(greeting("Søren,", ["Soren Example"]), true);
  assert.equal(greeting("Soren,", ["Søren Example"]), true);
});

test("the acronym test uses str.upper", () => {
  assert.deepEqual(classify(["WG", "IETF", "STRAßE"], null), ["group", "acro", "cap"]);
  assert.equal(greeting("WG, IETF,", null, "the body text follows."), true);
  assert.equal(greeting("STRAßE, IETF,", null, "the body text follows."), false);
});

// --- sender name lines (test_cleaning.py) ------------------------------------------------

test("trailing sender name lines", () => {
  for (const line of ["Jane", "jane", "Jane Example", "-jane", "— Jane", "/je", "JE", "Jane (as chair)"]) {
    assert.equal(senderLine(AUTHOR, line), true, line);
  }
  for (const line of ["/j", "-J."]) {
    assert.equal(isAuthorInitialLine(line, parseAuthorName(AUTHOR)), true, line);
  }
});

test("sender name lines need the author name", () => {
  for (const line of ["Jane", "-jane", "/j"]) {
    assert.equal(isAuthorNameLine(line, null), false);
    assert.equal(isAuthorInitialLine(line, null), false);
    assert.equal(senderLine("Bob Other", line), false, line);
    assert.equal(isAuthorInitialLine(line, parseAuthorName("Bob Other")), false, line);
  }
});

test("an initial line must be the decorated first initial", () => {
  const author = parseAuthorName(AUTHOR);
  assert.equal(isAuthorInitialLine("/x", author), false);
  assert.equal(isAuthorInitialLine("J", author), false);
});

test("sender name matching forms", () => {
  const cases: [string, string][] = [
    ["Robert Example", "Bob"],
    ["Robert Example", "RjE"],
    ["Robert Example", "Rob E."],
    ["Søren Ångström", "Soren"],
    ["Example, Jane", "JE"],
    ["jane.example@example.org", "Jane"],
    ["mail@janeexample.org", "Jane Example"],
    ["Jane Example via Example-List", "Jane"],
    ['"Jane (JJ) Example"', "JJ"],
  ];
  for (const [author, line] of cases) assert.equal(senderLine(author, line), true, `${author} / ${line}`);
});

test("parsed names: tokens, initials, prefix and suffix lookups", () => {
  const robert = parseAuthorName("Robert Example");
  assert.ok(robert);
  assert.deepEqual([...robert.tokens].sort(), ["bob", "bobby", "example", "rob", "robby", "robert"]);
  assert.deepEqual([...robert.initials].sort(), ["er", "re"]);
  assert.equal(parseAuthorName("Robert Q. Example")?.firstLast, "re");
  const jane = parseAuthorName("Jane Doe");
  assert.ok(jane);
  assert.equal(jane.hasPrefix("ja"), true);
  assert.equal(jane.hasSuffix("ne"), true);
  assert.equal(jane.hasPrefix("x"), false);
  assert.equal(matchesAuthor("Jane", parseAuthorName(AUTHOR) ?? jane), true);
  assert.equal(nameWordStatus("de", parseAuthorName("Juan de la Cruz") ?? jane), 1);
  assert.equal(nameWordStatus("de", jane), 0);
  assert.equal(nameWordStatus("J.", jane), 1);
  assert.equal(nameWordStatus("Jean-Luc", parseAuthorName("Jean-Luc Picard") ?? jane), 2);
});

test("an author name without tokens gives no evidence", () => {
  for (const author of ["", "IETF Secretariat", "राम"]) {
    assert.equal(parseAuthorName(author), null, author);
  }
});

test("addressees: nicknames and the sender's own name", () => {
  assert.equal(matchesAddressee("Pete,", parseAddresseeNames(["Peter Other"])), true);
  assert.equal(matchesAddressee("Pete,", null), false);
  const others = parseAddresseeNames(["Michael Example", "Bob Smith"], "Michael Example");
  assert.deepEqual([...(others?.tokens ?? [])].sort(), ["bob", "smith"]);
  assert.equal(parseAddresseeNames([]), null);
  // A greeting to a namesake of the sender.
  assert.equal(isOpeningGreeting("Michael,", "", parseAddresseeNames(["Michael Other"], "Michael Example")), true);
});

// --- greetings (test_cleaning.py) ----------------------------------------------------------

test("opening greetings", () => {
  const cases: [string, string[] | null][] = [
    ["Jane,", NAMES],
    ["Jane", NAMES],
    ["Jane Doe:", NAMES],
    ["Jane, Joe:", NAMES],
    ["Jane, Joe and all,", NAMES],
    ["Dr. Doe,", NAMES],
    ["Jan,", NAMES],
    ["Thanks Jane,", NAMES],
    ["Hi!", NAMES],
    ["Hiya Jane,", null],
    ["Moin,", null],
    ["Howdy", null],
    ["Good morning everyone,", null],
    ["All:", null],
    ["WG chairs,", null],
    ["Stefano,", null],
    ["Jane, Joe:", null],
    ["Hi Songbo, Karthik, all,", null],
    ["Hi", null],
    ["Hello,", null],
    ["Dear Community,", null],
    ["Good morning all,", null],
    ["Hallo Peter,", null],
    ["Guten Tag,", null],
    ["Bonjour à tous,", null],
    ["Hola Maria,", null],
    ["Kia ora koutou,", null],
    ["Beste Jan,", null],
  ];
  for (const [line, addressees] of cases) assert.equal(greeting(line, addressees), true, line);
});

test("opening lines that are not greetings", () => {
  const cases: [string, string[] | null][] = [
    ["Jan.", NAMES],
    ["Thanks Jane!", NAMES],
    ["++ Jane and Joe.", NAMES],
    ["Jane", null],
    ["Actually,", null],
    ["Finally,", null],
    ["Basically,", null],
    ["Inline:", null],
    ["People:", null],
    ["V/R,", null],
    ["Hi Jane, apologies for the late reply.", null],
    ["Hi, I'm writing about the draft.", null],
    ["Hey that reminds me of the earlier draft discussion", null],
  ];
  for (const [line, addressees] of cases) assert.equal(greeting(line, addressees), false, line);
});

test("a structural greeting needs a capitalized next line", () => {
  assert.equal(greeting("Stefano,", null, "the body text follows."), false);
  assert.equal(greeting("Stefano,", null, "The body text follows."), true);
  assert.equal(greeting("Stefano,", null, null), true);
});
