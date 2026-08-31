import gzip
import os
import shutil
import sqlite3
import sys

backup = sys.argv[1].replace("\\", "/")
tmp = "data/_restore_check.db"

with gzip.open(backup, "rb") as f_in, open(tmp, "wb") as f_out:
    shutil.copyfileobj(f_in, f_out)

conn = sqlite3.connect(tmp)
print("账套:", conn.execute("SELECT name FROM book").fetchone()[0])
print("科目数:", conn.execute("SELECT COUNT(*) FROM account").fetchone()[0])
print("报表模板行数:", conn.execute("SELECT COUNT(*) FROM report_template").fetchone()[0])
print("管理员账号:", conn.execute("SELECT username FROM user").fetchone()[0])
conn.close()
os.remove(tmp)
