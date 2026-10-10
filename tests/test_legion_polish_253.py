# -*- coding: utf-8 -*-
"""军团收尾优化判据（v4.253.0 · B/C 档）

覆盖：
- #1 _install_skill 死代码已删（v4.247 删按钮后无人调用，SkillInstallDialog 别处仍用）
- #2 授权超时前提醒：_request_auth 分段等待，剩余 AUTH_WARN_BEFORE 秒时发提醒
- #3 browser_read 正文去噪：砍导航/页脚短句 + 重复块，保留正文

跑法：QT_QPA_PLATFORM=offscreen python tests/test_legion_polish_253.py
退出码 0=全过，1=有失败。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# —— 隔离真实军团数据 ——
_SBX = tempfile.mkdtemp(prefix="xc_sbx_polish253_")
os.environ["XC_LEGION_DIR"] = _SBX
os.environ.setdefault("XC_USER_DATA_DIR", os.path.join(_SBX, "userdata"))

import legion  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


def test_install_skill_removed():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from legion_ui import LegionWindow
    check("死代码·_install_skill已删", not hasattr(LegionWindow, "_install_skill"))


def test_auth_timeout_warn():
    import inspect
    from legion_worker import LegionWorker, auth_warn_text, AUTH_WARN_BEFORE
    check("超时提醒·文本含剩余秒数",
          "即将超时" in auth_warn_text(99) and "99" in auth_warn_text(99))
    check("超时提醒·阈值在(0,600)内", 0 < AUTH_WARN_BEFORE < 600)
    src = inspect.getsource(LegionWorker._request_auth)
    check("超时提醒·_request_auth接入",
          "auth_warn_text" in src and "AUTH_WARN_BEFORE" in src)


def test_browser_read_denoise():
    from browser_control_tools import _denoise_browser_text
    dirty = ("登录\n注册\n这是正文内容，需要保留，长度足够\n"
             "版权所有 京公网安备\n这是正文内容，需要保留，长度足够")
    out = _denoise_browser_text(dirty)
    check("去噪·砍导航短句", "登录" not in out and "版权所有" not in out)
    check("去噪·保留正文", "这是正文内容" in out)


if __name__ == "__main__":
    test_install_skill_removed()
    test_auth_timeout_warn()
    test_browser_read_denoise()
    print(f"\nPASS={PASS} FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
