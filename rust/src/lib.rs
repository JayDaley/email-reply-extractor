//! Recover the newly written text of an email reply.
//!
//! A two-stage pipeline for mailing-list mail, ported from the Python
//! reference implementation of `email-reply-extractor` and tested against
//! its conformance suite, so that it produces the same output byte for byte:
//!
//! - **Stage 1**, [`extract_new_text`], removes quoted text, attribution
//!   lines, forwarded/quote-header blocks and other reproduced content. It
//!   keeps the author's full novel content, greetings, sign-offs and
//!   signatures included.
//! - **Stage 2**, [`clean_for_scoring`], strips that formulaic furniture
//!   from the stage-1 text, leaving the author's substantive prose, and
//!   reports which lines it removed.
//!
//! The HTML helpers [`html_to_text`] and [`split_html_parts`] render the
//! visible text of a `text/html` part; [`strip_parent_content`] removes
//! content that provably came from a thread parent; [`tolerant_lines`] and
//! [`texts_equivalent`] compare two derived texts while ignoring whitespace.
//!
//! [`EXTRACTION_VERSION`] identifies the generation of the routine that
//! derives the text. It is independent of the package version ([`VERSION`])
//! and only ever increases; consumers that store derived text compare a
//! stored stamp with it using `<`.
//!
//! # Arguments
//!
//! Functions take decoded strings; the crate does no MIME parsing. The
//! reference's optional arguments (Python default `None`) are `Option<&str>`
//! parameters in the reference's order, and its keyword-only flag
//! `continuation` is a `bool`.
//!
//! ```
//! use email_reply_extractor::{clean_for_scoring, extract_new_text, Status};
//!
//! let body = "Thanks, that works for me.\n\nBest,\nAnn\n\n\
//!             On Mon, 6 Jul 2026 at 10:00, Bob <bob@example.org> wrote:\n\
//!             > Does Thursday work?\n";
//! let stage1 = extract_new_text(Some(body), None, None);
//! assert_eq!(stage1.status, Status::Ok);
//! assert_eq!(stage1.text, "Thanks, that works for me.\n\nBest,\nAnn");
//!
//! let stage2 = clean_for_scoring(&stage1.text, None);
//! assert_eq!(stage2.text, "Thanks, that works for me.");
//! assert_eq!(stage2.ignored_lines, vec![2, 3]);
//! ```
//!
//! The rules of both stages are documented in the repository's
//! `docs/extraction.md`; the port contract is `docs/ports.md`.

#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod cleaning;
mod difflib;
mod equivalence;
mod extraction;
mod fragments;
mod html_entities;
mod html_parser;
mod html_text;
mod pyre;
mod pystr;
mod unicode_tables;

pub use cleaning::{clean_for_scoring, CleanResult};
pub use equivalence::{texts_equivalent, tolerant_lines};
pub use extraction::{
    extract_new_text, strip_parent_content, ExtractionResult, Status, EXTRACTION_VERSION,
    STATUS_EMPTY, STATUS_FAILED, STATUS_OK,
};
pub use html_text::{html_to_text, split_html_parts, HtmlParts};

/// The package version; equal across the Python, TypeScript and Rust ports.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Internal helpers of the reference, exported for the conformance suite's
/// internal vectors. Not part of the public API; may change in any release.
#[doc(hidden)]
pub mod internal {
    pub use crate::cleaning::{is_mobile_tagline, is_signature_delimiter};
    pub use crate::extraction::{
        count_unquoted_content_lines, custom_clean, find_quote_header_block, find_signoff_boundary,
        is_attribution_line, is_quote_line, is_signoff_name_line, normalize_body,
        strip_after_original_message_divider, strip_attribution_lines,
    };
    pub use crate::fragments::{read_fragments, Fragment, PatternError};
}

/// The README's examples, compiled and run as doctests.
#[cfg(doctest)]
#[doc = include_str!("../README.md")]
pub struct ReadmeDoctests;
