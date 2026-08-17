# -*- coding: utf-8 -*-
"""启动前置: 若目标数据库缺少工作流实例(首次部署/空库/仅角色无单据), 从镜像内种子库覆盖,
实现"数据库随代码迁移, 一次部署即有完整数据"。若库内已有 flow_instances 则跳过, 绝不覆盖真实数据。"""
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings

SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erp_seed.sqlite")
TARGET = settings.DB_URL


def _db_path(url: str) -> str:
    # sqlite:////app/data/erp.db 或 sqlite:///./data/erp.db 或 sqlite:///data/erp.db
    p = url.split("///", 1)[-1]
    return p.split("?", 1)[0]


def _has_flow_instances(db_path: str) -> bool:
    """库内是否已有工作流实例(有实例=真实数据,不覆盖)"""
    try:
        con = sqlite3.connect(db_path)
        try:
            cur = con.cursor()
            try:
                cur.execute("SELECT COUNT(*) FROM flow_instances")
                return (cur.fetchone()[0] or 0) > 0
            except Exception:
                # 表不存在=空库
                return False
            finally:
                con.close()
        except Exception:
            con.close()
            return False
    except Exception:
        return False


def main():
    target = _db_path(TARGET)
    if not target or not target.endswith(".db"):
        print("[seed] non-sqlite DB, skip")
        return
    if not os.path.exists(SEED):
        print(f"[seed] seed file missing: {SEED}, skip")
        return

    if os.path.exists(target) and _has_flow_instances(target):
        print(f"[seed] DB has flow_instances, skip ({target})")
        return

    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(SEED, target)
    print(f"[seed] copied seed -> {target}")


if __name__ == "__main__":
    main()
