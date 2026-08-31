from pydantic import BaseModel


class AccountOut(BaseModel):
    id: int
    code: str
    name: str
    category: str
    direction: int
    parent_code: str | None
    level: int
    is_active: bool
    is_leaf: bool
    is_preset: bool
    aux_types: str = ""

    model_config = {"from_attributes": True}


class AccountNode(AccountOut):
    children: list["AccountNode"] = []


class AccountCreate(BaseModel):
    book_id: int
    parent_code: str
    code: str
    name: str


class AccountPatch(BaseModel):
    is_active: bool
