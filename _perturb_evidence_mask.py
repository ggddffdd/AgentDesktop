# -*- coding: utf-8 -*-
"""扰动验证：证明 `tests/test_evidence_mask.py` 的判据「会红在该红的地方」。

判据绿不等于判据有效。本脚本逐个**拆掉 v4.214.0 脱敏所守的写法**，看判据是否真转红。

规矩（与 _perturb_automation_fire.py 同一套）：
  1. 期望采用**包含式**：expect ⊆ actual_red；只要求「该红的红了」，不禁止连带红；
  2. 每个 case 真的生成一份变异源码落 %TEMP% 临时文件（不吃沙箱删除配额），
     用 EV_PATH 指向它跑判据，**不在原文件上动刀**；
  3. 收尾跑一次「原样基线」，期望零红 —— 防「判据过宽、什么都判红」。

六个变异对应六面防线：
  EM1 删 raw 打码行（原文明文落库）                          → B1b / C2a
  EM2 删 args_s 打码行（工具入参里的 key 明文落库）          → B3 / C2b
  EM3 sk- 规则废掉（API key 漏脱敏）                         → A1
  EM4 bearer|basic 规则废掉（HTTP 认证头漏脱敏）             → A2a / A2b
  EM5 KV 键名规则废掉（api_key 键漏脱敏）                    → A3a
  EM6 过度打码（_mask_secrets 恒返 ***，证据链自毁）         → A6a / A6b / B4

用法：python _perturb_evidence_mask.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
JUDGE = os.path.join(ROOT, "tests", "test_evidence_mask.py")

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

EV_SRC = open(os.path.join(ROOT, "evidence.py"), encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(env_src):
    paths = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("EV_PATH", None)  # 清掉可能泄漏的上一次变异
    try:
        for var, (prefix, src) in env_src.items():
            # 变异副本落 %TEMP%：① 不吃工作区删除配额（L250）② 护栏预检不误扫
            fd, p = tempfile.mkstemp(prefix=prefix, suffix=".py",
                                     dir=tempfile.gettempdir())
            os.close(fd)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write("# 扰动：_perturb_evidence_mask 临时副本，跑完即删\n" + src)
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
# EM1：删 raw 打码行（外部审核 P1-3 的原始风险形态：明文落库）
# ---------------------------------------------------------------------------
_mut = sub(EV_SRC,
           "        raw = _mask_secrets(raw)\n",
           "        # 扰动：raw 不打码，明文落库\n",
           "EM1 drop raw mask")
assert "raw = _mask_secrets(raw)" not in _mut.replace("# 扰动", ""), "EM1 变异未生效"
case("EM1 删 raw 打码（凭据明文落库 = 审核原始风险）",
     {"EV_PATH": ("ev_mask_mut_", _mut)},
     ["B1b raw 落库前已打码", "C2a register 内对 raw 打码"])

# ---------------------------------------------------------------------------
# EM2：删 args_s 打码行（工具入参里的 api_key 明文落库）
# ---------------------------------------------------------------------------
_mut = sub(EV_SRC,
           "        args_s = _mask_secrets(args_s)\n",
           "        # 扰动：args 不打码，明文落库\n",
           "EM2 drop args mask")
case("EM2 删 args 打码（入参 key 明文落库）",
     {"EV_PATH": ("ev_mask_mut_", _mut)},
     ["B3  args 落库前已打码", "C2b register 内对 args_s 打码"])

# ---------------------------------------------------------------------------
# EM3：sk- 规则废掉
# ---------------------------------------------------------------------------
_mut = sub(EV_SRC,
           "sk-[A-Za-z0-9_-]{16,}",
           "zk-[A-Za-z0-9_-]{16,}",
           "EM3 kill sk- rule")
case("EM3 sk- 规则废掉（API key 漏脱敏）",
     {"EV_PATH": ("ev_mask_mut_", _mut)},
     ["A1  sk- API key 打码"])

# ---------------------------------------------------------------------------
# EM4：bearer|basic 规则废掉
# ---------------------------------------------------------------------------
_mut = sub(EV_SRC,
           "(bearer|basic)",
           "(bearerx|basicx)",
           "EM4 kill auth-scheme rule")
case("EM4 bearer|basic 规则废掉（HTTP 认证头漏脱敏）",
     {"EV_PATH": ("ev_mask_mut_", _mut)},
     ["A2a Bearer", "A2b Basic"])

# ---------------------------------------------------------------------------
# EM5：KV 键名规则废掉（api_key 键）
# ---------------------------------------------------------------------------
_mut = sub(EV_SRC,
           "api[-_]?key",
           "apx[-_]?key",
           "EM5 kill kv api_key rule")
case("EM5 KV 键名规则废掉（api_key 键漏脱敏）",
     {"EV_PATH": ("ev_mask_mut_", _mut)},
     ["A3a JSON api_key"])

# ---------------------------------------------------------------------------
# EM6：过度打码（恒返 ***，证据链自毁 —— 「默认只存摘要」的错误形态）
# ---------------------------------------------------------------------------
_mut = sub(EV_SRC,
           "    if not text:\n"
           "        return text\n"
           "    try:\n",
           "    if text is None:  # 扰动：过度打码\n"
           "        return None\n"
           "    if isinstance(text, str):\n"
           "        return \"***\"\n"
           "    try:\n",
           "EM6 over-mask")
case("EM6 过度打码（恒返 ***，证据链自毁）",
     {"EV_PATH": ("ev_mask_mut_", _mut)},
     ["A6a 短值", "A6b 普通文本", "B4  无凭据证据逐字保留"])

# ---------------------------------------------------------------------------
# 反向基线：原样副本必须零红（防判据过宽、什么都判红）
# ---------------------------------------------------------------------------
print("-" * 66)
print("case 反向基线（原样副本，期望零红）")
red, out = run_judge({"EV_PATH": ("ev_mask_base_", EV_SRC)})
if red:
    print("  [FAIL] 原样副本就红：%s" % red[:5])
    FAIL_N += 1
    FAILED_CASES.append("反向基线（原样翻红）")
else:
    print("  [OK  ] 原样零红")
    PASS_N += 1

# ---------------------------------------------------------------------------
print("=" * 66)
print("PERTURB PASS=%d FAIL=%d" % (PASS_N, FAIL_N))
if FAILED_CASES:
    for c in FAILED_CASES:
        print("  哑弹/漏红：%s" % c)
    sys.exit(1)
sys.exit(0)
