# -*- coding: utf-8 -*-
"""v4.235.0 版本单一真源扰动脚本：模块级 VERSION 漂移守卫。

反向照妖镜：变异三处修复点，跑 tests/test_version_single_source_235.py，
确认判据必红（改坏必红）。三个 case 各自针对修复的一个不同侧面：

  V1 漂移本体：把 agent_task_mixin 的 VERSION 改回 v4.225.0（历史漂移重现）
              → 判据 V2（无漂移）必须翻红
  V2 守卫恒空：把 `if not appv: return []` 改成 `if appv: return []`
              → 传真版本号时直接短路返回空（假绿），判据 V3 反向用例必须翻红
  V3 接线缺失：删掉 check_version 里的守卫调用块
              → 函数还在但没人调用，判据 V4 必须翻红

沿用已验证的「直接变异 + _perturb_guard 还原」模式。
原串均从真实字节取（Read 核对 + 脚本内 assert 唯一）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
JUDGE = os.path.join(ROOT, "tests", "test_version_single_source_235.py")

F_MIXIN = os.path.join(ROOT, "agent_task_mixin.py")
F_RC = os.path.join(ROOT, "release_check.py")

# ---- case 1：漂移本体（agent_task_mixin.py:33）----
OLD_V1 = 'VERSION = "v4.235.0"'
NEW_V1 = 'VERSION = "v4.225.0"'

# ---- case 2：守卫恒空（release_check.py:432）----
# 反转条件：appv 非空时直接 return []（假绿），为空时才真扫。
# 反向用例传 "v0.0.1-fake"（非空）→ 拿到 [] → V3 期望 >=8 落空 → 判红。
OLD_V2 = """    if not appv:
        return []"""
NEW_V2 = """    if appv:
        return []"""

# ---- case 3：接线缺失（release_check.py:481-487）----
OLD_V3 = """    # v4.235.0：模块级 VERSION 漂移守卫 —— ④ 号门禁原来只核 config/README/exe
    # 三方，管不到模块级常量，导致 4 处漂移长期存在。放在 exe 检查**之前**，
    # 这样即使没打包（下面 early return 分支）也会执行。
    drift = _module_version_drift(appv)
    check("模块级 VERSION 与 config.APP_VERSION 一致（唯一真源）", not drift,
          "、".join(drift[:6]) + ("…" if len(drift) > 6 else "")
          if drift else "全部对齐 %s" % appv)
"""
NEW_V3 = ""

CASES = [
    ("V1 模块 VERSION 改回 v4.225.0（漂移重现）", F_MIXIN, OLD_V1, NEW_V1, ["V2"]),
    ("V2 守卫条件反转 → 恒返回空（假绿）", F_RC, OLD_V2, NEW_V2, ["V3"]),
    ("V3 删除 check_version 内的守卫接线", F_RC, OLD_V3, NEW_V3, ["V4"]),
]


def run_judge():
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([F_MIXIN, F_RC])  # 三重还原 + 残留预检
    # 缓存各文件原文，逐个 case 独立还原
    orig = {}
    for f in (F_MIXIN, F_RC):
        with open(f, "r", encoding="utf-8") as fh:
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
            assert src.count(old) == 1, \
                "old 串出现 %d 次，非唯一：%s" % (src.count(old), name)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(src.replace(old, new, 1))
            rc, out = run_judge()
            # 判据应翻红：退出码非 0，且命中的是预期的那条断言
            failed = (rc != 0) and any(t in out for t in expect)
            if failed:
                hits += 1
                print("HIT  %s" % name)
            else:
                print("MISS %s（哑弹！判据未翻红）" % name)
                print("---- judge output ----\n" + out[:1500])
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(src)
    finally:
        for f, s in orig.items():
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(s)  # 最终兜底还原
    print("PERTURB PASS=%d FAIL=%d" % (hits, total - hits))
    sys.exit(0 if hits == total else 1)


if __name__ == "__main__":
    main()
