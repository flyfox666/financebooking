import hashlib
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.ledger.exceptions import VoucherError
from app.models.attachment import Attachment
from app.models.voucher import Voucher

MAX_FILE_SIZE = 10 * 1024 * 1024
EDITABLE_STATUSES = ("draft", "submitted", "audited")
SUFFIX_RE = re.compile(r"^[.A-Za-z0-9]{0,10}$")


def _ensure_editable(voucher: Voucher) -> None:
    if voucher.status not in EDITABLE_STATUSES:
        raise VoucherError("凭证已过账或已冲销，附件已锁定")


def _attachment_path(attachment: Attachment) -> Path:
    return Path(get_settings().ATTACHMENTS_DIR) / attachment.file_path


def _sync_attachment_count(db: Session, voucher: Voucher) -> None:
    count = db.scalar(
        select(func.count()).select_from(Attachment).where(Attachment.voucher_id == voucher.id)
    )
    voucher.attachment_count = count or 0


def list_attachments(db: Session, voucher_id: int) -> list[Attachment]:
    return list(
        db.scalars(
            select(Attachment)
            .where(Attachment.voucher_id == voucher_id)
            .order_by(Attachment.id)
        )
    )


def get_attachment(db: Session, voucher_id: int, attachment_id: int) -> Attachment:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.voucher_id != voucher_id:
        raise VoucherError("附件不存在")
    return attachment


def save_attachment(
    db: Session,
    *,
    voucher: Voucher,
    content: bytes,
    original_filename: str,
    content_type: str,
    operator_id: int,
) -> Attachment:
    _ensure_editable(voucher)
    if not content:
        raise VoucherError("附件内容为空")
    if len(content) > MAX_FILE_SIZE:
        raise VoucherError("附件大小不能超过 10MB")
    suffix = Path(original_filename or "").suffix[:10]
    if suffix and not SUFFIX_RE.fullmatch(suffix):
        suffix = ""
    relative_dir = Path(str(voucher.book_id)) / voucher.period
    target_dir = Path(get_settings().ATTACHMENTS_DIR) / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_name = uuid4().hex + suffix
    (target_dir / stored_name).write_bytes(content)
    attachment = Attachment(
        voucher_id=voucher.id,
        file_path=str(relative_dir / stored_name),
        original_filename=(original_filename or stored_name)[:200],
        content_type=(content_type or "application/octet-stream")[:100],
        file_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        uploaded_by=operator_id,
    )
    db.add(attachment)
    db.flush()
    _sync_attachment_count(db, voucher)
    db.commit()
    db.refresh(attachment)
    return attachment


def delete_attachment(db: Session, *, voucher: Voucher, attachment_id: int) -> None:
    _ensure_editable(voucher)
    attachment = get_attachment(db, voucher.id, attachment_id)
    path = _attachment_path(attachment)
    if path.exists():
        path.unlink()
    db.delete(attachment)
    db.flush()
    _sync_attachment_count(db, voucher)
    db.commit()


def purge_voucher_attachments(db: Session, voucher: Voucher) -> None:
    for attachment in list_attachments(db, voucher.id):
        path = _attachment_path(attachment)
        if path.exists():
            path.unlink()
        db.delete(attachment)
    db.flush()


def read_attachment_file(attachment: Attachment) -> bytes:
    path = _attachment_path(attachment)
    if not path.exists():
        raise VoucherError("附件文件已丢失")
    return path.read_bytes()
