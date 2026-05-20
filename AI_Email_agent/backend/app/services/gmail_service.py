"""
Gmail Service
─────────────
Handles all Gmail API interactions:
  - OAuth2 credential management
  - Sending outbound emails
  - Replying within an existing thread
  - Fetching unread messages
  - Fetching full thread message history
  - Parsing raw Gmail message payloads
"""

import base64
import email as email_lib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger
from app.services.email_parser import clean_email_body


# ── Retry helpers ─────────────────────────────────────────────────────────────

def _is_transient_error(exc: Exception) -> bool:
    """
    Return True only for errors that are worth retrying.

    Retryable:
      - 429 Too Many Requests (Gmail rate limit)
      - 5xx Server errors (Gmail service temporarily unavailable)
      - Any non-HttpError exception (network timeouts, connection resets, etc.)

    Non-retryable (wasting retries would hide bugs):
      - 400 Bad Request
      - 401 Unauthorized  (caller should refresh credentials)
      - 403 Forbidden
      - 404 Not Found
    """
    if isinstance(exc, HttpError):
        status = int(exc.resp.status)
        return status == 429 or status >= 500
    return True  # all non-HttpError exceptions are potentially transient


_RETRY_KWARGS = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception(_is_transient_error),
    reraise=True,
)


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class ParsedMessage:
    message_id: str
    thread_id: str
    sender: str
    recipient: str
    subject: str
    body: str
    timestamp: datetime
    raw_payload: dict[str, Any] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)


