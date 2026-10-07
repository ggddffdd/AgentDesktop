# -*- coding: utf-8 -*-
"""扰动验证 v4.227 P2-2：执行后验证加固。

手法：备份原字节 → 逐条退化 tool_contract.py / tool_verifiers_227.py / tools.py
→ 跑 test_post_exec_verify_227.py → 期望对应判据翻红 → 恢复原字节。

验收标准是**哑弹清零**。三条照妖镜防「怎么改都红」的假红：
  · V6 只降级语义被破坏（验证通过时把失败翻成成功）→ B1 组必须红
  · V7 分档表偷偷参与决策（变成第二套策略）→ D1 必须红
  · V8 验证器恒不表态（登记了等于没登记）→ A 组必须红
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join(ROOT, "tests", "test_post_exec_verify_227.py")
FILES = ["tool_contract.py", "tool_verifiers_227.py", "tools.py"]
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


def clean_pycache():
    """清掉全仓 __pycache__。

    ⚠️ 必须在**每条用例跑完后**就清，而不是只在 run_test 开头清：
    子进程 import 被扰动模块时会写回新的 .pyc，若清缓存发生在 run_test 开头，
    那么「上一条用例的最后一次导入」写下的 .pyc 会成为下一条的起始状态 ——
    实测 V2/V6 两条读到的红点全是**上一条用例的残留**
    （V2 读到 V1 留下的 read_file_text 幽灵名，V6 读到 V5 的 import 仍在生效）。
    这是「跨用例污染」，会让人误判成「判据抓不住」，实为环境脏。
    """
    n = 0
    for _root, _dirs, _files in os.walk(ROOT):
        if os.path.basename(_root) == "__pycache__":
            for _f in _files:
                if _f.endswith(".pyc"):
                    try:
                        os.remove(os.path.join(_root, _f))
                        n += 1
                    except Exception:
                        pass
    return n


def run_test():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONDONTWRITEBYTECODE="1")
    # ⚠️ 开跑前也清一次：被扰动的模块（tool_verifiers_227 / tool_contract）
    #    在判据子进程里是 import 进来的，.pyc 比源码旧就直接用缓存
    #    → 变异等于没发生 → 红 0 条 → 误判成「判据抓不住」。
    clean_pycache()
    r = subprocess.run([PY, TEST], capture_output=True, text=True, cwd=ROOT,
                       timeout=300, env=env, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    reds = re.findall(r"\[FAIL\] ([^\n]+)", out)
    clean_pycache()      # 跑完立刻清，别把 .pyc 留给下一条用例
    return reds


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

DEBUG = bool(os.environ.get("PERTURB_DEBUG"))

TC = "tool_contract.py"
TV = "tool_verifiers_227.py"
TL = "tools.py"

# 只降级语义的关键行（v4.224 起就是这条硬约束，v4.227 不许被破）
# ⚠️ 原串从真实字节取（v4.227 首版凭印象抄，漏了换行位置 → 原串未命中 → SKIP）。
_UPGRADE_OLD = (
    '    was_ok = bool(getattr(tr, "ok", False))\n'
    '    try:\n'
    '        if was_ok:\n'
    '            tr.ok = False\n'
    '            tr.error_code = "POST_VERIFY_FAILED"'
)
_UPGRADE_NEW = (
    '    was_ok = bool(getattr(tr, "ok", False))\n'
    '    try:\n'
    '        tr.ok = True          # 扰动：验证通过就翻成成功（破只降级语义）\n'
    '        if not was_ok:\n'
    '            tr.ok = True\n'
    '            tr.error_code = "POST_VERIFY_FAILED"'
)

# 分档登记三条循环（首版抄错：漏了循环之间的换行 → 原串未命中）
_TIER_LOOP_OLD = (
    'for _n in _VERIFIED:\n'
    '    register_verification_tier(_n, "verified")\n'
    'for _n, _why in _DEGRADED.items():\n'
    '    register_verification_tier(_n, "degraded")\n'
    'for _n in _OPEN:\n'
    '    register_verification_tier(_n, "open")'
)
_TIER_LOOP_NEW = (
    'if False:  # 扰动：分档登记整条不执行\n'
    '    pass'
)

CASES = [
    # ---- V1 write_file 验证器退化为「永远通过」（反编造失效）----
    ("V1 write_file 验证器改成恒通过（反编造失效）",
     TV,
     '        if not os.path.isfile(ap):\n'
     '            return (False, "复查：文件并不存在 %s" % p)',
     '        if False:  # 扰动\n'
     '            return (False, "复查：文件并不存在 %s" % p)',
     ["A2 反例：文件不存在 → 验证不通过（防编造成功）"]),

    # ---- V2 分档登记整条删掉（分档表消失 → 全是漏项）----
    #    ⚠️ 期望**不含** C4：本变异只删分档登记循环，验证器本身还在，
    #    所以「verified 档全部有真验证器」查不到任何 verified 档 → 真空 → 绿。
    #    把它写进期望就是把「本来就该绿的断言」当红点（首跑栽在这）。
    ("V2 分档登记整条删掉（分档表消失，全成漏项）",
     TV, _TIER_LOOP_OLD, _TIER_LOOP_NEW,
     ["C1 83 个已注册工具零漏项（人人有档）",
      "C3 unverified_tools 报空（分档表自身无漏洞）"]),

    # ---- V3 幽灵名混进分档表（登记了却从不改变结论）----
    #    ⚠️ 期望里**不含** C3：unverified_tools 会把幽灵名当「已登记」跳过，
    #    所以它仍然报空、保持绿 —— 把它写进期望就会把真 HIT 判成 MISS。
    ("V3 幽灵名混进分档表（登记了却从不改变结论）",
     TV,
     '    "read_file": "返回值即磁盘内容，验证无意义（自证）",',
     '    "read_file": "返回值即磁盘内容，验证无意义（自证）",\n'
     '    "read_file_text": "幽灵名：v4.222 清单里有但注册表里没有",',
     ["C2 零幽灵名（登记项必须都是真工具）"]),

    # ---- V4 degraded 理由被抹掉（登记了等于没登记）----
    ("V4 degraded 理由被抹成空串（登记了等于没登记）",
     TV,
     'def degraded_reasons():\n'
     '    """返回 {工具名: 降级理由}，给判据与排障用（理由必须存在，不能是空壳）。"""\n'
     '    return dict(_DEGRADED)',
     'def degraded_reasons():\n'
     '    return {n: "" for n in _DEGRADED}',
     ["C5 degraded 档全部带降级理由"]),

    # ---- V5 tools.py 的 import 删掉（注册不发生，验证链空转）----
    #    注意变异体仍**含有** `import tool_verifiers_227` 这串字面量（只是缩进到
    #    if False 底下）—— 这正是首版 E1 判据红 0 条的原因，E1 已改成 AST 判定。
    ("V5 tools.py 删掉 import（注册不发生，链空转）",
     TL,
     'import tool_verifiers_227  # noqa: F401,E402',
     'if False:  # 扰动\n'
     '    import tool_verifiers_227',
     ["E1 tools.py 顶层真 import 了 tool_verifiers_227（非 if False 缩进）"]),

    # ---- V6 照妖镜：破坏「只降级」语义 ----
    #    变异改的是 `if was_ok:` 那条**降级**分支 → 真正被打穿的是 B2
    #    （原本成功 + 验证不通过 → 不再降级）。B1（原本失败不许翻成功）走的是
    #    was_ok=False 那条路，本变异碰不到它，仍保持绿 —— 首跑把 B1 写进期望，
    #    误判成 MISS。凡期望串有疑问，先跑 PERTURB_DEBUG=1 看红明细，别猜。
    ("V6 照妖镜：只降级语义被破坏（该降级的没降级）",
     TC, _UPGRADE_OLD, _UPGRADE_NEW,
     ["B2 原本成功但验证不通过 → 降级为失败",
      "B2b 降级时错误码是 POST_VERIFY_FAILED"]),

    # ---- V7 照妖镜：分档表偷偷参与决策（变成第二套策略）----
    ("V7 照妖镜：分档表参与运行时决策（第二套策略）",
     TC,
     '    fn = _VERIFY_REGISTRY.get(name or "")\n'
     '    if fn is None:\n'
     '        return None',
     '    if _VERIFY_TIERS.get(name or "") == "degraded":\n'
     '        return None\n'
     '    fn = _VERIFY_REGISTRY.get(name or "")\n'
     '    if fn is None:\n'
     '        return None',
     ["D1 verify_after 体内不引用分档表（判定只看验证器）"]),

    # ---- V8 照妖镜：验证器恒不表态（登记了等于没登记）----
    ("V8 照妖镜：验证器恒返回 None（登记了等于没登记）",
     TV,
     '    p = (args or {}).get("path")\n'
     '    if not p or not isinstance(p, str):\n'
     '        return None',
     '    if True:  # 扰动：永远不表态\n'
     '        return None\n'
     '    p = (args or {}).get("path")\n'
     '    if not p or not isinstance(p, str):\n'
     '        return None',
     ["A1 write_file 真落地 → 验证通过",
      "A2 反例：文件不存在 → 验证不通过（防编造成功）"]),
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
        out = src.replace(old, new, 1)
        if DEBUG:
            print("      [dbg] %s 替换命中=%s  crlf=%s"
                  % (fp, old in src, _crlf[fp]))
        write_raw(fp, (out.replace("\n", "\r\n") if _crlf[fp] else out).encode("utf-8"))
        red = run_test()
        if DEBUG:
            print("      [dbg] 红明细=%r" % (red,))
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
