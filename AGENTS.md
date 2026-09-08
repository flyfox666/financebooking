# 有数 LedgerAI · 项目级 AI 行为规范（AGENTS.md）

> 本文件面向任何支持 AGENTS.md 的 AI IDE。所有在本项目 `D:\codexproject\financebooking` 下的对话均须遵守。
> 配套阅读：`开发进度与交接.md`（功能清单+发布记录）、`项目架构图.svg`（模块关系）。

---

## 一、环境铁律（触碰即错）

1. **环境隔离（2026-09-07 用户确认）**：本项目为 `D:\codexproject\financebooking`，旧目录禁令是复制遗留。当前测试统一用 `docker compose -f docker-compose.test.yml`，容器 `ledgerai-codex-test`、端口 18000、独立卷 `ledgerai-codex-test_test-data`。旧容器 `ledgerai-backend` 及 `ledgerai-*-1` 保留，不重建、不复用其财务数据卷。用户已授权仅复用旧环境的模型连接配置与密钥。
2. **不要用全局 Python 跑服务**：开发服务与业务测试统一走 Docker。旧 `backend\.deps`、`backend\.venv`、`build\venv` 已于2026-09-08经用户确认清理；Windows打包使用 `build/release-venv`。开发流程：改代码 → `docker compose -f docker-compose.test.yml build backend && docker compose -f docker-compose.test.yml up -d backend`。
3. **当前测试访问地址**：`http://127.0.0.1:18000/app`。构建和启动须显式带 `-f docker-compose.test.yml`，不要直接执行默认 Compose 文件，它仍指向旧容器名与 8000 端口。
4. **管理员账号**：admin / admin123456。LLM provider 与密钥在「设置」页配置（gateway 多 provider，加密存储）。
5. **禁止编造企业数据**：涉及中国企业工商/股权/风险一律走「天眼一下」Skill / 天眼查 MCP，先 `search_companies` 锚定主体再查询。
6. **浏览器操作统一走内置能力**：只用 `agent-browser` Skill 或「浏览器控制」插件，**不得** 安装 Playwright/Puppeteer/Selenium 或下载额外浏览器内核。

---

## 二、业务硬约束（红线，违反 = 功能错误）

### 2.1 发票与台账
- 发票唯一性 = `(账套 book_id, 方向 direction in {sales,purchase}, 发票号 invoice_no)` 三元组。
- **发票必须归类为销项（sales 开出发票）或进项（purchase 收到发票）**，导入与落账时不可省略方向。
- 发票号查重仅匹配 **已入账**（`voucher_id NOT NULL`）的发票；台账已导入但未生成凭证的发票不算"重复"（否则发票驱动 AI 记账必然误伤）。
- 金税 Excel 导入、单张 PDF/图片落账两条路径**都沉淀到 Invoice 台账**（全量底册、发票模块统管）。纯文本描述 source_kind=text 不补录（字段不足）。
- PDF 落账补录台账时，方向判定：购方税号/名称 = 账套公司 → 进项 purchase；销方匹配本公司 → 销项 sales；两方都不匹配或同时匹配时不得猜测，必须人工选择方向并记录核对说明；开票日期保留票面原始日期，红冲取负。

### 2.1b 账套与多租户
- 账套成员制 `user_book`（user×book×role）：全局 admin 天然可见全部账套；其他用户须为账套成员（可见性/访问均按此判定）。
- 建账套自动给创建者挂 admin 成员；前端切换账套必须清空 chatLog、CHAT_HISTORY、pendingAsk 与全部候选卡片，丢弃旧请求回包，并按账套记忆期间（防跨账套串上下文）。
- 新增涉及 book_id 的路由时，主数据管理写操作走 `require_admin`；凭证制单、编辑、提交及原件增删走 `require_bookkeeper`；审核/过账走 `require_auditor_or_admin`；所有操作都须校验 `require_book_access`（**存量业务路由已于 2026-09-05 全量挂**）。单实体端点（凭证/附件/文档）先查实体归属账套再校验；body.book_id 端点在函数体内显式调 `require_book_access`。
- 成员管理：成员端点仅 admin；整体替换须过「最低可管性」校验（账套不得无管理员）；删用户级联清理授权。
- 心智模型：全局角色=岗位（admin/bookkeeper/auditor，制审强制分离），账套成员=门禁（能进哪些账套）。

