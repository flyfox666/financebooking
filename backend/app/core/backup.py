import gzip
import shutil
import sqlite3
import hashlib
import json
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config import get_settings

KEEP_DAYS = 30
STAMP_FORMAT = "%Y-%m-%d_%H%M%S"


def _sqlite_path() -> Path:
    url = get_settings().DATABASE_URL
    if not url.startswith("sqlite"):
        raise RuntimeError("仅支持 SQLite 数据库备份")
    return Path(url.split("///")[-1])


def _stamp_from_name(name: str) -> datetime | None:
    try:
        return datetime.strptime(name.replace("ledger-", "").replace(".db.gz", ""), STAMP_FORMAT)
    except ValueError:
        return None


def _prune(backup_dir: Path) -> None:
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    for file in backup_dir.glob("ledger-*.db.gz"):
        stamp = _stamp_from_name(file.name)
        if stamp is None or stamp < cutoff:
            file.unlink(missing_ok=True)


def run_backup_now() -> Path:
    settings = get_settings()
    source = _sqlite_path()
    backup_dir = Path(settings.BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime(STAMP_FORMAT)
    temp = backup_dir / f"ledger-{stamp}.db"
    final = backup_dir / f"ledger-{stamp}.db.gz"

    source_conn = sqlite3.connect(str(source))
    dest_conn = sqlite3.connect(str(temp))
    with dest_conn:
        source_conn.backup(dest_conn)
    source_conn.close()
    dest_conn.close()

    with open(temp, "rb") as f_in, gzip.open(final, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    temp.unlink(missing_ok=True)

    _prune(backup_dir)
    return final


def run_full_backup() -> Path:
    """Snapshot the ledger and every referenced original; publish only a verified archive."""
    settings = get_settings()
    backup_dir = Path(settings.BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    root = Path(settings.ATTACHMENTS_DIR).resolve()
    stamp = datetime.now().strftime('%Y-%m-%d_%H%M%S_%f')
    final = backup_dir / f'ledger-full-{stamp}.zip'
    partial = final.with_suffix('.partial')
    try:
        with tempfile.TemporaryDirectory() as temp:
            snapshot = Path(temp) / 'ledger.db'
            with sqlite3.connect(str(_sqlite_path())) as source, sqlite3.connect(str(snapshot)) as dest:
                source.backup(dest)
                tables = {r[0] for r in dest.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                refs = {}
                if 'attachment' in tables:
                    for name, digest in dest.execute('SELECT file_path, sha256 FROM attachment'):
                        refs[name.replace('\\', '/')] = digest
                if 'ai_doc' in tables:
                    for (name,) in dest.execute("SELECT staging_path FROM ai_doc WHERE status IN ('parsed','suggested') AND staging_path <> ''"):
                        path = Path(name).resolve()
                        refs[path.relative_to(root).as_posix()] = None
            manifest = {'version':1, 'created_at':datetime.now().isoformat(), 'files':{}}
            with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as archive:
                def add(name, content):
                    archive.writestr(name, content)
                    manifest['files'][name] = hashlib.sha256(content).hexdigest()
                add('ledger.db', snapshot.read_bytes())
                for relative, expected in refs.items():
                    path = (root / relative).resolve()
                    path.relative_to(root)
                    content = path.read_bytes()
                    if expected and hashlib.sha256(content).hexdigest() != expected:
                        raise RuntimeError('原件校验失败，未生成完整备份')
                    add('attachments/' + relative, content)
                from app.core.model_secrets import key_path
                if key_path().exists():
                    add('.model_key', key_path().read_bytes())
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            with zipfile.ZipFile(partial) as archive:
                if archive.testzip():
                    raise RuntimeError('备份压缩校验失败')
            partial.replace(final)
        (backup_dir / 'status.json').write_text(json.dumps({'ok':True,'file':final.name,'time':datetime.now().isoformat()}), encoding='utf-8')
        cutoff = datetime.now().timestamp() - KEEP_DAYS * 86400
        for old in backup_dir.glob('ledger-full-*.zip'):
            if old != final and old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        return final
    except Exception:
        partial.unlink(missing_ok=True)
        (backup_dir / 'status.json').write_text(json.dumps({'ok':False,'time':datetime.now().isoformat(),'message':'完整备份失败，请检查原件、存储空间与服务日志'}), encoding='utf-8')
        raise


def restore_full_backup(archive_path: Path, destination: Path) -> None:
    """Restore only to a new/empty directory, checking every archive path and hash."""
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('恢复目标必须是空目录，不能覆盖已有账务数据')
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('version') != 1 or 'ledger.db' not in manifest.get('files', {}):
            raise ValueError('备份清单无效')
        for name, digest in manifest['files'].items():
            (destination / name).resolve().relative_to(destination)
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError('备份内容校验失败')
        for name in manifest['files']:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    with sqlite3.connect(str(destination / 'ledger.db')) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'ai_doc' in tables:
            for ident, name in db.execute("SELECT id, staging_path FROM ai_doc WHERE staging_path <> ''").fetchall():
                db.execute('UPDATE ai_doc SET staging_path=? WHERE id=?', (str(destination / 'attachments' / '_staging' / Path(name).name), ident))
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('恢复数据库完整性校验失败')
