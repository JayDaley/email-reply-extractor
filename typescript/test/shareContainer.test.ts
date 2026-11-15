// The Apple Mail Share-sheet container (generation 8): the segmenter rule in
// htmlText.ts and the extractNewText path (d). Mirrors the Share-sheet tests of
// python/tests/test_html_text.py and python/tests/test_extraction.py.

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { extractNewText } from "../src/extraction.js";
import { splitHtmlParts } from "../src/htmlText.js";

/** The repository's conformance/ directory, found by walking up from this file. */
function conformanceDir(): string {
  let dir = dirname(fileURLToPath(import.meta.url));
  for (;;) {
    const candidate = join(dir, "conformance");
    if (existsSync(join(candidate, "digest.json"))) return candidate;
    const parent = dirname(dir);
    if (parent === dir) throw new Error("conformance/ not found above the test file");
    dir = parent;
  }
}

// biome-ignore lint/suspicious/noExplicitAny: JSON data
const CORPUS: any = JSON.parse(readFileSync(join(conformanceDir(), "corpus.json"), "utf8"));

/** The decoded bodies of a fixture, from conformance/corpus.json. */
function fixture(stem: string): { body: string | null; html: string | null } {
  // biome-ignore lint/suspicious/noExplicitAny: JSON data
  const entry = CORPUS.entries.find((e: any) => e.stem === stem);
  assert.ok(entry, `fixture ${stem} not in corpus.json`);
  return { body: entry.body, html: entry.html_body };
}

const nonBlankLines = (text: string): string[] => text.split("\n").filter((ln) => ln.trim());

// --- segmenter (test_html_text.py) -------------------------------------------------

const SHARE_TOP = '<div class="Apple-Mail-URLShareUserContentTopClass"><br></div>';
const SHARE_OPEN =
  '<div class="Apple-Mail-URLShareWrapperClass" style="position: relative !important;">' +
  '<blockquote type="cite" style="border-left-style: none;">';
const SHARE_CLOSE = "</blockquote></div>";

describe("splitHtmlParts: Share-sheet container", () => {
  test("the share container blockquote is novel", () => {
    const html = `<div dir="auto">${SHARE_TOP}${SHARE_OPEN}Shared text.<br>Second line.${SHARE_CLOSE}</div>`;
    const parts = splitHtmlParts(html);
    assert.equal(parts.novelText, "Shared text.\nSecond line.");
    assert.equal(parts.quotedText, "");
    assert.equal(parts.hasShareContainer, true);
  });

  test("a blockquote nested in the container stays quoted", () => {
    const html =
      `${SHARE_OPEN}My answer.<br>On Monday, Someone wrote:` +
      '<blockquote type="cite">their earlier text</blockquote>' +
      `More of my answer.${SHARE_CLOSE}`;
    const parts = splitHtmlParts(html);
    assert.equal(parts.novelText, "My answer.\nOn Monday, Someone wrote:\n\nMore of my answer.");
    assert.equal(parts.quotedText, "their earlier text");
    assert.equal(parts.hasShareContainer, true);
  });

  test("a wrapper with several blockquote children", () => {
    const html =
      '<div class="Apple-Mail-URLShareWrapperClass">' +
      "<blockquote>first</blockquote><blockquote>second</blockquote></div>";
    const parts = splitHtmlParts(html);
    assert.equal(parts.novelText, "first\n\nsecond");
    assert.equal(parts.quotedText, "");
  });

  test("the wrapper class among other tokens", () => {
    const parts = splitHtmlParts(
      '<div class="x Apple-Mail-URLShareWrapperClass y"><blockquote>mine</blockquote></div>',
    );
    assert.equal(parts.novelText, "mine");
    assert.equal(parts.hasShareContainer, true);
  });

  const notDirectChild: [string, string][] = [
    [
      "an element between the wrapper and the blockquote",
      '<div class="Apple-Mail-URLShareWrapperClass"><div><blockquote>q</blockquote></div></div>',
    ],
    [
      "an unclosed element is the parent",
      '<div class="Apple-Mail-URLShareWrapperClass"><p>intro<blockquote>q</blockquote></div>',
    ],
    [
      "the class token in another case",
      '<div class="apple-mail-urlsharewrapperclass"><blockquote>q</blockquote></div>',
    ],
    ["a longer class token", '<div class="Apple-Mail-URLShareWrapperClassX"><blockquote>q</blockquote></div>'],
    ["the user-content class", '<div class="Apple-Mail-URLShareUserContentTopClass"><blockquote>q</blockquote></div>'],
    [
      "the wrapper closed before the blockquote",
      '<div class="Apple-Mail-URLShareWrapperClass"></div><blockquote>q</blockquote>',
    ],
  ];
  for (const [name, html] of notDirectChild) {
    test(`a blockquote not directly under the wrapper is quoted: ${name}`, () => {
      const parts = splitHtmlParts(html);
      assert.equal(parts.quotedText, "q");
      assert.equal(parts.hasShareContainer, false);
    });
  }

  test("a void tag between does not break parenthood", () => {
    const parts = splitHtmlParts(
      '<div class="Apple-Mail-URLShareWrapperClass"><br><blockquote>mine</blockquote></div>',
    );
    assert.equal(parts.novelText, "mine");
    assert.equal(parts.hasShareContainer, true);
  });

  test("an ordinary cite blockquote is still quoted", () => {
    const parts = splitHtmlParts('<div>my reply</div><blockquote type="cite">their text</blockquote>');
    assert.equal(parts.novelText, "my reply");
    assert.equal(parts.quotedText, "their text");
    assert.equal(parts.hasShareContainer, false);
  });

  test("a quoted share container stays quoted and is not reported", () => {
    const html =
      "<div>my reply</div>" +
      `<blockquote type="cite">On Monday, Someone wrote:${SHARE_OPEN}their shared text` +
      `${SHARE_CLOSE}</blockquote>`;
    const parts = splitHtmlParts(html);
    assert.equal(parts.novelText, "my reply");
    assert.ok(parts.quotedText.includes("their shared text"));
    assert.equal(parts.hasShareContainer, false);
  });

  test("a share container in head is not reported", () => {
    const html = `<html><head>${SHARE_OPEN}hidden${SHARE_CLOSE}</head><body><div>visible</div></body></html>`;
    const parts = splitHtmlParts(html);
    assert.equal(parts.novelText, "visible");
    assert.equal(parts.hasShareContainer, false);
  });

  test("an unclosed share container does not throw", () => {
    const parts = splitHtmlParts(`<div>typed in Mail</div>${SHARE_OPEN}shared text with no close`);
    assert.equal(parts.novelText, "typed in Mail\n\nshared text with no close");
    assert.equal(parts.hasShareContainer, true);
  });

  test("empty input reports no share container", () => {
    assert.equal(splitHtmlParts("").hasShareContainer, false);
  });
});

