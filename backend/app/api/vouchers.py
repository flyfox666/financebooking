from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_auditor_or_admin
from app.core.database import get_db
from app.ledger import close_service, voucher_service
from app.ledger.exceptions import LedgerError
from app.models.user import User
from app.schemas.voucher import VoucherCreateIn, VoucherOut, VoucherUpdateIn

router = APIRouter(prefix="/api", tags=["vouchers"])


def _bad_request(exc: LedgerError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.post("/vouchers", response_model=VoucherOut, status_code=201)
def create_voucher(
    body: VoucherCreateIn,
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return voucher_service.create_voucher(
            db,
            book_id=book_id,
            voucher_date=body.voucher_date,
            attachment_count=body.attachment_count,
            source=body.source,
            lines=[line.model_dump() for line in body.lines],
            operator_id=user.id,
        )
    except LedgerError as exc:
        raise _bad_request(exc)


@router.get("/vouchers", response_model=list[VoucherOut])
def list_vouchers(
    book_id: int,
    period: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return voucher_service.list_vouchers(db, book_id=book_id, period=period, status=status)


@router.get("/vouchers/{voucher_id}", response_model=VoucherOut)
def get_voucher(
    voucher_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return voucher_service.get_voucher(db, voucher_id)
    except LedgerError as exc:
        raise _bad_request(exc)


@router.patch("/vouchers/{voucher_id}", response_model=VoucherOut)
def update_voucher(
    voucher_id: int,
    body: VoucherUpdateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return voucher_service.update_voucher(
            db,
            voucher_id=voucher_id,
            voucher_date=body.voucher_date,
            attachment_count=body.attachment_count,
            lines=[line.model_dump() for line in body.lines] if body.lines is not None else None,
        )
    except LedgerError as exc:
        raise _bad_request(exc)


@router.delete("/vouchers/{voucher_id}", status_code=204)
def delete_voucher(
    voucher_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        voucher_service.delete_voucher(db, voucher_id=voucher_id)
    except LedgerError as exc:
        raise _bad_request(exc)


@router.post("/vouchers/{voucher_id}/submit", response_model=VoucherOut)
def submit_voucher(
    voucher_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return voucher_service.submit_voucher(db, voucher_id=voucher_id, operator_id=user.id)
    except LedgerError as exc:
        raise _bad_request(exc)


@router.post("/vouchers/{voucher_id}/reject", response_model=VoucherOut)
def reject_voucher(
    voucher_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_auditor_or_admin),
):
    try:
        return voucher_service.reject_voucher(db, voucher_id=voucher_id, operator=user)
    except LedgerError as exc:
        raise _bad_request(exc)


@router.post("/vouchers/{voucher_id}/audit", response_model=VoucherOut)
def audit_voucher(
    voucher_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_auditor_or_admin),
):
    try:
        return voucher_service.audit_voucher(db, voucher_id=voucher_id, operator=user)
    except LedgerError as exc:
        raise _bad_request(exc)


@router.post("/vouchers/{voucher_id}/post", response_model=VoucherOut)
def post_voucher(
    voucher_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_auditor_or_admin),
):
    try:
        return voucher_service.post_voucher(db, voucher_id=voucher_id, operator=user)
    except LedgerError as exc:
        raise _bad_request(exc)


@router.post("/vouchers/{voucher_id}/unpost", response_model=VoucherOut)
def unpost_voucher(
    voucher_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_auditor_or_admin),
):
    try:
        return voucher_service.unpost_voucher(db, voucher_id=voucher_id)
    except LedgerError as exc:
        raise _bad_request(exc)


@router.post("/vouchers/{voucher_id}/reverse", response_model=VoucherOut, status_code=201)
def reverse_voucher(
    voucher_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_auditor_or_admin),
):
    try:
        return voucher_service.reverse_voucher(db, voucher_id=voucher_id, operator=user)
    except LedgerError as exc:
        raise _bad_request(exc)


@router.post("/periods/{period}/carryover", response_model=list[VoucherOut], status_code=201)
def generate_carryover(
    period: str,
    book_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_auditor_or_admin),
):
    try:
        return close_service.generate_carryover(db, book_id=book_id, period=period, operator_id=user.id)
    except LedgerError as exc:
        raise _bad_request(exc)
