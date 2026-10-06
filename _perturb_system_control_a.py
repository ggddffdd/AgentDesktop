# -*- coding: utf-8 -*-
"""扰动验证：证明 tests/test_system_control_a.py 的判据「会红在该红的地方」。

判据绿不等于判据有效。一份只会点头的判据比没有更糟 —— 它让人以为防线还在。
本脚本逐个**拆掉 A 批三件改动赖以成立的写法**，看对应判据是否真的转红。

规矩（与 _perturb_canvas_*.py 同一套）：
  1. 期望采用**包含式**：expect ⊆ actual_red。只要求「该红的红了」，不禁止连带红其它项；
  2. 每条 case 都真的生成一份**变异后源码**落临时文件，用环境变量
     （SCT_PATH / SWC_PATH / RISK_PATH）指向它跑判据，**不在原文件上动刀**；
  3. 收尾跑一次「原样基线」，期望零红 —— 防「判据过宽、什么都判红」。

覆盖两个方向（按本仓约定，新判据要「整段删掉」与「只删动作留条件」各验一次）：
  A 组 G6：删一侧登记（漏登记方向） / 加一个僵尸键（反方向）
  B 组 G2：整段删掉入口检查（留签名） / 只回退签名（留检查）
  C 组 G3：整段还原「按类型挑第一个」 / 只加回「唯一即自动选」（留枚举、删判别）

用法：python _perturb_system_control_a.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
JUDGE = os.path.join(ROOT, "tests", "test_system_control_a.py")

# 护栏：快照被测源码 + 装 SIGTERM/SIGINT/atexit 还原 + 残留变异预检
sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

SCT_SRC = open(os.path.join(ROOT, "system_control_tools.py"), encoding="utf-8").read()
SWC_SRC = open(os.path.join(ROOT, "software_control_tools.py"), encoding="utf-8").read()
RISK_SRC = open(os.path.join(ROOT, "risk.py"), encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []

_STOP_BLOCK = re.compile(
    r'    if _aborted\(should_stop, stop_event\):\n'
    r'        return [^\n]*\n')

_SIG_OLD = re.compile(
    r"def (tool_\w+)\(cfg, app_dir, args, progress=None, "
    r"stop_event=None, should_stop=None\):")

_ORIG_LEVEL4 = (
    "    # 4) 仅按 control_type 找第一个\n"
    "    if control_type:\n"
    "        try:\n"
    "            ctrl = window.child_window(control_type=control_type)\n"
    '            ctrl.wait("exists", timeout=0.5)\n'
    "            return ctrl\n"
    "        except Exception:\n"
    "            pass\n"
    "\n"
    "    raise RuntimeError(f\"未找到控件: target='{target}', "
    "control_type='{control_type}'\")\n")


def run_judge(env_src):
    """env_src: {环境变量名: (临时文件名前缀, 变异源码)}。返回 (红名列表, 输出)。"""
    paths = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
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
    except Exception as e:                                    # noqa: BLE001
        out = "%s: %s" % (type(e).__name__, e)
    finally:
        for p in paths:
            try:
                os.remove(p)
            except Exception:                                 # noqa: BLE001
                pass
    red = re.findall(r"\[FAIL\]\s+(.+?)\s*$", out, re.M)
    return red, out


def sub(src, old, new, label, count=1):
    if src.count(old) != count:
        raise AssertionError("锚点失配(%s): 命中 %d 次，期望 %d" % (label, src.count(old), count))
    return src.replace(old, new)


def case(name, env_src, expects):
    """expects：期望转红的判据名（子串匹配即可）。"""
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
# A 组 G6：声明表 vs 风险登记表全等
# ---------------------------------------------------------------------------
case("PA1 risk 删掉一条登记（漏登记方向）",
     {"RISK_PATH": ("risk_mut_", sub(
         RISK_SRC, '    "db_query": RiskClass.READ,\n', "",
         "risk del db_query"))},
     ["没有『声明了但未登记』的工具", "两表集合完全相等"])

case("PA2 risk 加一个没有声明的僵尸键（反方向）",
     {"RISK_PATH": ("risk_mut_", sub(
         RISK_SRC, "RISK_MAP = {\n",
         'RISK_MAP = {\n    "__zombie_tool__": RiskClass.READ,\n',
         "risk add zombie"))},
     ["没有『登记了但已无声明』的僵尸条目", "两表集合完全相等"])

# ---------------------------------------------------------------------------
# B 组 G2：system_control 可中断
# ---------------------------------------------------------------------------
_mut_b1, _n_b1 = _STOP_BLOCK.subn("", SCT_SRC)
print("PA3 锚点命中 %d 处入口检查（期望 15）" % _n_b1)
assert _n_b1 == 15, "PA3 锚点失配"
case("PA3 整段删掉 15 处入口检查（签名留着）",
     {"SCT_PATH": ("sct_mut_", _mut_b1)},
     ["should_stop=True → 全部 15 个工具入口即停",
      "stop_event 已 set → 全部 15 个工具入口即停",
      "★ 15 处入口检查一处不少",
      "TOOL_REGISTRY 包装器把停止信号透传到底"])

_mut_b2, _n_b2 = _SIG_OLD.subn(r"def \1(cfg, app_dir, args):", SCT_SRC)
print("PA4 锚点命中 %d 处新签名（期望 15）" % _n_b2)
assert _n_b2 == 15, "PA4 锚点失配"
case("PA4 只回退签名三参数（入口检查留着）",
     {"SCT_PATH": ("sct_mut_", _mut_b2)},
     ["全部 15 个工具签名都声明了三个扩展参数",
      "should_stop=True → 全部 15 个工具入口即停",
      "TOOL_REGISTRY 包装器把停止信号透传到底"])

# ---------------------------------------------------------------------------
# C 组 G3：_find_control 第 4 级兜底
# ---------------------------------------------------------------------------
_c_start = SWC_SRC.index("    # 4) 只给了 control_type")
_c_end = SWC_SRC.index("control_type='{control_type}'\")\n", _c_start)
_c_end += len("control_type='{control_type}'\")\n")
_mut_c1 = SWC_SRC[:_c_start] + _ORIG_LEVEL4 + SWC_SRC[_c_end:]
assert "仅按 control_type 找第一个" in _mut_c1, "PA5 变异未生效"
case("PA5 整段还原「按类型挑第一个」",
     {"SWC_PATH": ("swc_mut_", _mut_c1)},
     ["同类多候选不匹配 → 抛 RuntimeError",
      "★ 同类仅 1 个也不自动选中（仍抛错）",
      "★ app_click 报「点击失败」而不是「已点击」",
      "★ app_click 全程零点击",
      "★ app_type 报「输入失败」",
      "源码里不再有按类型裸取一个的回落",
      "不再无条件用 child_window 兜底选中"])

# 只加回「唯一即自动选」：枚举与候选清单都还在，删掉的只有「不许替调用方选」这一条判别
_mut_c2 = sub(
    SWC_SRC,
    "        cands = _list_candidates(window, control_type)\n",
    "        cands = _list_candidates(window, control_type)\n"
    "        if len(cands) == 1:  # 扰动：唯一就自动选\n"
    "            return window.child_window(control_type=control_type)\n",
    "pa6 insert auto-pick")
case("PA6 只加回「唯一就自动选」（枚举留着）",
     {"SWC_PATH": ("swc_mut_", _mut_c2)},
     ["★ 同类仅 1 个也不自动选中（仍抛错）",
      "异常给出可操作提示（改成该名字重试）"])

# ---------------------------------------------------------------------------
# 反向基线：原样源码必须零红
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
print("=== PERTURB_SYSTEM_CONTROL_A_OK ===")
