# 喷涂加工 ERP DEMO V2 重构方案

## Context

V1 已实现业财联动骨架（47 测试通过），但客户现场交流暴露出重大方向偏差与缺失模块：
- **方向纠正**：业务是「订单全程驱动」，不是收货驱动；之前客户事后补单是不规范操作，ERP 要正向驱动
- **销售权限**：订单仅 owner+GM+运营助理+财务+ADMIN 可见，其他销售互不可见；客户名默认显示字母简称防泄密
- **双公司主体**：一般纳税人（专票）+小规模（普票/现金），订单开始即勾选开票主体，款项三流两公司
- **新增模块**：合同管理、商机管理、来货登记（订单关联）、送货单（三联单）、实收调价审批（GM 必审）、车间大屏、动态透视分析、回款预期预警
- **UX 铁律**：厂里文化偏低，能菜单选择的不输入，字典化下拉降低学习成本与错误

目标：在现有 V1 基础上增量重构，保留业财联动核心，补齐销售域/发货域/分析域，不推翻重写。

## 架构决策（YAGNI 取舍）

- 不引入 Alembic，沿用 `create_all` + 一次性 `migrate_v2.py` 跑 ALTER ADD COLUMN
- 不引入 pandas/Vue Router/构建工具，保持 CDN 单页
- 新销售域 6 张表集中放 `app/models/sales.py`
- 字典单表 `dicts`，按 `type` 区分
- 透视表引擎纯 SQLAlchemy 动态构造，不引外部库
- 打印走浏览器原生 `window.print()`，不引 PDF 库
- 大屏独立 HTML（`screen.html`），不复用 app.js

## 数据模型变更

### 新建 `app/models/sales.py`（6 张新表）

| 表 | 关键字段 |
|---|---|
| `companies` | code/name/tax_type(GENERAL\|SMALL)/tax_no/bank_name/bank_account |
| `contracts` | contract_no/customer_id/company_id/amount/signed_date/attachment_url/status/owner_user_id |
| `opportunities` | customer_id/expected_amount/stage(LEAD\|FOLLOW\|QUOTE\|WON\|LOST)/expected_close_date/owner_user_id |
| `receiving_logs` | order_id(必填)/customer_id/received_at/part_name/part_spec/qty/status |
| `delivery_notes` + `delivery_note_items` | delivery_no/order_id/company_id/status(PENDING\|SHIPPED)/shipped_at/shipped_by_user_id |
| `sales_adjustments` | order_id/original_amount/adjusted_amount/reason/status(PENDING\|APPROVED\|REJECTED)/initiator_user_id/approval_instance_id |

### 新建 `app/models/dict.py`
`Dict: type/code/name/parent_code/sort/status`，单表通用，`(type,code)` 唯一。

### 修改现有模型
- `customer.py`：+`short_code`(字母简称)、+`default_company_id`
- `order.py`：+`company_id`/`contract_id`/`opportunity_id`/`billing_type`(SPECIAL_VAT\|NORMAL\|CASH)/`delivery_status`(PENDING_DELIVERY\|DELIVERED)；`status` 枚举加 PROCESSING/PENDING_DELIVERY/DELIVERED
- `finance.py`：`FinanceDoc` +`company_id`/`billing_type`/`adjusted_amount`
- `analysis.py`：+`PaymentSchedule`(contract_id/order_id/due_date/expected_amount/actual_amount/status) 用于回款节点
- `models/__init__.py`：注册新模型

## 权限模型升级（`app/core/permissions.py`）

`ROLE_SCOPE` 升级为订单可见性矩阵：
- SALES → orders: `own`（仅自己的）
- OPERATION/FINANCE → orders: `all_read`（全可见只读）
- GM → `*`: read
- ADMIN → 跳过

新增函数：
- `mask_customer(customer, user) -> dict`：非 owner/GM/ADMIN 看到的 `name` 替换为 `short_code`
- `apply_scope_filter` 增加 `all_read` 分支
- 订单 `_to_dict`、客户列表调用 `mask_customer` 做后端脱敏

## 业财联动钩子（追加到 `app/hooks/builtin_hooks.py`）

- 扩展 `_order_effective_finance`：创建应收时带 `company_id`/`billing_type`
- 扩展 `completion.confirmed`：完工后置订单 `delivery_status=PENDING_DELIVERY`
- 新增 `delivery.shipped`：扣成品库存 + 通知销售催尾款
- 新增 `sales_adjustment.approved`：调整应收 `adjusted_amount` + 审计
- 扩展 `_receipt_settle`：双主体校验（收款主体必须=订单主体，防串户）
- `approvals.py._apply_approval_result` 加 `SALES_ADJUSTMENT` biz_type 分支

## 动态分析引擎

### 新建 `app/core/pivot.py`
```
build_pivot(db, source, row_dim, col_dim, metrics, filters) -> {rows, cols, cells, totals}
```
- source 白名单：orders/finance_docs/work_orders/completions/inventory_txns
- metrics: [{field, agg: SUM|COUNT|AVG|DISTINCT_COUNT, alias}]
- 纯 SQLAlchemy `func` 动态 group_by，Python 二次拼矩阵

