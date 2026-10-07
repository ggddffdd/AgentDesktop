# -*- coding: utf-8 -*-
"""扰动验证「技能启用检查必须早于加载」（test_skill_and_route_gates 的 B4 / B4b）。

为什么单独一个脚本：B4/B4b 属于 test_skill_and_route_gates.py，不在
_perturb_unified_intent_225 的 TESTS 里；且本轮刚把 B4 从写死签名
（`load_skill_prompt(skill_name, d)`）升级为正则 + 注释剔除 —— **改过的判据
必须立刻有扰动证明它非恒真**，否则「升级」可能只是把断言改松了。

两条变异：
  ① 整段删：把启用检查整块挪到加载之后（白检：先加载再检查）→ B4 应红
  ② 只删动作留条件：检查还在原位，但条件恒真（`if not True:` → 永不走禁用
     分支）→ B4b 应红（拒用/禁用两条拒绝路径的顺序约束仍在，但语义被打穿）

⚠️ 本脚本必须**独占运行**：护栏还原会静默回滚并行编辑的源码改动。
变异标记只能用 `# 扰动`（「变异」二字会误报 skill_loader.py）。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TESTS = [os.path.join(ROOT, "tests", "test_skill_and_route_gates.py")]
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


def run_tests():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    reds = []
    for t in TESTS:
        try:
            r = subprocess.run([PY, t], capture_output=True, text=True, cwd=ROOT,
                               timeout=300, env=env, encoding="utf-8",
                               errors="replace")
            out = (r.stdout or "") + (r.stderr or "")
            reds += re.findall(r"\[FAIL\]\s*([^\n]+)", out)
        except Exception as e:
            reds.append("崩溃:%s" % e)
    return reds


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
# 统一护栏：快照 + 三重还原 + 残留预检。
# 不接的话 test_perturb_harness 的 C1 会红 —— 那正是它存在的意义
# （本轮实测：新脚本第一版就漏了，C1 当场抓到）。
_guard.arm()


# ── 变异原串（逐字等于 tools.py 现状；框架是纯字符串替换，不支持正则）──
# ⚠️ 缩进是 8 空格（这段在 tool_use_skill 的 try 里，外层还有一层 try）。
# 抄原串必须从真实字节取，不能凭肉眼数缩进 —— 否则原串未命中 → 整条变异
# 静默 SKIP（哑弹），而且看起来「脚本跑过了」最容易骗过去。
_GATE = (
    "        from config import is_skill_enabled\n"
    "        if not is_skill_enabled(skill_name):\n"
    "            _log_skill_hit(skill_name, ok=False)\n"
)
_LOAD = (
    "                p = load_skill_prompt(skill_name, d, strict_meta=False)\n"
)

CASES = [
    # ① 只删动作留条件：启用检查的条件判断被打穿（改成恒真 → 永不走禁用分支）。
    #    位置不动，所以 B4 的「顺序」仍成立 —— 这正是它该被打穿的那一半。
    #    ⚠️ old 必须逐字是真源码：_GATE 已含 if 行，别再叠一遍（叠加会造出
    #    源码里不存在的串 → 原串未命中 → 整条变异 SKIP）。
    ("只删动作留条件：启用检查条件恒真（禁用技能照样能加载）",
     "tools.py",
     "        if not is_skill_enabled(skill_name):\n"
     "            _log_skill_hit(skill_name, ok=False)\n",
     "        if not True:  # 扰动：启用检查恒真\n"
     "            _log_skill_hit(skill_name, ok=False)\n",
     ["B4c 启用判断真调用 is_skill_enabled"]),

    # ② 位置颠倒：把「检查在加载之前」改成「检查在加载之后」。
    #    删掉原位置的检查块，再在第一次加载**之后**补一个同形检查 ——
    #    源码里仍有两处 is_skill_enabled，B4 的位置断言必然翻红。
    ("整段删：启用检查被挪到加载之后（先加载再检查＝白检）",
     "tools.py",
     _GATE,
     "    # 扰动：启用检查被挪到加载之后\n",
     ["B4 校验在扫描目录之前", "B4b"]),
]

HIT, MISS = [], []
try:
    for _n, fp, _o, _nw, _e in CASES:
        if fp not in _backup:
            _backup[fp] = read_raw(fp)
            _crlf[fp] = is_crlf(fp)

    for name, fp, old, new, expect in CASES:
        src = _backup[fp].decode("utf-8").replace("\r\n", "\n")
        if old not in src:
            MISS.append(name + "（原串未命中）")
            print("  [SKIP] " + name)
            continue
        mut = src.replace(old, new, 1)
        if name.startswith("整段删"):
            # 补到第一次加载之后，制造「顺序颠倒」
            mut = mut.replace(_LOAD,
                              _LOAD + "    try:\n"
                              "        from config import is_skill_enabled\n"
                              "        if not is_skill_enabled(skill_name):  # 扰动：迟到的检查\n"
                              "            return (f\"技能「{skill_name}」当前处于**禁用**状态，无法加载。\")\n",
                              1)
        write_raw(fp, (mut.replace("\n", "\r\n")
                       if _crlf[fp] else mut).encode("utf-8"))
        red = run_tests()
        ok = all(any(e in r for r in red) for e in expect)
        tag = "HIT " if ok else "MISS"
        print("  [%s] %s → 红 %d 条" % (tag, name, len(red))
              + ("" if ok else ("；期望含 %s" % expect)))
        (HIT if ok else MISS).append(name)
        write_raw(fp, _backup[fp])
finally:
    for fp, blob in _backup.items():
        write_raw(fp, blob)
    print("  （已恢复原文件，原始字节无损）")

print("\n=== 扰动汇总：命中 %d/%d ===" % (len(HIT), len(CASES)))
print("PERTURB PASS=%d FAIL=%d" % (len(HIT), len(MISS)))
sys.exit(1 if MISS else 0)