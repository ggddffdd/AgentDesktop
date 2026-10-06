# -*- coding: utf-8 -*-
"""toast 验收判据的扰动验证：注入真实错误，确认对应判据会红。

每个用例：改 toast.py → 跑 tests/test_toast_204.py → 看预期判据是否出现在
失败名单里 → 恢复源码。全绿而扰动不红 = 判据是空的。
"""
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(ROOT, "toast.py")
BAK = os.path.join(ROOT, "_toast.py.perturb.bak")
PY = sys.executable

# (说明, 原串, 新串, 预期变红的判据关键字)
CASES = [
    ("① 堆叠上限 3→5（应能同时 5 条）",
     "MAX_VISIBLE = 3  ", "MAX_VISIBLE = 5  ", "4a"),
    ("② 合并窗口 2s→0（同内容不再合并）",
     "MERGE_WINDOW = 2.0 ", "MERGE_WINDOW = 0.0 ", "3a"),
    ("③ error 改成 800ms 自动消失",
     '"error": 0,', '"error": 800,', "B2"),
    ("④ 不再避让输入区（会遮住输入框）",
     "        av = self._avoid() if self._avoid else None",
     "        av = None\n        if False:\n            pass", "B6"),
    ("⑤ 右边距 24→0（贴边）",
     "EDGE_MARGIN = 24 ", "EDGE_MARGIN = 0 ", "B7"),
    ("⑥ 未注册时改为抛异常（不再降级）",
     '            _log.info("[toast:%s] %s%s", kind, msg,',
     '            raise RuntimeError(msg)\n            _log.info("[toast:%s] %s%s", kind, msg,', "6d"),
    ("⑦ 悬停不再暂停计时",
     "        self._timer.stop()\n        self._close_btn.setVisible(True)",
     "        self._close_btn.setVisible(True)", "5b"),
    ("⑧ 宽度不再夹到 [280,420]",
     "        w = max(MIN_W, min(MAX_W, w))", "        w = w", "B8"),
    ("⑨ 宿主 resize 后不再重新定位",
     "        host.installEventFilter(self)", "        pass", "B9"),
]

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 三重还原 + 残留变异预检。
# ⚠️ v4.216.0 加固：原先还原只在**整轮循环之后**做一次，中途任何一次超时/强杀都会把
# toast.py 留在变异态 —— 后面的 case 全部读着脏文件跑（2026-10-05 在 %TEMP% 副本实测：
# ⑤ 报「锚点未命中」，而文件里躺着的正是 ⑤ 自己写的 EDGE_MARGIN = 0，脚本结尾的
# `same == orig` 还诚实地报「已恢复」，因为 orig 本身就是脏的）。
# 现在改成每个 case 用 `finally` 立刻还原，循环后再兜一次 + 自检。
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402

_guard.arm([TARGET])

orig = open(TARGET, encoding="utf-8", newline="").read()
shutil.copy2(TARGET, BAK)
results = []

for desc, old, new, expect in CASES:
    if old not in orig:
        results.append((desc, False, f"锚点未命中：{old[:40]!r}"))
        continue
    open(TARGET, "w", encoding="utf-8", newline="").write(orig.replace(old, new, 1))
    try:
        p = subprocess.run(
            [PY, os.path.join(ROOT, "tests", "test_toast_204.py")],
            capture_output=True, text=True, timeout=180, cwd=ROOT,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        results.append((desc, False, "测试超时"))
        continue
    finally:
        # v4.216.0：立刻还原 —— 别让「超时/异常」把变异态带进下一个 case。
        open(TARGET, "w", encoding="utf-8", newline="").write(orig)
    failed = re.findall(r"✗ (.+)", out)
    hit = any(expect in f for f in failed)
    results.append((desc, hit, ("命中：" + "; ".join(failed[:2])) if hit
                    else f"未命中，失败名单={failed[:3]}"))

open(TARGET, "w", encoding="utf-8", newline="").write(orig)
if os.path.exists(BAK):
    os.remove(BAK)

print("=== toast 判据扰动验证 ===")
for desc, ok, extra in results:
    print(f"  [{'OK  ' if ok else 'FAIL'}] {desc}")
    if extra:
        print(f"          {extra}")
n = sum(1 for _, ok, _ in results if ok)
print(f"\n=== 扰动结果：{n}/{len(results)} 命中 ===")
same = open(TARGET, encoding="utf-8", newline="").read()
print("源码已恢复" if same == orig else "**源码未恢复，请检查！**")
# 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总。
print("PERTURB PASS=%d FAIL=%d" % (n, len(results) - n))
sys.exit(0 if n == len(results) else 1)