### 2.2 凭证与落账
- **AI 无任何过账/审核权限**：只能生成「候选凭证」，最终落账必须经用户在卡片上点击「确认生成草稿」，再由不同审核人审核/过账（工具白名单仅查询类 + ask_user）。
- 所有发票↔凭证关联必须通过 `Invoice.voucher_id` 回写，AI 落账时 `_link_invoice` 自动按发票号关联。
- 小规模纳税人**进项发票严禁拆税**：价税合计全额计入费用/资产，不动 `2221 应交税费`；销项按 1% 征收率价税分离。一般纳税人双向分离。
- 手工凭证可先保存草稿，提交、审核、过账前必须存在至少一条真实 Attachment 且 SHA256 校验通过；只填 attachment_count 不能代替原件。审核岗不能制单/改分录/改原件；制单人及所有修改人均不能审核该凭证。待审/已审凭证增删原件后退回草稿重新审核。
- **凭证附件存储规则**：物理路径 `/data/attachments/<book_id>/<period>/<uuid>.<ext>`（宿主映射 `data/attachments`），Attachment 表存 UUID 路径 + 原名 + SHA256；删除 = 磁盘文件 `path.unlink()` + 数据库记录真删 + 重新 recount；**已过账/已冲销凭证的附件锁定不可删**（仅草稿/待审/已审三态可编辑）。

### 2.3 报表与现金流量
- **表结法**：资产负债表「未分配利润」行公式 = `3103本年利润 + 3104利润分配 + 全部损益类科目净额`（本项目 3001 为实收资本），确保期中/期末恒等式 Assets=Liabilities+Equity 始终成立；期末结转后损益归零，公式仍正确。
- **现金流量行级标注优先**：`voucher_line.cf_item` 为 14 项 key（见 cashflow.py ITEM_LABELS）。一张凭证若含多行现金可分别标注不同流量项。`cashflow._flows` 先累计所有已标注净量 → 剩余未标注走「对方最大行科目 × 方向」做兜底推断。
- `ReportTemplate` 按账套隔离 seed 默认模板；映射体检必须实时校验恒等式 + 列出未映射叶子科目（有余额的标红）。
- **勾稽校验（check_service）**：方向/余额异常（资产贷方、负债借方、货币资金赤字）、未分配利润↔净利润、货币资金↔现金流、跨期衔接（**对比 PeriodBalance 结账快照**，非现算值——现算两边同源恒等是失效校验）；上期未结账明确标注跳过。

### 2.4 凭证显示
- 凭证详情/编辑页**科目编码与科目名称分两列**（编码列等宽字体左对齐）。
- 凭证附件**默认隐藏**，通过详情头部「📎 附件（N）」按钮打开居中浮层管理（列表/打开/删除/批量上传）。
- 窄窗口凭证行用**整体 zoom 缩放**（不是横向滚动），父容器 `.vc-fit` 包裹，`fitVoucherLines()` 按可用宽度比例缩放，下限 zoom=0.55。

---

## 三、AI 记账三层护栏模式（核心开发范式）

所有涉及 AI 生成账务数据的功能必须套用三层，缺任何一层视为未完成：

| 层级 | 实现位置 | 作用 |
|---|---|---|
| 第 1 层 · 软约束（Prompt） | `agent.py` SYSTEM_PROMPT 规则 1-N | 明确告诉模型应该做什么；注入账套公司名/税号 + 今天日期 + 纳税人类型价税规则 |
| 第 2 层 · 用户确认 | 前端凭证候选卡片 + ask_user 工具 | 购方不匹配、疑似重复、往来新建等场景必须向用户二次确认，AI 不得自行拍板 |
| 第 3 层 · 代码硬兜底 | voucher_service / suggest.py | 模型不听话时拦截/修正：`_hard_check_duplicate`、`_apply_cf_items`、`_sanitize_contacts` |

已落地的硬兜底清单（新增功能时要照此模式加）：
1. `_hard_check_duplicate(amount, date, invoice_no, history)` → 候选展示警告，确认端重新校验；发票身份重复硬拦截，疑似重复须匹配服务器风险指纹并填写至少4字理由。聊天历史不视为授权
2. `_apply_cf_items(lines)` → 非现金行清 cf_item；合法值保留；缺/非法 → 按对方最大行科目映射
3. `_sanitize_contacts(lines)` → 逐行校验 contact_id 在数据库中真实存在，无效 ID 置空（卡片中手动补选）
4. `_tax_rule(book)` → 按 book.taxpayer_type 动态输出价税口径，避免小规模/一般纳税人混用
5. 发票号正则 → 20 位长号优先匹配于 8 位短号，避免截断

