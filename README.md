# 喷涂加工企业ERP系统

面向表面喷涂加工企业的业财一体化ERP，本地局域网部署，开箱即用。

## 核心特性

- **业财强联动**：事件总线+钩子驱动，业务发生瞬间自动生成财务单据/库存流水，杜绝月底Excel回追
- **工单成本归集**：所有材料/人工/费用强制挂工单，完工时实时汇总成本与利润
- **喷涂行业特化**：客供料台账、涂料利用率追踪、批次追溯、返工成本、委外管理
- **表驱动审批**：审批流可配置，运营后台自助调整
- **Agent API**：scoped token，可读不可改业务数据，仅可写配置类（预警/报表/审批流），migration走人工审批
- **开箱即用**：Docker一键部署，SQLite起步，PostgreSQL储备

## 技术栈

FastAPI + SQLAlchemy 2.0 + JWT + Docker | 前端预留 Vue3

## 快速开始

### Docker部署（推荐）

```bash
cp .env.example .env  # 按需修改飞书/邮件webhook
docker compose up -d
# 访问 http://服务器IP:8000/docs
```

### 本地开发

```bash
pip install -r requirements.txt
python scripts/seed_data.py   # 初始化基础数据+模拟数据
uvicorn app.main:app --reload --port 8000
```

## 默认账号（密码均为 123456）

| 用户名 | 角色 | 用途 |
|---|---|---|
| admin | ADMIN | 超管 |
| sales01 | SALES | 销售 |
| ops01 | OPERATION | 运营助理 |
| fin01 | FINANCE | 财务 |
| wh01 | WAREHOUSE | 仓管 |
| gm01 | GM | 总经理 |
| mgr_a | MANAGER | 车间厂长 |

## 业务主链路

```
客户池 → 销售下单(DRAFT→SUBMITTED→EFFECTIVE)
  → 财务自动建应收草稿 + 预收款核销 + 抄送运营/总经理
  → 运营清点(客供料/自营料)
    ├ 客供料: 记台账,不进库存账,不生成领料单
    └ 自营料: 生成加工单 → 自动领料单 → 仓管出库扣库存 + 写材料成本
  → 厂长完工填实际用量/工时
  → 运营确认完工:
      ① 应收转OPEN  ② 成本汇总(材料+人工+制造费用)
      ③ 成品入库    ④ 退料入仓   ⑤ 涂料利用率计算
      ⑥ 通知销售催款
  → 财务收款核销应收
```

## 事件钩子（业财联动核心）

| 事件 | 财务动作 | 库存动作 | 通知 |
|---|---|---|---|
| order.submitted | - | - | 运营+GM |
| order.effective | 建应收草稿+预收款核销 | - | 运营+GM |
| work_order.released | - | 自动领料单/客供料台账 | 厂长 |
| material.confirmed | 写材料成本 | 扣库存 | - |
| completion.confirmed | 应收OPEN+成本结转 | 成品入库+退料 | 运营/GM+销售催款 |
| purchase.received | 建应付 | 入库 | - |
| payroll.confirmed | 建付款(期间费用) | - | - |
| receipt.created | 核销应收 | - | - |

## Agent API 安全边界

| 允许 | 禁止 |
|---|---|
| 查询业务表(白名单) | 直连DB |
| 写 alert_rules/report_templates/flow_definitions | 写 finance_docs/inventory_txns |
| 提交 migration 提案 | 直接执行 migration |
| 读 KPI/schema | 访问 users/roles 等敏感表 |

## 测试

```bash
python -m pytest tests/ -q
# 47 tests passed
```

覆盖：认证/RBAC、订单全流程、客供料vs自营料、完工业财联动、收款核销、采购审批+入库+应付、工资发放、KPI/预警/利用率、Agent API、审计日志、状态机校验。

## 切换 PostgreSQL

1. 取消 `docker-compose.yml` 中 postgres 服务注释
2. 改环境变量 `DB_DRIVER=postgresql`、`DB_URL=postgresql://erp:erp_secret@postgres:5432/erp`
3. SQL方言已中立，零代码改动

## 目录结构

```
app/
├── core/        db/auth/permissions/audit/event_bus/notify
├── models/      11域ORM模型
├── api/         15个路由模块
├── hooks/       业财联动钩子(核心)
└── main.py
scripts/seed_data.py   模拟数据
tests/test_full_flow.py  全流程测试
```
