# -*- coding: utf-8 -*-
"""v4.221 P2#3：系统提示不得再引用不存在的 software_run。

审查报告 P2#3 指出：系统提示能力路由表里写有 `software_run`，但工具注册表中
根本没有这个工具（真实是 app_launch / app_close / app_click / app_type /
app_window_state / app_list_controls），模型会尝试调用不存在的工具。

修复：`config.py` 路由表把那一行换成真实工具组。

判据：
  A) 全文件不再出现 software_run（已知唯一出处即旧路由行）；
  B) 路由表含六个真实软件控制工具名。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CONFIG_PATH = os.path.join(ROOT, "config.py")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


def main():
    print("-- P2#3 系统提示路由无幽灵工具名 --")
    src = open(CONFIG_PATH, encoding="utf-8-sig").read()
    check("A 不存在 software_run 幽灵名", "software_run" not in src,
          "config.py 仍含 software_run")
    for t in ("app_launch", "app_close", "app_click", "app_type",
              "app_window_state", "app_list_controls"):
        check("B 路由含真实工具 %s" % t, t in src)
    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
