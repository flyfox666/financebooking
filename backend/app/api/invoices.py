from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.ledger import invoice_service
from app.ledger.exceptions import LedgerError
from app.models.user import User
from app.schemas.tax import ImportResultOut, InvoiceOut

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


@router.post("/import", response_model=ImportResultOut)
async def import_invoices(
    book_id: int,
    kind: str = Query(default="sales", pattern="^(sales|purchase)$"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    content = await file.read()
    try:
        return invoice_service.import_invoices(db, book_id=book_id, kind=kind, content=content)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("", response_model=list[InvoiceOut])
def list_invoices(
    book_id: int,
    kind: str | None = None,
    period: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return invoice_service.list_invoices(db, book_id=book_id, kind=kind, period=period)
