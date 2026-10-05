# -*- coding: utf-8 -*-
"""扰动验证：证明 `tests/test_automation_isolation.py` 的判据「会红在该红的地方」。

判据绿不等于判据有效。本脚本逐个**拆掉 v4.215.0 修复所守的写法**，看判据是否真转红。

规矩（与 _perturb_automation_fire.py 同一套）：
  1. 期望采用**包含式**：expect ⊆ actual_red；只要求「该红的红了」，不禁止连带红；
  2. 每个 case 真的生成一份变异源码落 %TEMP% 临时文件（不吃沙箱删除配额），
     用 UI_PATH / AUTO_PATH 指向它跑判据，**不在原文件上动刀**；
     （AUTO_PATH 变异由判据 A 组用 importlib 从副本加载，C 组仍测真模块）
  3. 收尾跑一次「原样基线」，期望零红 —— 防「判据过宽、什么都判红」。

六个变异对应 v4.215.0 的六个守卫：
  PI1 _fire 回退 store.active()（任务又混进用户当前会话）    → B1/B4
  PI2 删 _agent_run 的受限工具分支（权限继承回归）           → B6
  PI3 full_tools 守卫反转（默认放开全部工具）                → B7
  PI4 filter_tools_safe 变 identity（滤除全失效）            → A3/A4/A5
  PI5 删 manual 档滤除（write_file 等卡确认框回归）          → A5
  PI6 new_task full_tools 默认 True（保守侧失守）            → A8

用法：python _perturb_automation_isolation.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
JUDGE = os.path.join(ROOT, "tests", "test_automation_isolation.py")

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

UI_SRC = open(os.path.join(ROOT, "ui.py"), encoding="utf-8").read()
AUTO_SRC = open(os.path.join(ROOT, "automation.py"), encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(env_src):
    paths = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("UI_PATH", None)  # 清掉可能泄漏的上一次变异
    env.pop("AUTO_PATH", None)
    try:
        for var, (prefix, src) in env_src.items():
            fd, p = tempfile.mkstemp(prefix=prefix, suffix=".py",
                                     dir=tempfile.gettempdir())
            os.close(fd)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write("# 扰动：_perturb_automation_isolation 临时副本，跑完即删\n" + src)
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
# PI1：_fire 回退 store.active()（任务混进用户当前会话 = v4.214.0 原始形态）
# ---------------------------------------------------------------------------
_mut = sub(UI_SRC,
           "            session = self._automation_session(task)\n",
           "            session = self.store.active()  # 扰动：回退混流\n",
           "PI1 revert to active session")
case("PI1 _fire 回退 store.active()（混流回归）",
     {"UI_PATH": ("ui_iso_mut_", _mut)},
     ["B1 _fire 不再调用", "B4 消息 append"])

# ---------------------------------------------------------------------------
# PI2：删 _agent_run 的受限工具分支（工具权限继承回归）
# ---------------------------------------------------------------------------
_mut = sub(UI_SRC,
           "        _auto_t = getattr(self, \"_auto_task_active\", None)\n"
           "        if _auto_t is not None and not _auto_t.get(\"full_tools\"):\n"
           "            all_tools = automation.filter_tools_safe(all_tools)\n",
           "",
           "PI2 drop tool filter")
case("PI2 删 _agent_run 受限工具分支",
     {"UI_PATH": ("ui_iso_mut_", _mut)},
     ["B6 _agent_run 有受限工具分支"])

# ---------------------------------------------------------------------------
# PI3：full_tools 守卫反转（默认放开全部工具）
# ---------------------------------------------------------------------------
_mut = sub(UI_SRC,
           "        if _auto_t is not None and not _auto_t.get(\"full_tools\"):\n",
           "        if _auto_t is not None and _auto_t.get(\"full_tools\"):  # 扰动：反转\n",
           "PI3 invert full_tools guard")
case("PI3 full_tools 守卫反转（默认全放开）",
     {"UI_PATH": ("ui_iso_mut_", _mut)},
     ["B7 full_tools=True 才放开"])

# ---------------------------------------------------------------------------
# PI4：filter_tools_safe 变 identity（滤除全失效）
# ---------------------------------------------------------------------------
_mut = sub(AUTO_SRC,
           "        kept.append(t)\n"
           "    return kept\n",
           "        kept.append(t)\n"
           "    return list(tools or [])  # 扰动：identity\n",
           "PI4 identity filter")
case("PI4 filter_tools_safe 变 identity（EXEC/EXTERNAL/硬确认全漏过）",
     {"AUTO_PATH": ("auto_iso_mut_", _mut)},
     ["A3 EXEC/EXTERNAL/未登记全滤", "A4 硬确认档滤", "A5 manual 档本地写滤"])

# ---------------------------------------------------------------------------
# PI5：删 manual 档滤除（write_file 等卡确认框回归）
# ---------------------------------------------------------------------------
_mut = sub(AUTO_SRC,
           "        if _tier_of(name) == \"manual\":\n"
           "            continue\n",
           "",
           "PI5 drop manual-tier filter")
case("PI5 删 manual 档滤除（无人值守卡确认框回归）",
     {"AUTO_PATH": ("auto_iso_mut_", _mut)},
     ["A5 manual 档本地写滤"])

# ---------------------------------------------------------------------------
# PI6：new_task full_tools 默认 True（保守侧失守）
# ---------------------------------------------------------------------------
_mut = sub(AUTO_SRC,
           "        \"full_tools\": bool(full_tools),\n",
           "        \"full_tools\": True,  # 扰动：默认全开\n",
           "PI6 full_tools default True")
case("PI6 new_task full_tools 默认 True",
     {"AUTO_PATH": ("auto_iso_mut_", _mut)},
     ["A8 new_task full_tools 默认 False"])

# ---------------------------------------------------------------------------
# 反向基线：原样源码必须零红（防「判据过宽、什么都判红」）
# ---------------------------------------------------------------------------
red, out = run_judge({})
print("-" * 66)
print("case 反向基线（原样源码，期望零红）")
if red:
    print("  [FAIL] 原样就红，判据过宽：%s" % red)
    FAIL_N += 1
    FAILED_CASES.append("反向基线（原样红）")
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
