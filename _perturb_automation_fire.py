# -*- coding: utf-8 -*-
"""扰动验证：证明 `tests/test_automation_fire_order.py` 的判据「会红在该红的地方」。

判据绿不等于判据有效。本脚本逐个**拆掉 v4.213.0 修复所守的写法**，看判据是否真转红。

规矩（与 _perturb_critical_proc_deny.py 同一套）：
  1. 期望采用**包含式**：expect ⊆ actual_red；只要求「该红的红了」，不禁止连带红；
  2. 每个 case 真的生成一份变异源码落 %TEMP% 临时文件（不吃沙箱删除配额），
     用 UI_PATH 指向它跑判据，**不在原文件上动刀**；
  3. 收尾跑一次「原样基线」，期望零红 —— 防「判据过宽、什么都判红」。

六个变异对应六组判据：
  AF1 旧顺序回归（mark_fired 先于 fire = v4.212.0 的原始 bug 形态）→ A1a/A1b/A2
  AF2 删空消息守卫（每秒无限重试回归）                        → C1/C2/C3
  AF3 删成功路径 return True（fire 永远不算成功）              → D1b
  AF4 删异常回滚块（重试时消息重复堆积回归）                  → D2a/D2b
  AF5 删异常路径 return False（失败也返回成功）                → D1c
  AF6 删 busy 跳过（Agent 跑着时再塞任务）                    → B

用法：python _perturb_automation_fire.py
"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = HERE
JUDGE = os.path.join(ROOT, "tests", "test_automation_fire_order.py")

sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

UI_SRC = open(os.path.join(ROOT, "ui.py"), encoding="utf-8").read()

PASS_N = 0
FAIL_N = 0
FAILED_CASES = []


def run_judge(env_src):
    paths = []
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.pop("UI_PATH", None)  # 清掉可能泄漏的上一次变异
    try:
        for var, (prefix, src) in env_src.items():
            # 变异副本落 %TEMP%：① 不吃工作区删除配额（L250）② 护栏预检不误扫
            fd, p = tempfile.mkstemp(prefix=prefix, suffix=".py",
                                     dir=tempfile.gettempdir())
            os.close(fd)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write("# 扰动：_perturb_automation_fire 临时副本，跑完即删\n" + src)
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
# AF1：旧顺序回归（v4.212.0 及之前的原始 bug 形态）
# ---------------------------------------------------------------------------
_mut = sub(UI_SRC,
           "                    if self._fire_automation_run(t):\n"
           "                        automation.mark_fired(t, now)\n"
           "                        self.automation_store.save()\n",
           "                    automation.mark_fired(t, now)  # 扰动：旧顺序回归\n"
           "                    self.automation_store.save()\n"
           "                    self._fire_automation_run(t)\n",
           "AF1 revert fire-first order")
case("AF1 恢复旧顺序：先标记后执行（原始 bug 形态）",
     {"UI_PATH": ("ui_fire_mut_", _mut)},
     ["A1a 存在", "A1b 成功守卫", "A2  执行分支无守卫外的裸 mark_fired"])

# ---------------------------------------------------------------------------
# AF2：删空消息守卫
# ---------------------------------------------------------------------------
_mut = sub(UI_SRC,
           "                    if not (t.get(\"message\") or \"\").strip():\n"
           "                        # 任务本身坏了（指令为空），重试也没用 → 标记 fired 防每秒重试\n"
           "                        log.error(\"自动化任务 %r 指令为空，标记已执行防止无限重试\",\n"
           "                                  t.get(\"name\", \"\"))\n"
           "                        automation.mark_fired(t, now)\n"
           "                        self.automation_store.save()\n"
           "                        continue\n",
           "",
           "AF2 drop empty-msg guard")
assert "指令为空，标记已执行防止无限重试" not in _mut, "AF2 变异未生效"
case("AF2 删空消息守卫（空指令任务每秒无限重试回归）",
     {"UI_PATH": ("ui_fire_mut_", _mut)},
     ["C1  空消息守卫存在", "C2  空消息守卫内 mark_fired + continue",
      "C3  空消息守卫在 fire 之前"])

# ---------------------------------------------------------------------------
# AF3：删成功路径 return True
# ---------------------------------------------------------------------------
_mut = sub(UI_SRC,
           "            self._agent_run()\n"
           "            return True\n",
           "            self._agent_run()\n"
           "            pass  # 扰动：成功不返回 True\n",
           "AF3 drop return True")
case("AF3 删成功路径 return True（fire 永远不算成功 → 永不标记）",
     {"UI_PATH": ("ui_fire_mut_", _mut)},
     ["D1b 成功路径 return True"])

# ---------------------------------------------------------------------------
# AF4：删异常回滚块
# v4.215.0 锚点更新：回滚块随独立会话改造换形（旧版 except 里重新取
# store.active()，新版 session 由 try 前置取得、失败回滚同一引用）。
# ---------------------------------------------------------------------------
_mut = sub(UI_SRC,
           "            if appended is not None and session is not None:\n"
           "                try:\n"
           "                    if appended in session.messages:\n"
           "                        session.messages.remove(appended)\n"
           "                        self.store.save()\n"
           "                except Exception:\n"
           "                    pass\n",
           "",
           "AF4 drop rollback")
assert "session.messages.remove(appended)" not in _mut, "AF4 变异未生效"
case("AF4 删异常回滚块（重试时同一指令重复堆积回归）",
     {"UI_PATH": ("ui_fire_mut_", _mut)},
     ["D2a 异常路径回滚 messages.remove", "D2b 回滚后 store.save"])

# ---------------------------------------------------------------------------
# AF5：删异常路径 return False
# ---------------------------------------------------------------------------
_mut = sub(UI_SRC,
           "            log.error(\"自动化任务执行失败（已回滚，将自动重试）: %s\", e)\n"
           "            return False\n",
           "            log.error(\"自动化任务执行失败（已回滚，将自动重试）: %s\", e)\n"
           "            pass  # 扰动：失败不返回 False\n",
           "AF5 drop return False")
case("AF5 删异常路径 return False（失败被当成功 → 任务静默丢失回归）",
     {"UI_PATH": ("ui_fire_mut_", _mut)},
     ["D1c 异常路径 return False"])

# ---------------------------------------------------------------------------
# AF6：删 busy 跳过
# ---------------------------------------------------------------------------
_mut = sub(UI_SRC,
           "                    # 执行任务：App 忙则跳过，等下一 tick 重试（不标记 fired）\n"
           "                    if self._busy:\n"
           "                        continue\n",
           "",
           "AF6 drop busy skip")
assert "if self._busy:" not in _mut.split("def _fire_automation_run")[0].split(
    "def _on_automation_tick")[1], "AF6 变异未生效"
case("AF6 删 busy 跳过（Agent 跑着时再塞任务 → 上下文互踩回归）",
     {"UI_PATH": ("ui_fire_mut_", _mut)},
     ["B   `if self._busy: continue` 跳过保留"])

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
print("=== PERTURB_AUTOMATION_FIRE_OK ===")
