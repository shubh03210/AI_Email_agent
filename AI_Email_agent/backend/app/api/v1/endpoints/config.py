from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def get_config():
    pass


@router.put("/")
async def update_config():
    pass
