alwaysApply: false
description: 涉及 Docker 部署启动、容器操作、数据库备份与恢复、数据迁移、Alembic upgrade、配置变更等运维任务时智能触发。
---

# 部署与运维规范（智能生效：部署/启动/容器/备份/迁移相关）

## 环境隔离（2026-09-07 用户已确认）
- 工作目录 `D:\codexproject\financebooking`，复制来的旧目录禁令不适用。
- 本轮使用 `ledgerai-codex-test` 容器、18000 端口与独立命名卷 `ledgerai-codex-test_test-data`。
- `ledgerai-backend` 和 `ledgerai-*-1` 属于旧环境，不重建、不复用其业务卷。仅模型连接配置与密钥获授权复用。
- 服务和测试都走 Docker；测试不挂业务卷。

## 部署流程
### 标准改代码→上线
```bash
# 在项目根 D:\codexproject\financebooking
docker compose -f docker-compose.test.yml build backend   # 重建镜像（改了 Python/requirements 必跑；纯静态 html 也可不用，直接覆盖容器内 static 会随 build 同步）
docker compose -f docker-compose.test.yml up -d backend   # 重启容器
docker compose -f docker-compose.test.yml logs -f backend # 看启动日志：Alembic auto-migrate → Uvicorn running on 0.0.0.0:8000
```
### 快速验证启动完成
日志里出现以下两条即服务就绪：
1. `INFO  [alembic.runtime.migration] Running upgrade <prev> -> <latest>, ...`（迁移跑完，可能有多条）
2. `INFO:  Uvicorn running on http://0.0.0.0:8000`

然后浏览器访问 `http://127.0.0.1:18000/app`（**注意是 127.0.0.1，不是 localhost**，IPv6 解析会导致转发失败）。纯 HTTP，HTTPS 不可用。

## 账号与密钥
- 登录：admin / admin123456。
- LLM 配置在「设置」页（登录后右上角齿轮）。gateway 多 provider（dashscope / openai-compatible / doubao 等），密钥经加密写入 DB（不是明文 .env）。
- `.env.example` 是模板文件，不含真实密钥；生产配置在容器环境变量或 settings 页录入。

## 数据与备份
- `/data` 保存在独立 Docker 命名卷，不映射到旧项目 data 目录。
- `ledger.db` 为账务库；`attachments/` 包含归档原件及 `_staging` 待处理原件；`.model_key` 是模型凭据解密文件。
- 完整备份为 `ledger-full-*.zip`，含数据库快照、所有引用原件、待处理文件、解密文件与 SHA256 清单。每日03:00及当天首次启动补做；成功备份保留30天，失败写入 status.json 并记录服务日志。
- 管理员可在“完整备份”页下载。ZIP 包含敏感配置，须受控保存；旧版 db.gz 仅有数据库，不是完整备份。
- 恢复函数 `app.core.backup.restore_full_backup(Path(archive), Path(empty_target))` 仅接受新的空目录，逐文件校验清单并修正 staging 路径。退出服务后用容器内 Python 执行到新挂载目录，核对完整性后才切换数据目录，不能覆盖旧账。
- 删除附件是真删；必须先有含对应原件的完整备份，才可能恢复。

## Alembic 迁移
- 新增/修改列必须写迁移脚本（参考 `backend/alembic/versions/b3e1c8f4d2a7_add_cf_item_to_voucher_line.py`）。
- 迁移放在 `backend/alembic/versions/<hash>_<slug>.py`，upgrade/downgrade 对称。
- 容器启动自动 `alembic upgrade head`，日志可见 running upgrade。
- 迁移后若有存量缺值（如 cf_item），提供一次性回填脚本：容器内 `python backfill_cf.py` 模式（幂等、分页更新）。

## 常用运维命令
```bash
# 进入容器 shell
docker exec -it ledgerai-codex-test bash

# 容器内跑 Python 脚本（如回填）
docker exec -it ledgerai-codex-test python backfill_cf.py

# 看最近 200 行日志
docker compose -f docker-compose.test.yml logs backend --tail=200

# 重启服务（不改代码只重启）
docker compose -f docker-compose.test.yml restart backend

# 完全重起（清状态再启，不删卷）
docker compose -f docker-compose.test.yml down && docker compose -f docker-compose.test.yml up -d backend

# 备份当前 DB（宿主机执行，一次性手工备份）
copy D:\codexproject\financebooking\data\ledger.db D:\codexproject\financebooking\data\backups\ledger-manual-%Y%m%d.db
```

## 常见部署坑
| 现象 | 根因 | 解决 |
|---|---|---|
| `http://localhost:8000` 打不开 | 两种可能：①误走 HTTPS（浏览器默认升级）②localhost 解析 IPv6 ::1 | 用 `http://127.0.0.1:18000/app` |
| 登录 admin 密码不对 | 交接文档中过旧密码 admin123 已废弃 | 用 admin123456 |
| DB 被锁 / 操作无响应 | 过账/反过账事务超时或手工 attach 锁住 | `docker compose -f docker-compose.test.yml restart backend` 释放 |
| Python 改代码后没变 | 没 build 直接 up，容器里还是旧镜像 | 先 `docker compose -f docker-compose.test.yml build backend` |
| 横溢 / UI 挤 | 代码没加 minmax 或触发点没调 fitVoucherLines | 见前端规则 02-frontend-guidelines |

## 在 TraeIDE 内的首次启动检查清单
1. 打开项目目录：`D:\codexproject\financebooking`（确认不是 codexproject 那套）。
2. 终端跑 `docker compose -f docker-compose.test.yml build backend && docker compose -f docker-compose.test.yml up -d backend`。
3. 观察日志：`docker compose -f docker-compose.test.yml logs -f backend` 等 alembic + uvicorn 两条 ready 行。
4. 打开 `http://127.0.0.1:18000/app` 登录 admin / admin123456。
5. 快速冒烟：①侧边栏凭证/报表/发票三个 tab 点开无错 ②侧栏本期概要数字不为全 0（若有 demo 数据）③AI 悬浮窗可拖拽+可输入 → OK 迁移成功。
