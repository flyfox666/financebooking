alwaysApply: false
globs: "**/*.py"
description: 后端 Python 开发规范。当编辑 backend 下任何 .py 文件（API、业务、模型、迁移、脚本）时自动生效。
---

# 后端开发规范（*.py 匹配生效）

## 架构分层（严禁跨层调用绕开）
```
api/*（HTTP 路由，参数校验+响应）
   ↓ 调用
ledger/*（业务核心：voucher_service / report_service / invoice_service / attachment_service / period_end / tax / cashflow / ai/）
   ↓ 读写
models/*（SQLAlchemy ORM） + schemas/*（Pydantic I/O）
   ↓
core/*（DB Session、安全、配置、LLM gateway 加密）
```
- 路由层只做 HTTP 接入：取 session → 调 service → 返回 JSON；**不在路由里写业务 SQL 或业务分支**。
- AI 业务集中在 `ledger/ai/`（agent.py + suggest.py + parse.py），其他模块不可直接 instantiate LLM client。

## 三层护栏模式（AI 生成数据必须套用）
1. **Prompt 软约束**（agent.py SYSTEM_PROMPT）：注入账套公司名/税号、今天日期、纳税人类型价税规则、重复入账检查要求。
2. **用户确认**（前端候选卡片 + ask_user 工具）：购方不匹配、疑似重复、新建往来时必须 ask_user，AI 不能自行拍板。
3. **代码硬兜底**（voucher_service / suggest.py）：模型不听话时拦截/修正——新增 AI 路径功能必须增加对应兜底函数，按以下既有模式：
   - `_hard_check_duplicate(amount, date, invoice_no, history)` 命中转 ask_user，历史已放行则通过（防死循环）
   - `_apply_cf_items(lines)` 非现金行清 cf_item；合法值保留；缺/非法 → 对方最大行科目映射
   - `_sanitize_contacts(lines, session)` 逐行校验 contact_id 真实存在，无效→置空
   - `_tax_rule(book)` 动态注入价税口径

## 数据库与迁移
- **任何列/表/索引变更必须走 Alembic 迁移**（`backend/alembic/versions/<hash>_<desc>.py`），严禁直接改 model.py 就说完成。
- 迁移参考 `b3e1c8f4d2a7_add_cf_item_to_voucher_line.py` 模式：upgrade 加 `op.add_column()` + downgrade 对称 `op.drop_column()`。容器启动脚本会 auto `alembic upgrade head`。
- 新增 nullable 列通常需要一次性回填脚本（参考 `backend/backfill_cf.py`）：按兜底规则查询缺值行 → 分页 update。脚本幂等，跑完一次即可。
- ORM 风格：SQLAlchemy 2.0 declarative，`Mapped[T|None] = mapped_column(...)`，关系用 `relationship()` lazy='selectin'。

## I/O 契约
- 所有对外接口入参/出参必须定义 Pydantic Schema（`schemas/*.py`），禁止路由里 `request.json()` 裸用。
- 新增列时 **同步改 model.py + schemas 输入输出** 两处（例：cf_item 同时在 VoucherLineIn/VoucherLineOut 声明）。
- Decimal 字段统一 `condecimal(max_digits=18, decimal_places=2)`；金额转 JSON 后保持两位字符串。
- 错误码：业务拒绝统一抛 HTTPException(4xx, detail=中文说明)；用户可读 detail 用中文。

## 关键模块规则
### voucher_service.py
- prepared 行初始 cf_item=None，借贷平衡后调 _apply_cf_items。
- `source=manual` 必须 attachment_count ≥ 1；AI 来源自动挂电子原件。
- 编辑/删除前调 `_ensure_editable(voucher)`：草稿/待审/已审可改，过账/冲销锁。

### cashflow.py
- `_flows(vouchers)` 返回 `[ (cf_key, net_amount) ]`：先累计所有 `cf_item` 标注净量 → 剩余未标注现金净量走「对方最大行科目映射」兜底。
- CASH_ACCOUNTS = 1001/1002/1012 前缀；INFLOW_MAP / OUTFLOW_MAP 对方科目 key → cf_key。

### report_service.py
- `template_check(book_id, report)` 必须包含四部分：恒等式结果、未映射叶子科目（标红有余额）、行→科目映射全景、公式脏引用。
- `PUT template-row` 校验科目存在/符号 ±1、calc 行拒绝编辑；`POST template-reset` 幂等（删行再 seed 默认）。
- 默认模板「未分配利润」含损益类 14 项，保证期中恒平衡。

### agent.py
- SYSTEM_PROMPT 规则每加一条**必须同步想清楚：如果模型不遵守，代码层用什么兜底**。
- `run_agent` 开始时注入：book.company_name、book.tax_no、book.taxpayer_type（_tax_rule）、today_date（避免模型编造日期偏移查重窗口）。
- 工具白名单仅查询类 + ask_user，**不加入账/审核/删除类工具**。

### suggest.py
- 落账时 `_link_invoice` 按发票号回写 voucher_id；发票号正则 20 位优先于 8 位。
- 台账查不到发票号时 `_create_invoice_from_fields` 自动补录：方向按购方匹配判定、开票日期保留票面原始日期。

### attachment_service.py
- save_attachment 后写入 SHA256，UUID 文件名覆盖原名；删除物理路径 + 数据库记录双删。

## 代码风格
- PEP 8，snake_case 变量/函数，PascalCase 类，全面类型注解。
- 业务函数写 docstring："""说明参数/返回/抛出异常"""。关键分支（如硬兜底的三种路径）加行级注释说明触发条件。
- 长函数拆小：例 voucher_service._apply_cf_items 独立成函数，而不是埋在 create_voucher 的 300 行里。
- 改 `开发进度与交接.md` 时更新「更新时间」戳与对应小节；不要光改代码忘了文档。
