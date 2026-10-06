# -*- coding: utf-8 -*-
"""扰动验证 P1 不可信内容边界：退化工具包边界 / 技能包边界，看判据是否翻红。

手法：备份原字节 → 退化 _wrap_tool_content（不包边界）/ wrap_skill_prompt（不包边界）→
跑 test_untrusted_boundary_222.py → 期望 B1 / D 分别翻红 → 恢复原字节。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_untrusted_boundary_222.py")
FILES = ["agent.py", "skill_loader.py"]
_backup = {}
_crlf = {}


def read_raw(fp):
    with open(os.path.join(ROOT, fp), "rb") as f:
        return f.read()


def write_raw(fp, blob):
    with open(os.path.join(ROOT, fp), "wb") as f:
        f.write(blob)


def is_crlf(fp):
    return b"\r\n" in read_raw(fp)


def run_test():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([PY, TEST], capture_output=True, text=True, cwd=ROOT,
                       timeout=300, env=env, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    red = re.findall(r"\[FAIL\] ([^\n]+)", out)
    return red


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

CASES = [
    ("_wrap_tool_content 退化（不包边界）",
     "agent.py",
     'def _wrap_tool_content(name, content, evidence_id=None):\n'
     '    """v4.222：不可信工具产出包边界；可信工具原样返回。"""\n'
     '    if name in _UNTRUSTED_TOOLS:\n'
     '        return wrap_untrusted(content, name, evidence_id)\n'
     '    return content',
     'def _wrap_tool_content(name, content, evidence_id=None):\n'
     '    return content',
     ["B1 不可信工具(web_fetch)产出被包边界"]),
    ("wrap_skill_prompt 退化（不包边界）",
     "skill_loader.py",
     'def wrap_skill_prompt(text, name):\n'
     '    """v4.222：技能指令包进不可信边界，模型须当数据而非指令。"""\n'
     '    if not text:\n'
     '        return text\n'
     '    return \'<untrusted skill="%s">\\n%s\\n</untrusted skill>\' % (name, text)',
     'def wrap_skill_prompt(text, name):\n'
     '    return text',
     ["D load_skill_prompt 返回内容被 <untrusted skill> 包边界"]),
]

HIT, MISS = [], []
try:
    for fp in FILES:
        _backup[fp] = read_raw(fp)
        _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] " + name)
            continue
        write_raw(fp, (src.replace(old, new, 1).replace("\n", "\r\n")
                        if _crlf[fp] else src.replace(old, new, 1)).encode("utf-8"))
        red = run_test()
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print("  [%s] %s → 红 %d 条" % (tag, name, len(red))
              + ("" if ok else ("；期望 %s" % expect)))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)
