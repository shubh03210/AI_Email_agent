from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    password: str = Field(..., min_length=8, max_length=128)
    role: str = Field(default="operator", pattern="^(admin|operator)$")


class UserRead(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    last_login_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
