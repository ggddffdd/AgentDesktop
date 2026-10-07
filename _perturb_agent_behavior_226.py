# -*- coding: utf-8 -*-
"""扰动验证 v4.226 行为级套件：改坏主循环接线，看**行为断言**是否翻红。

与另两个扰动脚本的分工
----------------------
  _perturb_agent_loop_226.py  钉静态层（调用点/判定层）
  _perturb_skill_meta_226.py  钉元数据闸
  **本脚本**（行为层）        钉「接上了但动作没做 / 时序错了」

前两者都可能在源码串仍留存的情况下让静态判据照绿 —— 那种变异只有本脚本能抓。
所以本脚本的期望串**只取行为级断言（AB-*）**，不碰静态断言。

⚠️ 会改 agent.py / agent_loop_mixin.py / agent_loop.py，**必须独占运行**。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TESTS = [
    os.path.join(ROOT, "tests", "test_agent_behavior_226.py"),
]
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
            reds += re.findall(r"\[FAIL\] ([^\n]+)", out)
            if "Traceback" in out and not reds:
                reds.append("崩溃:%s" % t)
        except Exception as e:
            reds.append("崩溃:%s" % e)
    return reds


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

# (名称, 文件, 原串, 新串, 期望翻红的行为级断言片段)
CASES = [
    # ── 接线整段删（静态层也红，但这里证明**行为**确实塌了）──
    ("整段删：run() 不调 _loop_start（行为：无清单注入）",
     "agent.py",
     "        self._loop_start(agent_loop)\n",
     "",
     ["AB-1-1 行为级：PLAN 阶段真的注入了任务清单"]),

    ("整段删：工具后不调 _loop_verify（行为：无核验记录）",
     "agent.py",
     "                self._loop_verify(agent_loop)\n",
     "",
     ["AB-1-5 行为级：VERIFY 阶段固化了核验记录"]),

    ("整段删：收尾不调 _loop_summary（行为：小结消失）",
     "agent.py",
     "                self._loop_summary(agent_loop)\n",
     "",
     ["AB-1-7 行为级：收尾发出了机器小结"]),

    # ── 只删动作留条件：源码串仍在，**只有行为断言能抓** ──
    ("只删动作留条件：start 注入的计划消息不真进 messages（源码串仍留）",
     "agent_loop_mixin.py",
     "            self.messages.append({\"role\": \"system\", \"content\": instr,\n"
     "                                  \"_internal\": True})",
     "            if False:\n"
     "                self.messages.append({\"role\": \"system\", \"content\": instr,\n"
     "                                      \"_internal\": True})",
     ["AB-1-1 行为级：PLAN 阶段真的注入了任务清单"]),

    ("只删动作留条件：小结生成但不发出（黑进黑洞，用户看不到）",
     "agent_loop_mixin.py",
     "            self.stream_commit.emit(\"\\n\\n\" + text)",
     "            if False:\n                self.stream_commit.emit(\"\\n\\n\" + text)",
     ["AB-1-7 行为级：收尾发出了机器小结"]),

    ("只删动作留条件：verify 不固化记录（账本结论没人看）",
     "agent_loop_mixin.py",
     "            _lp.verification = agent_loop.verify_report(ts)",
     "            if False:\n                _lp.verification = agent_loop.verify_report(ts)",
     ["AB-1-5 行为级：VERIFY 阶段固化了核验记录"]),

    # ⚠️ 下面两条 case 曾长期是哑弹，**已删除**并在此记录原因（不是「跑绿了
    # 就留着」，而是它们检验的东西根本不成立）：
    #  ① 「summarize 反复放行」：_loop_summary 的**调用点只有一处**（break 前），
    #     把 emitted 换成 False 在行为上无害（仍只发一次小结）。真正的抓手是
    #     静态层 LP11-15（直接连调两次看是否拦），归loop 扰动管。
    #  ② 「build_summary 零事实也出串」：纯咨询场景同时缺 has_requirements，
    #     被那道门兜住；行为层无解。它的抓手是 LP7-4b（静态直接测 build_summary）。
    #     教训：扰动红 0 条时先问「**这条变异到底改变了行为吗**」，而不是
    #     急着改判据——那会把真缺口伪装成「判据已覆盖」。

    # ── 时序变异：静态判据 LP9-10抓得到，但这里证明**用户可见行为**也塌了 ──
    ("时序错：把 _loop_summary 挪到 tstate 闸门**之前**（半截结论里插注解）",
     "agent.py",
     "                if self._tstate_nudge_now(task_state, step, self._max_steps):\n"
     "                    self._tstate_trace_nudge(step, _tracer)\n"
     "                    continue\n"
     "                # ── v4.226（四阶段循环）：SUMMARIZE 收尾 ──\n"
     "                # 位置在 tstate 闸门**之后**、break 之前：闸门放行是「再给一轮」，\n"
     "                # 那时还没收尾，发小结就成了半截结论里的注解。真正走到这里才追加。\n"
     "                # 小结内容全部来自账本事实（工具真调过没、产物落没落盘），\n"
     "                # 与模型自述冲突时以小结为准。\n"
     "                self._loop_summary(agent_loop)\n"
     "                break",
     "                self._loop_summary(agent_loop)\n"
     "                if self._tstate_nudge_now(task_state, step, self._max_steps):\n"
     "                    self._tstate_trace_nudge(step, _tracer)\n"
     "                    continue\n"
     "                break",
     ["AB-3-5 小结出现在最终结论**之后**（不是半截结论的注解）"]),

    # ── 判定层变异（源码串在，只抓行为）──
    ("只删动作留条件：PLAN 门槛降到 1（单硬要求也开始注入清单）",
     "agent_loop.py",
     "PLAN_MIN_REQUIREMENTS = 2",
     "PLAN_MIN_REQUIREMENTS = 1",
     ["AB-2-1 单硬要求不注入任务清单（零行为变化）"]),

    # ⚠️ 「build_summary 空事实也出串」曾长期是哑弹，**已删除**：
    #   纯咨询场景（AB-4）同时缺 has_requirements，被那道门兜住；
    #   有事实场景（AB-7）根本不走那个空串早返回。行为层无解。
    #   它的抓手在静态层 LP7-4b（直接测 build_summary），归 loop 扰动管。
    #   这正是「静态层 + 行为层分工」的例证：不是每条变异都该由行为层抓。

    ("只删动作留条件：has_requirements 门槛失效（点名核对被绕过）",
     "agent_loop.py",
     "        if not ts.has_requirements():\n            return False",
     "        if False:\n            pass",
     # 只期望 AB-7-2：AB-4（纯咨询）同时也没事实，被 build_summary 的
     # 「无事实→空串」兜住，那道门单独失效它照绿。
     ["AB-7-2 但用户没点名 → 不出核对小结"]),

    ("只删动作留条件：小结不列未完成项（半截结论被说成全做完了）",
     "agent_loop.py",
     '        lines.append("未完成：%s" % ("；".join(unmet[:6]) if unmet else "无"))',
     '        lines.append("未完成：无")',
     ["AB-6-4 小结的未完成段如实列出 write_file 未被调用",
      "AB-6-5 小结不得出现「未完成：无」（掩盖未达标）"]),

    ("只删动作留条件：is_verified 恒真（未达标也报已核验）",
     "agent_loop.py",
     "        return bool(tstate.is_complete())",
     "        return True",
     # AB-1-6（全部落实→True）在这条变异下仍照绿（本来就该True），
     # 真正的抓手是 AB-6-2（未达标→必须 False）。
     ["AB-6-2 未达标时 verified=False（不得自称已核验）"]),

    # ── 假执行链路退化：验证「工具真被调用」这条断言非恒真 ──
    ("反向钉子：账本记账被摘掉（工具调了但账本空 → 小结失真）",
     "agent_task_mixin.py",
     "            _ts.record_tool(name, _args, result_str, ok=_ok,\n"
     "                            step=_ts.current_step)",
     "            if False:  # 不记账\n"
     "                _ts.record_tool(name, _args, result_str, ok=_ok,\n"
     "                                step=_ts.current_step)",
     ["AB-1-4 账本记录了两个工具",
      "AB-1-8 小结列出了真实调用的工具"]),
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