from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_threads():
    return []


@router.get("/{thread_id}")
async def get_thread(thread_id: str):
    pass


@router.post("/{thread_id}/process")
async def process_thread(thread_id: str):
    pass
