# 有数 LedgerAI · NAS 部署操作文档

> 适用版本：v0.3-m4 及以上（已含 Alembic 自动迁移、每日自动备份）
> 部署架构：单后端容器 + NAS 数据卷（SQLite 数据库 / 电子附件 / 自动备份）
> 适用 NAS：群晖 / 威联通 / 极空间 / 绿联等支持 Docker（Container Manager）的 x86_64 机型

---

## 一、架构与数据布局

```text
NAS (x86_64, 局域网)
└── docker compose
    └── ledgerai-backend 容器（FastAPI + Uvicorn，端口 8000）
        └── 挂载卷  /data  ←→  NAS 目录 /volume1/docker/ledger/data
            ├── ledger.db          账套数据库（SQLite，唯一账本）
            ├── attachments/       凭证电子附件（按账套/期间分目录）
            └── backups/           每日凌晨3点自动备份（gzip，保留30天）
```

要点：
- **账本就是一个 SQLite 文件**，备份 = 复制文件；容器删除重建不影响数据；
- 数据库结构升级由 **Alembic 在容器启动时自动执行**，升级版本 = 重启容器；
- 前端容器待前端开发完成后加入 compose（届时只增不改）。

---

## 二、前置条件

| 项目 | 要求 |
|---|---|
| NAS | 支持 Docker / Container Manager，x86_64（Intel/AMD CPU）架构 |
| 内存 | ≥ 2GB 空闲（实际运行占用约 150–300MB） |
| 网络 | 局域网内可访问；建议不暴露公网（见第七节安全建议） |
| 文件 | 本机已验证通过的项目文件夹（含 `docker-compose.yml`、`backend/`、`.env`） |

---

## 三、首次部署（6 步）

### 第 1 步：拷贝项目到 NAS

把整个 `financebooking` 文件夹上传到 NAS，例如：

```text
/volume1/docker/ledger/
├── docker-compose.yml
├── backend/
└── （.env 在下一步创建）
```

> 群晖：File Station 直接上传或用局域网共享复制；威联通：File Station / Hybrid Backup Sync。

### 第 2 步：准备 .env 配置文件

在项目根目录创建 `.env`（可从 `.env.example` 复制修改）：

```ini
SECRET_KEY=换成一串随机长字符（可用下面命令生成）
DATABASE_URL=sqlite:////data/ledger.db
ATTACHMENTS_DIR=/data/attachments
BACKUP_DIR=/data/backups
ACCESS_TOKEN_EXPIRE_MINUTES=720
```

生成随机密钥（任选一种方式执行）：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
# 或在 NAS 的 SSH 里：
openssl rand -hex 32
```

> ⚠️ `SECRET_KEY` 决定登录令牌的签名，换新密钥后所有已登录设备需重新登录。**不要**把 `.env` 提交到代码仓库或分享给他人。

### 第 3 步：构建并启动

SSH 登录 NAS（群晖：控制面板 → 终端机和 SNMP → 启用 SSH；或直接用 Container Manager 的图形界面导入 compose）：

```bash
cd /volume1/docker/ledger
docker compose up -d --build
```

首次构建会拉取 `python:3.12-slim` 基础镜像（约 50MB），之后增量构建很快。

查看状态：

```bash
docker compose ps        # STATUS 应为 Up (healthy)
docker compose logs -f   # 观察启动日志，Ctrl+C 退出查看
```

### 第 4 步：初始化管理员与账套

```bash
docker compose exec backend python scripts/init_dev_db.py \
  --admin-username admin \
  --admin-password 你的管理员密码 \
  --book-name 你的公司全称 \
  --start-period 2026-08
