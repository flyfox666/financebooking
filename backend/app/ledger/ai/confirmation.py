"""Deterministic, transactional confirmation of a document candidate."""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import mimetypes

from sqlalchemy import select, update
from app.models.ai import AIDoc
from app.models.book import Book
from app.models.voucher import Voucher
from app.models.tax import Invoice
from app.ledger.exceptions import VoucherError
from app.ledger.attachment_service import save_attachment, MAX_FILE_SIZE
from app.ledger.voucher_service import create_voucher


class CandidateRisk(VoucherError):
    def __init__(self, warnings, fingerprint):
        super().__init__('请核对风险并填写确认理由（至少4字）')
        self.detail = {'message': str(self), 'warnings': warnings, 'risk_fingerprint': fingerprint}


def party_kind(book, fields):
    if book is None:
        return None
    def matches(party):
        tax = str(fields.get(party + '_tax_no') or '').strip().upper()
        if tax and book.tax_no:
            return tax == book.tax_no.strip().upper()
        name = str(fields.get(party + '_name') or '').strip()
        return bool(name and name == (book.name or '').strip())
    buyer, seller = matches('buyer'), matches('seller')
    return ('purchase' if buyer else 'sales') if buyer != seller else None


def invoice_number(fields):
    import re
    number = str(fields.get('invoice_no') or '').strip()
    if not number:
        match = re.search(r'发票号[码]?\s*[：: ]?\s*(\d{20}|\d{8})', str(fields.get('note') or ''))
        number = match.group(1) if match else ''
    return number


def resolve_kind(db, book_id, fields):
    detected = party_kind(db.get(Book, book_id), fields)
    confirmed = fields.get('confirmed_kind')
    if detected and confirmed and detected != confirmed:
        raise VoucherError('所选发票方向与票面主体不一致')
    if detected or confirmed in ('sales', 'purchase'):
        return detected or confirmed
    # A unique existing ledger identity is evidence, not a guessed direction.
    number = invoice_number(fields)
    if number:
        kinds = list(db.scalars(select(Invoice.kind).where(Invoice.book_id == book_id, Invoice.invoice_no == number)))
        if len(kinds) == 1:
            return kinds[0]
    return None


def cleanup_staging(db, doc):
    if not doc.staging_path:
        return
    active = db.scalar(select(AIDoc.id).where(AIDoc.staging_path == doc.staging_path,
        AIDoc.status.in_(['parsed', 'suggested', 'confirming'])))
    if active is None:
        try:
            Path(doc.staging_path).unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning('Temporary cleanup deferred: document %s', doc.id)


def confirm(db, **kwargs):
    # Serialize validation and writing, so two requests cannot both pass a stale check.
    try:
        connection = db.connection()
        if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        return _confirm_locked(db, **kwargs)
    except Exception:
        db.rollback()
        raise


