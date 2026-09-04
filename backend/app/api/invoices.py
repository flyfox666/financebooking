from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_book_access
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
    user: User = Depends(require_book_access),
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
    user: User = Depends(require_book_access),
):
    invoices = invoice_service.list_invoices(db, book_id=book_id, kind=kind, period=period)
    voucher_ids = {inv.voucher_id for inv in invoices if inv.voucher_id}
    vouchers = {}
    if voucher_ids:
        from app.models.voucher import Voucher

        for v in db.scalars(select(Voucher).where(Voucher.id.in_(voucher_ids))):
            vouchers[v.id] = v.voucher_no_display
    out = []
    for inv in invoices:
        item = InvoiceOut.model_validate(inv)
        item.voucher_no_display = vouchers.get(inv.voucher_id)
        out.append(item)
    return out
