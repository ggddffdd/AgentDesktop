# -*- coding: utf-8 -*-
r"""v4.260.0 扰动脚本：截图工具归一到单一真源（P1-7）。

反向照妖镜：逐个把改动打回「未修」状态，跑 tests/test_screenshot_delegate_260.py，
确认判据必红（改坏必红、零哑弹）。SKIP/MISS = 哑弹，判据白写。

V1  改回内联实现（不委托）  → 又变成两份独立实现，死代码复活（T1/T4/T5）
V2  委托但丢停止信号        → stop_event/should_stop 不透传，停止按钮对截图失效（T2）
V3  吞异常                  → 委托目标抛错被吞成返回字符串（T5）
V4  返回值包一层            → 调用方拿到的数据结构变了（T1-2）
V5  位置参数顺序打乱        → (cfg, args, app_dir) 错位（T2-1）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
TL = os.path.join(ROOT, "tools.py")
JUDGE = os.path.join(ROOT, "tests", "test_screenshot_delegate_260.py")

DELEGATE = (
    "    from system_control_tools import tool_screenshot as _impl\n"
    "    return _impl(cfg, app_dir, args, progress=progress,\n"
    "                 stop_event=stop_event, should_stop=should_stop)"
)

CASES = [
    ("V1 改回内联实现（不委托）", TL, DELEGATE,
     "    return \"扰动：又变回内联实现，死代码复活\", [], []"),

    ("V2 委托但丢停止信号", TL, DELEGATE,
     "    from system_control_tools import tool_screenshot as _impl\n"
     "    return _impl(cfg, app_dir, args, progress=progress)  # 扰动：丢停止信号"),

    ("V3 吞异常", TL, DELEGATE,
     "    from system_control_tools import tool_screenshot as _impl\n"
     "    try:\n"
     "        return _impl(cfg, app_dir, args, progress=progress,\n"
     "                     stop_event=stop_event, should_stop=should_stop)\n"
     "    except Exception:\n"
     "        return \"扰动：吞掉异常\", [], []"),

    ("V4 返回值包一层", TL, DELEGATE,
     "    from system_control_tools import tool_screenshot as _impl\n"
     "    return (_impl(cfg, app_dir, args, progress=progress,\n"
     "                  stop_event=stop_event, should_stop=should_stop),)  # 扰动：包一层"),

    ("V5 位置参数顺序打乱", TL, DELEGATE,
     "    from system_control_tools import tool_screenshot as _impl\n"
     "    return _impl(cfg, args, app_dir, progress=progress,\n"
     "                 stop_event=stop_event, should_stop=should_stop)  # 扰动：顺序错位"),
]


def run_judge():
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("PYTHONUTF8", "1")
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True,
                       cwd=ROOT, env=env)
    return p.returncode, p.stdout + p.stderr


def main():
    files = [TL]
    G.arm(files)
    snaps = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            snaps[fp] = f.read()
    total = 0
    hits = 0
    misses = []
    try:
        for name, fp, old, new in CASES:
            total += 1
            src = snaps[fp]
            if old not in src:
                misses.append(name)
                print("SKIP %s: old 串未命中（源码已变，需更新脚本）" % name)
                continue
            cnt = src.count(old)
            if cnt != 1:
                misses.append(name)
                print("SKIP %s: old 串出现 %d 次，非唯一（无法安全替换）" % (name, cnt))
                continue
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new, 1))
            try:
                rc, out = run_judge()
                failed = (rc != 0) and ("FAIL" in out)
            except Exception as e:  # noqa: BLE001
                failed, out = False, "judge 崩了: %r" % e
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                misses.append(name)
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    finally:
        for fp, src in snaps.items():
            with open(fp, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    if misses:
        print("哑弹清单: %s" % ", ".join(misses))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
