"""email-reply-extractor: recover the author's newly written text from an email.

A two-stage, pure-stdlib pipeline for mailing-list mail:

- **Stage 1** (:func:`extract_new_text`) removes quoted text, attribution
  lines, forwarded/quote-header blocks and other reproduced content, keeping
  the author's full novel content — greetings, sign-offs and signatures
  included.
- **Stage 2** (:func:`clean_for_scoring`) strips that formulaic furniture from
  the stage-1 text, leaving only the author's substantive prose, and reports
  which lines it removed.

:data:`EXTRACTION_VERSION` identifies the generation of the routine that
derives the text; it is independent of the package version and only ever
increases. See ``CONTRIBUTING.md`` for the contract.
"""

from .cleaning import CleanResult, clean_for_scoring
from .extraction import (
    EXTRACTION_VERSION,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_OK,
    ExtractionResult,
    extract_new_text,
    strip_parent_content,
)
from .html_text import HtmlParts, html_to_text, split_html_parts

__version__ = "1.0.0"

__all__ = [
    "EXTRACTION_VERSION",
    "STATUS_EMPTY",
    "STATUS_FAILED",
    "STATUS_OK",
    "CleanResult",
    "ExtractionResult",
    "HtmlParts",
    "__version__",
    "clean_for_scoring",
    "extract_new_text",
    "html_to_text",
    "split_html_parts",
    "strip_parent_content",
]
