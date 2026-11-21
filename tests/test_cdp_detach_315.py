# -*- coding: utf-8 -*-
"""v4.315.0 判据：_ensure_cdp 自动拉起 Edge 必须真正脱离父进程（DETACHED_PROCESS）。

背景（2026-11-21 实测定性）：`_ensure_cdp` 已实现「9222 不通时自动拉起带
--remote-debugging-port 的 Edge」，但 Popen 用的是 `CREATE_NEW_PROCESS_GROUP`——
它只建新进程组，**不脱离父进程生命周期**。实测：父进程（一次性子进程如定时任务
脚本）退出后，拉起的 Edge 调试实例跟着死，9222 从 ready=True 掉回 False
（`_cdp_ready` False、debug 实例数 0）。生产 app 常驻时无碍，但任何子进程调用
都不可靠。加固：Popen 的 creationflags 加 `DETACHED_PROCESS`(0x8)，实测父进程
退出后 Edge 独立存活、9222 保持 ready。

零副作用第一原则：不真启动 Edge —— `_cdp_ready`/`_find_edge`/`_find_vpn_extension`/
`_default_cdp_profile`/`_cdp_has_vpn` 全部换桩，`subprocess.Popen` 用 mock 捕获
creationflags。
"""
import os
import re
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import browser_control_tools as bct

DETACHED = 0x00000008
CDP = "http://127.0.0.1:9222"

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print(f"  [PASS] {name}")
        _p += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        _f += 1


def read_src(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


def run_ensure(ready_seq, popen_exc=None):
    """换桩调 _ensure_cdp，捕获 Popen 的 creationflags。

    ready_seq: 依次返回给 `_cdp_ready` 的布尔序列（短路检查用第 1 个，Popen 后
    轮询用后面的）。默认最后耗尽时返回 True（让轮询立即成功）。
    """
    seq = list(ready_seq)

    def fake_ready(cdp):
        return seq.pop(0) if seq else True

    prof = tempfile.mkdtemp(prefix="cdp_detach_")
    if popen_exc is not None:
        pm = mock.patch.object(bct.subprocess, "Popen", side_effect=popen_exc)
    else:
        pm = mock.patch.object(bct.subprocess, "Popen", return_value=mock.Mock())
    with mock.patch.object(bct, "_cdp_ready", side_effect=fake_ready), \
         mock.patch.object(bct, "_find_edge", return_value="C:/edge/msedge.exe"), \
         mock.patch.object(bct, "_find_vpn_extension", return_value=None), \
         mock.patch.object(bct, "_default_cdp_profile", return_value=prof), \
         mock.patch.object(bct, "_cdp_has_vpn", return_value=True), \
         pm as popen_mock:
        ok, msg = bct._ensure_cdp(CDP)
    return ok, msg, popen_mock, prof


# ===========================================================================
# A 组：行为判据（mock Popen，捕获 creationflags）
# ===========================================================================
print("== A 组：_ensure_cdp 自动拉起 detach 行为 ==")

# A1：9222 不通 → Popen 被调用，且 creationflags 含 DETACHED_PROCESS
ok, msg, pm, prof = run_ensure([False, True])
popen_calls = pm.call_args_list
flags = None
if popen_calls:
    kwargs = popen_calls[0][1] or {}
    flags = kwargs.get("creationflags")
flags_int = isinstance(flags, int)  # 非 int（如扰动后成 tuple）应判 FAIL 而非崩溃
check("A1  Popen 的 creationflags 含 DETACHED_PROCESS(0x8)",
      popen_calls and flags_int and (flags & DETACHED) == DETACHED,
      f"called={bool(popen_calls)} flags={flags!r}")

# A2：Popen 后轮询就绪 → 返回 (True, "")
check("A2  Popen 后 9222 就绪即返回成功 (True, '')",
      ok is True and msg == "", f"ok={ok!r} msg={msg!r}")

# A3：Popen 抛异常 → 返回 (False, '自动启动 Edge 失败…')，不吞
ok3, msg3, pm3, _ = run_ensure([False, True], popen_exc=RuntimeError("boom"))
check("A3  Popen 异常返回 (False, '自动启动 Edge 失败…')",
      ok3 is False and "自动启动 Edge 失败" in (msg3 or ""),
      f"ok={ok3!r} msg={msg3!r}")

# A4：9222 已通（短路）→ 不调 Popen
ok4, msg4, pm4, _ = run_ensure([True])
check("A4  9222 已通时短路，不调 Popen",
      ok4 is True and pm4.call_count == 0,
      f"ok={ok4!r} popen_calls={pm4.call_count}")

# A5：Windows 分支下 flags 是「合并」而非覆盖（保留 CREATE_NEW_PROCESS_GROUP）
check("A5  flags 同时含 CREATE_NEW_PROCESS_GROUP 与 DETACHED_PROCESS（合并不覆盖）",
      popen_calls and flags_int
      and (flags & bct.subprocess.CREATE_NEW_PROCESS_GROUP)
      and (flags & DETACHED) == DETACHED,
      f"flags={flags!r}")

# ===========================================================================
# B 组：源码契约（防绕开加固）
# ===========================================================================
print("== B 组：源码契约 ==")
SRC = read_src(os.path.join(ROOT, "browser_control_tools.py"))

check("B1  源码含 DETACHED_PROCESS 常量（0x00000008）",
      "0x00000008" in SRC or "DETACHED_PROCESS" in SRC)

m = re.search(r"creationflags[^=]*?=\s*[^=]*?CREATE_NEW_PROCESS_GROUP\s*\|\s*0x00000008",
              SRC)
check("B2  creationflags 是 CREATE_NEW_PROCESS_GROUP | 0x00000008 合并形式",
      m is not None, "no merge form found")

# B3：该行在 `if os.name == "nt"` 分支内（找最近前置的 os.name 判断）
nt_idx = SRC.rfind('if os.name == "nt"', 0, m.start() if m else 0) if m else -1
check("B3  合并形式位于 if os.name == \"nt\" 分支内",
      m is not None and nt_idx != -1 and nt_idx < m.start(),
      f"nt_idx={nt_idx}")

print(f"\n汇总：PASS={_p} FAIL={_f}")
sys.exit(1 if _f else 0)
