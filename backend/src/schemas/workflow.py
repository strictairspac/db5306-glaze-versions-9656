from typing import Any

from pydantic import BaseModel, Field


class LoginForm(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class CaseForm(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]


class ReviewForm(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class RenumberChangeForm(BaseModel):
    formula_id: str = Field(min_length=1, max_length=80)
    glaze_no: str | None = Field(default=None, max_length=80)


class RenumberForm(BaseModel):
    commit_seq: int = Field(ge=1, le=2**53 - 1)
    changes: list[RenumberChangeForm] = Field(min_length=1, max_length=200)


class LookupForm(BaseModel):
    glaze_no: str = Field(min_length=1, max_length=80)
    read_seq: int = Field(ge=0, le=2**53 - 1)
