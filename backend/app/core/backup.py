import gzip
import shutil
import sqlite3
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
