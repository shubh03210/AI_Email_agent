from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_prospects():
    return []


@router.post("/")
async def create_prospect():
    pass


@router.get("/{prospect_id}")
async def get_prospect(prospect_id: int):
    pass


@router.delete("/{prospect_id}")
async def delete_prospect(prospect_id: int):
    pass