---

## 四、开发执行流程（必须遵守）

### 4.1 任务开始前
1. 涉及 3+ 步文件改动、跨模块修改、高风险变更（删除/重构/数据库迁移）必须先建 TODO 清单（`TodoWrite` 工具）。
2. 改动前先 `Read` 目标文件确认最新内容，**严禁凭记忆做 Edit 替换**。

### 4.2 编码中
1. **优先 Edit 现有文件**，非必要不新建文件；`.md` 文档（README 等）未经用户明确要求禁止主动创建。
2. 数据库列变更（新增/修改/删除）必须通过 Alembic 迁移：`backend/alembic/versions/<hash>_<desc>.py`，容器启动 auto-migrate（日志会显示 upgrade 进度）。
3. 前端改动集中在 `backend/app/static/app.html`，该文件是单页应用所有 UI 交互的唯一承载（~2000 行），尽量定位函数后局部 Edit，不要整文件重写。
4. 响应式：主 layout 中列为 `minmax(0,1fr)` 而非纯 `1fr`（否则 nowrap 表格撑爆网格）；≤1024px 藏右栏、≤680px 单列+侧栏横滑。

### 4.3 改动后验证（声称完成前必须）
1. **后端级**：对新增接口/业务函数走 API 级场景测试（构造请求、断言状态码、断言关键字段）。
2. **存量兼容**：对已有数据断言回填/迁移正确（如 cf_item 存量、报表恒等式）。
3. **前端级**：至少测 4 档宽度 1280/800/640/400（容器 overflow-x=0、凭证 zoom 生效）、主要端到端流程（登录→AI记账→确认→列表刷新）。
4. **部署级**：改完必须 `docker compose -f docker-compose.test.yml build backend && docker compose -f docker-compose.test.yml up -d backend` 一轮（除非是纯静态资源无后端改动可直接刷新）。
5. **文档同步（2026-09-07 用户长期要求）**：每次修改完成，都更新 `开发进度与交接.md` 的当前状态、验证结果、部署范围和待办，并在 `CHANGELOG.md` 写清变动内容；涉及功能/接口变化时同步 README 对应章节。
6. **提交与推送（2026-09-07 用户长期授权）**：每轮更新通过必要验证后，创建说明清晰的 Git 提交并推送到本项目已配置的 GitHub 远程跟踪分支；不能仅更新本地或 Docker 就交付。不重复询问是否推送。推送前检查差异与敏感文件，禁止提交密钥、账务库、原件、备份及临时构建产物；禁止强制覆盖远程历史。推送后核对远程提交与本地 HEAD 一致，最终回复提供提交链接；失败则明确说明阻塞原因，不声称已推送。

---

## 五、代码与命名规范

### pre-test 发布附件（2026-09-08 用户要求）
- 当前为预发布测试阶段，功能版本采用 `vX.Y.Z-pretest.N` 标签；新功能版本通过必要验收后，在 GitHub Releases 发布对应安装包、便携ZIP、SHA256清单和版本说明。README内容修订号与软件版本号分开。
- `build/`、`dist/` 继续忽略，二进制作为Release附件，不提交进Git；使用 `installer/build-release.ps1` 构建新的版本目录，不冒用旧包代表新源码，不覆盖历史附件。
- 发布前必须验证打包exe的独立空库启动、迁移与登录，扫描包内无数据库、原件、`.env`、API Key、`.model_key`等私有内容。系统导出的完整备份ZIP绝不能作为公开附件。
- 未测试的安装向导、升级路径或特定功能需在Release说明中明确，不因编译成功声称全面验收。文档修订本身不要求重发相同二进制。

- 后端 Python：PEP 8，snake_case 变量/函数，PascalCase 类；类型注解 `Mapped[T|None]` + Pydantic schema。
- 数据库层：SQLAlchemy 2.0 declarative，`mapped_column()`，迁移用 Alembic（手写 upgrade/downgrade 不要 auto-generate 依赖缺失的 env）。
- 前端：原生 HTML/CSS/JS（无构建步骤、无框架），所有逻辑嵌在 `app.html` 的 `<script>` 中或 `<script src="...">` 外链。查询用 `$=document.querySelector`、`$$=querySelectorAll`（如果有定义），否则标准 DOM API。
- 文件引用：使用当前项目 `D:/codexproject/financebooking/` 下的绝对路径链接。
- 沟通语言：中文（用户母语），技术名词保留英文原词（如 FastAPI、Docker、cf_item、Alembic）。