def _confirm_locked(db, *, doc_id, voucher_date, lines, operator_id, invoice_kind=None,
            risk_fingerprint=None, risk_reason=''):
    from app.ledger.ai.suggest import validate_candidate, _link_invoice, _dec
    from app.ledger.ai.agent import _hard_check_duplicate
    doc = db.get(AIDoc, doc_id, populate_existing=True)
    if doc is None:
        raise VoucherError('AI 解析记录不存在')
    request_hash = hashlib.sha256(json.dumps({'date':str(voucher_date), 'lines':lines, 'kind':invoice_kind},
        sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
    if doc.status == 'confirmed' and doc.voucher_id:
        if json.loads(doc.confirmation_json or '{}').get('request_hash') == request_hash:
            voucher = db.get(Voucher, doc.voucher_id)
            if voucher is not None:
                return voucher
        raise VoucherError('该记录已确认，请到凭证列表查看')
    if doc.status not in ('parsed', 'suggested'):
        raise VoucherError('该记录已确认或已废弃')
    fields = json.loads(doc.fields_json or '{}')
    if invoice_kind is not None:
        if invoice_kind not in ('sales', 'purchase'):
            raise VoucherError('请选择有效的发票方向')
        fields['confirmed_kind'] = invoice_kind
    kind = resolve_kind(db, doc.book_id, fields)
    number = invoice_number(fields)
    if number:
        has_identity = db.scalar(select(Invoice.id).where(Invoice.book_id == doc.book_id, Invoice.invoice_no == number))
        if not kind and (doc.source_kind != 'text' or has_identity):
            raise VoucherError('无法确定发票方向，请核对原件并选择进项或销项')
        existing = db.scalar(select(Invoice).where(Invoice.book_id == doc.book_id,
            Invoice.kind == kind, Invoice.invoice_no == number))
        if existing and existing.voucher_id:
            raise VoucherError('该方向的发票已经关联凭证，不能重复入账')
    book = db.get(Book, doc.book_id)
    if kind == 'purchase' and book.taxpayer_type == 'small_scale':
        if any(str(l.get('account_code', '')).startswith('2221') and _dec(l.get('debit', 0)) for l in lines):
            raise VoucherError('小规模纳税人进项不可拆税，请将价税合计计入费用或资产')
    payload = {'voucher_date': str(voucher_date), 'lines': lines}
    errors, warnings = validate_candidate(db, doc.book_id, payload, fields if doc.doc_type == 'invoice' else None)
    if errors:
        raise VoucherError('；'.join(errors))
    duplicate = _hard_check_duplicate(db, doc.book_id, payload)
    if duplicate:
        warnings.append(duplicate)
    if fields.get('confirmed_kind') and not party_kind(book, fields):
        warnings.append('票面主体未匹配本账套，发票方向由人工指定，请核对原件')
    fingerprint = hashlib.sha256(json.dumps([request_hash, fields, warnings], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if warnings and (risk_fingerprint != fingerprint or len(risk_reason.strip()) < 4):
        raise CandidateRisk(warnings, fingerprint)
    path = Path(doc.staging_path) if doc.staging_path else None
    if path and not path.is_file():
        raise VoucherError('原始单据已丢失，请重新上传')
    content = path.read_bytes() if path else None
    if content is not None and (not content or len(content) > MAX_FILE_SIZE):
        raise VoucherError('原始单据为空或超过10MB，请重新上传')
    stored_path = None
    try:
        claimed = db.execute(update(AIDoc).where(AIDoc.id == doc_id,
            AIDoc.status.in_(['parsed', 'suggested'])).values(status='confirming'))
        if claimed.rowcount != 1:
            db.rollback()
            raise VoucherError('该记录正在处理或已确认，请刷新查看')
        doc.fields_json = json.dumps(fields, ensure_ascii=False)
        voucher = create_voucher(db, book_id=doc.book_id, voucher_date=voucher_date,
            lines=lines, operator_id=operator_id, attachment_count=0, source='ai', commit=False)
        if content is not None:
            attachment = save_attachment(db, voucher=voucher, content=content,
                original_filename=doc.file_name or path.name,
                content_type=mimetypes.guess_type(doc.file_name or path.name)[0] or 'application/octet-stream',
                operator_id=operator_id, commit=False)
            from app.core.config import get_settings
            stored_path = Path(get_settings().ATTACHMENTS_DIR) / attachment.file_path
        doc.status = 'confirmed'
        doc.voucher_id = voucher.id
        doc.confirmation_json = json.dumps({'request_hash':request_hash,'risk_fingerprint':fingerprint,
            'warnings':warnings,'reason':risk_reason.strip(),'operator_id':operator_id,
            'confirmed_at':datetime.now(timezone.utc).isoformat()}, ensure_ascii=False)
        _link_invoice(db, doc=doc, voucher_id=voucher.id)
        db.commit()
        db.refresh(voucher)
    except Exception:
        db.rollback()
        if stored_path:
            stored_path.unlink(missing_ok=True)
        raise
    cleanup_staging(db, doc)
    return voucher
