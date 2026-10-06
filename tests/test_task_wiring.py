# -*- coding: utf-8 -*-
"""任务状态「接线契约」测试（可观测性）

纯标准库（只读源码，不启动 Qt）：
    python tests/test_task_wiring.py
退出码 0=全过，1=有失败。

为什么需要它
------------
状态总线本身有 test_task_status.py 覆盖，但**生产者有没有正确接线**是另一回事。
最典型的坏味道有两类，都不会报错、只会静默劣化：

1. 登记了任务却漏了收口 → 状态条永远停在「处理中」，比没有还糟。
2. 收口时对闭包变量赋值却没写 nonlocal → 读取处 UnboundLocalError
   （本项目 main.py 的网页抓取回调就踩过这个坑）。

本测试对源码做结构性断言，专门挡住这两类回归。
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def has_resolution(src, var):
    """该任务变量是否真有收口调用。

    收口有两种常见写法，都要认：
    1) 直接以成员变量收口：  task_status.fail(self._task_x, ...)
    2) 先取到局部再收口：    _tid = getattr(self, "_task_x", "")  →  task_status.fail(_tid, ...)
    """
    if re.search(rf"task_status\.(done|fail)\(\s*(?:self\.)?{var}\b", src):
        return True
    if re.search(rf'getattr\(self,\s*"{re.escape(var)}"', src) and \
       re.search(r"task_status\.(done|fail)\(\s*_tid\b", src):
        return True
    return False


# 生产者清单：(文件, 任务变量名, 中文说明)
PRODUCERS = [
    ("ui.py", "_task_model", "模型调用"),
    ("main.py", "_task_browser", "网页抓取"),
    ("ui.py", "_task_image", "图片生成"),
    ("ui.py", "_task_video", "视频生成"),
    ("ui.py", "_task_asr", "语音识别"),
    ("ui.py", "_task_agent", "Agent 任务"),
]


def main():
    print("== 接线契约 ==")

    ui_src = read("ui.py")
    main_src = read("main.py")
    srcs = {"ui.py": ui_src, "main.py": main_src}

    # --- 基础接入 ---
    check("ui.py 已导入 task_status", re.search(r"^import task_status$", ui_src, re.M) is not None)
    check("main.py 已导入 task_status", re.search(r"^import task_status$", main_src, re.M) is not None)

    # --- 每个生产者：有登记，也有收口 ---
    for fname, var, label in PRODUCERS:
        src = srcs[fname]
        has_begin = re.search(rf"{var}\s*=\s*task_status\.begin\(", src) is not None
        has_end = has_resolution(src, var)
        check(f"{label}：已登记任务", has_begin, f"{fname} 缺 {var} = begin(...)")
        check(f"{label}：有收口（done/fail）", has_end,
              f"{fname} 中 {var} 没有 done/fail —— 会永远停在「处理中」")

    # --- 防 UnboundLocalError：网页抓取闭包必须写 nonlocal ---
    check("网页抓取保留 nonlocal 守卫（防 UnboundLocalError）",
          "nonlocal _task_browser" in main_src,
          "main.py 缺少 nonlocal _task_browser")

    # --- 不允许「登记了但没记到变量」的孤儿 begin ---
    for fname, src in srcs.items():
        all_begins = len(re.findall(r"task_status\.begin\(", src))
        assigned = len(re.findall(r"=\s*task_status\.begin\(", src))
        check(f"{fname} 中 begin 均为赋值形式（无孤儿登记）",
              all_begins == assigned and all_begins > 0,
              f"begin={all_begins} 赋值={assigned}")

    # --- 模型调用：失败分支与收敛点都要收口（否则失败/成功都会漏）---
    check("模型调用在失败分支收口",
          re.search(r"task_status\.fail\(self\._task_model", ui_src) is not None)
    check("模型调用在收敛点兜底成功",
          re.search(r"task_status\.done\(self\._task_model\)", ui_src) is not None)

    # --- Agent：阶段文本要持续转发，否则状态条停在「规划中」---
    check("Agent 阶段文本已转发到状态条",
          "w.status.connect(self._agent_task_progress)" in ui_src)
    check("Agent 状态转发实现存在",
          "def _agent_task_progress(self" in ui_src)

    # --- 界面接入 ---
    check("状态栏已接入 TaskStatusStrip",
          "TaskStatusStrip(self.status_bar)" in ui_src)
    # v4.216.0：TaskStatusStrip 类体已迁 ui_widgets.py（接线调用点仍在 ui.py）
    wg_src = read("ui_widgets.py")
    check("TaskStatusStrip 已订阅状态总线",
          "task_status.subscribe(" in wg_src)
    check("TaskStatusStrip 会退订（防控件销毁后仍被回调）",
          "task_status.unsubscribe(" in wg_src or "closeEvent" in wg_src,
          "未发现退订/销毁处理")
    check("ui 暴露 TaskStatusStrip 供测试引用",
          re.search(r"^class TaskStatusStrip", wg_src, re.M) is not None)

    # --- 状态条渲染引用了正确的状态常量（不是硬编码字符串）---
    check("状态条用常量判定失败态（非硬编码 'failed'）",
          "task_status.STATE_FAILED" in wg_src,
          "建议使用 task_status.STATE_FAILED 而非字面量")

    print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