---

## 六、关键文件快速索引

| 路径 | 作用 |
|---|---|
| `backend/app/static/app.html` | 全部前端 UI + 交互（悬浮窗、工具栏、金额=平衡、缩放、响应式、附件弹层、映射体检、现金流量列） |
| `backend/app/ledger/ai/agent.py` | AI 智能体 system prompt + 工具 + 硬护栏（_hard_check_duplicate / _sanitize_contacts / _tax_rule / 日期与公司信息注入） |
| `backend/app/ledger/ai/suggest.py` | suggest 老路径 + _link_invoice（发票号回写 voucher_id）+ _create_invoice_from_fields（PDF 落账补录台账） |
| `backend/app/ledger/voucher_service.py` | 凭证创建/校验 + _apply_cf_items 兜底 + source=manual 附件校验 |
| `backend/app/ledger/cashflow.py` | 现金流量表 _flows（行级标注优先、未标注兜底推断）+ CASH_ACCOUNTS/INFLOW_MAP/OUTFLOW_MAP/ITEM_LABELS |
| `backend/app/ledger/report_service.py` | period_summary / template_check / PUT template-row / POST template-reset + 表结法默认模板 |
| `backend/app/ledger/check_service.py` | 勾稽关系校验（方向异常/利润·现金勾稽/跨期衔接快照对比）+ run_checks 聚合 |
| `backend/app/ledger/book_service.py` | 账套创建（预置科目/模板/税务参数）+ 成员管理（book_members/set_book_members/user_books/set_user_books + 最低可管性校验） |
| `backend/app/models/voucher.py` | Voucher / VoucherLine ORM，含 cf_item 列 |
| `backend/app/schemas/voucher.py` | Voucher/VoucherLine 输入输出 Schema，含 cf_item |
| `backend/app/api/` | 13 路由：auth/vouchers/accounts/reports/tax/invoices/ai/attachments/contacts/books/settings/period-end/cashflow |
| `backend/alembic/versions/b3e1c8f4d2a7_add_cf_item_to_voucher_line.py` | cf_item 列迁移（参考模板） |
| `backend/backfill_cf.py` | 一次性存量 cf_item 回填脚本（参考模式：新列缺值如何按兜底规则回填） |
| `docker-compose.yml` | 单容器编排（backend → ledgerai-backend:8000），data/ 卷映射宿主 |
| `开发进度与交接.md` | 本项目完整进度条、功能清单、环境细节、待办、社媒发布记录（AI 每轮新会话必读） |
| `项目架构图.svg` | 模块关系图（待同步，当前未反映本期概要、重复检测、发票模块、映射体检、现金流量、响应式） |

---

## 七、当前待办清单（优先级 高→低）

1. ~~**架构图同步**~~ ✅（2026-09-04 已同步）：更新为 AI 助手主视图、勾稽校验 check_service、行业偏好库 packs、设置页 LLM 配置。
2. ~~**可选：check_duplicate 摘要相似度**~~ ✅（2026-09-04 已用更实质的方案解决）：查重口径已收紧为实质性重复——发票号精确命中已入账发票，或同日+同金额+科目类别一致+往来对象不冲突；仅金额相同不拦（重复金额是正常业务）。摘要相似度辅信号不再需要。
3. **已实现：AI voucher_date 优先结构化单据日期**：提示词已约束未说明时用今天，说明开票日期时应优先。模型偶有不听话，当前可手动改卡片日期，可加硬兜底从历史/发票 fields 提取。
4. **已实现：小规模进项拆税硬拦截与多科目回归**：目前已回归 1 场（2825 元 13% 专票固定资产），可补 2-3 场（差旅/住宿/打印等常见费用场景）。

---

## 八、用户提醒机制

如果 AI 未按本规范执行（例如直接改代码不建 TODO、误操作旧容器或财务数据、编造企业数据、违反业务硬约束、未验证就声称完成），用户可直接用以下短语提醒重来：
- 「请按 AGENTS.md 规范执行」
- 「先建 TODO 再改代码」
- 「这条违反了硬约束 N，请退回」
- 「声称完成前请跑一轮验证」