```

脚本会自动：建表（Alembic 迁移已在启动时执行）→ 创建管理员 → 建账套并预置 66 个会计科目与报表模板、税务参数。

### 第 5 步：验证

浏览器访问：

```text
http://NAS内网IP:8000/docs        ← 接口调试页（测试用页面）
```

用 admin 登录后可依次验证：`GET /api/health`、`GET /api/accounts`（应见 66 科目）、`GET /api/reports/trial-balance`。

### 第 6 步：确认数据落在 NAS 上

检查 `/volume1/docker/ledger/data/` 目录出现：

```text
data/
├── ledger.db
├── attachments/
└── backups/
```

**强烈建议**：在 NAS 的套件里对 `/volume1/docker/ledger/data` 配置第二重保障（快照计划 / 云同步 / 异地备份）。

---

## 四、日常操作

| 操作 | 命令 |
|---|---|
| 启动 | `docker compose up -d` |
| 停止 | `docker compose down`（数据保留） |
| 查看日志 | `docker compose logs -f --tail 100` |
| 重启 | `docker compose restart` |
| 进入容器 | `docker compose exec backend bash` |

### 版本升级流程

```bash
# 1. 备份当前数据（保险）
docker compose exec backend python -c "from app.core.backup import run_backup_now; print(run_backup_now())"

# 2. 用新版代码覆盖 backend/ 目录与 docker-compose.yml
# 3. 重建并启动（Alembic 自动完成表结构迁移）
docker compose up -d --build
```

数据在卷上，重建容器不会丢账。

---

## 五、备份与恢复

### 自动备份

- 每天凌晨 03:00 自动执行（容器内置定时任务）；
- 采用 SQLite 安全快照 API（备份时写入不受影响）→ gzip 压缩；
- 文件名：`backups/ledger-YYYY-MM-DD_HHMMSS.db.gz`，自动保留最近 30 天。

### 手动立即备份

```bash
docker compose exec backend python -c "from app.core.backup import run_backup_now; print(run_backup_now())"
```

### 恢复（两种场景）

**场景 A：整库恢复到某个备份点**

```bash
docker compose down
cd /volume1/docker/ledger/data
cp ledger.db ledger.db.broken            # 保留现场
gzip -dc backups/ledger-2026-08-31_054202.db.gz > ledger.db
cd .. && docker compose up -d
```

**场景 B：只验证备份文件好不好（不覆盖现库）**

```bash
docker compose exec backend python scripts/restore_check.py data/backups/ledger-2026-08-31_054202.db.gz
# 输出：账套名 / 科目数 / 报表模板行数 / 管理员账号 → 与预期一致即备份有效
```

---

## 六、故障排查

| 现象 | 排查 |
|---|---|
| 容器反复重启 | `docker compose logs --tail 50`；常见原因：`.env` 缺失、SECRET_KEY 为空 |
| 健康检查失败 | 启动前 20 秒属正常（start-period）；持续失败看日志中 Alembic 迁移报错 |
| 端口 8000 被占 | compose 中把 `ports` 改为 `"8080:8000"`，用 8080 访问 |
| NAS 上构建失败 | 确认 NAS Docker 版本支持 Compose v2（`docker compose version`）；老版套件用 Container Manager 图形界面导入 |
| 登录 401 且密钥换过 | SECRET_KEY 变更会导致旧令牌失效，重新登录即可 |
| 数据库锁死 (database is locked) | SQLite 单写者，正常使用不会出现；若出现多为两个进程直写同一文件，确认只有容器在访问 data 目录 |

---

## 七、安全建议（重要）

1. **默认只走局域网**：不要在路由器上把 8000 端口映射到公网；
2. **外网访问需求**：推荐 Tailscale / WireGuard 组网（点对点加密、不开端口），或 NAS 自带反向代理 + HTTPS + 强密码；
3. **账号**：首次初始化后立即改掉默认密码；制单（bookkeeper）与审核（auditor）使用不同账号，系统强制制审分离；
4. **密钥**：大模型/OCR 的 API Key（M6 起使用）一律放 `.env`，不进代码仓库；
5. **第二重备份**：NAS 自带快照或云同步 `/volume1/docker/ledger/data`，防止 NAS 盘故障。

---

## 八、本机 ↔ NAS 差异速查

| 项 | 本机（Windows 开发） | NAS |
|---|---|---|
| 启动方式 | `docker compose up -d` | 相同 |
| 数据卷 | `./data`（项目目录下） | `/volume1/docker/ledger/data`（按实际路径） |
| DATABASE_URL | `sqlite:////data/ledger.db`（容器内） | 相同（容器内路径不变） |
| 镜像 | 本机构建 | x86_64 直接复用，或 NAS 上 `--build` |
| 代码改动 | 即时 | 拷贝后 `docker compose up -d --build` |

---

*文档版本：v1.0（随 v0.3-m4）· 更新方式：修改后随代码一起提交*
