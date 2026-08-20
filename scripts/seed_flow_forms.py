# -*- coding: utf-8 -*-
"""为各工作流首节点写入标准表单配置(宜搭模式: 关联选择器+数据填充映射)。
幂等: 仅当流程为ACTIVE且首节点无form_config时写入, 已有用户自定义表单一律跳过。
运行: python scripts/seed_flow_forms.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.db import SessionLocal
from app.models.approval import FlowDefinition

# 字段快捷构造
def f(key, label, type="input", required=False, span=1, placeholder="", options=None, config=None, default=""):
    d = {"key": key, "label": label, "type": type, "required": required,
         "columnWidth": span, "placeholder": placeholder, "readonly": False,
         "defaultValue": default, "options": options or [], "config": config or {}}
    return d

def ref(key, label, source, fill_map, required=False, span=1, placeholder=""):
    return f(key, label, "ref_picker", required, span, placeholder,
             config={"source": source, "fillMap": fill_map})

def table(key, label, columns, required=False):
    return f(key, label, "detail_table", required, 1, config={"columns": columns})

def sel(key, label, opts, required=False, span=1):
    return f(key, label, "select", required, span,
             options=[{"label": o, "value": o} for o in opts])

def sec(label):
    return f("sec_" + label, label, "section", False, 1)

# ============ 各流程标准表单 ============
FLOW_FORMS = {
    "CORE_PRODUCTION": {
        "title": "订单转生产申请单",
        "fields": [
            sec("关联订单"),
            ref("order_ref", "选择订单", "orders",
                {"order_id": "id", "order_no": "order_no", "customer_name": "customer_name",
                 "order_amount": "total_amount"}, True, 1, "从订单模块选择，自动带出客户与金额"),
            f("order_no", "订单编号", "input", False, 2),
            f("customer_name", "客户", "input", True, 2),
            f("order_amount", "订单金额", "number", False, 2),
            f("delivery_date", "要求交期", "date", False, 2),
            sec("生产安排"),
            f("product_spec", "产品规格", "input", True, 2, "如：M6不锈钢螺栓"),
            f("plan_qty", "计划数量", "number", True, 2),
            f("plan_start", "计划开工", "date", False, 2),
            f("plan_end", "计划完工", "date", False, 2),
            sel("priority", "优先级", ["普通", "加急", "特急"], False, 2),
            f("workshop", "生产车间", "input", False, 2),
            f("remark", "备注", "textarea", False, 1),
        ],
    },
    "PURCHASE_REQUEST": {
        "title": "采购申请单",
        "fields": [
            sec("采购事由"),
            f("reason", "采购事由", "textarea", True, 1, "请说明采购用途与必要性"),
            ref("supplier_ref", "意向供应商", "suppliers",
                {"supplier_name": "name", "supplier_id": "id"}, False, 1, "可从供应商库选择，也可跳过手填"),
            f("supplier_name", "供应商名称", "input", False, 2),
            f("expected_date", "期望到货", "date", False, 2),
            sec("采购明细"),
            table("items", "物料明细", [
                {"key": "item_name", "label": "物料名称", "type": "text"},
                {"key": "spec", "label": "规格型号", "type": "text"},
                {"key": "qty", "label": "数量", "type": "number"},
                {"key": "est_price", "label": "预估单价", "type": "number"},
                {"key": "remark", "label": "备注", "type": "text"},
            ], True),
            f("total_amount", "预估总金额", "number", False, 2),
            f("remark", "备注", "textarea", False, 1),
        ],
    },
    "PROCUREMENT": {
        "title": "采购审批单",
        "fields": [
            sec("采购事由"),
            f("reason", "采购事由", "textarea", True, 1),
            ref("supplier_ref", "供应商", "suppliers",
                {"supplier_name": "name", "supplier_id": "id"}, False, 1),
            f("supplier_name", "供应商名称", "input", False, 2),
            f("expected_date", "期望到货", "date", False, 2),
            sec("采购明细"),
            table("items", "物料明细", [
                {"key": "item_name", "label": "物料名称", "type": "text"},
                {"key": "spec", "label": "规格型号", "type": "text"},
                {"key": "qty", "label": "数量", "type": "number"},
                {"key": "est_price", "label": "单价", "type": "number"},
                {"key": "remark", "label": "备注", "type": "text"},
            ], True),
            f("total_amount", "总金额", "number", True, 2),
            f("remark", "备注", "textarea", False, 1),
        ],
    },
    "EXPENSE": {
        "title": "费用报销单",
        "fields": [
            sec("费用信息"),
            sel("expense_type", "费用类型", ["差旅费", "办公用品", "业务招待", "交通费", "通讯费", "培训费", "其他"], True, 2),
            f("amount", "报销金额", "number", True, 2),
            f("occur_date", "发生日期", "date", True, 2),
            f("invoice_count", "票据张数", "number", False, 2),
            f("reason", "费用说明", "textarea", True, 1, "请说明费用发生的事由"),
            sec("关联业务(可选)"),
            ref("customer_ref", "关联客户", "customers",
                {"customer_id": "id", "customer_name": "name"}, False, 1, "招待/差旅涉及客户时选择"),
            f("customer_name", "客户名称", "input", False, 2),
            ref("order_ref", "关联订单", "orders",
                {"order_id": "id", "order_no": "order_no"}, False, 2),
            f("order_no", "订单编号", "input", False, 2),
            f("attachment_note", "附件说明", "input", False, 1, "发票/行程单等"),
        ],
    },
    "COMPLETION": {
        "title": "完工确认单",
        "fields": [
            sec("关联工单"),
            ref("wo_ref", "选择工单", "work_orders",
                {"work_order_id": "id", "wo_no": "work_order_no", "order_no": "order_no",
                 "customer_name": "customer_name", "product_spec": "product_spec",
                 "plan_qty": "plan_qty"}, True, 1, "从工单模块选择，自动带出产品与计划数"),
            f("wo_no", "工单号", "input", False, 2),
            f("customer_name", "客户", "input", False, 2),
            f("product_spec", "产品规格", "input", False, 2),
            f("plan_qty", "计划数量", "number", False, 2),
            sec("完工情况"),
            f("qualified_qty", "合格数量", "number", True, 2),
            f("defect_qty", "不良数量", "number", False, 2),
            f("finish_date", "完工日期", "date", False, 2),
            f("worker", "生产负责人", "input", False, 2),
            f("remark", "备注", "textarea", False, 1),
        ],
    },
    "RECEIVING": {
        "title": "来货登记单",
        "fields": [
            sec("来货信息"),
            ref("supplier_ref", "供应商", "suppliers",
                {"supplier_name": "name", "supplier_id": "id"}, True, 1, "从供应商库选择"),
            f("supplier_name", "供应商名称", "input", True, 2),
            f("received_date", "到货日期", "date", True, 2),
            f("purchase_no", "采购单号", "input", False, 2),
            f("delivery_no", "送货单号", "input", False, 2),
            sec("到货明细"),
            table("items", "到货明细", [
                {"key": "item_name", "label": "物料名称", "type": "text"},
                {"key": "spec", "label": "规格", "type": "text"},
                {"key": "qty", "label": "到货数量", "type": "number"},
                {"key": "qualified_qty", "label": "合格数量", "type": "number"},
                {"key": "remark", "label": "备注", "type": "text"},
            ], True),
            f("remark", "备注", "textarea", False, 1),
        ],
    },
}


def main():
    db = SessionLocal()
    try:
        defs = db.query(FlowDefinition).filter(FlowDefinition.status == "ACTIVE").all()
        updated, skipped, noform = [], [], []
        for fd in defs:
            # 必须深拷贝: 直接改ORM持有的list会污染快照, 导致flush时"无净变更"被跳过
            nodes = json.loads(json.dumps(fd.nodes or [], ensure_ascii=False))
            if not nodes:
                continue
            biz = fd.biz_type
            if nodes[0].get("form_config"):
                skipped.append(f"{biz}(已有表单,跳过)")
                continue
            form = FLOW_FORMS.get(biz)
            if not form:
                noform.append(f"{biz}(无预置表单)")
                continue
            nodes[0]["form_config"] = form
            fd.nodes = nodes
            updated.append(f"{biz}(写入{len(form['fields'])}字段)")
        db.commit()
        print("=== 表单入库结果 ===")
        for s in updated: print("  [更新]", s)
        for s in skipped: print("  [跳过]", s)
        for s in noform: print("  [忽略]", s)
        print(f"共更新 {len(updated)} 个流程")
    finally:
        db.close()


if __name__ == "__main__":
    main()
