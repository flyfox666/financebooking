alwaysApply: false
globs: "**/*.html, **/*.css, **/*.js"
description: 前端开发规范。当编辑 app.html、index.html 或任何前端 JS/CSS 时自动生效。
---

# 前端开发规范（*.html / *.js / *.css 匹配生效）

## 单文件架构
- 主界面全部集中在 `backend/app/static/app.html`（~2000 行，单页应用，无框架、无构建步骤）。
- 改代码前先定位函数/块，局部 Edit，**禁止整文件重写**（极易丢已验证功能）。
- 全局入口：layout、renderVoucherDetail、renderReports、loadSummary、showCard、fitVoucherLines、toggleAttModal、chatPanel（悬浮窗）——改动任一需回归对应流程。

## 布局与响应式（极高频问题）
- 主网格中间列必须是 `minmax(0,1fr)`，不能写死 `1fr`（否则 nowrap 表格/卡片撑破 grid 造成横向溢出）。
- 两档断点：`@media (max-width: 1024px)` 隐藏右栏概要；`@media (max-width: 680px)` 单列 + 侧栏变顶部横滑 + topbar 换行。
- 所有容器宽度变化触发点**必须调用 fitVoucherLines()**：窗口 resize、悬浮窗缩放 `mouseup`、悬浮窗折叠/展开、凭证渲染 `rerenderCard/showCard/renderVoucherDetail`、增删行 `addLine`。
- 凭证 zoom 下限 0.55，父容器必须外包 `.vc-fit`。
- AI 悬浮窗最小宽度 = `Math.min(320px, innerWidth - 32)`。恢复/resize 两处都钳制。
- 验证：4 档宽度 1280/800/640/400 横向溢出 = 0；520px 容器凭证 zoom 计算正确（自然宽 ÷ 可用宽，例 787÷492=0.584）。

## 凭证渲染七条
- 凭证卡片/编辑器表头、详情表都是**7 列**：摘要 / 科目编码 / 科目名称 / 往来单位 / 现金流量 / 借方 / 贷方 + 最右删除操作列。
- 现金类科目（1001/1002/1012 前缀匹配）行才渲染 cf_select 下拉；非现金行该列留空或填 `"-"`。
- `cfSelectHTML(val)` 输出 14 项 `<option>`，value = CASHFLOW KEYS（sales_in / other_op_in / ... / other_out）。
- 科目编码列左对齐（去掉 `.num` 右对齐类），编码与名称两列独立。
- 金额输入：失焦 `toFixed(2)`；按 `=` 光标所在列自动填平衡值（贷方=借差，借方反向可为负数提示）；`normLines()` 统一清洗空金额防 "Input should be a valid decimal"。
- 新增行默认 `cf_item: null`（addLine 缺字段补齐）。

## 附件弹层
- 头部按钮「📎 附件（vc_att_count）」→ 打开居中浮层。
- 浮层内：文件名/大小 + 打开新窗口 + 删除 confirm + 「＋上传原件」+ 批量多文件上传。
- 上传/删除后实时刷新 vc_att_count 与列表。
- 过账/冲销凭证不显示删除按钮。

## 本期概要侧边栏
- `loadSummary()` 固定三组 9 行：📄 凭证状态（草稿/待审/已审/已过账，可点击跳转列表筛选）· 📈 本期经营（营业收入/净利润）· 💰 资金与往来（货币资金/应收/应付）；空值固定 0.00。

## 映射体检（报表 Tab）
- renderReports 四个标签：①恒等式徽章 ✅/❌ 数字 ②未映射科目（有余额标红）③映射全景行表（BS/PL 各一张）④行级编辑 + 「恢复默认模板」。
- 行编辑内联：±符号选择器 + 66 科目下拉 + 添加/移除/保存/取消。保存后恒等式徽章实时变化。

## 代码风格
- 选择器：若已定义 `$=document.querySelector, $$=...` 就复用；否则标准 DOM API。
- 事件绑定用内联 `onclick` 或 `addEventListener`，保持一致性（不要一半内联一半监听）。
- 长函数拆命名子函数（已有的 cfSelectHTML / lineEditorHTML / fitVoucherLines 模式），新 UI 别堆在一个 200 行函数里。
