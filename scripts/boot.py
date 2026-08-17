# -*- coding: utf-8 -*-
"""Zeabur/生产启动入口: 种子导入 + seed_data + 启动 uvicorn 替换 PID1
设计目标: 任何情况下都要把 uvicorn 拉起来, 子步骤失败只降级不致命。
用法: python scripts/boot.py"""
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

def _run_step(name, args):
    print(f"[BOOT] step: {name}", flush=True)
    try:
        r = subprocess.run(args, check=False, capture_output=False)
        print(f"[BOOT] step {name} exit={r.returncode}", flush=True)
    except Exception as e:
        print(f"[BOOT] step {name} FAILED: {e}", flush=True)

# ---- 2. 首次部署: 若无工作流实例则从种子库覆盖 ----
_run_step("seed_if_empty", ["python", "scripts/seed_if_empty.py"])

# ---- 3. 角色/用户/流程定义幂等初始化 ----
_run_step("seed_data", ["python", "scripts/seed_data.py"])

# ---- 4. 启动 uvicorn, 先尝试正常模式, 失败则降级 ----
def _try_start_uvicorn(env_extra=None):
    cmd = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port, "--no-access-log"]
    print(f"[BOOT] exec uvicorn: {' '.join(cmd)}", flush=True)
    if env_extra:
        env = os.environ.copy()
        env.update(env_extra)
        os.execvpe("uvicorn", cmd, env)
    else:
        os.execvp("uvicorn", cmd)

try:
    _try_start_uvicorn()
except Exception as e:
    print(f"[BOOT] uvicorn start crashed: {e}", flush=True)
    print("[BOOT] fallback: retry with PYTHONUNBUFFERED=1 only", flush=True)
    # 兜底: 加 PYTHONUNBUFFERED, 并清空可能的错误 env
    fallback_env = {"PYTHONUNBUFFERED": "1", "PYTHONPATH": "/app", "PORT": port}
    _try_start_uvicorn(env_extra=fallback_env)
