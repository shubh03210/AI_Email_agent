from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_meetings():
    return []


@router.post("/")
async def create_meeting():
    pass


@router.get("/{meeting_id}")
async def get_meeting(meeting_id: int):
    pass


@router.put("/{meeting_id}/reschedule")
async def reschedule_meeting(meeting_id: int):
    pass
