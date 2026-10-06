# -*- coding: utf-8 -*-
"""扰动验证：证明 `tests/test_critical_proc_deny.py` 的判据「会红在该红的地方」。

判据绿不等于判据有效。本脚本逐个**拆掉判据所守的写法**，看对应判据是否真转红。

规矩（与 _perturb_system_control_b.py 同一套）：
  1. 期望采用**包含式**：expect ⊆ actual_red；只要求「该红的红了」，不禁止连带红；
  2. 每个 case 真的生成一份变异源码落临时文件，用 SCT_PATH / SWC_PATH / TOOLS_PATH
     指向它跑判据，**不在原文件上动刀**；
  3. 收尾跑一次「原样基线」，期望零红 —— 防「判据过宽、什么都判红」。

六个变异覆盖三个入口 × 两个方向（「整段删掉」与「只删动作留条件」）：
  入口 1 tool_process_kill：PC1 整段删判定（留 helper） / PC2 只删 return（判定照做）
                            PC4 只删归一化补 .exe
  名单本身：                PC3 清空 CRITICAL_PROCESS_NAMES
  入口 2 tool_app_kill：    PC5 删掉复用 sct 的那段调用
  入口 3 命令层：           PC6 删掉两条 taskkill/Stop-Process 模式

用法：python _perturb_critical_proc_deny.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
JUDGE = os.path.join(ROOT, "tests", "test_critical_proc_deny.py")

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

SCT_SRC = open(os.path.join(ROOT, "system_control_tools.py"), encoding="utf-8").read()
SWC_SRC = open(os.path.join(ROOT, "software_control_tools.py"), encoding="utf-8").read()
TOOLS_SRC = open(os.path.join(ROOT, "tools.py"), encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(env_src):
    paths = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    # 清掉外层可能注入的覆盖点，避免「上一次的变异」顺着环境泄漏进来
    for _k in ("SCT_PATH", "SWC_PATH", "TOOLS_PATH"):
        env.pop(_k, None)
    try:
        for var, (prefix, src) in env_src.items():
            fd, p = tempfile.mkstemp(prefix=prefix, suffix=".py", dir=ROOT)
            os.close(fd)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(src)
            paths.append(p)
            env[var] = p
        r = subprocess.run([sys.executable, JUDGE], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=420,
                           env=env, cwd=ROOT)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:                                     # noqa: BLE001
        out = "%s: %s" % (type(e).__name__, e)
    finally:
        for p in paths:
            try:
                os.remove(p)
            except Exception:                                  # noqa: BLE001
                pass
    red = re.findall(r"\[FAIL\]\s+(.+?)\s*$", out, re.M)
    return red, out


def sub(src, old, new, label, count=1):
    if src.count(old) != count:
        raise AssertionError("锚点失配(%s): 命中 %d 次，期望 %d" % (label, src.count(old), count))
    return src.replace(old, new)


def cut(src, start, end, new, label):
    """按首尾标记切片替换（用于含大量转义字符/长块的锚点）。"""
    i = src.index(start)
    j = src.index(end, i) + len(end)
    return src[:i] + new + src[j:], (i, j)


def case(name, env_src, expects):
    global PASS_N, FAIL_N
    red, out = run_judge(env_src)
    print("-" * 66)
    print("case %s" % name)
    if not red:
        print("  [FAIL] 变异后居然零红 —— 判据根本没抓到这个改动")
        print("  ---- 判据输出尾部 ----")
        print("\n".join(out.strip().splitlines()[-12:]))
        FAIL_N += 1
        FAILED_CASES.append(name + "（零红）")
        return
    missed = [e for e in expects if not any(e in r for r in red)]
    if missed:
        print("  [FAIL] 期望转红但没红：%s" % missed)
        print("  实际红项：")
        for r in red:
            print("    -", r)
        FAIL_N += 1
        FAILED_CASES.append(name + "（漏红）")
        return
    print("  [OK  ] 命中 %d 项：%s" % (len(red), "；".join(red[:3]) + ("…" if len(red) > 3 else "")))
    PASS_N += 1


# ---------------------------------------------------------------------------
# 入口 1：tool_process_kill
# ---------------------------------------------------------------------------
_DENY_BLOCK = ("    # G4 第二步：系统关键进程一律硬拒绝（在任何 spawn 之前）。\n"
               "    _deny = _critical_process_deny(name)\n"
               "    if _deny:\n"
               "        return ToolResult.fail(_deny)\n\n")

# PC1：整段删掉判定（helper 留着 → 死代码，杀 lsass 照常执行）
_mut = sub(SCT_SRC, _DENY_BLOCK, "", "PC1 drop deny block")
assert "_deny = _critical_process_deny(name)" not in _mut, "PC1 变异未生效"
case("PC1 整段删掉 process_kill 的关键进程判定（helper 变死代码）",
     {"SCT_PATH": ("sct_mut_", _mut)},
     ["★ process_kill('lsass.exe') 直接拒绝",
      "★ …且零 spawn（是硬拒绝，不是「弹确认」）",
      "★ 9 个名单成员逐个实测：全部拒绝且零 spawn",
      "★ force=True 不是逃生口（照样拒绝且零 spawn）",
      "★ 源码契约：tool_process_kill 内先做 deny 判定、后 spawn（顺序不许被挪）"])

# PC2：只删动作留条件（判定照做、结果丢掉 → 继续往下 spawn）
# 注：顺序判据（源码契约）**不**在期望里 —— PC2 保留 `_deny = ...` 赋值、只删 return，
# 顺序没被挪，它本就该绿；抓这个变异的是行为判据（零 spawn / 直接拒绝）。
_mut = sub(SCT_SRC,
           "    _deny = _critical_process_deny(name)\n"
           "    if _deny:\n"
           "        return ToolResult.fail(_deny)\n",
           "    _deny = _critical_process_deny(name)\n",
           "PC2 drop deny return")
assert "_deny = _critical_process_deny(name)" in _mut, "PC2 变异未生效"
case("PC2 只删 return（判定照做、但不阻断 → 继续 taskkill）",
     {"SCT_PATH": ("sct_mut_", _mut)},
     ["★ process_kill('lsass.exe') 直接拒绝",
      "★ …且零 spawn（是硬拒绝，不是「弹确认」）",
      "★ 9 个名单成员逐个实测：全部拒绝且零 spawn"])

# PC3：名单清空（判定还在、判据的依据没了）
_mut, _rg = cut(SCT_SRC, "CRITICAL_PROCESS_NAMES = frozenset({", "})",
                "CRITICAL_PROCESS_NAMES = frozenset()", "PC3 empty names")
assert "lsass.exe" not in _mut.split("def _critical_process_hit")[0], "PC3 变异未生效"
case("PC3 把 CRITICAL_PROCESS_NAMES 清空（名单没了）",
     {"SCT_PATH": ("sct_mut_", _mut)},
     ["★ 名单覆盖 9 个系统关键进程（少一个就是缺口）",
      "★ 命中：'lsass.exe'（标准写法）",
      "★ process_kill('lsass.exe') 直接拒绝",
      "★ 命令层的名字集合与 sct 的 CRITICAL_PROCESS_NAMES 完全一致（防两处漂移）"])

# PC4：只删归一化补 .exe（带扩展名还能命中、不带就不认了）
_mut = sub(SCT_SRC,
           '    if not n.endswith(".exe"):\n        n += ".exe"\n',
           "",
           "PC4 drop .exe normalization")
case("PC4 只删归一化补 .exe（'lsass' 这种写法漏网）",
     {"SCT_PATH": ("sct_mut_", _mut)},
     ["★ 命中：'lsass'（无扩展名 → 补 .exe 后命中）",
      "★ 'LSASS'（大写、无扩展名）也拒绝",
      "★ app_kill('explorer')（无扩展名，原逻辑会补 .exe）→ 拒绝"])

# ---------------------------------------------------------------------------
# 入口 2：tool_app_kill
# ---------------------------------------------------------------------------
_APP_DENY = ("    from system_control_tools import _critical_process_deny\n"
             "    _deny = _critical_process_deny(target)\n"
             "    if _deny:\n"
             "        return ToolResult.fail(_deny)\n\n")
_mut = sub(SWC_SRC, _APP_DENY, "", "PC5 drop app_kill deny")
case("PC5 删掉 app_kill 的关键进程判定（第二个入口失守）",
     {"SWC_PATH": ("swc_mut_", _mut)},
     ["★ app_kill('lsass.exe') 直接拒绝",
      "★ …且零 spawn",
      "★ app_kill('explorer')（无扩展名，原逻辑会补 .exe）→ 拒绝",
      "★ 源码契约：swc 从 sct 取判定（自带 import，不抄第二份）"])

# ---------------------------------------------------------------------------
# 入口 3：命令层
# ---------------------------------------------------------------------------
_mut, _rg = cut(
    TOOLS_SRC,
    "    # G4 第二步：终止系统关键进程。",
    '     "终止系统关键进程 (Stop-Process)"),\n',
    "", "PC6 drop cmd patterns")
assert "终止系统关键进程" not in _mut, "PC6 变异未生效"
case("PC6 删掉命令层两条 taskkill/Stop-Process 模式（文本入口失守）",
     {"TOOLS_PATH": ("tools_mut_", _mut)},
     ["★ 命令层：taskkill /IM <9 个关键进程> 全部被拦",
      "★ 命令层：Stop-Process -Name lsass 也被拦（PowerShell 同一条路）",
      "★ 命令层恰好 2 条关键进程模式（taskkill / Stop-Process）",
      "★ 源码契约：命令层两条模式都在（taskkill + Stop-Process）"])

# ---------------------------------------------------------------------------
# 反向基线
# ---------------------------------------------------------------------------
print("-" * 66)
print("反向基线：原样源码跑判据，不许有任何红项")
_red0, _out0 = run_judge({})
if _red0:
    print("  [FAIL] 未变异的源码居然红了：%s" % _red0)
    FAIL_N += 1
    FAILED_CASES.append("baseline")
else:
    n_pass = len(re.findall(r"\[PASS\]", _out0))
    print("  [OK  ] 未变异源码全绿（%d 项判据）" % n_pass)
    PASS_N += 1

print("\nPERTURB PASS=%d FAIL=%d" % (PASS_N, FAIL_N))
if FAILED_CASES:
    print("失效 case：")
    for c in FAILED_CASES:
        print("  -", c)
    sys.exit(1)
print("=== PERTURB_CRITICAL_PROC_DENY_OK ===")
