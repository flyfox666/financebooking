from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db
from app.ledger import aux_service
from app.ledger.exceptions import LedgerError
from app.models.user import User

router = APIRouter(prefix="/api/contacts", tags=["contacts"])


@router.get("")
def list_contacts(
    book_id: int,
    ctype: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return [
        {
            "id": c.id,
            "name": c.name,
            "tax_no": c.tax_no,
            "ctype": c.ctype,
            "is_active": c.is_active,
        }
        for c in aux_service.list_contacts(db, book_id, ctype)
    ]


@router.post("", status_code=201)
def create_contact(
    book_id: int,
    name: str,
    ctype: Literal["customer", "supplier"] = "customer",
    tax_no: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        contact = aux_service.create_contact(db, book_id=book_id, name=name, ctype=ctype, tax_no=tax_no)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": contact.id, "name": contact.name, "tax_no": contact.tax_no, "ctype": contact.ctype, "is_active": contact.is_active}


@router.patch("/{contact_id}")
def update_contact(
    contact_id: int,
    name: str | None = None,
    tax_no: str | None = None,
    ctype: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        contact = aux_service.update_contact(
            db, contact_id, name=name, tax_no=tax_no, ctype=ctype, is_active=is_active
        )
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"id": contact.id, "name": contact.name, "tax_no": contact.tax_no, "ctype": contact.ctype, "is_active": contact.is_active}
