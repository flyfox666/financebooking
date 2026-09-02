alwaysApply: false
description: 涉及 Docker 部署启动、容器操作、数据库备份与恢复、数据迁移、Alembic upgrade、配置变更等运维任务时智能触发。
---

# 部署与运维规范（智能生效：部署/启动/容器/备份/迁移相关）

## 环境隔离（绝对不可违反）
- **只操作 `d:\traecnproject\financebooking`**。`d:\codexproject\financebooking` 完全独立，禁碰其代码/容器/数据库。
- 全局 Python 3.13 缺依赖；服务统一走 Docker Compose，不要 `python -m uvicorn` 起本地（除非装了 backend\.deps 且你确认 PYTHONPATH）。
- 容器名：本项目是 `ledgerai-backend`（单容器）。codexproject 那套容器名带 `-1` 后缀，一看就分得清。

## 部署流程
### 标准改代码→上线
```bash
# 在项目根 d:\traecnproject\financebooking
docker compose build backend   # 重建镜像（改了 Python/requirements 必跑；纯静态 html 也可不用，直接覆盖容器内 static 会随 build 同步）
docker compose up -d backend   # 重启容器
docker compose logs -f backend # 看启动日志：Alembic auto-migrate → Uvicorn running on 0.0.0.0:8000
```
### 快速验证启动完成
日志里出现以下两条即服务就绪：
1. `INFO  [alembic.runtime.migration] Running upgrade <prev> -> <latest>, ...`（迁移跑完，可能有多条）
2. `INFO:  Uvicorn running on http://0.0.0.0:8000`

然后浏览器访问 `http://127.0.0.1:8000/app`（**注意是 127.0.0.1，不是 localhost**，IPv6 解析会导致转发失败）。纯 HTTP，HTTPS 不可用。

## 账号与密钥
- 登录：admin / admin123456。
- LLM 配置在「设置」页（登录后右上角齿轮）。gateway 多 provider（dashscope / openai-compatible / doubao 等），密钥经加密写入 DB（不是明文 .env）。
- `.env.example` 是模板文件，不含真实密钥；生产配置在容器环境变量或 settings 页录入。

## 数据与备份
### 路径映射
| 容器内路径 | 宿主机路径 | 内容 |
|---|---|---|
| `/data/ledger.db` | `d:\traecnproject\financebooking\data\ledger.db` | SQLite 单文件数据库（核心！） |
| `/data/attachments/` | `d:\traecnproject\financebooking\data\attachments\` | 凭证电子原件分层目录 `<book_id>/<period>/<uuid>.<ext>` |
| `/data/backups/` | `d:\traecnproject\financebooking\data\backups\` | 每日 3:00 AM 自动备份的 ledger.db gzip |

### 备份机制
- 每日 03:00 容器内 cron/entrypoint 脚本 dump ledger.db → gzip → backups 目录，文件名带日期。
- 恢复：停容器 → 把对应 gzip 解压覆盖 ledger.db → 启容器。
- **附件删除是真删**：磁盘文件 + DB 记录双删，仅 03:00 之前删除的文件还留在前一天的备份里。

## Alembic 迁移
- 新增/修改列必须写迁移脚本（参考 `backend/alembic/versions/b3e1c8f4d2a7_add_cf_item_to_voucher_line.py`）。
- 迁移放在 `backend/alembic/versions/<hash>_<slug>.py`，upgrade/downgrade 对称。
- 容器启动自动 `alembic upgrade head`，日志可见 running upgrade。
- 迁移后若有存量缺值（如 cf_item），提供一次性回填脚本：容器内 `python backfill_cf.py` 模式（幂等、分页更新）。

## 常用运维命令
```bash
# 进入容器 shell
docker exec -it ledgerai-backend bash

# 容器内跑 Python 脚本（如回填）
docker exec -it ledgerai-backend python backfill_cf.py

# 看最近 200 行日志
docker compose logs backend --tail=200

# 重启服务（不改代码只重启）
docker compose restart backend

# 完全重起（清状态再启，不删卷）
docker compose down && docker compose up -d backend

# 备份当前 DB（宿主机执行，一次性手工备份）
copy d:\traecnproject\financebooking\data\ledger.db d:\traecnproject\financebooking\data\backups\ledger-manual-%Y%m%d.db
```

## 常见部署坑
| 现象 | 根因 | 解决 |
|---|---|---|
| `http://localhost:8000` 打不开 | 两种可能：①误走 HTTPS（浏览器默认升级）②localhost 解析 IPv6 ::1 | 用 `http://127.0.0.1:8000/app` |
| 登录 admin 密码不对 | 交接文档中过旧密码 admin123 已废弃 | 用 admin123456 |
| DB 被锁 / 操作无响应 | 过账/反过账事务超时或手工 attach 锁住 | `docker compose restart backend` 释放 |
| Python 改代码后没变 | 没 build 直接 up，容器里还是旧镜像 | 先 `docker compose build backend` |
| 横溢 / UI 挤 | 代码没加 minmax 或触发点没调 fitVoucherLines | 见前端规则 02-frontend-guidelines |

## 在 TraeIDE 内的首次启动检查清单
1. 打开项目目录：`d:\traecnproject\financebooking`（确认不是 codexproject 那套）。
2. 终端跑 `docker compose build backend && docker compose up -d backend`。
3. 观察日志：`docker compose logs -f backend` 等 alembic + uvicorn 两条 ready 行。
4. 打开 `http://127.0.0.1:8000/app` 登录 admin / admin123456。
5. 快速冒烟：①侧边栏凭证/报表/发票三个 tab 点开无错 ②侧栏本期概要数字不为全 0（若有 demo 数据）③AI 悬浮窗可拖拽+可输入 → OK 迁移成功。
