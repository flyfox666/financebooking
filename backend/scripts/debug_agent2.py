import json
import traceback

from sqlalchemy import select

from app.core.database import SessionLocal
from app.ledger.ai.agent import run_agent
from app.models.ai import AIDoc

with SessionLocal() as db:
    doc = db.scalar(select(AIDoc).where(AIDoc.status != "confirmed").order_by(AIDoc.id.desc()))
    if doc is None:
        print("no unconfirmed doc")
        raise SystemExit(0)
    fields = json.loads(doc.fields_json or "{}")
    warnings = json.loads(doc.warnings_json or "[]")
    doc_context = json.dumps(
        {"doc_type": doc.doc_type, "file_name": doc.file_name, "fields": fields, "warnings": warnings},
        ensure_ascii=False,
    )
    history = [{"role": "user", "content": fields.get("note", "")}]
    print(f"doc_id={doc.id} doc_type={doc.doc_type} fields={fields}")
    try:
        result = run_agent(db, book_id=doc.book_id, history=history, doc_context=doc_context)
        print("OK reply:", str(result["reply"])[:300])
        print("trace:", [(t["tool"], str(t["arguments"])[:100]) for t in result["trace"]])
        print("voucher:", json.dumps(result.get("voucher"), ensure_ascii=False)[:500])
    except Exception:
        traceback.print_exc()
