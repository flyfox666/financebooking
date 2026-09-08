# 有数 LedgerAI · NAS 部署操作文档

> 适用版本：v0.2.0-pretest.1 / 当前源码；仅用于另建目录的NAS部署。当前本机开发使用docker-compose.test.yml和18000端口，见README。
> 日常使用入口：主界面 `http://NAS内网IP:8000/app`（接口调试页 /docs 仅开发用）
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
            └── backups/           每日凌晨3点完整ZIP备份，保留30天
```

要点：
- 数据库、原件、待处理文件和模型解密文件共同构成可恢复数据，不能只备份数据库。
- 数据库结构升级由 **Alembic 在容器启动时自动执行**；源码更新须先重建镜像，单纯重启仍会运行旧代码。
- 前端由同一后端容器提供，不需要单独的前端容器。

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

通过Git克隆或复制已跟踪源码到NAS，不复制本地 `.env`、`data/`、`build/`、`dist/` 和私有备份。例如：

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

空库首次启动自动创建管理员 `admin / admin123456`。登录后立即在“用户管理”修改密码，再按页面引导建账套并核对启用期间和期初余额。不要把旧初始化脚本当作修改默认管理员密码的方法。

### 第 5 步：配置模型服务（使用 AI 记账才需要）

登录主界面 → 左侧「模型设置」→ 添加 provider（OpenAI 兼容 / 火山 / 通义等），
填入 API Key 后点「测试连通」→ 设为默认。**密钥加密存储在数据库**，不落 .env、不进代码仓库。

> `.env` 中的 `LLM_API_KEY` 等变量是可选的「首次启动种子」：容器第一次启动且库中无 provider 时
> 会自动导入一条；日常增删改一律在设置页操作。

### 第 6 步：验证

浏览器访问：

```text
http://NAS内网IP:8000/app         ← 主界面（日常使用入口，admin 登录）
http://NAS内网IP:8000/docs        ← 接口调试页（仅开发/测试用）
```

主界面登录后依次验证：右侧「本期概要」有数据、凭证/报表/发票三个 Tab 正常打开；
如需 AI 记账，先到左侧「模型设置」页添加 LLM provider（见下节）。

### 第 7 步：确认数据落在 NAS 上

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
docker compose exec backend python -c "from app.core.backup import run_full_backup; print(run_full_backup())"

# 2. 用新版代码覆盖 backend/ 目录与 docker-compose.yml
# 3. 重建并启动（Alembic 自动完成表结构迁移）
docker compose up -d --build
```

数据在卷上，重建容器不会丢账。

---

## 五、备份与恢复

### 自动与手动完整备份

- 服务运行时每天凌晨03:00执行，启动时补做；成功备份保留30天。
- 生成 `backups/ledger-full-时间戳.zip`，含SQLite安全快照、引用的电子原件/待处理文件、`.model_key`及校验清单；不是旧版只有数据库的gzip。
- 管理员可在主界面“完整备份”下载，或在此NAS部署目录运行：

```bash
docker compose -f docker-compose.yml exec backend python -c "from app.core.backup import run_full_backup; print(run_full_backup())"
```

完整备份含可用于恢复模型凭据的文件，只保存到受控的私有备份位置；不要上传GitHub或Release。NAS部署的 `.env` 含登录签名配置，需另行私有保存，不包含在应用完整ZIP中。

### 恢复与回退

先停止对应服务，保留当前整个数据目录及升级前程序版本。维护函数 `app.core.backup.restore_full_backup` 只接受新的空目标目录，校验路径、SHA256和数据库完整性，禁止直接覆盖现有账务库。恢复后还须核对最终数据挂载位置、待处理单据路径、模型凭据和附件，再切换运行。当前没有面向普通用户的一键恢复界面，不要沿用旧文档的“只解压ledger.db覆盖现库”方式。

若仅备份整个NAS数据目录，应先停止服务再复制；原目录及备份不能被两个实例同时写入。数据已迁移时，回退须恢复升级前完整备份，不应直接用旧镜像读取新数据库。

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
| AI 记账报模型错误 / 无响应 | 主界面「模型设置」页点 provider 的「测试连通」；多为 Key 失效、余额不足或 base_url 错；勾稽/报表功能不依赖 LLM，不受影响 |

---

## 七、安全建议（重要）

1. **默认只走局域网**：不要在路由器上把 8000 端口映射到公网；
2. **外网访问需求**：推荐 Tailscale / WireGuard 组网（点对点加密、不开端口），或 NAS 自带反向代理 + HTTPS + 强密码；
3. **账号**：首次初始化后立即改掉默认密码；制单（bookkeeper）与审核（auditor）使用不同账号，系统强制制审分离；
4. **密钥**：模型服务 API Key 在主界面「模型设置」页配置（加密存数据库）；`.env` 不进代码仓库，
   其中的 `LLM_*` 变量仅作首启种子，长期不用的可删；
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

*文档版本：v1.1（2026-09-04，同步 AI 记账/设置页密钥/主界面入口）· 更新方式：修改后随代码一起提交*
