import gzip
import shutil
import sqlite3

from app.core.backup import run_backup_now
from app.core.config import get_settings


def test_backup_restore_and_retention(tmp_path, monkeypatch):
    db_file = tmp_path / "ledger.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(get_settings(), "DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setattr(get_settings(), "BACKUP_DIR", str(backup_dir))

    stale = backup_dir / "ledger-2020-01-01_000000.db.gz"
    stale.write_bytes(b"stale")

    output = run_backup_now()
    assert output.exists()
    assert output.stat().st_size > 0
    assert output.name.startswith("ledger-")

    restored = tmp_path / "restored.db"
    with gzip.open(output, "rb") as f_in, open(restored, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    check = sqlite3.connect(str(restored))
    assert check.execute("SELECT x FROM t").fetchone()[0] == 42
    check.close()

    assert not stale.exists()


def test_backup_keeps_recent_files(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from app.core.backup import STAMP_FORMAT, _prune

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    recent_stamp = (datetime.now() - timedelta(days=1)).strftime(STAMP_FORMAT)
    recent = backup_dir / f"ledger-{recent_stamp}.db.gz"
    recent.write_bytes(b"recent")

    _prune(backup_dir)
    assert recent.exists()
