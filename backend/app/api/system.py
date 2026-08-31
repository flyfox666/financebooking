from fastapi import APIRouter

router = APIRouter(tags=["system"])


@router.get("/api/health")
def health():
    return {"status": "ok", "app": "LedgerAI"}