// --- extractNewText path (d) (test_extraction.py) -----------------------------------

const SHARE_PROSE = [
  "The registry proposal in the second draft keeps the allocation policy unchanged.",
  "I suggest we move the open question about expiry to the next interim meeting.",
];

/** An HTML body shaped like Apple Mail's Share sheet around `inner`. */
function shareHtml(inner: string, before = "", after = ""): string {
  return (
    '<html><head><meta charset="utf-8"></head><body><div dir="auto">' +
    `<div class="Apple-Mail-URLShareUserContentTopClass">${before}<br></div>` +
    '<div class="Apple-Mail-URLShareWrapperClass" style="position: relative !important;">' +
    '<blockquote type="cite" style="border-left-style: none; color: inherit;">' +
    `${inner}</blockquote></div>${after}<br><br></body></html>`
  );
}

/** The text/plain rendering of a Share-sheet body: every line `>`-prefixed. */
function cite(lines: readonly string[]): string {
  return `${lines.map((line) => (line ? `> ${line}` : ">")).join("\n")}\n`;
}

describe("extractNewText: Share-sheet container", () => {
  test("recovers text the plain part quotes", () => {
    const lines = ["Carol,", "", ...SHARE_PROSE, "", "Alice"];
    const html = shareHtml(lines.join("<br>"));
    const body = cite(lines);
    assert.equal(extractNewText(body).status, "empty");
    const result = extractNewText(body, { htmlBody: html });
    assert.equal(result.status, "ok");
    assert.equal(result.method, "html-erp");
    assert.equal(result.text, `Carol,\n\n${SHARE_PROSE.join("\n")}\n\nAlice`);
  });

  test("keeps text typed outside the wrapper, in order", () => {
    const html = shareHtml(
      SHARE_PROSE.join("<br>"),
      "Forwarding my notes from the review.",
      "<div>Sent from my phone</div>",
    );
    const body = `Forwarding my notes from the review.\n\n${cite(SHARE_PROSE)}\nSent from my phone\n`;
    const result = extractNewText(body, { htmlBody: html });
    assert.ok(result.method.startsWith("html-"));
    assert.deepStrictEqual(nonBlankLines(result.text), [
      "Forwarding my notes from the review.",
      ...SHARE_PROSE,
      "Sent from my phone",
    ]);
  });

  test("a nested blockquote is removed", () => {
    const html = shareHtml(
      `${SHARE_PROSE.join("<br>")}<blockquote type="cite">An earlier message that this one quotes in full.</blockquote>`,
    );
    const body = cite([...SHARE_PROSE, "", "> An earlier message that this one quotes in full."]);
    const result = extractNewText(body, { htmlBody: html });
    assert.ok(result.method.startsWith("html-"));
    assert.equal(result.text, SHARE_PROSE.join("\n"));
  });

  test("attribution and quoted lines are removed by the core path", () => {
    const attribution = "On Mon, 2 Nov 2026 at 10:00, Bob Example <bob@example.org> wrote:";
    const inner = [
      SHARE_PROSE[0],
      "",
      attribution,
      "&gt; The allocation policy should change in the next revision.",
      "&gt; Expiry also needs discussion.",
      "",
      SHARE_PROSE[1],
    ].join("<br>");
    const body = cite([
      SHARE_PROSE[0] as string,
      "",
      attribution,
      "> The allocation policy should change in the next revision.",
      "> Expiry also needs discussion.",
      "",
      SHARE_PROSE[1] as string,
    ]);
    const result = extractNewText(body, { htmlBody: shareHtml(inner) });
    assert.equal(result.status, "ok");
    assert.ok(result.method.startsWith("html-"));
    assert.ok(!result.text.includes("wrote:"));
    assert.ok(!result.text.includes("allocation policy should change"));
    assert.deepStrictEqual(nonBlankLines(result.text), SHARE_PROSE);
  });

  test("the parent is passed to the core path", () => {
    const parentLines = [
      "Here is the agenda for the interim meeting next week.",
      "Item one covers the registry and its allocation policy in detail.",
      "Item two covers expiry and the remaining open questions from Prague.",
    ];
    const parent = parentLines.join("\n");
    const html = shareHtml([SHARE_PROSE[0], "", ...parentLines].join("<br>"));
    const body = cite([SHARE_PROSE[0] as string, "", ...parentLines]);
    const result = extractNewText(body, { parentBody: parent, htmlBody: html });
    assert.equal(result.method, "html-erp+parent-diff");
    assert.equal(result.text, SHARE_PROSE[0]);
  });

  test("the Share-sheet fixture derives its text from the HTML", () => {
    const { body, html } = fixture("toppost-share-sheet-synthetic-01");
    const result = extractNewText(body, { htmlBody: html });
    assert.equal(result.method, "html-erp+custom");
    assert.ok(result.text.startsWith("Hi Carol,\n\nThank you for the review."));
    assert.ok(result.text.endsWith("Best,\nAlice"));
    assert.ok(!result.text.includes("wrote:"));
    assert.ok(!result.text.includes("I could not find it"));
  });

  test("the plain body alone yields text for every fixture except a Share-sheet message", () => {
    // biome-ignore lint/suspicious/noExplicitAny: JSON data
    for (const entry of CORPUS.entries as any[]) {
      const result = extractNewText(entry.body, { htmlBody: entry.html_body });
      assert.equal(result.status, "ok", entry.stem);
      const plain = extractNewText(entry.body);
      const share = entry.html_body !== null && splitHtmlParts(entry.html_body).hasShareContainer;
      assert.equal(plain.status, share ? "empty" : "ok", entry.stem);
    }
  });

  test("an ordinary cite blockquote does not take the share path", () => {
    const reply = "My reply to the agenda, written below the quote.";
    const html = `<div>${reply}</div><blockquote type="cite">${SHARE_PROSE.join("<br>")}</blockquote>`;
    const body = `${reply}\n\n${cite(SHARE_PROSE)}`;
    const result = extractNewText(body, { htmlBody: html });
    assert.ok(!result.method.startsWith("html-"));
    assert.equal(result.text, reply);
  });

  test("a share container with blank novel text keeps the plain path", () => {
    const body = cite(SHARE_PROSE);
    const result = extractNewText(body, { htmlBody: shareHtml("<br>") });
    assert.deepStrictEqual(result, extractNewText(body));
    assert.equal(result.status, "empty");
    assert.ok(!result.method.startsWith("html-"));
  });

  test("a Share-shaped plain body without HTML is unchanged", () => {
    const body = cite(SHARE_PROSE);
    const result = extractNewText(body);
    assert.equal(result.status, "empty");
    assert.deepStrictEqual(extractNewText(body, { htmlBody: null }), result);
  });

  test("a reply quoting a Share-sheet message keeps the plain path", () => {
    // The share container sits inside this reply's own quote, so the HTML
    // reports no share container and the plain pipeline runs as before.
    const reply = "I agree with both points and will update the draft this week.";
    const html =
      `<div>${reply}</div>` +
      '<blockquote type="cite"><div>On Mon, 2 Nov 2026, Bob Example wrote:</div>' +
      shareHtml(SHARE_PROSE.join("<br>")) +
      "</blockquote>";
    const body = `${reply}\n\nOn Mon, 2 Nov 2026, Bob Example wrote:\n\n${cite(SHARE_PROSE)}`;
    const result = extractNewText(body, { htmlBody: html });
    assert.ok(!result.method.startsWith("html-"));
    assert.equal(result.text, reply);
  });
});
