import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_book_access, require_bookkeeper
from app.core.config import get_settings
from app.core.database import get_db
from app.ledger.ai import parse as ai_parse
from app.ledger.ai import suggest as ai_suggest
from app.ledger.exceptions import LedgerError, VoucherError
from app.models.ai import AIDoc
from app.models.user import User
from app.schemas.ai import ChatIn, ConfirmIn, SuggestIn
from app.schemas.voucher import VoucherOut

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get('/documents/{doc_id}/original')
def document_original(doc_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from fastapi.responses import FileResponse, Response
    from app.ledger.attachment_service import list_attachments, read_attachment_file
    doc = db.get(AIDoc, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail='单据不存在')
    require_book_access(doc.book_id, db=db, user=user)
    if doc.voucher_id:
        attachments = list_attachments(db, doc.voucher_id)
        if attachments:
            item = attachments[0]
            content = read_attachment_file(item)
            safe_type = item.content_type if item.content_type in ('application/pdf','image/png','image/jpeg','image/webp') else 'application/octet-stream'
            return Response(content, media_type=safe_type, headers={'X-Content-Type-Options':'nosniff'})
    path = Path(doc.staging_path) if doc.staging_path else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail='该记录没有可查看的原始文件')
    import mimetypes
    mime = mimetypes.guess_type(doc.file_name)[0]
    if mime not in ('application/pdf','image/png','image/jpeg','image/webp'):
        mime = 'application/octet-stream'
    return FileResponse(path, media_type=mime, headers={'X-Content-Type-Options':'nosniff'})


