# -*- coding: utf-8 -*-
"""v4.276.0 DeepSeek 前缀缓存扰动。

改坏四处，跑 tests/test_prompt_cache_276.py，确认判据必红。
SKIP / MISS 都算哑弹。原串从真实文件取，且必须唯一。

  P1 当轮背景插到前缀中间
  P2 工具不再按名字排序
  P3 命中率恒为 0
  P4 记忆写进稳定前缀
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = sys.executable
JUDGE = os.path.join(ROOT, "tests", "test_prompt_cache_276.py")
F_CACHE = os.path.join(ROOT, "prompt_cache.py")
F_UI = os.path.join(ROOT, "ui.py")

OLD_P1 = (
    "    msgs.append(_context_message(block))\n"
    "    return msgs\n"
)
NEW_P1 = (
    "    msgs.insert(1, _context_message(block))  # 扰动：上下文插到前缀中间\n"
    "    return msgs\n"
)

OLD_P2 = (
    "    ordered = sorted(indexed, key=_tool_sort_key)\n"
    "    return [tool for _, tool in ordered]\n"
)
NEW_P2 = (
    "    ordered = indexed  # 扰动：工具不再排序\n"
    "    return [tool for _, tool in ordered]\n"
)

OLD_P3 = '        rate = (float(_STATS["hit"]) / float(denom)) if denom else 0.0\n'
NEW_P3 = "        rate = 0.0  # 扰动：命中率恒为 0\n"

OLD_P4 = (
    "        mem = self._prompt_section_memory()\n"
    "        if mem:\n"
    "            dyn_parts.append(mem)\n"
)
NEW_P4 = (
    "        mem = self._prompt_section_memory()\n"
    "        if mem:\n"
    '            base += "\\n\\n" + mem  # 扰动：记忆写进稳定前缀\n'
)

CASES = [
    ("P1 背景插到前缀中间", F_CACHE, OLD_P1, NEW_P1,
     ["动态消息在历史之后", "较早历史仍是下一轮的前缀"]),
    ("P2 工具不再排序", F_CACHE, OLD_P2, NEW_P2,
     ["乱序两次得到同一顺序", "概览按名字排，不跟传入顺序走"]),
    ("P3 命中率恒为 0", F_CACHE, OLD_P3, NEW_P3,
     ["首笔命中率是 0.8000"]),
    ("P4 记忆写进稳定前缀", F_UI, OLD_P4, NEW_P4,
     ["两轮稳定前缀字节相同", "稳定前缀不含记忆"]),
]


def run_judge():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run(
        [PY, JUDGE], capture_output=True, text=True, cwd=ROOT, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    G.arm([F_CACHE, F_UI])
    orig = {}
    for f in (F_CACHE, F_UI):
        with open(f, "r", encoding="utf-8", newline="") as fh:
            orig[f] = fh.read()
    total = 0
    hits = 0
    try:
        for name, target, old, new, expect in CASES:
            total += 1
            src = orig[target]
            if old not in src:
                print("SKIP %s: old 串未命中（可能已改）" % name)
                continue
            n = src.count(old)
            if n != 1:
                print("SKIP %s: old 串出现 %d 次，非唯一" % (name, n))
                continue
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(src.replace(old, new, 1))
            rc, out = run_judge()
            failed = (rc != 0) and all(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[-2000:])
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
    finally:
        for f, s in orig.items():
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(s)
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
