# -*- coding: utf-8 -*-
"""Zeabur/生产启动入口: 种子导入 + seed_data + 启动 uvicorn 替换 PID1
设计目标: 绝不在 shell 里拼 Python -c, 彻底避开 YAML/shell 引号地狱。
用法: python scripts/boot.py
环境依赖: Zeabur 注入 $PORT, 默认 8000; DB_URL 由 app.config 读取。"""
import os
import subprocess
import sys

# ---- 1. PORT 校验: 非数字/越界一律回落 8000 ----
_raw = os.environ.get("PORT", "8000")
try:
    _int = int(_raw) if str(_raw).isdigit() else 8000
    if _int < 1 or _int > 65535:
        _int = 8000
except Exception:
    _int = 8000
port = str(_int)
print(f"[BOOT] resolved PORT={port} (raw={_raw!r})", flush=True)

# ---- 2. 首次部署: 若无工作流实例则从种子库覆盖 ----
try:
    subprocess.run(["python", "scripts/seed_if_empty.py"], check=False)
except Exception as e:
    print(f"[BOOT] seed_if_empty failed (ignored): {e}", flush=True)

# ---- 3. 角色/用户/流程定义幂等初始化 ----
try:
    subprocess.run(["python", "scripts/seed_data.py"], check=False)
except Exception as e:
    print(f"[BOOT] seed_data failed (ignored): {e}", flush=True)

# ---- 4. 启动 uvicorn, exec 替换当前进程为 PID1, 符合容器最佳实践 ----
print(f"[BOOT] starting uvicorn on port {port}", flush=True)
os.execvp(
    "uvicorn",
    ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port, "--no-access-log"],
)