### `app/api/analysis.py` 新接口
- `POST /api/analysis/pivot`：通用透视
- `GET /api/analysis/dims?source=xxx`：返回可用维度/指标元数据（前端动态生成下拉）
- `POST /api/analysis/finance-pivot`：财务专用（带 scope 过滤）
- `check_alerts` 加 `PAYMENT_DUE`：扫描 PaymentSchedule 到期前 N 天

## 车间大屏（独立页面）

- 新建 `static/screen.html` + `static/screen.js`（Vue3 + ECharts，不复用 app.js）
- `main.py` 加路由 `GET /screen` 返回 screen.html
- 新建 `app/api/screen.py`：`GET /api/screen/overview` 一次聚合所有大屏数据
- 内容：顶部时钟/工厂名霓虹、左侧订单状态环图+车间卡片、中部产线进度条+滚动告警、右侧预警清单+KPI 翻牌、底部跑马灯
- 30s 轮询，车间场景用公开端点（不走登录）

## 前端模块调整（`static/app.js` + `style.css`）

### 新增菜单
```
业务: 工作台/商机管理★/客户管理/销售订单(改)/合同管理★/来货登记★
生产: 加工单(改+打印)/完工单/客供料台账
发货: 送货单★
财务: 财务单据(改)/实收调价★/工资发放
采购: 采购申请/采购单
分析: 动态透视★/财务分析★/预警分析(改)
系统: 审批待办/通知日志/字典管理★/Agent API
```

### 改造点
- OrdersPage：表单加 company_id/billing_type/contract_id 下拉；列表加开票主体列；客户名按角色脱敏
- CustomersPage：加 short_code/default_company_id
- FinancePage：收款带 company_id；调价申请入口
- WorkOrdersPage/CompletionsPage：加打印按钮 → `window.open('/api/print/...')`
- 新增 `loadDict(type)` 工具，所有 PROCESS_TYPE/PAINT_SPEC/PART_SPEC/INDUSTRY 等字段改 `el-select allow-create`

## 打印功能（`app/api/print.py`）

- `GET /api/print/work-order/{id}` → HTML + `window.print()`
- `GET /api/print/delivery-note/{id}`
- `GET /api/print/process-card/{work_order_id}`（工艺单）

## 字典化（`app/api/dicts.py`）

| type | 落点 |
|---|---|
| PROCESS_TYPE | OrderItem.process_requirement 下拉 |
| PAINT_SPEC | OrderItem.paint_spec 下拉 |
| PART_SPEC | OrderItem.part_spec 下拉 |
| INDUSTRY / SETTLEMENT_CYCLE / STAGE / WORKSHOP | 对应字段下拉 |

接口：`GET /api/dicts?type=` / `POST` / `PUT` / `DELETE`（仅 ADMIN）

## 实施顺序

1. **基础模型+迁移**：新建 sales.py/dict.py，改 customer/order/finance/analysis 模型，写 migrate_v2.py + 更新 seed_data.py
2. **权限+字典**：改 permissions.py，新建 dicts.py API
3. **销售域接口**：companies/contracts/opportunities/receiving_logs/deliveries/sales_adjustments API + 改 orders/customers
4. **业财钩子**：扩展 builtin_hooks.py + approvals.py
5. **动态分析**：pivot.py + analysis.py 接口
6. **前端业务模块**：app.js 新增 6 页 + 改造 4 页 + 字典化 + 菜单树
7. **打印+大屏**：print.py + screen.html/js + screen.py API + main.py 路由
8. **测试**：扩展 test_full_flow.py（双主体分流/调价审批/发货链路/透视/订单可见性矩阵）

## Critical Files

- `e:\trae\liu\app\models\sales.py`（新建：6 张销售域表）
- `e:\trae\liu\app\models\dict.py`（新建：字典表）
- `e:\trae\liu\app\core\permissions.py`（订单可见性矩阵 + 脱敏）
- `e:\trae\liu\app\core\pivot.py`（新建：透视表引擎）
- `e:\trae\liu\app\hooks\builtin_hooks.py`（追加发货/调价/双主体钩子）
- `e:\trae\liu\app\api\orders.py`（加公司/合同/主体字段 + 脱敏）
- `e:\trae\liu\app\api\analysis.py`（加 pivot/dims 接口）
- `e:\trae\liu\app\api\print.py`（新建：3 个打印端点）
- `e:\trae\liu\app\api\screen.py`（新建：大屏聚合接口）
- `e:\trae\liu\static\app.js`（6 新页 + 4 改造页 + 字典化）
- `e:\trae\liu\static\screen.html` + `screen.js`（新建：车间大屏）
- `e:\trae\liu\scripts\migrate_v2.py`（新建：字段迁移）
- `e:\trae\liu\tests\test_full_flow.py`（扩展测试）

## Verification

1. 跑 `python scripts/migrate_v2.py` 增字段 + seed 公司/字典
2. 重启 uvicorn，访问 `/docs` 确认新接口注册
3. `pytest tests/test_full_flow.py -v` 全绿（含新测试）
4. 浏览器验证：
   - sales01 登录看不到 sales02 的订单，客户名显示简称
   - 新建订单能选开票主体/合同/商机
   - 完工后订单进 PENDING_DELIVERY，送货单确认后进 DELIVERED
   - 调价申请提交后 GM 待办出现，审批通过应收余额调整
   - 动态透视页选维度+指标能出表格+图表
   - 访问 `/screen` 看到车间大屏
   - 加工单点打印弹出打印对话框
