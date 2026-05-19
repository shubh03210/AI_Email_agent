"""
Google Calendar Service
────────────────────────
Handles all Google Calendar API interactions:
  - OAuth2 credential management
  - Fetch available free slots within working hours
  - Create calendar events with Google Meet links
  - Cancel (delete) events with attendee notification
  - Reschedule events (patch existing event)
  - Fetch event details by ID
  - Format slots for human-readable email body
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Optional
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class TimeSlot:
    start: datetime     # UTC-aware
    end: datetime       # UTC-aware

    def to_display(self, tz_name: str = "UTC") -> str:
        local_tz = ZoneInfo(tz_name)
        local_start = self.start.astimezone(local_tz)
        return local_start.strftime("%A, %B %d %Y at %I:%M %p %Z")

    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)


@dataclass
class CalendarEvent:
    event_id: str
    summary: str
    description: str
    start: datetime
    end: datetime
    attendees: list[str] = field(default_factory=list)
    status: str = "confirmed"
    meet_link: Optional[str] = None
    html_link: Optional[str] = None


# ── Auth ──────────────────────────────────────────────────────────────────────

def _get_credentials() -> Credentials:
    """
    Load or refresh Calendar OAuth token.
    Reuses the same GCP project credentials as Gmail.
    Token cached at CALENDAR_TOKEN_JSON path.
    """
    creds: Optional[Credentials] = None
    token_path = settings.CALENDAR_TOKEN_JSON
    creds_path = settings.CALENDAR_CREDENTIALS_JSON
    scopes = settings.CALENDAR_SCOPES

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Calendar token expired — refreshing...")
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"Calendar credentials not found at '{creds_path}'. "
                    "Download OAuth 2.0 JSON from Google Cloud Console."
                )
            logger.info("Starting Calendar OAuth flow...")
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
            creds = flow.run_local_server(port=0)

        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        logger.info(f"Calendar token saved to {token_path}")

    return creds


def _build_service():
    """Build and return an authenticated Google Calendar API service client."""
    creds = _get_credentials()
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


# ── Slot Generation ───────────────────────────────────────────────────────────

def get_available_slots(
    duration_minutes: int = 60,
    days_ahead: int = 7,
    tz_name: str = "UTC",
    working_hours_start: Optional[int] = None,
    working_hours_end: Optional[int] = None,
    calendar_id: str = "primary",
    max_slots: int = 5,
) -> list[TimeSlot]:
    """
    Find available free time slots using the Calendar freebusy API.

    Args:
        duration_minutes:     Length of each slot in minutes.
        days_ahead:           How many calendar days forward to search.
        tz_name:              IANA timezone string (e.g. "Asia/Kolkata").
        working_hours_start:  Working day start hour (0-23). Falls back to config.
        working_hours_end:    Working day end hour (0-23). Falls back to config.
        calendar_id:          Google Calendar ID ("primary" = authenticated account).
        max_slots:            Maximum number of free slots to return.

    Returns:
        List of free TimeSlot objects in UTC.
    """
    service = _build_service()

    wh_start = working_hours_start if working_hours_start is not None else settings.AGENT_DEFAULT_WORKING_HOURS_START
    wh_end   = working_hours_end   if working_hours_end   is not None else settings.AGENT_DEFAULT_WORKING_HOURS_END

    local_tz = ZoneInfo(tz_name)
    now = datetime.now(local_tz)
    search_start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    search_end   = search_start + timedelta(days=days_ahead)

    try:
        freebusy_result = service.freebusy().query(
            body={
                "timeMin": search_start.isoformat(),
                "timeMax": search_end.isoformat(),
                "timeZone": tz_name,
                "items": [{"id": calendar_id}],
            }
        ).execute()
    except HttpError as e:
        logger.error(f"Freebusy query failed: {e}")
        raise

    busy_periods: list[tuple[datetime, datetime]] = []
    for busy in freebusy_result.get("calendars", {}).get(calendar_id, {}).get("busy", []):
        b_start = datetime.fromisoformat(busy["start"]).astimezone(local_tz)
        b_end   = datetime.fromisoformat(busy["end"]).astimezone(local_tz)
        busy_periods.append((b_start, b_end))

    free_slots: list[TimeSlot] = []
    candidate = search_start

    while candidate < search_end and len(free_slots) < max_slots:
        # Skip weekends
        if candidate.weekday() >= 5:
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=wh_start, minute=0, second=0, microsecond=0)
            continue

        # Skip before working hours start
        if candidate.hour < wh_start:
            candidate = candidate.replace(hour=wh_start, minute=0, second=0, microsecond=0)
            continue

        # Skip after working hours end
        if candidate.hour >= wh_end:
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=wh_start, minute=0, second=0, microsecond=0)
            continue

        slot_end = candidate + timedelta(minutes=duration_minutes)

        # Ensure slot ends within working hours
        if slot_end.hour > wh_end or (slot_end.hour == wh_end and slot_end.minute > 0):
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=wh_start, minute=0, second=0, microsecond=0)
            continue

        # Check overlap with any busy period
        is_busy = any(
            candidate < b_end and slot_end > b_start
            for b_start, b_end in busy_periods
        )

        if not is_busy:
            free_slots.append(TimeSlot(
                start=candidate.astimezone(dt_timezone.utc),
                end=slot_end.astimezone(dt_timezone.utc),
            ))

        candidate += timedelta(minutes=30)

    logger.info(f"Found {len(free_slots)} available slot(s) for {duration_minutes}min meeting.")
    return free_slots


# ── Event Operations ──────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def create_event(
    summary: str,
    description: str,
    start: datetime,
    attendee_emails: list[str],
    duration_minutes: int = 60,
    tz_name: str = "UTC",
    calendar_id: str = "primary",
    add_meet_link: bool = True,
) -> CalendarEvent:
    """
    Create a Google Calendar event and send invites to attendees.

    Args:
        summary:          Event title.
        description:      Event description / agenda.
        start:            Event start (timezone-aware datetime).
        attendee_emails:  List of attendee email addresses.
        duration_minutes: Event duration in minutes.
        tz_name:          IANA timezone string.
        calendar_id:      Target calendar.
        add_meet_link:    Attach a Google Meet link to the event.

    Returns:
        CalendarEvent with event_id, meet_link, and html_link.
    """
    service = _build_service()
    end = start + timedelta(minutes=duration_minutes)

    event_body: dict = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "end":   {"dateTime": end.isoformat(),   "timeZone": tz_name},
        "attendees": [{"email": e} for e in attendee_emails],
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email",  "minutes": 24 * 60},
                {"method": "popup",  "minutes": 15},
            ],
        },
        "status": "confirmed",
    }

    if add_meet_link:
        event_body["conferenceData"] = {
            "createRequest": {
                "requestId": f"meet-{int(start.timestamp())}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    try:
        created = service.events().insert(
            calendarId=calendar_id,
            body=event_body,
            conferenceDataVersion=1 if add_meet_link else 0,
            sendUpdates="all",
        ).execute()

        entry_points = created.get("conferenceData", {}).get("entryPoints", [])
        meet_link = entry_points[0].get("uri") if entry_points else None

        logger.info(f"Event created: '{summary}' at {start.isoformat()} | id={created['id']}")
        return CalendarEvent(
            event_id=created["id"],
            summary=created.get("summary", summary),
            description=created.get("description", description),
            start=start,
            end=end,
            attendees=attendee_emails,
            status=created.get("status", "confirmed"),
            meet_link=meet_link,
            html_link=created.get("htmlLink"),
        )
    except HttpError as e:
        logger.error(f"Failed to create event '{summary}': {e}")
        raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def cancel_event(
    event_id: str,
    calendar_id: str = "primary",
) -> None:
    """
    Cancel (delete) a Google Calendar event and notify attendees.

    Args:
        event_id:    Google Calendar event ID.
        calendar_id: Calendar containing the event.
    """
    service = _build_service()
    try:
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
            sendUpdates="all",
        ).execute()
        logger.info(f"Event {event_id} cancelled — attendees notified.")
    except HttpError as e:
        if e.resp.status == 404:
            logger.warning(f"Event {event_id} not found — may already be deleted.")
        else:
            logger.error(f"Failed to cancel event {event_id}: {e}")
            raise


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def reschedule_event(
    event_id: str,
    new_start: datetime,
    duration_minutes: int = 60,
    tz_name: str = "UTC",
    calendar_id: str = "primary",
) -> CalendarEvent:
    """
    Reschedule an existing event by patching its start/end times in place.
    All other event fields (attendees, Meet link, title) are preserved.
    Attendees receive an update notification automatically.

    Args:
        event_id:         Google Calendar event ID.
        new_start:        New start datetime (timezone-aware).
        duration_minutes: Duration of the rescheduled meeting.
        tz_name:          IANA timezone string.
        calendar_id:      Calendar containing the event.

    Returns:
        Updated CalendarEvent.
    """
    service = _build_service()
    new_end = new_start + timedelta(minutes=duration_minutes)

    try:
        existing = service.events().get(
            calendarId=calendar_id,
            eventId=event_id,
        ).execute()

        existing["start"] = {"dateTime": new_start.isoformat(), "timeZone": tz_name}
        existing["end"]   = {"dateTime": new_end.isoformat(),   "timeZone": tz_name}

        updated = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=existing,
            sendUpdates="all",
        ).execute()

        entry_points = updated.get("conferenceData", {}).get("entryPoints", [])
        meet_link    = entry_points[0].get("uri") if entry_points else None
        attendees    = [a["email"] for a in updated.get("attendees", [])]

        logger.info(f"Event {event_id} rescheduled → {new_start.isoformat()}")
        return CalendarEvent(
            event_id=updated["id"],
            summary=updated.get("summary", ""),
            description=updated.get("description", ""),
            start=new_start,
            end=new_end,
            attendees=attendees,
            status=updated.get("status", "confirmed"),
            meet_link=meet_link,
            html_link=updated.get("htmlLink"),
        )
    except HttpError as e:
        logger.error(f"Failed to reschedule event {event_id}: {e}")
        raise


def get_event(
    event_id: str,
    calendar_id: str = "primary",
) -> Optional[CalendarEvent]:
    """
    Fetch a single Calendar event by its ID.

    Returns:
        CalendarEvent if found, None if deleted/not found.
    """
    service = _build_service()
    try:
        ev = service.events().get(calendarId=calendar_id, eventId=event_id).execute()

        start_raw = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
        end_raw   = ev.get("end",   {}).get("dateTime") or ev.get("end",   {}).get("date", "")

        start = datetime.fromisoformat(start_raw).astimezone(dt_timezone.utc) if start_raw else datetime.now(dt_timezone.utc)
        end   = datetime.fromisoformat(end_raw).astimezone(dt_timezone.utc)   if end_raw   else start + timedelta(hours=1)

        entry_points = ev.get("conferenceData", {}).get("entryPoints", [])
        meet_link    = entry_points[0].get("uri") if entry_points else None

        return CalendarEvent(
            event_id=ev["id"],
            summary=ev.get("summary", ""),
            description=ev.get("description", ""),
            start=start,
            end=end,
            attendees=[a["email"] for a in ev.get("attendees", [])],
            status=ev.get("status", "confirmed"),
            meet_link=meet_link,
            html_link=ev.get("htmlLink"),
        )
    except HttpError as e:
        if e.resp.status == 404:
            logger.warning(f"Event {event_id} not found.")
            return None
        logger.error(f"Failed to fetch event {event_id}: {e}")
        raise


# ── Utility ───────────────────────────────────────────────────────────────────

def format_slots_for_email(slots: list[TimeSlot], tz_name: str = "UTC") -> str:
    """
    Format a list of TimeSlots into a numbered list for use in email body.

    Example:
        1. Monday, May 20 2026 at 10:00 AM IST (60 min)
        2. Tuesday, May 21 2026 at 02:00 PM IST (60 min)
    """
    if not slots:
        return "No available slots found at this time."
    return "\n".join(
        f"{i}. {slot.to_display(tz_name)} ({slot.duration_minutes()} min)"
        for i, slot in enumerate(slots, 1)
    )
