"""
Email Body Parser
──────────────────
Pure-Python utilities for extracting clean, actionable text from raw email
bodies before they are fed to the LLM.

Handles (in order, as a pipeline):
  1. HTML → plain-text   (stdlib html.parser — no extra deps)
  2. Forwarded-block stripping
  3. Quoted-reply stripping  ("> text" lines, "On … wrote:" headers)
  4. Signature stripping     (RFC 2822 "-- " separator + common sign-offs)
  5. Whitespace normalisation

Design principles:
  - No third-party dependencies.
  - All functions are pure, stateless, and never raise.
  - Designed to be called on every inbound message before persisting to DB.
"""

from __future__ import annotations

import html as html_stdlib
import re
from html.parser import HTMLParser
from typing import Optional


# ── HTML → plain-text ─────────────────────────────────────────────────────────

class _StripHTMLParser(HTMLParser):
    """
    Minimal, stdlib-only HTML → text converter.

    Strategy:
      - <br>, <p>, <div>, <li>, <tr>, <td>, <h1>-<h6>  → newline
      - <script>, <style>                                 → skip entirely
      - Character / named entities                        → decoded to Unicode
      - Everything else                                   → plain text
    """

    _BLOCK_TAGS = frozenset(
        {"br", "p", "div", "li", "tr", "td", "th",
         "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
    )
    _SKIP_TAGS = frozenset({"script", "style", "head", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        tl = tag.lower()
        if tl in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tl in self._BLOCK_TAGS and not self._skip_depth:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tl = tag.lower()
        if tl in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def html_to_text(html_content: str) -> str:
    """
    Convert an HTML email body to clean plain text.

    Steps:
      1. Parse with stdlib HTMLParser (skips scripts/styles, converts block
         elements to newlines, decodes entities via convert_charrefs=True).
      2. Unescape any remaining HTML entities (e.g. &amp;, &nbsp;).
      3. Collapse runs of blank lines to a single blank line.

    Returns empty string on any error (never raises).
    """
    if not html_content:
        return ""
    try:
        parser = _StripHTMLParser()
        parser.feed(html_content)
        text = parser.get_text()
        # Unescape any surviving entities
        text = html_stdlib.unescape(text)
        # Collapse 3+ consecutive blank lines → at most 1 blank line
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    except Exception:
        # Last-resort fallback: raw tag stripping
        return re.sub(r"<[^>]+>", "", html_content).strip()


# ── Forwarded-email stripping ─────────────────────────────────────────────────

# Separator lines from common email clients
_FORWARD_SEPARATORS = re.compile(
    r"^(-{3,}\s*(Forwarded message|Original Message|Begin forwarded message)\s*-{3,}"
    r"|_{3,}"
    r"|From:\s+.+$\n(?:Sent|Date):\s)",
    re.IGNORECASE | re.MULTILINE,
)


def strip_forwarded(text: str) -> str:
    """
    Remove forwarded-email blocks (Outlook dashes, Gmail forwarded header, etc.).
    Returns the text UP TO the first forwarding separator.
    """
    match = _FORWARD_SEPARATORS.search(text)
    if match:
        return text[: match.start()].strip()
    return text


# ── Quoted-reply stripping ────────────────────────────────────────────────────

# "On Mon, 19 May 2026 at 10:30, John <john@x.com> wrote:" — may span 2 lines
# We match the last such header in the text and truncate there.
_ON_WROTE_RE = re.compile(
    r"\bOn\s.{10,300}?\bwrote:\s*$",
    re.IGNORECASE | re.DOTALL | re.MULTILINE,
)

# Continuous block of ">"-prefixed lines at any point in the text
_QUOTE_BLOCK_RE = re.compile(
    r"(^>[ \t]?.+$\n?)+",
    re.MULTILINE,
)


def strip_quoted_reply(text: str) -> str:
    """
    Remove quoted reply sections from an email body.

    Handles:
      - Lines starting with ">" (RFC 3676 / Gmail plain-text quotes)
      - "On [date], [sender] wrote:" header that immediately precedes a quote block
    """
    # 1 — Find "On … wrote:" headers that are directly above quote lines.
    #     Walk backwards through matches; truncate at the earliest such header
    #     that has a quote block immediately after it.
    lines = text.splitlines(keepends=True)
    cut_at: Optional[int] = None  # character index to cut

    pos = 0
    for line in lines:
        stripped = line.lstrip()
        # Plain ">"-quoted lines — record start position and stop
        if stripped.startswith(">"):
            # Look for the "On … wrote:" line BEFORE the first quoted line
            # by scanning backwards over blank lines.
            candidate = text[:pos].rstrip()
            m = _ON_WROTE_RE.search(candidate)
            if m:
                cut_at = m.start()
            else:
                cut_at = pos
            break
        pos += len(line)

    if cut_at is not None:
        return text[:cut_at].strip()

    # 2 — Remove any remaining ">"-prefixed blocks that weren't caught above
    result = _QUOTE_BLOCK_RE.sub("", text)
    return result.strip()


# ── Signature stripping ───────────────────────────────────────────────────────

# RFC 2822 signature separator: "-- " (dash dash space) on its own line,
# or the two-hyphen variant without trailing space used by many clients.
_RFC_SIG_RE = re.compile(r"\n--[ \t]*\n", re.MULTILINE)

# Common English sign-off phrases at the end of the body.
# We only strip if the sign-off is in the last ~5 lines (avoids false positives
# mid-thread where "Regards" might be part of the actual message content).
_SIGNOFF_PHRASES = re.compile(
    r"\n(?:Thanks|Best(?: regards)?|Kind regards|Warm regards|Best wishes|"
    r"Regards|Sincerely|Cheers|With appreciation|Yours truly|"
    r"Many thanks)[,. \t]*\n",
    re.IGNORECASE,
)


def strip_signature(text: str) -> str:
    """
    Remove email signatures from the bottom of an email body.

    Handles:
      - RFC 2822 "-- " separator lines
      - Common sign-off phrases near the end of the body
    """
    # 1 — RFC 2822 "-- " separator
    m = _RFC_SIG_RE.search(text)
    if m:
        return text[: m.start()].strip()

    # 2 — Common sign-off phrases (only in the last 10 lines to avoid FP)
    lines = text.splitlines()
    if len(lines) > 10:
        tail = "\n".join(lines[-10:])
        m = _SIGNOFF_PHRASES.search("\n" + tail)
        if m:
            # Find the absolute character position in the original text
            tail_start = len("\n".join(lines[:-10]))
            return text[: tail_start + m.start()].strip()
    else:
        m = _SIGNOFF_PHRASES.search("\n" + text)
        if m:
            return text[: m.start()].strip()

    return text


# ── Whitespace normalisation ──────────────────────────────────────────────────

def _normalise_whitespace(text: str) -> str:
    """
    Collapse internal runs of blank lines to one, strip leading/trailing space.
    """
    text = re.sub(r"[ \t]+\n", "\n", text)          # trailing spaces on each line
    text = re.sub(r"\n{3,}", "\n\n", text)           # 3+ blank lines → 1
    return text.strip()


# ── Public pipeline ───────────────────────────────────────────────────────────

def clean_email_body(raw_body: str, is_html: bool = False) -> str:
    """
    Full parsing pipeline for an inbound email body.

    Steps (in order):
      1. HTML → plain-text (if is_html=True)
      2. Strip forwarded-email blocks
      3. Strip quoted reply sections
      4. Strip email signatures
      5. Normalise whitespace

    Args:
        raw_body: Raw email body as received from the Gmail API.
        is_html:  True if the content-type is text/html.

    Returns:
        Clean, actionable text suitable for LLM consumption.
        Returns empty string if the result is entirely whitespace.

    Never raises.
    """
    if not raw_body:
        return ""

    try:
        text = html_to_text(raw_body) if is_html else raw_body
        text = strip_forwarded(text)
        text = strip_quoted_reply(text)
        text = strip_signature(text)
        text = _normalise_whitespace(text)
        return text
    except Exception:
        # Safety net: return the raw body as-is rather than crashing
        return raw_body.strip()
