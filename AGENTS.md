# 有数 LedgerAI · 项目级 AI 行为规范（AGENTS.md）

> 本文件面向 TraeIDE / 任何支持 AGENTS.md 的 AI IDE。所有在本项目 `d:\traecnproject\financebooking` 下的对话均须遵守。
> 配套阅读：`开发进度与交接.md`（功能清单+发布记录）、`项目架构图.svg`（模块关系）。

---

## 一、环境铁律（触碰即错）

1. **两套并行项目绝对隔离**：本项目在 `d:\traecnproject\financebooking`（容器名 `ledgerai-backend`，端口 8000）。另一套 `d:\codexproject\financebooking` 完全独立，**严禁** 读写该目录或操作其容器（名带 `-1` 后缀）。
2. **不要用全局 Python 跑服务**：全局 Python 3.13 缺依赖，依赖装在 `backend\.deps`。开发流程统一走 Docker：改代码 → `docker compose build backend && docker compose up -d backend`。
3. **访问地址**：用 `http://127.0.0.1:8000/app`（`localhost` 的 IPv6 解析 ::1 会导致转发失败；纯 HTTP，HTTPS 不可用）。
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
- PDF 落账补录台账时，方向判定：购方税号/名称 = 账套公司 → 销项 sales；否则 → 进项 purchase；开票日期保留票面原始日期，红冲取负。

### 2.2 凭证与落账
- **AI 无任何过账/审核权限**：只能生成「候选凭证」，最终落账必须经用户在卡片上点击「确认入账」（工具白名单仅查询类 + ask_user）。
- 所有发票↔凭证关联必须通过 `Invoice.voucher_id` 回写，AI 落账时 `_link_invoice` 自动按发票号关联。
- 小规模纳税人**进项发票严禁拆税**：价税合计全额计入费用/资产，不动 `2221 应交税费`；销项按 1% 征收率价税分离。一般纳税人双向分离。
- 手工凭证 `source=manual` 必须至少附 1 张原始单据（attachment_count ≥ 1），否则 400 拦截。
- **凭证附件存储规则**：物理路径 `/data/attachments/<book_id>/<period>/<uuid>.<ext>`（宿主映射 `data/attachments`），Attachment 表存 UUID 路径 + 原名 + SHA256；删除 = 磁盘文件 `path.unlink()` + 数据库记录真删 + 重新 recount；**已过账/已冲销凭证的附件锁定不可删**（仅草稿/待审/已审三态可编辑）。

### 2.3 报表与现金流量
- **表结法**：资产负债表「未分配利润」行公式 = `3103(实收资本? 不—— 3101实收 /3103利润分配 + 3104本年利润 + 全部损益类科目净额 14 项)`，确保期中/期末恒等式 Assets=Liabilities+Equity 始终成立；期末结转后损益归零，公式仍正确。
- **现金流量行级标注优先**：`voucher_line.cf_item` 为 14 项 key（见 cashflow.py ITEM_LABELS）。一张凭证若含多行现金可分别标注不同流量项。`cashflow._flows` 先累计所有已标注净量 → 剩余未标注走「对方最大行科目 × 方向」做兜底推断。
- `ReportTemplate` 按账套隔离 seed 默认模板；映射体检必须实时校验恒等式 + 列出未映射叶子科目（有余额的标红）。

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
1. `_hard_check_duplicate(amount, date, invoice_no, history)` → 重复拦截转 ask_user，对话历史已警告放行过的不再死循环
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
4. **部署级**：改完必须 `docker compose build backend && docker compose up -d backend` 一轮（除非是纯静态资源无后端改动可直接刷新）。
5. **文档同步**：如果涉及架构、功能、接口新增/变化，同步更新 `开发进度与交接.md` 对应章节和日期戳。

---

## 五、代码与命名规范

- 后端 Python：PEP 8，snake_case 变量/函数，PascalCase 类；类型注解 `Mapped[T|None]` + Pydantic schema。
- 数据库层：SQLAlchemy 2.0 declarative，`mapped_column()`，迁移用 Alembic（手写 upgrade/downgrade 不要 auto-generate 依赖缺失的 env）。
- 前端：原生 HTML/CSS/JS（无构建步骤、无框架），所有逻辑嵌在 `app.html` 的 `<script>` 中或 `<script src="...">` 外链。查询用 `$=document.querySelector`、`$$=querySelectorAll`（如果有定义），否则标准 DOM API。
- 文件引用：绝对路径 `file:///d:/traecnproject/financebooking/...` + 必要时行锚 `#L<start>-L<end>`。
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

1. **架构图同步**：`项目架构图.svg` 尚未反映近月大改动（本期概要、重复检测、发票模块、映射体检、现金流量行级、响应式/缩放），需重绘。
2. **可选：check_duplicate 摘要相似度**：当前主信号 = 发票号 + 金额±30天窗口；可加摘要相似度做辅信号（软提示即可，不要硬拦避免误伤）。
3. **可选：AI voucher_date 优先开票日期**：提示词已约束未说明时用今天，说明开票日期时应优先。模型偶有不听话，当前可手动改卡片日期，可加硬兜底从历史/发票 fields 提取。
4. **可选：小规模进项拆税回归补测**：目前已回归 1 场（2825 元 13% 专票固定资产），可补 2-3 场（差旅/住宿/打印等常见费用场景）。

---

## 八、用户提醒机制

如果 AI 未按本规范执行（例如直接改代码不建 TODO、碰 codexproject 目录、编造企业数据、违反业务硬约束、未验证就声称完成），用户可直接用以下短语提醒重来：
- 「请按 AGENTS.md 规范执行」
- 「先建 TODO 再改代码」
- 「这条违反了硬约束 N，请退回」
- 「声称完成前请跑一轮验证」
