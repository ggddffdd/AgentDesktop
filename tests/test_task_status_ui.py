# -*- coding: utf-8 -*-
"""任务状态条 UI 回归测试（可观测性）

离屏运行，不需要真实显示：
    python tests/test_task_status_ui.py
退出码 0=全过，1=有失败。

覆盖：
- TaskStatusStrip 能在离屏环境正常构造（QSS/控件不炸）
- 五种界面状态渲染正确：留白 / 已接收 / 处理中(带阶段) / 完成 / 失败(带原因)
- 按钮显隐：仅「完成」「失败」显示清除；仅「可重试的失败」显示重试
- 无重试钩子时点重试给出诚实提示（不假装已重试）
- 点清除会清空已完成历史
- 多任务并存时显示首个任务并附 +N 计数
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

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


def main():
    print("== 任务状态条 UI ==")

    try:
        from PySide6.QtWidgets import QApplication
    except Exception as e:
        print(f"  [FAIL] PySide6 不可用：{e!r}")
        print("\n结果：PASS=0  FAIL=1")
        return 1

    app = QApplication.instance() or QApplication([])

    try:
        import ui
        import task_status as ts
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  [FAIL] 界面模块导入失败：{e!r}")
        print("\n结果：PASS=0  FAIL=1")
        return 1

    check("ui 模块可导入（含 task_status 接入）", True)
    check("ui 暴露 TaskStatusStrip", hasattr(ui, "TaskStatusStrip"))

    try:
        strip = ui.TaskStatusStrip()
    except Exception as e:
        check("TaskStatusStrip 可离屏构造", False, repr(e))
        print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
        return 1
    check("TaskStatusStrip 可离屏构造", True)

    ts.registry.reset()
    ts.set_retry_hook(None)

    # --- 留白 ---
    strip.refresh()
    check("无任务时留白", strip.text.text() == "", repr(strip.text.text()))
    check("无任务时两个按钮都隐藏",
          strip.retry_btn.isHidden() and strip.clear_btn.isHidden())

    # --- 已接收 ---
    t = ts.begin("browser", "抓取「测试页」")
    strip.refresh()
    check("已接收显示类别与状态", "网页抓取" in strip.text.text() and "已接收" in strip.text.text(),
          strip.text.text())
    check("已接收阶段中按钮隐藏",
          strip.retry_btn.isHidden() and strip.clear_btn.isHidden())

    # --- 处理中（带阶段说明）---
    ts.progress(t, "已注入输入框")
    strip.refresh()
    check("处理中显示状态", "处理中" in strip.text.text(), strip.text.text())
    check("处理中显示阶段说明", "已注入输入框" in strip.text.text(), strip.text.text())

    # --- 多任务并存：+N 计数 ---
    t_extra = ts.begin("video", "生成第 2 段")
    strip.refresh()
    check("多任务显示 +N 计数", "+1" in strip.text.text(), strip.text.text())
    ts.done(t_extra)

    # --- 完成 ---
    ts.done(t)
    strip.refresh()
    check("完成后显示完成", "完成" in strip.text.text(), strip.text.text())
    check("完成后显示清除按钮", not strip.clear_btn.isHidden())
    check("完成后不显示重试按钮", strip.retry_btn.isHidden())

    # --- 失败（不可重试）---
    t2 = ts.begin("file", "解析大文档")
    ts.fail(t2, "编码不支持", retryable=False)
    strip.refresh()
    check("失败显示类别与原因", "文件解析" in strip.text.text() and "编码不支持" in strip.text.text(),
          strip.text.text())
    check("不可重试时不显示重试按钮", strip.retry_btn.isHidden())

    # --- 失败（可重试）+ 无钩子时的诚实提示 ---
    t3 = ts.begin("model", "对话回复")
    ts.fail(t3, "连接超时", retryable=True)
    strip.refresh()
    check("可重试时显示重试按钮", not strip.retry_btn.isHidden())
    strip._on_retry()
    check("无重试钩子时给出诚实提示（不假装成功）",
          "无法自动重试" in strip.text.text(), strip.text.text())

    # --- 有钩子时点重试应真的生效 ---
    called = {}

    def hook(task_dict):
        called["id"] = task_dict.get("id")
        return True

    ts.set_retry_hook(hook)
    strip._on_retry()
    check("有钩子时重试被调用", called.get("id") == t3, str(called))
    check("重试成功后该失败条被清除",
          all(r["id"] != t3 for r in ts.snapshot()["recent"]), "仍存在")

    # --- 清除 ---
    strip._on_clear()
    check("点清除后历史清空", len(ts.snapshot()["recent"]) == 0,
          str(len(ts.snapshot()["recent"])))
    strip.refresh()
    check("清除后界面回到留白", strip.text.text() == "", repr(strip.text.text()))

    ts.set_retry_hook(None)
    ts.registry.reset()

    print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
