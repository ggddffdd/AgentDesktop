# -*- coding: utf-8 -*-
"""扰动验证 P2 单条消息预算：退化各维度上限，看判据是否翻红。

手法：备份原字节 → 豁免最后一条 / 去掉截断标记 / 放开图片数上限 / 放开单图体积
上限 → 跑 test_msg_budget_cap_224.py → 期望对应判据翻红 → 恢复原字节。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_msg_budget_cap_224.py")
FILES = ["ui_msg.py"]
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
    ("豁免最后一条（旧行为复活：巨消息绕过预算）",
     "ui_msg.py",
     '            _nm, _ch = _cap_message_to_budget(_m, msg_budget)',
     '            _nm, _ch = ((_m, False) if _m is cleaned[-1]\n'
     '                        else _cap_message_to_budget(_m, msg_budget))',
     ["MB6-2 最后一条内容被削到上限内", "MB6-3 最后一条带截断标记"]),
    ("截断不留标记（模型不知道内容被砍过）",
     "ui_msg.py",
     '    return s[:cap] + _TRUNC_SUFFIX.format(n=n), True',
     '    return s[:cap], True',
     ["MB1-2 截断后带可见标记", "MB2-2 工具结果截断也带标记"]),
    ("放开图片数上限",
     "ui_msg.py",
     '                if max_imgs and imgs >= max_imgs:',
     '                if False and max_imgs and imgs >= max_imgs:',
     ["MB4-1 图片数被压到上限"]),
    ("放开单图体积上限",
     "ui_msg.py",
     '                if max_img_chars and len(url) > max_img_chars:',
     '                if False and max_img_chars and len(url) > max_img_chars:',
     ["MB5-1 超大图被整张丢弃"]),
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