def _doc_summary(doc: AIDoc) -> dict:
    return {
        "id": doc.id,
        "book_id": doc.book_id,
        "doc_type": doc.doc_type,
        "source_kind": doc.source_kind,
        "file_name": doc.file_name,
        "status": doc.status,
        "voucher_id": doc.voucher_id,
        "fields": json.loads(doc.fields_json or "{}"),
        "warnings": json.loads(doc.warnings_json or "[]"),
        "layers": json.loads(doc.layers_json or "[]"),
        "model_output": json.loads(doc.model_output) if doc.model_output else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def _degraded_result(reason: str) -> dict:
    """解析失败时的降级结果：原件已存 staging，用户可在对话中补充描述走人工路径。"""
    return {
        "doc_type": "unknown",
        "source_kind": "file",
        "fields": {},
        "warnings": [f"自动识别失败（{reason}），原件已保存，请在对话中补充业务描述或手动录入凭证"],
        "layers": [],
        "degraded": True,
    }


@router.post("/parse")
async def parse_document(
    book_id: int,
    file: UploadFile | None = File(default=None),
    note: str = Form(default=""),
    invoice_id: int | None = Form(default=None),
    allow_vlm: bool = Form(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
):
    if file is None and not note.strip():
        raise HTTPException(status_code=400, detail="请上传单据文件或输入业务描述")

    if file is not None:
        from app.ledger.attachment_service import MAX_FILE_SIZE
        content = await file.read(MAX_FILE_SIZE + 1)
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail='单据文件不能超过10MB')
        filename = file.filename or ""
        # 解析失败是正常业务路径而非异常：除"文件类型不识别"外一律降级，绝不 500
        try:
            result = ai_parse.route_and_parse(db, filename=filename, content=content, allow_vlm=allow_vlm)
        except VoucherError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except LedgerError as exc:  # 视觉模型不可用/输出异常等
            result = _degraded_result(str(exc))
        except Exception:  # noqa: BLE001 - 兜住一切内部 bug（如漏 import 的 NameError）
            result = _degraded_result("内部解析异常")

        staging_path = ""
        if content:
            suffix = Path(filename or "").suffix.lower()[:10]
            staging_dir = Path(get_settings().ATTACHMENTS_DIR) / "_staging"
            staging_dir.mkdir(parents=True, exist_ok=True)
            staging_path = str(staging_dir / (uuid.uuid4().hex + suffix))
            Path(staging_path).write_bytes(content)

        doc = AIDoc(
            book_id=book_id,
            doc_type=result["doc_type"],
            source_kind=result["source_kind"],
            file_name=filename[:200],
            staging_path=staging_path,
            fields_json=json.dumps(result["fields"], ensure_ascii=False),
            warnings_json=json.dumps(result["warnings"], ensure_ascii=False),
            layers_json=json.dumps(result["layers"], ensure_ascii=False),
            status="parsed",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        # 多票据同图：其余候选各建一条解析记录，前端逐张走 agent 流程
        extra_doc_ids: list[int] = []
        for candidate in result.get("extra_docs") or []:
            extra = AIDoc(
                book_id=book_id,
                doc_type=candidate["doc_type"],
                source_kind=result["source_kind"],
                file_name=filename[:200],
                staging_path=staging_path,
                fields_json=json.dumps(candidate["fields"], ensure_ascii=False),
                warnings_json=json.dumps(result["warnings"], ensure_ascii=False),
                layers_json=json.dumps(result["layers"], ensure_ascii=False),
                status="parsed",
            )
            db.add(extra)
            db.commit()
            db.refresh(extra)
            extra_doc_ids.append(extra.id)
        return {"doc_id": doc.id, "extra_doc_ids": extra_doc_ids, **result}

    fields = {"note": note.strip()}
    if invoice_id is not None:
        from app.models.tax import Invoice
        invoice = db.get(Invoice, invoice_id)
        if invoice is None or invoice.book_id != book_id:
            raise HTTPException(status_code=404, detail='发票不存在')
        if invoice.voucher_id:
            raise HTTPException(status_code=409, detail='发票已经关联凭证')
        for key in ('invoice_no','invoice_date','buyer_name','buyer_tax_no','seller_name','seller_tax_no','goods_name','amount_total','status'):
            fields[key] = str(getattr(invoice, key, '') or '')
        fields['confirmed_kind'] = invoice.kind
        fields['ledger_invoice_id'] = invoice.id
    doc = AIDoc(
        book_id=book_id,
        doc_type="invoice" if invoice_id is not None else "text",
        source_kind="text",
        fields_json=json.dumps(fields, ensure_ascii=False),
        status="parsed",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {
        "doc_id": doc.id,
        "doc_type": doc.doc_type,
        "source_kind": "text",
        "fields": fields,
        "warnings": [],
        "layers": ["text"],
    }


@router.post("/suggest")
def suggest_voucher(
    body: SuggestIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_book_access(body.book_id, db=db, user=user)
    doc = None
    doc_type = "text"
    fields: dict = {}
    note = body.note or ""
    if body.doc_id is not None:
        doc = db.get(AIDoc, body.doc_id)
        if doc is None or doc.book_id != body.book_id:
            raise HTTPException(status_code=404, detail="AI 解析记录不存在")
        if doc.status == "confirmed":
            raise HTTPException(status_code=400, detail="该记录已确认落账")
        doc_type = doc.doc_type
        fields = json.loads(doc.fields_json or "{}")
        note = fields.get("note", "")

    try:
        result = ai_suggest.suggest_voucher(
            db, book_id=body.book_id, doc_type=doc_type, fields=fields, note=note
        )
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if doc is not None:
        doc.model_output = json.dumps(result["voucher"], ensure_ascii=False)
        doc.warnings_json = json.dumps(
            json.loads(doc.warnings_json or "[]") + result["warnings"], ensure_ascii=False
        )
        doc.status = "suggested"
        db.commit()
        doc_id = doc.id
    else:
        doc_id = None

    return {
        "doc_id": doc_id,
        "voucher": result["voucher"],
        "warnings": result["warnings"],
        "confidence": result["confidence"],
        "validation_status": result["validation_status"],
    }


@router.post("/chat")
def chat_with_agent(
    body: ChatIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_book_access(body.book_id, db=db, user=user)
    from app.ledger.ai.agent import run_agent

    doc_context = ""
    doc = None
    if body.doc_id is not None:
        doc = db.get(AIDoc, body.doc_id)
        if doc is None or doc.book_id != body.book_id:
            raise HTTPException(status_code=404, detail="AI 解析记录不存在")
        if doc.status == "confirmed":
            raise HTTPException(status_code=400, detail="该记录已确认落账")
        fields = json.loads(doc.fields_json or "{}")
        warnings = json.loads(doc.warnings_json or "[]")
        doc_context = json.dumps(
            {"doc_type": doc.doc_type, "file_name": doc.file_name, "fields": fields, "warnings": warnings},
            ensure_ascii=False,
        )

    history = [
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
        for m in body.history
        if m.get("role") in ("user", "assistant") and m.get("content")
    ][-12:]

    try:
        result = run_agent(db, book_id=body.book_id, history=history, doc_context=doc_context)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    doc_id = None
    voucher_saved = False
    if doc is not None and result.get("voucher"):
        doc.model_output = json.dumps(result["voucher"], ensure_ascii=False)
        doc.status = "suggested"
        db.commit()
        doc_id = doc.id
        voucher_saved = True

    return {
        "doc_id": doc_id,
        "doc_type": doc.doc_type if doc is not None else None,
        "reply": result["reply"],
        "voucher": result.get("voucher"),
        "trace": result["trace"],
        "stopped_by_ask_user": result["stopped_by_ask_user"],
        "voucher_saved": voucher_saved,
    }


@router.post("/confirm", response_model=VoucherOut, status_code=201)
def confirm_suggestion(
    body: ConfirmIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_bookkeeper(user)
    doc = db.get(AIDoc, body.doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="AI 解析记录不存在")
    require_book_access(doc.book_id, db=db, user=user)
    try:
        voucher = ai_suggest.confirm_suggestion(
            db,
            doc_id=body.doc_id,
            voucher_date=body.voucher_date,
            lines=body.lines,
            operator_id=user.id,
            invoice_kind=body.invoice_kind,
            risk_fingerprint=body.risk_fingerprint,
            risk_reason=body.risk_reason,
        )
    except LedgerError as exc:
        from app.ledger.ai.confirmation import CandidateRisk
        if isinstance(exc, CandidateRisk):
            raise HTTPException(status_code=409, detail=exc.detail)
        raise HTTPException(status_code=400, detail=str(exc))
    return voucher


@router.get("/documents")
def list_documents(
    book_id: int,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_book_access),
):
    stmt = select(AIDoc).where(AIDoc.book_id == book_id)
    if status:
        stmt = stmt.where(AIDoc.status == status)
    stmt = stmt.order_by(AIDoc.id.desc())
    docs = db.scalars(stmt).all()
    return [_doc_summary(doc) for doc in docs]


@router.post("/documents/{doc_id}/discard")
def discard_document(
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_bookkeeper(user)
    doc = db.get(AIDoc, doc_id)
    if doc is not None:
        require_book_access(doc.book_id, db=db, user=user)
    try:
        ai_suggest.discard_document(db, doc_id)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"doc_id": doc_id, "status": "discarded"}
