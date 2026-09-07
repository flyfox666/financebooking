from fastapi import APIRouter, Depends, HTTPException
from app.api.deps import require_admin

router = APIRouter(tags=["system"])


@router.get("/api/health")
def health():
    import os
    return {"status": "ok", "app": "LedgerAI", "version":"0.2.0", "instance":os.environ.get('LEDGER_INSTANCE_ID','docker')}


@router.post('/api/system/backup')
def create_backup(admin=Depends(require_admin)):
    from fastapi.responses import FileResponse
    from app.core.backup import run_full_backup
    try:
        path = run_full_backup()
    except Exception:
        raise HTTPException(status_code=500, detail='备份失败，请检查原件完整性与存储空间')
    return FileResponse(path, media_type='application/zip', filename=path.name)


@router.get('/api/system/backup-status')
def backup_status(admin=Depends(require_admin)):
    import json
    from pathlib import Path
    from app.core.config import get_settings
    path = Path(get_settings().BACKUP_DIR) / 'status.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'ok':False,'message':'尚无完整备份'}
