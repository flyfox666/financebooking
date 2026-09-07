alwaysApply: false
description: 当对话涉及 AI 记账智能体、凭证候选生成、发票解析、重复入账检测、现金流量标注、往来单位处理相关任务时，智能触发该规则。
---

# AI 记账三层护栏模式（AI 记账相关任务智能触发）

> 背景：AI 生成账务数据时模型可能"不听话"（编造 ID、忽略开票日期、错拆税、漏标现金流量、购方不匹配还入账）。所有 AI 路径统一三层：Prompt 软约束 → 用户确认 → 代码硬兜底。缺任何一层视为未完成。

## 第 1 层 · System Prompt（agent.py SYSTEM_PROMPT）
每次调 `run_agent` 必注入上下文：
- **账套公司信息**：`book.company_name` + `book.tax_no`（用于购方校验——购方名/税号≠账套时必须 ask_user 警告）。
- **今天日期**：字符串注入（模型自行得知日期是幻觉高发区，会把日期编造成 2025/2024 导致查重窗口偏移）。
- **纳税人价税规则**：`_tax_rule(book)` 根据 book.taxpayer_type 输出。小规模：进项严禁拆税 + 销项 1% 分离；一般纳税人双向分离。
- **重复入账前置要求**：生成凭证前**必须先调用 check_duplicate 工具**；命中则转述给用户确认。
- **现金流量标注**：现金类行必须按"摘要+对方科目经济实质"填 cf_item（附 14 项对照表+示例）。
- **最终 JSON 格式说明**：明确 lines[].cf_item 字段，模型要输出合法 key 之一或 null。

## 第 2 层 · 用户确认
以下场景 AI 不得自行决策，**必须 ask_user**：
| 场景 | 判定条件 | 问法示例 |
|---|---|---|
| 购方不匹配 | PDF 购方名/税号 ≠ 账套公司 | 「该发票购方为「戴德梁行…」与账套「…」不一致，是否仍按本企业收票登记进项？」 |
| 疑似重复入账 | check_duplicate 命中（发票号已入账；或同日+同金额+科目类别一致+往来不冲突——同一单据二次录入特征） | 「检测到 X 月 X 日已有 ¥N 的同科目同往来业务（凭证号 XXX），是否为同一单据重复录入？继续 / 取消」 |
| 新建往来单位 | 只读工具未找到档案，返回 requires_user_action，用户须在往来管理创建 | 「供应商「XXX」在台账中未找到，是否新建？地址/税号=…」 |
| AI 没填开票日期 / 用了今天 | 用户描述里给出了开票日期但 voucher_date != 该日期 | 「本次开票日期为 2026-08-15，是否使用该日期而非今天？」 |

重复风险确认必须在候选卡片中完成：服务器重查、返回风险指纹，用户填写核对理由后提交。模型回复或历史中的“确认”均不构成授权；精确发票身份重复不可放行。

## 第 3 层 · 代码硬兜底（必写！）
Prompt + 用户确认都挡不住的模型犯错，靠代码层在数据落库前强制修正/拦截：

| 兜底函数 | 位置 | 修正逻辑 |
|---|---|---|
| `_hard_check_duplicate` | agent.py（run_agent 凭证返回前） | 发票号精确匹配（已入账 only）+ 实质性重复（同日+同金额+科目类别含前缀+往来不冲突）；疑似命中→带警告候选，确认端重新校验并要求指纹/理由。仅金额相同不拦 |
| `_apply_cf_items` | voucher_service.prepare 后 | ①非现金行清 cf_item→None ②合法 key 保留 ③缺/非法→对方最大行科目×方向查 INFLOW/OUTFLOW MAP |
| `_sanitize_contacts` | voucher_service.create_voucher 入参 lines | 逐行查 Contact 表，无效 ID（数据库不存在 / 类型不匹配行方向）→ force None，卡片里让用户补 |
| `_tax_rule` 动态注入 | agent.run_agent 开头 + suggest | 不再写死小规模口径，按 book.taxpayer_type 分 3 档返回文本注入 system prompt |
| 发票号正则顺序 | suggest._link_invoice + 解析工具 | 先 20 位长号 `\b\d{20}\b` 后 8 位 `\b\d{8}\b`，防止截前 8 位 |
| cf_item 存量回填 | backfill_cf.py（一次性脚本模式参考） | 对所有 history_cash_lines 调用同一 _apply_cf_items 规则 update；幂等 |

### 新增功能时的护栏检查清单
完成 AI 路径新功能后，自问：
1. [ ] SYSTEM_PROMPT 是否明确讲了规则 + 注入上下文？
2. [ ] 至少找 2 个模型可能犯的典型错，并写了对应的兜底 if？
3. [ ] 回归 3+ 场景（正常 / 边界 / 异常输入）都测过？
4. [ ] 存量数据如果有缺值，有一次性回填脚本？

## 常见坑与教训（来自实际修复）
1. ❌ 旧版：重复入账检测"发票号查重"没加 voucher_id not null 条件 → ✅ 加条件（台账已导入未入账不重复）
2. ❌ 旧版：小规模进项拆税规则只在 suggest.py 写了没在 agent.py system prompt 注入 → ✅ _tax_rule 统一动态注入两处
3. ❌ 旧版：模型编造 contact_id 不调 find_or_create_contact → ✅ _sanitize_contacts 逐行校验
4. ❌ 旧版：发票号正则 8 位放前面 → 20 位截成 8 → ✅ 长号优先
5. ❌ 旧版：system prompt 没给今天日期 → 模型编造 2025 年日期，查重窗口对不上 → ✅ 注入 today_date 字符串
