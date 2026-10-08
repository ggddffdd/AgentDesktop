# -*- coding: utf-8 -*-
"""v4.237.0 pre-commit 接线扰动：反向照妖镜（改坏必红）。

四个 case 各自打在接线的一个不同侧面，只变异**源码/配置**，不改判据本身：

  V1 检查器把 PEP 701 的结果丢掉（`fail` 换成一次性空表）
     → 判据 B2「行为级：PEP 701 违规文件被报出」翻红
  V2 密钥检查结果丢弃 → 判据 C2 翻红
  V3 扰动残留结果丢弃 → 判据 D1 翻红（这条最要紧：它守的是 P25 事故）
  V4 配置 entry 指向不存在的脚本 → 判据 E1 翻红
     （防「配置与脚本各说各话」：脚本改了、配置没跟上，hook 装了也不生效）

为什么全部用「把结果丢进一次性空表」而不是删掉整段调用：
  删调用会改变文件行数与结构，容易被后续重构静默吞掉；换容器则保持
  调用形状不变，**专门模拟「检查还在跑，结论被丢掉」**这类最阴的失效
  —— 它跟 P12「装饰性 gate（算了变量却不用）」是同一种病。

⚠️ 本脚本必须**独占运行**：护栏还原会静默回滚并行编辑的源码改动。
变异标记只能用 `# 扰动`。原串均从真实字节取（脚本内 assert 唯一）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import _perturb_guard as G  # noqa: E402

PY = "C:/Users/xyb/AppData/Local/Programs/Python/Python312/python.exe"
JUDGE = os.path.join(ROOT, "tests", "test_precommit_hooks_237.py")

F_PC = os.path.join(ROOT, "precommit_check.py")
F_CFG = os.path.join(ROOT, ".pre-commit-config.yaml")

# ---- V1：PEP 701 结果丢弃 ----
OLD_V1 = "    _check_pep701(paths, fail)"
NEW_V1 = "    _check_pep701(paths, [])  # 扰动：PEP 701 结论被丢掉"

# ---- V2：密钥结果丢弃 ----
OLD_V2 = "    _check_secrets(paths, fail)"
NEW_V2 = "    _check_secrets(paths, [])  # 扰动：密钥结论被丢掉"

# ---- V3：扰动残留结果丢弃 ----
OLD_V3 = "    _check_leftover(paths, fail)"
NEW_V3 = "    _check_leftover(paths, [])  # 扰动：残留结论被丢掉"

# ---- V4：配置与脚本脱钩 ----
OLD_V4 = "        entry: python precommit_check.py"
NEW_V4 = "        entry: python precommit_typo.py  # 扰动：配置指向不存在的脚本"

# ---- V5：残留检查绕开护栏的排除规则（误报回归）----
# 仓库里 68 个 `_perturb_*.py` 本来就带 `# 扰动` 标记（那是变异的定义）。
# 一旦不复用护栏的排除规则，它们会被全报成「没还原的残留」——
# 后果是每次改扰动脚本都被拦，大家只好 --no-verify，防线等于没有。
OLD_V5 = "            for rel, ln, txt in find_leftovers():"
NEW_V5 = ("            for rel, ln, txt in "
          "[(os.path.relpath(p, HERE), 1, '# 扰动') for p in inside]:"
          "  # 扰动：绕开护栏排除规则")

CASES = [
    ("V1 PEP 701 结论被丢掉（检查在跑但结果不要）", F_PC, OLD_V1, NEW_V1,
     ["B2 行为级：PEP 701 违规文件被报出"]),
    ("V2 密钥结论被丢掉", F_PC, OLD_V2, NEW_V2,
     ["C2 行为级：含密钥的文件被拦下"]),
    ("V3 扰动残留结论被丢掉（P25 防线失效）", F_PC, OLD_V3, NEW_V3,
     ["D1 行为级：带「# 扰动」标记的残留被拦下"]),
    ("V4 配置 entry 指向不存在的脚本（配置与脚本脱钩）", F_CFG, OLD_V4, NEW_V4,
     ["E1 配置的 entry 字段指向 precommit_check.py",
      "E3 entry 指向的脚本真实存在（配置与脚本没脱钩）"]),
    ("V5 残留检查绕开护栏排除规则（把合法扰动脚本全报成残留）", F_PC,
     OLD_V5, NEW_V5,
     ["H1 行为级：合法的 _perturb_*.py 不被误报为残留"]),
]


def run_judge():
    p = subprocess.run([PY, JUDGE], capture_output=True, text=True, cwd=ROOT)
    return p.returncode, p.stdout + p.stderr


def main():
    G.arm([F_PC, F_CFG])
    orig = {}
    for f in (F_PC, F_CFG):
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
