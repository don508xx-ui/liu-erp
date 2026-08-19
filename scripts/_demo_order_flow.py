# -*- coding: utf-8 -*-
"""订单→审批→转工单 演示链路修复与生成。
1) 更新 CORE_PRODUCTION 流程定义为: 销售发起→部门主管审批→运营核单(自动转工单)→总经理抄送
2) 清理孤立的旧CORE_PRODUCTION实例(未关联订单)
3) 提交3张DRAFT订单进入审批流(SUBMITTED+待部门主管审批)
4) 其中1张: 跳审批→生效→自动生成工单(演示销售单转工单闭环)
可选参数: --advance=ORDER_ID 对指定订单直接跑赢整个审批并转工单
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.db import SessionLocal
from app.models.approval import FlowDefinition, FlowInstance, FlowTask
from app.models.order import Order, OrderItem
from app.models.workshop import WorkOrder
from app.models.system import User
from app.api.approvals import start_flow, _advance, _apply_approval_result, bjt_now, _find_assignee

NEW_NODES = [
    {"seq": 1, "name": "销售发起", "type": "process", "approver_role": "SALES"},
    {"seq": 2, "name": "部门主管审批", "type": "approve", "approver_role": "DEPARTMENT_HEAD"},
    {"seq": 3, "name": "运营核单转工单", "type": "approve", "approver_role": "OPERATION"},
    {"seq": 4, "name": "总经理抄送", "type": "cc", "approver_role": "GM", "cc_roles": ["GM"]},
]


def main():
    db = SessionLocal()
    try:
        # 1. 更新流程定义
        fd = db.query(FlowDefinition).filter(
            FlowDefinition.biz_type == "CORE_PRODUCTION",
            FlowDefinition.status == "ACTIVE",
        ).order_by(FlowDefinition.version.desc()).first()
        if fd:
            fd.name = "订单生产审批(运营转工单)"
            fd.nodes = NEW_NODES
            fd.version = (fd.version or 1) + 1
            db.flush()
            print(f"[ok] CORE_PRODUCTION 流程定义更新, id={fd.id}")

        # 2. 清理孤立的未关联订单的CORE_PRODUCTION实例
        orphan = db.query(FlowInstance).filter(
            FlowInstance.biz_type == "CORE_PRODUCTION",
            FlowInstance.status == "RUNNING",
        ).all()
        for inst in orphan:
            o = db.query(Order).get(inst.biz_id)
            if not o or o.approval_instance_id != inst.id:
                print(f"[clean] 孤立流程实例 {inst.id} (biz_id={inst.biz_id}), 置为ARCHIVED")
                inst.status = "ARCHIVED"
                db.query(FlowTask).filter(FlowTask.instance_id == inst.id,
                                          FlowTask.status == "PENDING").update({"status": "SKIPPED"}, synchronize_session=False)
        db.flush()

        # 3. 找一个销售用户作为发起人
        sales_user = db.query(User).filter(User.username == "sales01").first()
        if not sales_user:
            sales_user = db.query(User).filter(User.status == "ACTIVE").first()
        print(f"[ok] 发起人: {sales_user.username}")

        # 4. 提交3张DRAFT订单进入审批流
        draft_orders = db.query(Order).filter(Order.status == "DRAFT").order_by(Order.id).limit(3).all()
        submitted_count = 0
        for o in draft_orders:
            running = db.query(FlowInstance).filter(
                FlowInstance.biz_type == "CORE_PRODUCTION",
                FlowInstance.biz_id == o.id,
                FlowInstance.status == "RUNNING",
            ).first()
            if running:
                print(f"[skip] 订单 {o.order_no} 已有进行中流程")
                continue
            before = o.status
            o.status = "SUBMITTED"
            o.signed_at = bjt_now()
            inst = start_flow(db, "CORE_PRODUCTION", o.id, sales_user)
            if inst:
                o.approval_instance_id = inst.id
                submitted_count += 1
                print(f"[advance] 订单 {o.order_no} 已提交, 流程实例={inst.id}, 待审节点={inst.current_node_seq}")
            else:
                o.status = before
        db.flush()

        # 5. 可选: 对指定订单直接进行到"运营核单"节点
        advance_id = None
        args = sys.argv[1:]
        for idx, a in enumerate(args):
            if a == "--advance" and idx + 1 < len(args):
                advance_id = int(args[idx + 1])
        if advance_id:
            o = db.query(Order).get(advance_id)
            if o and o.approval_instance_id:
                inst = db.query(FlowInstance).get(o.approval_instance_id)
                fd_cur = db.query(FlowDefinition).get(inst.definition_id)
                # 批准部门主管节点(节点2), 推到运营核单(节点3)
                t = db.query(FlowTask).filter(FlowTask.instance_id == inst.id,
                                              FlowTask.node_seq == 2,
                                              FlowTask.status == "PENDING").first()
                approve_pending = db.query(FlowTask).filter(
                    FlowTask.instance_id == inst.id, FlowTask.status == "PENDING"
                ).order_by(FlowTask.node_seq).all()
                # 直接用引擎推进: 将当前审批节点逐条置为APPROVED并advance到运营核单
                for task in approve_pending:
                    if task.node_seq >= 3:
                        break
                    task.status = "APPROVED"
                    task.handled_at = bjt_now()
                    inst.current_node_seq = task.node_seq + 1
                    db.flush()
                    _advance(db, inst, fd_cur, inst.initiator_user_id)
                    db.flush()
                print(f"[advance] 订单 {o.order_no} 已推至节点 {inst.current_node_seq} (待运营核单), 实例={inst.id}")

        db.commit()
        print("\n===== 完成 =====")
        # 汇总
        for o in db.query(Order).filter(Order.status.in_(['SUBMITTED', 'EFFECTIVE'])).order_by(Order.id).all():
            wos = db.query(WorkOrder).filter(WorkOrder.order_id == o.id).count()
            print(f"订单 {o.order_no} [{o.status}] 工单数={wos}")
    finally:
        db.close()


if __name__ == "__main__":
    main()