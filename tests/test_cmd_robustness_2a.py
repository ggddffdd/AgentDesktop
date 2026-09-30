# -*- coding: utf-8 -*-
"""②-A 命令执行健壮性（其余）回归测试。

覆盖三项：
  A) ②-A.1 大输出防御性截断（4MB 硬上限）
  B) ②-A.2 run_command 支持自定义 cwd / 环境变量
  C) ②-A.3 长任务流式输出（progress 回显）+ 可中断（should_stop / stop_event）

直接用项目解释器跑：python tests/test_cmd_robustness_2a.py
不依赖 pytest，沿用项目 check() 累积计数 + main() 返回 0/1 的约定。
"""
import os
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


# ---------- A) ②-A.1 大输出防御性截断 ----------
def test_huge_output_truncation():
    print("== ②-A.1 大输出防御性截断 ==")
    # 生成约 4.9MB 输出（远超 _RAW_OUTPUT_HARD_LIMIT = 4MB）
    huge = '("A"*80 + [Environment]::NewLine) * 60000'
    out = tools.tool_run_command(APP_DIR, huge)
    check("超大输出触发截断标记", "输出过大已截断" in out,
          f"未出现截断标记；len={len(out)}")
    check("截断后长度受控(<7000)", len(out) < 7000,
          f"返回长度异常：{len(out)}")
    # 正常小输出不应带截断标记
    small = tools.tool_run_command(APP_DIR, "echo HELLO_2A_SMALL")
    check("正常小输出无截断标记", "输出过大已截断" not in small,
          f"小输出误带截断标记：{small!r}")
    # Python 侧同样生效
    big_py = 'print("B" * (5 * 1024 * 1024))'  # 5MB
    py_out, _ = tools.tool_run_python(APP_DIR, big_py)
    check("run_python 超大输出触发截断", "输出过大已截断" in py_out,
          f"py 未出现截断标记；len={len(py_out)}")
    check("run_python 截断后长度受控(<7000)", len(py_out) < 7000,
          f"py 返回长度异常：{len(py_out)}")


# ---------- B) ②-A.2 cwd / env ----------
def test_cwd_and_env():
    print("== ②-A.2 run_command cwd / env ==")
    # cwd：切到 C:\Windows 后应反映在工作目录里
    cwd_out = tools.tool_run_command(
        APP_DIR, "(Get-Location).Path", cwd=r"C:\Windows")
    check("cwd 生效（落在 C:\\Windows）", "Windows" in cwd_out,
          f"cwd 未生效：{cwd_out!r}")
    # env：注入变量后应能被读到
    env_out = tools.tool_run_command(
        APP_DIR, "$env:MY_TEST_VAR_2A", env={"MY_TEST_VAR_2A": "hello_2a"})
    check("env 注入生效", "hello_2a" in env_out,
          f"env 未生效：{env_out!r}")
    # 无效 cwd：应回退默认工作区并给出提示
    bad = tools.tool_run_command(
        APP_DIR, "echo noop", cwd=r"Z:\__definitely_not_exist_2a__")
    check("无效 cwd 给出回退提示", "指定的 cwd 不存在" in bad,
          f"无效 cwd 未提示：{bad!r}")


# ---------- C) ②-A.3 流式 + 可中断 ----------
def test_streaming_progress():
    print("== ②-A.3 流式输出（progress 回显） ==")
    chunks = []
    cmd = '1..5 | ForEach-Object { "PROG_LINE_$_" }'
    out = tools.tool_run_command(APP_DIR, cmd, progress=chunks.append)
    check("progress 收到流式分块", any("PROG_LINE" in c for c in chunks),
          f"progress 未收到分块：{chunks}")
    check("流式后仍拿到完整结果", "PROG_LINE_5" in out,
          f"结果不完整：{out!r}")


def _make_stopper(delay, flag):
    """delay 秒后把 flag[0] 置 True，模拟用户点停止。"""
    def _t():
        time.sleep(delay)
        flag[0] = True
    threading.Thread(target=_t, daemon=True).start()


def test_interrupt_should_stop():
    print("== ②-A.3 可中断（should_stop 回调） ==")
    flag = [False]
    _make_stopper(0.4, flag)
    t0 = time.time()
    out = tools.tool_run_command(
        APP_DIR, "Start-Sleep -Seconds 20",
        should_stop=lambda: flag[0])
    dt = time.time() - t0
    check("should_stop 触发停止返回", "已停止（用户请求）" in out,
          f"未返回停止文案：{out!r}")
    check("停止在数秒内生效(<5s)", dt < 5, f"停止耗时过长：{dt:.1f}s")


def test_interrupt_stop_event():
    print("== ②-A.3 可中断（stop_event） ==")
    import subprocess as _sp  # noqa
    ev = threading.Event()
    threading.Thread(target=lambda: (time.sleep(0.4), ev.set()),
                    daemon=True).start()
    t0 = time.time()
    out = tools.tool_run_command(
        APP_DIR, "Start-Sleep -Seconds 20", stop_event=ev)
    dt = time.time() - t0
    check("stop_event 触发停止返回", "已停止（用户请求）" in out,
          f"未返回停止文案：{out!r}")
    check("stop_event 停止在数秒内生效(<5s)", dt < 5, f"停止耗时过长：{dt:.1f}s")


def test_run_python_interrupt():
    print("== ②-A.3 run_python 可中断 ==")
    flag = [False]
    _make_stopper(0.4, flag)
    t0 = time.time()
    out, dels = tools.tool_run_python(
        APP_DIR, "import time; time.sleep(20)",
        should_stop=lambda: flag[0])
    dt = time.time() - t0
    check("run_python 停止返回", "已停止（用户请求）" in out,
          f"未返回停止文案：{out!r}")
    check("run_python 停止无交付物", dels == [], f"交付物异常：{dels}")
    check("run_python 停止在数秒内(<5s)", dt < 5, f"停止耗时过长：{dt:.1f}s")


def test_normal_regression():
    print("== ②-A 回归：普通命令/代码行为不变 ==")
    out = tools.tool_run_command(APP_DIR, "echo 2A_REGRESSION_OK")
    check("普通命令正常返回", "2A_REGRESSION_OK" in out,
          f"普通命令异常：{out!r}")
    py_out, dels = tools.tool_run_python(APP_DIR, "print(1+1)")
    check("普通 python 正常返回", "2" in py_out,
          f"python 异常：{py_out!r}")


def main():
    test_huge_output_truncation()
    test_cwd_and_env()
    test_streaming_progress()
    test_interrupt_should_stop()
    test_interrupt_stop_event()
    test_run_python_interrupt()
    test_normal_regression()
    print(f"\n==== ②-A 结果：PASS={_p}  FAIL={_f} ====")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