@dataclass
class SentMessage:
    message_id: str
    thread_id: str
    label_ids: list[str] = field(default_factory=list)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_credentials() -> Credentials:
    """
    Load existing OAuth token or trigger browser-based OAuth flow.
    Token is cached at GMAIL_TOKEN_JSON path for subsequent runs.
    """
    creds: Optional[Credentials] = None
    token_path = settings.GMAIL_TOKEN_JSON
    creds_path = settings.GMAIL_CREDENTIALS_JSON
    scopes = settings.GMAIL_SCOPES

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Gmail token expired — refreshing...")
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"Gmail credentials file not found at '{creds_path}'. "
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
            logger.info("Starting Gmail OAuth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
            creds = flow.run_local_server(port=0)

        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        logger.info(f"Gmail token saved to {token_path}")

    return creds


def _build_service():
    """Build and return an authenticated Gmail API service client."""
    creds = _get_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ── Message Parsing ───────────────────────────────────────────────────────────

def _decode_body(payload: dict) -> tuple[str, bool]:
    """
    Recursively extract plain-text body from a Gmail message payload.

    Preference order:
      1. text/plain  (already plain text — return as-is)
      2. text/html   (convert to plain text via email_parser.html_to_text)
      3. Multipart   (recurse into parts, depth-first)

    Returns:
        (body_text, is_html) — is_html=True means the raw content was HTML
        and html_to_text has already been applied.
    """
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        text = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        return text, False

    if mime_type == "text/html" and body_data:
        raw_html = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        return clean_email_body(raw_html, is_html=True), True

    # Prefer text/plain part over text/html in multipart messages
    plain_body: Optional[str] = None
    for part in payload.get("parts", []):
        body, is_html = _decode_body(part)
        if body and not is_html and plain_body is None:
            plain_body = body
        elif body and plain_body is None:
            plain_body = body  # accept HTML-derived text as fallback

    return plain_body or "", False


def _extract_header(headers: list[dict], name: str) -> str:
    """Extract a single header value by name (case-insensitive)."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def parse_message(raw_msg: dict) -> ParsedMessage:
    """
    Convert a raw Gmail API message dict into a typed ParsedMessage.
    """
    payload = raw_msg.get("payload", {})
    headers = payload.get("headers", [])

    sender    = _extract_header(headers, "From")
    recipient = _extract_header(headers, "To")
    subject   = _extract_header(headers, "Subject")
    date_str  = _extract_header(headers, "Date")

    try:
        parsed_dt = email_lib.utils.parsedate_to_datetime(date_str)
        timestamp = parsed_dt.astimezone(timezone.utc)
    except Exception:
        timestamp = datetime.now(timezone.utc)

    raw_body, was_html = _decode_body(payload)
    # For plain-text bodies (not already processed by html_to_text), run the
    # full parsing pipeline: strip forwarded blocks, quoted replies, signatures.
    body = raw_body if was_html else clean_email_body(raw_body, is_html=False)

    return ParsedMessage(
        message_id=raw_msg.get("id", ""),
        thread_id=raw_msg.get("threadId", ""),
        sender=sender,
        recipient=recipient,
        subject=subject,
        body=body,
        timestamp=timestamp,
        raw_payload=raw_msg,
        labels=raw_msg.get("labelIds", []),
    )


# ── Core Gmail Operations ─────────────────────────────────────────────────────

@retry(**_RETRY_KWARGS)
def send_email(
    to: str,
    subject: str,
    body: str,
    sender: str = "me",
    html: bool = False,
) -> SentMessage:
    """
    Send a new email (starts a fresh thread).

    Args:
        to:      Recipient email address.
        subject: Email subject line.
        body:    Email body (plain text or HTML).
        sender:  Gmail user ID — "me" uses the authenticated account.
        html:    If True, body is sent as text/html.

    Returns:
        SentMessage with the new message_id and thread_id.
    """
    service = _build_service()

    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    mime_type = "html" if html else "plain"
    msg.attach(MIMEText(body, mime_type, "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    try:
        result = service.users().messages().send(
            userId=sender,
            body={"raw": raw},
        ).execute()
        logger.info(f"Email sent to {to} | message_id={result['id']} thread_id={result['threadId']}")
        return SentMessage(
            message_id=result["id"],
            thread_id=result["threadId"],
            label_ids=result.get("labelIds", []),
        )
    except HttpError as e:
        logger.error(f"Failed to send email to {to}: {e}")
        raise


@retry(**_RETRY_KWARGS)
def reply_to_thread(
    thread_id: str,
    to: str,
    subject: str,
    body: str,
    sender: str = "me",
    html: bool = False,
) -> SentMessage:
    """
    Send a reply within an existing Gmail thread.

    Thread continuity is preserved via two mechanisms:
      1. ``threadId`` in the API request body  — Gmail-side grouping (mandatory).
      2. ``In-Reply-To`` / ``References`` MIME headers  — RFC 2822 cross-client
         threading (Outlook, Apple Mail, etc.).  These are populated by fetching
         the RFC 2822 ``Message-ID`` header of the last message in the thread
         using a lightweight ``format=metadata`` API call.  If the lookup fails
         for any reason we fall back to ``threadId``-only behaviour.

    Args:
        thread_id: The Gmail threadId to reply into.
        to:        Recipient email address.
        subject:   Subject line (prefixed with "Re: " if not already present).
        body:      Reply body (plain text or HTML).
        sender:    Gmail user ID ("me" = authenticated account).
        html:      Send body as text/html instead of text/plain.

    Returns:
        SentMessage with the new message_id and thread_id.
    """
    service = _build_service()

    mime_type = "html" if html else "plain"
    reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"

    # Best-effort: look up the RFC 2822 Message-ID of the last message in the
    # thread so we can set proper In-Reply-To / References headers.
    in_reply_to: Optional[str] = _get_last_rfc_message_id(service, thread_id)

    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = reply_subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.attach(MIMEText(body, mime_type, "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    try:
        result = service.users().messages().send(
            userId=sender,
            body={"raw": raw, "threadId": thread_id},
        ).execute()
        logger.info(
            f"Reply sent to {to} in thread {thread_id} | "
            f"message_id={result['id']} in_reply_to={'set' if in_reply_to else 'none'}"
        )
        return SentMessage(
            message_id=result["id"],
            thread_id=result["threadId"],
            label_ids=result.get("labelIds", []),
        )
    except HttpError as e:
        logger.error(f"Failed to reply to thread {thread_id}: {e}")
        raise


def _get_last_rfc_message_id(service, gmail_thread_id: str) -> Optional[str]:
    """
    Fetch the RFC 2822 ``Message-ID`` header of the most recent message in a
    Gmail thread.

    Uses ``format=metadata`` with ``metadataHeaders=["Message-ID"]`` — this is
    the lightest API call possible (no body fetch).

    Returns the raw header value (e.g. "<CABdQ5c4Y2g@mail.gmail.com>") or None
    if the lookup fails for any reason.
    """
    try:
        thread_data = service.users().threads().get(
            userId="me",
            id=gmail_thread_id,
            format="metadata",
            metadataHeaders=["Message-ID"],
        ).execute()
        messages = thread_data.get("messages", [])
        if messages:
            headers = messages[-1].get("payload", {}).get("headers", [])
            for h in headers:
                if h.get("name", "").lower() == "message-id":
                    return h.get("value")
    except Exception as exc:
        logger.debug(
            f"[gmail] Could not fetch Message-ID header for thread "
            f"{gmail_thread_id}: {exc}"
        )
    return None


@retry(**_RETRY_KWARGS)
def fetch_unread_messages(
    user_id: str = "me",
    max_results: int = 20,
    label_ids: Optional[list[str]] = None,
    q: Optional[str] = None,
) -> list[ParsedMessage]:
    """
    Fetch and parse unread messages from the inbox.

    Args:
        user_id:     Gmail user ID.
        max_results: Maximum number of messages to return.
        label_ids:   Label filters (default: INBOX + UNREAD).
        q:           Gmail search query string (e.g. "from:a@b.com is:unread").
                     When provided, label_ids are still applied alongside q.

    Returns:
        List of ParsedMessage sorted by timestamp ascending.
    """
    service = _build_service()

    try:
        list_kwargs: dict = {
            "userId": user_id,
            "maxResults": max_results,
        }
        if q:
            # When a q= is provided it already contains is:unread; adding
            # labelIds=["UNREAD"] on top causes no issues but is redundant.
            list_kwargs["q"] = q
        else:
            # Default: only INBOX + UNREAD labels
            list_kwargs["labelIds"] = label_ids or ["INBOX", "UNREAD"]

        list_resp = service.users().messages().list(**list_kwargs).execute()

        message_refs = list_resp.get("messages", [])
        if not message_refs:
            logger.debug("No unread messages found.")
            return []

        messages: list[ParsedMessage] = []
        for ref in message_refs:
            raw = service.users().messages().get(
                userId=user_id,
                id=ref["id"],
                format="full",
            ).execute()
            messages.append(parse_message(raw))

        messages.sort(key=lambda m: m.timestamp)
        logger.info(f"Fetched {len(messages)} unread message(s).")
        return messages

    except HttpError as e:
        logger.error(f"Failed to fetch unread messages: {e}")
        raise


@retry(**_RETRY_KWARGS)
def fetch_thread_messages(
    thread_id: str,
    user_id: str = "me",
) -> list[ParsedMessage]:
    """
    Fetch all messages in a specific Gmail thread.

    Args:
        thread_id: The Gmail thread ID.
        user_id:   Gmail user ID.

    Returns:
        List of ParsedMessage in chronological order.
    """
    service = _build_service()

    try:
        thread = service.users().threads().get(
            userId=user_id,
            id=thread_id,
            format="full",
        ).execute()

        messages = [parse_message(msg) for msg in thread.get("messages", [])]
        messages.sort(key=lambda m: m.timestamp)
        logger.info(f"Fetched {len(messages)} message(s) from thread {thread_id}.")
        return messages

    except HttpError as e:
        logger.error(f"Failed to fetch thread {thread_id}: {e}")
        raise


def mark_as_read(message_id: str, user_id: str = "me") -> None:
    """Remove the UNREAD label from a message after processing."""
    service = _build_service()
    try:
        service.users().messages().modify(
            userId=user_id,
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        logger.debug(f"Marked message {message_id} as read.")
    except HttpError as e:
        logger.warning(f"Failed to mark message {message_id} as read: {e}")


def get_authenticated_email(user_id: str = "me") -> str:
    """Return the email address of the authenticated Gmail account."""
    service = _build_service()
    profile = service.users().getProfile(userId=user_id).execute()
    return profile.get("emailAddress", "")
