# -*- coding: utf-8 -*-
"""启动前置: 若 /app/data/erp.db 不存在(首次部署空卷), 从镜像内种子库复制,
实现"数据库随代码迁移, 一次部署即有完整数据"。已存在则跳过, 绝不覆盖线上数据。"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import settings

SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erp_seed.sqlite")
TARGET = settings.DB_URL

def _db_path(url: str) -> str:
    # sqlite:////app/data/erp.db 或 sqlite:///./data/erp.db 或 sqlite:///data/erp.db
    p = url.split("///", 1)[-1]
    return p.split("?", 1)[0]

def main():
    target = _db_path(TARGET)
    if not target or not target.endswith(".db"):
        print("[seed] non-sqlite DB, skip")
        return
    if os.path.exists(target) and os.path.getsize(target) > 0:
        print(f"[seed] DB already exists ({target}), skip")
        return
    if not os.path.exists(SEED):
        print(f"[seed] seed file missing: {SEED}, skip")
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(SEED, target)
    print(f"[seed] copied {SEED} -> {target}")

if __name__ == "__main__":
    main()
