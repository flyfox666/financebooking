from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_auditor_or_admin, require_book_access
from app.core.database import get_db
from app.ledger import close_service
from app.ledger.exceptions import LedgerError
from app.models.user import User

router = APIRouter(prefix="/api/periods", tags=["periods"])


@router.post("/{period}/close")
def close_period(
    period: str,
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_auditor_or_admin),
):
    try:
        return close_service.close_period(db, book_id=book_id, period=period, operator_id=user.id)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{period}/unclose")
def unclose_period(
    period: str,
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_auditor_or_admin),
):
    try:
        close_service.unclose_period(db, book_id=book_id, period=period)
        return {"period": period, "closed": False}
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
