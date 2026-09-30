# -*- coding: utf-8 -*-
"""②-B 软件控制可靠性（其余）回归测试。

覆盖三项：
  B.1 控件树输出截断（_cap_lines 硬上限）
  B.2 统一超时 + 可中断（入口即停 + exec_tool→_wrap→tool_app_* 透传链路）
  B.3 启动/结束健壮性（app_kill PID/未找到诊断、app_launch 目标校验）

无头可跑：所有用例都不依赖真实 GUI / pywinauto（停止检查在 import pywinauto 之前）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import software_control_tools as sct  # noqa: E402
import tools  # noqa: E402

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}  {detail}")


APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- B.1 控件树截断 ----------
def test_cap_lines():
    print("== ②-B.1 控件树输出截断 ==")
    capped, trunc, total = sct._cap_lines(list(range(500)))
    check("超上限触发截断", trunc is True, f"trunc={trunc}")
    check("截断后行数=上限+1(标记)", len(capped) == sct.MAX_CONTROL_TREE_LINES + 1,
          f"len={len(capped)}")
    check("截断标记存在", "已截断" in capped[-1], f"tail={capped[-1]!r}")
    capped2, trunc2, _ = sct._cap_lines(list(range(10)))
    check("未超上限不截断", trunc2 is False and len(capped2) == 10,
          f"trunc={trunc2} len={len(capped2)}")
    capped3, trunc3, _ = sct._cap_lines([])
    check("空列表安全", trunc3 is False and capped3 == [], f"capped3={capped3}")


# ---------- B.2 可中断（入口即停） ----------
def test_entry_abort():
    print("== ②-B.2 可中断（入口即停） ==")
    stop = {"v": True}
    out, _, _ = sct.tool_app_kill(APP_DIR, APP_DIR, {"target": "x.exe"}, should_stop=lambda: stop["v"])
    check("app_kill 入口即停", "已停止（用户请求）" in out, f"out={out!r}")

    out2, _, _ = sct.tool_app_launch(
        APP_DIR, APP_DIR, {"target": "x.exe", "wait_ready": False}, should_stop=lambda: stop["v"])
    check("app_launch 入口即停（不触发 pywinauto）", "已停止（用户请求）" in out2, f"out={out2!r}")

    out3, _, _ = sct.tool_app_focus(APP_DIR, APP_DIR, {"title": "x"}, should_stop=lambda: stop["v"])
    check("app_focus 入口即停", "已停止（用户请求）" in out3, f"out={out3!r}")


def test_exec_tool_plumbing():
    print("== ②-B.2 exec_tool→_wrap 透传链路 ==")
    # 经完整派发链路验证 should_stop 能透传到 software control handler
    out, _, _ = tools.exec_tool(None, APP_DIR, "app_kill",
                                {"target": "x.exe"}, should_stop=lambda: True)
    check("exec_tool 透传 should_stop 到 app_kill", "已停止（用户请求）" in out, f"out={out!r}")
    # 正常（不停止）应走真实逻辑
    out2, _, _ = tools.exec_tool(None, APP_DIR, "app_kill",
                                 {"target": "nonexistent_xyz_12345.exe"},
                                 should_stop=lambda: False)
    check("exec_tool 不停止时走真实逻辑", "未找到匹配的进程" in out2, f"out={out2!r}")


# ---------- B.3 启动/结束健壮性 ----------
def test_kill_not_found_and_pid():
    print("== ②-B.3 app_kill PID/未找到诊断 ==")
    out, _, _ = sct.tool_app_kill(APP_DIR, APP_DIR, {"target": "nonexistent_xyz_12345.exe"})
    check("镜像名未找到给友好诊断", "未找到匹配的进程" in out, f"out={out!r}")

    out2, _, _ = sct.tool_app_kill(APP_DIR, APP_DIR, {"target": "999999"})
    check("数字 PID 未找到给精确诊断", "未找到 PID=999999" in out2, f"out={out2!r}")


def test_launch_validation():
    print("== ②-B.3 app_launch 目标校验 ==")
    # wait_ready=False 走 Popen 分支，校验目标不存在应友好报错（不触发 pywinauto）
    out, _, _ = sct.tool_app_launch(
        APP_DIR, APP_DIR, {"target": "no_such_exe_xyz_98765.exe", "wait_ready": False})
    check("目标不存在给出校验错误", "找不到可执行文件" in out, f"out={out!r}")

    # 真实存在但 wait_ready=False 应返回 PID（用 cmd /c 让其立即退出，避免测试残留进程）
    out2, _, _ = sct.tool_app_launch(
        APP_DIR, APP_DIR, {"target": "cmd.exe", "args": "/c echo hi", "wait_ready": False})
    check("存在目标返回 PID", "PID=" in out2, f"out={out2!r}")


def main():
    test_cap_lines()
    test_entry_abort()
    test_exec_tool_plumbing()
    test_kill_not_found_and_pid()
    test_launch_validation()
    print(f"\n==== ②-B 结果：PASS={_p}  FAIL={_f} ====")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
