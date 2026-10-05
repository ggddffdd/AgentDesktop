# -*- coding: utf-8 -*-
"""扰动验证：证明 `tests/test_browser_control.py` 的判据「会红在该红的地方」。

判据绿不等于判据有效。本脚本逐个拆掉 browser 链的血泪修复/权限登记，
看判据是否真转红 —— G5 判据倒挂的最后一角（browser 链此前零判据）。

规矩（与 _perturb_automation_fire.py 同一套）：
  1. 期望采用**包含式**：expect ⊆ actual_red；只要求「该红的红了」，不禁止连带红；
  2. 每个 case 生成变异源码落 %TEMP% 临时文件（不吃沙箱删除配额），
     用 BR_PATH / BCT_PATH / RISK_PATH 指向它跑判据，**不在原文件上动刀**；
  3. 收尾跑一次「原样基线」，期望零红 —— 防「判据过宽、什么都判红」。

六个变异（外部审核 P1-4 的担忧 + 项目自己的血泪修复）：
  PB1 browser_click 权限 EXEC→READ（静默降级，审核报告最担心的形态） → F1
  PB2 子进程 env 注入 utf-8→gbk（H-09：中文静默丢字）                → C5+G5
  PB3 stdin 阈值 800→99999（P2-10：长文走命令行被 32767 截断）       → C2+G4
  PB4 删 CDP read 导航分支（v4.147.9：read 永远读到当前页）          → G2
  PB5 inner_text("body")→无参（H-07：整页 read 直接 TypeError）      → G1
  PB6 JSON 解析 reversed→正序（取到第一行的 ok:false 旧行）          → D1

用法：python _perturb_browser_control.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
JUDGE = os.path.join(ROOT, "tests", "test_browser_control.py")

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

# runner 是 LF 无 BOM；control 是 CRLF 无 BOM；risk 是 CRLF + BOM（utf-8-sig 读掉再写回无 BOM
# —— coding cookie 被扰动头挤到第 2 行仍合法，Python 只认前两行）
BR_SRC = open(os.path.join(ROOT, "browser_runner.py"), encoding="utf-8").read()
BCT_SRC = open(os.path.join(ROOT, "browser_control_tools.py"), encoding="utf-8").read()
RISK_SRC = open(os.path.join(ROOT, "risk.py"), encoding="utf-8-sig").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(env_src):
    paths = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("BR_PATH", "BCT_PATH", "RISK_PATH"):  # 清掉可能泄漏的上一次变异
        env.pop(var, None)
    try:
        for var, (prefix, src) in env_src.items():
            fd, p = tempfile.mkstemp(prefix=prefix, suffix=".py",
                                     dir=tempfile.gettempdir())
            os.close(fd)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write("# 扰动：_perturb_browser_control 临时副本，跑完即删\n" + src)
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
# PB1：browser_click 权限静默降级（EXEC → READ = 外部审核 P1-4 的核心担忧）
# ---------------------------------------------------------------------------
_mut = sub(RISK_SRC,
           '"browser_click": RiskClass.EXEC,',
           '"browser_click": RiskClass.READ,  # 扰动：权限静默降级',
           "PB1 click EXEC->READ")
case("PB1 browser_click EXEC→READ（点击不再手动确认，静默降级）",
     {"RISK_PATH": ("risk_br_mut_", _mut)},
     ["F1"])

# ---------------------------------------------------------------------------
# PB2：子进程 env 注入 utf-8 → gbk（H-09：中文 JSON 被 GBK 污染静默丢字）
# ---------------------------------------------------------------------------
_mut = sub(BCT_SRC,
           '_run_env["PYTHONIOENCODING"] = "utf-8"',
           '_run_env["PYTHONIOENCODING"] = "gbk"  # 扰动',
           "PB2 env gbk")
case("PB2 子进程 env 注入 utf-8→gbk（中文结果静默乱码丢字）",
     {"BCT_PATH": ("bct_br_mut_", _mut)},
     ["C5", "G5"])

# ---------------------------------------------------------------------------
# PB3：stdin 阈值 800 → 99999（P2-10：长文走命令行被 Windows 32767 截断）
# ---------------------------------------------------------------------------
_mut = sub(BCT_SRC,
           'use_stdin = bool(text) and len(text) > 800',
           'use_stdin = bool(text) and len(text) > 99999  # 扰动',
           "PB3 stdin threshold")
case("PB3 stdin 阈值 800→99999（长文回到命令行被 32767 截断）",
     {"BCT_PATH": ("bct_br_mut_", _mut)},
     ["C2", "G4"])

# ---------------------------------------------------------------------------
# PB4：删 CDP read 导航分支（v4.147.9：read 永远读到当前页的根因）
# ---------------------------------------------------------------------------
_mut = sub(BR_SRC,
           'or (args.action == "read" and args.url not in page.url)',
           'or (args.action == "read" and False)  # 扰动',
           "PB4 drop cdp read nav")
case("PB4 删 CDP read 导航分支（read 永远读到当前页，军团抓取全废）",
     {"BR_PATH": ("br_br_mut_", _mut)},
     ["G2"])

# ---------------------------------------------------------------------------
# PB5：inner_text("body") → 无参（H-07：整页 read 直接 TypeError）
# ---------------------------------------------------------------------------
_mut = sub(BR_SRC,
           'text = page.inner_text("body")',
           'text = page.inner_text()  # 扰动',
           "PB5 inner_text no arg")
case("PB5 inner_text(\"body\")→无参（整页 read 直接 TypeError 崩掉）",
     {"BR_PATH": ("br_br_mut_", _mut)},
     ["G1"])

# ---------------------------------------------------------------------------
# PB6：JSON 解析 reversed → 正序（取到第一行的 ok:false 旧行）
# ---------------------------------------------------------------------------
_mut = sub(BCT_SRC,
           'for line in reversed(proc.stdout.strip().splitlines()):',
           'for line in proc.stdout.strip().splitlines():  # 扰动',
           "PB6 no reversed")
case("PB6 JSON 尾行解析改正序（取到第一行 ok:false 旧行，结果误判失败）",
     {"BCT_PATH": ("bct_br_mut_", _mut)},
     ["D1"])

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
print("=== PERTURB_BROWSER_CONTROL_OK ===")
