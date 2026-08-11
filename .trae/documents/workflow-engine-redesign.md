# 真正的工作流引擎 + 可视化设计器 重构方案

## Context（为什么做）

当前系统是"伪工作流"——有三张表骨架（[FlowDefinition/FlowInstance/FlowTask](file:///e:/trae/liu/app/models/approval.py#L6-L42)），但：
- 全系统只有采购请求一种业务接入（[purchase_requests.py:59](file:///e:/trae/liu/app/api/purchase_requests.py#L59) 调 `start_flow`），其他单据全是死状态枚举
- `_apply_approval_result` 只认 `PURCHASE_REQUEST`（[approvals.py:116](file:///e:/trae/liu/app/api/approvals.py#L116)），审批通过后其他业务状态不会变
- 无 seed 数据，库里没流程定义，`start_flow` 直接返回 None = 没审批
- 前端 [ApprovalsPage](file:///e:/trae/liu/static/app.js#L2090) 有个写死的 `APPROVAL_FLOW` 装饰步骤条，不是真实节点
- 全 app.js 零 `el-timeline/el-steps`，单据详情看不到"卡在哪个节点、谁在审、停多久"
- 无流程设计器，节点不能可视化调节

用户诉求：**真正的工作流模式**——不只是审批，业务流转（如来货登记→运营核对→财务入账→归档）也是工作流；可视化目的是一眼看出"流程怎么走、我在哪、还剩几环"；要拖拽设计器、节点模块化可调，"不能躺在老式表格里"。

目标：从"伪工作流骨架"升级为"闭环可用 + 可视化拖拽设计器 + 单据详情真实流转轨迹"。

---

## 设计原则

1. **流转 ≠ 审批**：节点类型分 `approve`（审批：同意/驳回）和 `process`（流转：确认/推进），统一进 FlowInstance
2. **节点模块化预留**：FlowDefinition.nodes 扩展为 `[{seq,name,type,approver_role,condition,next}]`，预留 `condition`（条件分支）和 `type`（节点类型）字段，本期实现线性流转，字段先留好供迭代
3. **复用优先**：前端复用现有 [flow-steps CSS](file:///e:/trae/liu/static/style.css#L160-L169)，设计器用轻量 CDN 库 Drawflow，不引入构建步骤
4. **接入不动业务状态枚举**：业务单据的 `status`（如 ReceivingLog.RECEIVED）保持原样，流程状态由 FlowInstance.status 表达，二者解耦

---

## 后端改动

### 1. 扩展流程引擎 — [app/api/approvals.py](file:///e:/trae/liu/app/api/approvals.py)

**泛化 `_apply_approval_result`**（当前 [L116-L122](file:///e:/trae/liu/app/api/approvals.py#L116) 只认 PURCHASE_REQUEST）：
改为 biz_type → handler 注册表模式：
```python
BIZ_HANDLERS = {
    "PURCHASE_REQUEST": lambda db, inst, ok: _set_status(db, PurchaseRequest, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
    "RECEIVING":        lambda db, inst, ok: _set_status(db, ReceivingLog, inst.biz_id, "CONFIRMED" if ok else "REJECTED", inst.id),
    "COMPLETION":       lambda db, inst, ok: _set_status(db, Completion, inst.biz_id, "CONFIRMED" if ok else "REJECTED", inst.id),
    "EXPENSE":          lambda db, inst, ok: _set_status(db, Expense, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
    "SALES_ADJUSTMENT": lambda db, inst, ok: _set_status(db, SalesAdjustment, inst.biz_id, "APPROVED" if ok else "REJECTED", inst.id),
}
```
`_set_status` 通用工具：查模型、设 status、设 approval_instance_id。

**新增端点**：
- `GET /api/approvals/instances/{biz_type}/{biz_id}` — 核心：返回流程实例 + 全部节点（已完成的 FlowTask 历史 + 当前节点 + 未来节点定义），供前端画时间轴。结构：
  ```json
  {"instance": {...}, "nodes": [{"seq","name","type","status":"done|current|pending","assignee_name","handled_at","comment","duration"}]}
  ```
- `POST /api/approvals/tasks/{tid}/transfer` — 转交（body: {to_user_id, comment}）
- `POST /api/approvals/tasks/{tid}/urge` — 催办（发通知给 assignee）
- `PUT /api/approvals/definitions/{id}` — 更新流程定义（设计器保存）
- `DELETE /api/approvals/definitions/{id}` — 删除（软删 status=INACTIVE）

**扩展 `start_flow`**（[L40](file:///e:/trae/liu/app/api/approvals.py#L40)）：节点无 assignee 时，按 role 兜底分配；首个节点 type=process 时自动跳过审批直接推进（纯流转场景）。

### 2. 各业务 submit 接入 start_flow

| 业务 | 文件 | 接入点 | biz_type |
|---|---|---|---|
| 来货登记 | [app/api/sales.py](file:///e:/trae/liu/app/api/sales.py) 来货登记创建接口(~L320) | 创建后调 start_flow | RECEIVING |
| 完工单 | [app/api/completions.py:90](file:///e:/trae/liu/app/api/completions.py#L90) confirm | 提交时调 start_flow | COMPLETION |
| 费用申请 | [app/api/expense.py:58](file:///e:/trae/liu/app/api/expense.py#L58) submit | 提交时调 start_flow | EXPENSE |
| 调价申请 | [app/api/sales.py](file:///e:/trae/liu/app/api/sales.py) 调价接口 | 提交时调 start_flow（已有 approval_instance_id 字段 [sales.py:146](file:///e:/trae/liu/app/models/sales.py#L146)） | SALES_ADJUSTMENT |
| 采购请求 | [purchase_requests.py:59](file:///e:/trae/liu/app/api/purchase_requests.py#L59) | 已接入，无需改 | PURCHASE_REQUEST |

模型层：ReceivingLog/Completion/Expense 需补 `approval_instance_id = Column(Integer)` 字段（参考 [purchase.py:30](file:///e:/trae/liu/app/models/purchase.py#L30) 已有写法）。

### 3. Seed 默认流程定义

在 [app/main.py](file:///e:/trae/liu/app/main.py) startup 或现有 init 脚本里 seed 5 条 FlowDefinition：
```python
SEED_FLOWS = [
  ("来货登记流转","RECEIVING",[{"seq":1,"name":"仓管登记","type":"process","approver_role":"WAREHOUSE"},
                              {"seq":2,"name":"运营核对","type":"approve","approver_role":"OPS"},
                              {"seq":3,"name":"财务入账","type":"approve","approver_role":"FINANCE"},
                              {"seq":4,"name":"归档","type":"process","approver_role":"OPS"}]),
  ("完工单确认","COMPLETION",[{"seq":1,"name":"厂长提交","type":"process","approver_role":"PRODUCTION"},
                             {"seq":2,"name":"质检确认","type":"approve","approver_role":"PRODUCTION"},
                             {"seq":3,"name":"运营归档","type":"approve","approver_role":"OPS"}]),
  ("费用审批","EXPENSE",[{"seq":1,"name":"部门主管","type":"approve","approver_role":"GM"},
                        {"seq":2,"name":"财务审核","type":"approve","approver_role":"FINANCE"},
                        {"seq":3,"name":"总经理审批","type":"approve","approver_role":"GM"}]),
  ("采购请求审批","PURCHASE_REQUEST",[{"seq":1,"name":"部门主管","type":"approve","approver_role":"GM"},
                                      {"seq":2,"name":"财务审核","type":"approve","approver_role":"FINANCE"}]),
  ("调价申请","SALES_ADJUSTMENT",[{"seq":1,"name":"总经理审批","type":"approve","approver_role":"GM"}]),
]
```
幂等：先查 biz_type+ACTIVE 是否存在，存在跳过。

---

## 前端改动（[static/app.js](file:///e:/trae/liu/static/app.js) + [static/index.html](file:///e:/trae/liu/static/index.html)）

### 1. 引入 Drawflow（CDN，纯JS流程图拖拽库）

[index.html](file:///e:/trae/liu/static/index.html) 加：
```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/drawflow@0.0.59/dist/drawflow.min.css">
<script src="https://cdn.jsdelivr.net/npm/drawflow@0.0.59/dist/drawflow.min.js"></script>
```
选型理由：纯JS无构建依赖、CDN友好、轻量(~20KB)、原生支持节点拖拽+连线，适合线性+简单分支的流程设计。LogicFlow 功能更强但体积大，本期用不到。

### 2. 新增 FlowTrack 组件（真实流转时间轴）— 核心

替代 [ApprovalsPage:2141](file:///e:/trae/liu/static/app.js#L2141) 的写死 `APPROVAL_FLOW`。横向步骤条，调 `GET /api/approvals/instances/{biz_type}/{biz_id}`：
- **已完成节点**：绿色实心圆+对勾，下方显示处理人姓名+处理时间+审批意见
- **当前节点**：蓝色脉冲动画高亮，显示当前处理人+已停留时长（"已停留 2 小时"）
- **未来节点**：灰色虚线圆，显示节点名+预计角色
- **驳回**：红色折线标记，显示驳回人和意见
- 复用扩展 [flow-steps CSS](file:///e:/trae/liu/static/style.css#L160)（已有 done/current 样式，补 pending 虚线 + 脉冲动画 + 处理人/时间气泡）

### 3. makeListPage 详情 drawer 嵌入 FlowTrack

[makeListPage](file:///e:/trae/liu/static/app.js#L38) 的详情 drawer（[L133-L165](file:///e:/trae/liu/static/app.js#L133)）在 detail-hero 后、基本信息前插入：
```html
<div class="detail-section" v-if="detail.data && cfg.bizType">
  <div class="ds-title">🔄 流转轨迹</div>
  <flow-track :biz-type="cfg.bizType" :biz-id="detail.data.id"/>
</div>
```
各业务页配置加 `bizType`：ReceivingPage→`RECEIVING`、CompletionsPage→`COMPLETION`、ExpensePage→`EXPENSE`、PRPage→`PURCHASE_REQUEST`。零侵入，配一行即开启。

### 4. 流程设计器页面 FlowDesignPage（拖拽配节点）

新增组件，左侧节点面板（审批节点/流转节点/条件节点拖入画布），中间 Drawflow 画布拖拽连线，右侧属性面板配节点名/角色/类型。保存调 `POST/PUT /api/approvals/definitions`。路由 `flow-design`，加入 [pageMap](file:///e:/trae/liu/static/app.js#L3472) 和左侧 [icon-rail](file:///e:/trae/liu/static/app.js#L3446)。

### 5. ApprovalsPage 改造

[ApprovalsPage:2090](file:///e:/trae/liu/static/app.js#L2090)：
- 删除写死 `APPROVAL_FLOW` 常量（[L2085](file:///e:/trae/liu/static/app.js#L2085)）
- 详情 drawer 用真实 FlowTrack 组件（按 instance.biz_type/biz_id 加载）
- 操作按钮加：通过/拒绝/转交/催办
- 列表卡片显示：单据号、节点名、处理人、停留时长、单据类型图标

### 6. 工作台待办穿透

[DashboardPage](file:///e:/trae/liu/static/app.js#L290) 右侧"我的待办"卡片，点击待办项跳转 ApprovalsPage 并定位该任务，而非跳到业务列表。

---

## 分阶段交付

**阶段1（核心闭环，先跑通）**：后端泛化 _apply_approval_result + seed 数据 + 来货登记接入 + FlowTrack 组件 + makeListPage 嵌入。验收：创建来货登记→自动起流程→运营收到任务→通过→财务收到→通过→归档，详情能看到4节点时间轴。

**阶段2（多业务接入）**：完工单/费用/调价接入 + ApprovalsPage 改造（真实 FlowTrack + 转交催办）。验收：5 种业务都能走流程，审批中心能看到真实节点。

**阶段3（拖拽设计器）**：引入 Drawflow + FlowDesignPage + 定义 CRUD。验收：能在画布拖节点连线配角色，保存后新单据按新流程走。

---

## 验证方法（端到端）

1. 服务启动后检查 seed：`GET /api/approvals/definitions` 应返回 5 条
2. 登录仓管账号，创建来货登记 → 检查 `GET /api/approvals/instances/RECEIVING/{id}` 返回 instance + 4 节点（第1个 current）
3. 登录运营账号，`GET /api/approvals/tasks/pending` 应有该任务 → POST handle approve
4. 来货登记详情 drawer 应显示 FlowTrack：第1节点绿勾、第2节点蓝脉冲、3-4灰虚线
5. 流程走完后单据 status 变 CONFIRMED，instance.status=APPROVED
6. 设计器：拖3个审批节点连线配角色保存 → 新建该 biz_type 单据按新流程走
7. 缓存：[index.html](file:///e:/trae/liu/static/index.html) 静态资源版本号递增，强刷生效

## 关键文件清单

| 文件 | 改动 |
|---|---|
| [app/api/approvals.py](file:///e:/trae/liu/app/api/approvals.py) | 泛化 apply、新增 instances/transfer/urge/CRUD 端点 |
| [app/main.py](file:///e:/trae/liu/app/main.py) | seed 默认流程定义 |
| [app/api/sales.py](file:///e:/trae/liu/app/api/sales.py) | 来货登记/调价接入 start_flow |
| [app/api/completions.py](file:///e:/trae/liu/app/api/completions.py) | 完工单接入 |
| [app/api/expense.py](file:///e:/trae/liu/app/api/expense.py) | 费用接入 |
| [app/models/sales.py](file:///e:/trae/liu/app/models/sales.py) | ReceivingLog 加 approval_instance_id |
| [app/models/workshop.py](file:///e:/trae/liu/app/models/workshop.py) | Completion 加 approval_instance_id |
| [app/models/expense.py](file:///e:/trae/liu/app/models/expense.py) | Expense 加 approval_instance_id |
| [static/index.html](file:///e:/trae/liu/static/index.html) | 引入 Drawflow CDN + 版本号 |
| [static/app.js](file:///e:/trae/liu/static/app.js) | FlowTrack/FlowDesignPage 组件、makeListPage 嵌入、ApprovalsPage 改造、路由注册 |
| [static/style.css](file:///e:/trae/liu/static/style.css) | flow-steps 扩展（pending虚线/脉冲/处理人气泡） |
