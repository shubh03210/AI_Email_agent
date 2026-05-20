"""
Metrics API
────────────
Single aggregate endpoint returning pipeline KPIs for the dashboard.

GET /api/v1/metrics/  — admin, operator

Metrics returned:
  total_prospects        — all prospects in the system
  outreach_sent          — prospects that were contacted (left pending state)
  responses_received     — prospects who showed positive signal (replied)
  response_rate          — responses_received / outreach_sent  (0–1)
  meetings_booked        — confirmed + rescheduled + completed meetings
  booking_conversion     — meetings_booked / outreach_sent  (0–1)
  negotiations_resolved  — negotiations that reached a definitive outcome
  negotiation_success    — accepted / resolved_negotiations  (0–1)
  reschedule_pct         — rescheduled_meetings / total_outcomes  (0–1)
  walkaway_pct           — walkaway negotiations / resolved_negotiations (0–1)
  active_negotiations    — negotiations currently in ACTIVE or PENDING state
  pipeline               — per-status prospect counts {status: count}
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_db
from app.models.meeting import Meeting, MeetingStatus
from app.models.negotiation import Negotiation, NegotiationStatus
from app.models.prospect import Prospect, ProspectStatus

router = APIRouter()


def _safe_rate(numerator: int, denominator: int) -> float:
    """Return numerator/denominator rounded to 4 dp, or 0.0 when denominator is 0."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


@router.get(
    "/",
    summary="Pipeline KPIs for the dashboard",
)
async def get_metrics(db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    """
    Aggregate pipeline metrics computed from Prospect, Meeting, and Negotiation
    tables.  All counts are computed in two lightweight DB queries.
    """

    # ── Prospect pipeline counts ───────────────────────────────────────────────
    pipeline_result = await db.execute(
        select(Prospect.status, func.count(Prospect.id).label("cnt"))
        .group_by(Prospect.status)
    )
    pipeline: Dict[str, int] = {}
    for row in pipeline_result.all():
        pipeline[row.status] = row.cnt

    total_prospects: int = sum(pipeline.values())

    # Outreach sent = any status beyond "pending" (we left the door)
    outreach_sent: int = sum(
        v for k, v in pipeline.items()
        if k != ProspectStatus.PENDING.value
    )

    # Positive responses = interested + negotiating + scheduled + closed
    # (declined means they replied but said no — we count them in outreach but
    #  not as positive responses)
    positive_statuses = {
        ProspectStatus.INTERESTED.value,
        ProspectStatus.NEGOTIATING.value,
        ProspectStatus.SCHEDULED.value,
        ProspectStatus.CLOSED.value,
    }
    responses_received: int = sum(
        v for k, v in pipeline.items() if k in positive_statuses
    )

    # ── Meeting counts ─────────────────────────────────────────────────────────
    meeting_result = await db.execute(
        select(Meeting.status, func.count(Meeting.id).label("cnt"))
        .group_by(Meeting.status)
    )
    meeting_counts: Dict[str, int] = {}
    for row in meeting_result.all():
        meeting_counts[row.status] = row.cnt

    total_meetings: int = sum(meeting_counts.values())
    meetings_booked: int = sum(
        meeting_counts.get(s, 0)
        for s in (
            MeetingStatus.CONFIRMED.value,
            MeetingStatus.RESCHEDULED.value,
            MeetingStatus.COMPLETED.value,
        )
    )
    rescheduled_count: int = meeting_counts.get(MeetingStatus.RESCHEDULED.value, 0)
    # denominator for reschedule %: all meetings that had some outcome
    meetings_with_outcome: int = sum(
        meeting_counts.get(s, 0)
        for s in (
            MeetingStatus.CONFIRMED.value,
            MeetingStatus.RESCHEDULED.value,
            MeetingStatus.COMPLETED.value,
            MeetingStatus.CANCELLED.value,
        )
    )

    # ── Negotiation counts ─────────────────────────────────────────────────────
    neg_result = await db.execute(
        select(Negotiation.status, func.count(Negotiation.id).label("cnt"))
        .group_by(Negotiation.status)
    )
    neg_counts: Dict[str, int] = {}
    for row in neg_result.all():
        neg_counts[row.status] = row.cnt

    active_negotiations: int = sum(
        neg_counts.get(s, 0)
        for s in (NegotiationStatus.ACTIVE.value, NegotiationStatus.PENDING.value)
    )
    accepted_negotiations: int = neg_counts.get(NegotiationStatus.ACCEPTED.value, 0)
    walkaway_negotiations: int = neg_counts.get(NegotiationStatus.WALKAWAY.value, 0)
    # resolved = had a definitive outcome
    resolved_negotiations: int = (
        accepted_negotiations
        + neg_counts.get(NegotiationStatus.REJECTED.value, 0)
        + walkaway_negotiations
    )

    return {
        "total_prospects":       total_prospects,
        "outreach_sent":         outreach_sent,
        "responses_received":    responses_received,
        "response_rate":         _safe_rate(responses_received, outreach_sent),
        "meetings_booked":       meetings_booked,
        "booking_conversion":    _safe_rate(meetings_booked, outreach_sent),
        "active_negotiations":   active_negotiations,
        "negotiations_resolved": resolved_negotiations,
        "negotiation_success":   _safe_rate(accepted_negotiations, resolved_negotiations),
        "reschedule_pct":        _safe_rate(rescheduled_count, meetings_with_outcome),
        "walkaway_pct":          _safe_rate(walkaway_negotiations, resolved_negotiations),
        "pipeline":              pipeline,
        "meetings_by_status":    meeting_counts,
    }
