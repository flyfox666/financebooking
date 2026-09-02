import traceback

from app.core.database import SessionLocal
from app.ledger.ai.agent import run_agent

with SessionLocal() as db:
    try:
        result = run_agent(db, book_id=1, history=[], doc_context="测试上下文")
        print("OK reply:", str(result["reply"])[:200])
        print("trace:", [(t["tool"], str(t["arguments"])[:80]) for t in result["trace"]])
    except Exception:
        traceback.print_exc()
