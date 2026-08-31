from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.ledger import attachment_service
from app.ledger.exceptions import LedgerError
from app.models.user import User
from app.models.voucher import Voucher
from app.schemas.attachment import AttachmentOut

router = APIRouter(prefix="/api/vouchers", tags=["attachments"])


def _load_voucher_or_404(db: Session, voucher_id: int) -> Voucher:
    voucher = db.get(Voucher, voucher_id)
    if voucher is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    return voucher


@router.post("/{voucher_id}/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    voucher_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    voucher = _load_voucher_or_404(db, voucher_id)
    content = await file.read()
    try:
        return attachment_service.save_attachment(
            db,
            voucher=voucher,
            content=content,
            original_filename=file.filename or "",
            content_type=file.content_type or "",
            operator_id=user.id,
        )
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{voucher_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(
    voucher_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _load_voucher_or_404(db, voucher_id)
    return attachment_service.list_attachments(db, voucher_id)


@router.get("/{voucher_id}/attachments/{attachment_id}/download")
def download_attachment(
    voucher_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        attachment = attachment_service.get_attachment(db, voucher_id, attachment_id)
        content = attachment_service.read_attachment_file(attachment)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    quoted = quote(attachment.original_filename)
    return Response(
        content=content,
        media_type=attachment.content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
    )


@router.delete("/{voucher_id}/attachments/{attachment_id}", status_code=204)
def delete_attachment(
    voucher_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    voucher = _load_voucher_or_404(db, voucher_id)
    try:
        attachment_service.delete_attachment(db, voucher=voucher, attachment_id=attachment_id)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
