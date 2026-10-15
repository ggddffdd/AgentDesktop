# -*- coding: utf-8 -*-
"""v4.281.0 早停 toast 判据的扰动验证：注入回归，确认对应判据会红。

每个用例：改 agent.py → 跑 tests/test_stop_toast_281.py → 看预期判据是否出现在
失败名单里 → 即时还原源码（finally）。全绿而扰动不红 = 判据是空的（哑弹）。

⚠️ agent.py 是 CRLF：读用 newline="" 保留原字节，匹配前先归一成 LF，
写回再转回 CRLF，避免扰动脚本把工程换行约定冲掉。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "agent.py")
PY = sys.executable

# (说明, 原串, 新串, 预期变红的判据关键字) —— 锚点用 LF
CASES = [
    # ① 删掉 _note_exit 里的 toast 调用 → 2.2（命中映射时弹 toast）应红
    ("① 删 _note_exit 的 toast 调用",
     "            if m:\n                self._stop_toast(m[0], m[1])",
     "            if m:\n                pass  # perturbed: 删 toast 调用",
     "2.2"),
    # ② 删一处超时 toast（elapsed 分支唯一串）→ 4.1（三处超时）应红
    ("② 删一处超时 toast 调用",
     '            self._stop_toast(f"任务执行超时（{int(elapsed)}秒），已自动停止。可切回 DeepSeek 提速。", "warn")  # v4.281.0：跨视图可见',
     '            pass  # perturbed: 删超时 toast',
     "4.1"),
    # ③ 删澄清卡住 toast 调用 → 4.2（澄清分支）应红
    ("③ 删澄清卡住 toast 调用",
     '                    self._stop_toast("任务需要你确认方向，已暂停：" + _why, "warn")  # v4.281.0：澄清卡住也可见',
     '                    pass  # perturbed: 删澄清 toast',
     "4.2"),
]

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "_perturb"))
import _perturb_guard as _guard  # noqa: E402

_guard.arm([TARGET])

raw = open(TARGET, encoding="utf-8", newline="").read()  # 保留 CRLF 原字节
norm = raw.replace("\r\n", "\n")  # 归一成 LF 便于锚点匹配


def _to_crlf(s):
    return s.replace("\n", "\r\n")


results = []


def _failed_keywords(out):
    # 本判据套件失败行形如：[FAIL] 2.2 _note_exit(tool_fail) ...
    return re.findall(r"\[FAIL\]\s*([0-9]+\.[0-9]+)", out)


for desc, old, new, expect in CASES:
    if old not in norm:
        results.append((desc, False, "锚点未命中：%r" % old[:50]))
        continue
    mutated = norm.replace(old, new, 1)
    open(TARGET, "w", encoding="utf-8", newline="").write(_to_crlf(mutated))
    try:
        p = subprocess.run(
            [PY, os.path.join(ROOT, "tests", "test_stop_toast_281.py")],
            capture_output=True, text=True, timeout=180, cwd=ROOT,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        results.append((desc, False, "测试超时"))
        continue
    finally:
        # v4.216.0 风格：立刻还原，别把变异态带进下一个 case。
        open(TARGET, "w", encoding="utf-8", newline="").write(raw)
    keys = _failed_keywords(out)
    hit = expect in keys
    results.append((desc, hit, ("命中失败项：" + ", ".join(keys[:4])) if hit
                    else "未命中，失败项=%s" % keys[:4]))

open(TARGET, "w", encoding="utf-8", newline="").write(raw)

print("=== 早停 toast 判据扰动验证 ===")
for desc, ok, extra in results:
    print("  [%s] %s" % ("OK  " if ok else "FAIL", desc))
    if extra:
        print("          %s" % extra)
n = sum(1 for _, ok, _ in results if ok)
print("\n=== 扰动结果：%d/%d 命中 ===" % (n, len(results)))
same = open(TARGET, encoding="utf-8", newline="").read()
print("源码已恢复" if same == raw else "**源码未恢复，请检查！**")
print("PERTURB PASS=%d FAIL=%d" % (n, len(results) - n))
sys.exit(0 if n == len(results) else 1)
