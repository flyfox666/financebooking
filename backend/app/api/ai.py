import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import get_db
from app.ledger.ai import parse as ai_parse
from app.ledger.ai import suggest as ai_suggest
from app.ledger.exceptions import LedgerError
from app.models.ai import AIDoc
from app.models.user import User
from app.schemas.ai import ChatIn, ConfirmIn, SuggestIn
from app.schemas.voucher import VoucherOut

router = APIRouter(prefix="/api/ai", tags=["ai"])


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


@router.post("/parse")
async def parse_document(
    book_id: int,
    file: UploadFile | None = File(default=None),
    note: str = Form(default=""),
    allow_vlm: bool = Form(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if file is None and not note.strip():
        raise HTTPException(status_code=400, detail="请上传单据文件或输入业务描述")

    if file is not None:
        content = await file.read()
        filename = file.filename or ""
        try:
            result = ai_parse.route_and_parse(db, filename=filename, content=content, allow_vlm=allow_vlm)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

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
        return {"doc_id": doc.id, **result}

    doc = AIDoc(
        book_id=book_id,
        doc_type="text",
        source_kind="text",
        fields_json=json.dumps({"note": note.strip()}, ensure_ascii=False),
        status="parsed",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {
        "doc_id": doc.id,
        "doc_type": "text",
        "source_kind": "text",
        "fields": {"note": note.strip()},
        "warnings": [],
        "layers": ["text"],
    }


@router.post("/suggest")
def suggest_voucher(
    body: SuggestIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
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
    }


@router.post("/chat")
def chat_with_agent(
    body: ChatIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
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
    try:
        voucher = ai_suggest.confirm_suggestion(
            db,
            doc_id=body.doc_id,
            voucher_date=body.voucher_date,
            lines=body.lines,
            operator_id=user.id,
        )
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return voucher


@router.get("/documents")
def list_documents(
    book_id: int,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
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
    try:
        ai_suggest.discard_document(db, doc_id)
    except LedgerError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"doc_id": doc_id, "status": "discarded"}
