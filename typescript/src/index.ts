/**
 * email-reply-extractor: recover the author's newly written text from an email.
 *
 * - Stage 1 ({@link extractNewText}) removes quoted text, attribution lines,
 *   forwarded/quote-header blocks and other reproduced content, keeping the
 *   author's full novel content — greetings, sign-offs and signatures
 *   included.
 * - Stage 2 ({@link cleanForScoring}) strips that formulaic furniture from the
 *   stage-1 text and reports which lines it removed.
 *
 * {@link EXTRACTION_VERSION} identifies the generation of the routine that
 * derives the text. This package is a port of the Python reference
 * implementation and derives byte-identical output.
 *
 * @packageDocumentation
 */

export { type CleanOptions, type CleanResult, cleanForScoring } from "./cleaning.js";
export { textsEquivalent, tolerantLines } from "./equivalence.js";
export {
  EXTRACTION_VERSION,
  type ExtractionResult,
  type ExtractionStatus,
  type ExtractOptions,
  extractNewText,
  STATUS_EMPTY,
  STATUS_FAILED,
  STATUS_OK,
  type StripParentOptions,
  stripParentContent,
} from "./extraction.js";
export { type HtmlParts, htmlToText, splitHtmlParts } from "./htmlText.js";

/** The package version (equal to the Python package's `__version__`). */
export const VERSION = "1.2.1";
