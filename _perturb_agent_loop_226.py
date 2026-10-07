# -*- coding: utf-8 -*-
"""扰动验证 v4.226 四阶段循环：抽掉阶段/接线/门槛，看判据是否翻红。

手法：备份原字节 → 整段删 + 只删动作留条件 → 跑判据 → 期望对应条目翻红 → 恢复。
本脚本验证的是「判据非空谓词」：**哑弹 = 改坏了却没人抓 = 判据废了**。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TESTS = [
    os.path.join(ROOT, "tests", "test_agent_loop_phases_226.py"),
    os.path.join(ROOT, "tests", "test_task_state_machine_225.py"),
    os.path.join(ROOT, "tests", "test_split_216.py"),
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
            reds += re.findall(r"\[FAIL\] ([^\n]+)", (r.stdout or "") + (r.stderr or ""))
            reds += re.findall(r"\[FAIL\] ([^\n]+)", (r.stdout or "") + (r.stderr or ""))
        except Exception as e:
            reds.append("崩溃:%s" % e)
    return reds


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

# (名称, 文件, 原串, 新串, 期望翻红的判据片段)
CASES = [
    # ── 整段删 ──
    ("整段删：agent.py 不再 import 四阶段模块（接线全成死引用）",
     "agent.py",
     "import agent_loop  # v4.226：计划-执行-验证-总结 四阶段循环（判定层）\n",
     "",
     ["LP9-1 agent.py import agent_loop"]),

    # ⚠️ 原串必须**逐字等于**当前类声明（框架是纯字符串替换，且匹配前已把
#    \r\n 归一化成 \n，故必须写 LF）。v4.226 下半轮新增第三个 mixin
#    （AgentResultMixin）且类声明因行宽折成两行 → 原串同步（实测踩中静默 SKIP）。
    ("整段删：AgentWorker 不再 mixin AgentLoopMixin",
     "agent.py",
     "class AgentWorker(AgentTaskMixin, AgentLoopMixin,\n"
     "                   AgentResultMixin, QThread):",
     "class AgentWorker(AgentTaskMixin, QThread):",
     ["LP9-3 AgentWorker 继承 AgentLoopMixin"]),

    ("整段删：run() 不再调 _loop_start（四阶段永不启动）",
     "agent.py",
     "        self._loop_start(agent_loop)\n",
     "",
     ["LP9-4 run() 开头调 _loop_start"]),

    ("整段删：工具后不再调 _loop_verify（永远不进 VERIFY）",
     "agent.py",
     "                self._loop_verify(agent_loop)\n",
     "",
     ["LP9-5 工具执行后调 _loop_verify"]),

    ("整段删：收尾不再调 _loop_summary（小结消失）",
     "agent.py",
     "                self._loop_summary(agent_loop)\n",
     "",
     ["LP9-6 收尾调 _loop_summary"]),

    ("整段删：每步不再调 _loop_step",
     "agent.py",
     "            self._loop_step(step)    # v4.226：四阶段循环同步步号\n",
     "",
     ["LP9-7 每步调 _loop_step"]),

    # ── 只删动作留条件 ──
    # 注意：这一类变异**保留原串字面**（包进 if False），源码层断言照绿，
    # 真正能抓它的是**行为级** LP11-* —— 这正是四层判据的分工。
    ("只删动作留条件：start 不再注入计划消息（阶段动了、用户看不到）",
     "agent_loop_mixin.py",
     "            self.messages.append({\"role\": \"system\", \"content\": instr,\n"
     "                                  \"_internal\": True})",
     "            if False:  # 不注入\n"
     "                self.messages.append({\"role\": \"system\", \"content\": instr,\n"
     "                                      \"_internal\": True})",
     ["LP11-2 行为级：messages 里确有计划指令"]),

    ("只删动作留条件：start 注入后不置 plan_injected（可重复注入计划）",
     "agent_loop_mixin.py",
     "            self._loop.plan_injected = True",
     "            pass",
     ["LP11-4 行为级：plan_injected 置位且计划已存"]),

    ("只删动作留条件：start 不推进到 EXECUTE（卡死在 PLAN 阶段）",
     "agent_loop_mixin.py",
     "            self._loop.enter(agent_loop.PHASE_EXECUTE, 1)",
     "            pass",
     ["LP11-3 行为级：阶段推进到 execute"]),

    ("只删动作留条件：summary 不经 stream_commit 发出（小结写进黑洞）",
     "agent_loop_mixin.py",
     "            self.stream_commit.emit(\"\\n\\n\" + text)",
     "            pass",
     ["LP11-13 行为级：小结经 stream_commit 发出"]),

    ("只删动作留条件：summary 不置 summary_emitted（每轮重复发小结）",
     "agent_loop_mixin.py",
     "            _lp.summary_emitted = True",
     "            pass",
     ["LP11-15 行为级：第二次 _loop_summary 不重复追加"]),

    ("只删动作留条件：verify 不进 VERIFY 阶段（阶段永远停在 execute）",
     "agent_loop_mixin.py",
     "            _lp.enter(agent_loop.PHASE_VERIFY, _lp.step)",
     "            pass",
     ["LP11-10 行为级：阶段进 verify 且 verified=False（有未达标）"]),

    # 注意：这条把 verified 硬编码成 True → LP11-11（全部达标时 verified=True）
    # 仍照绿（它本来就该True），真正红的是 LP11-10（有未达标却报已核验）。
    # 判据不该把「本来就绿的」写进期望 —— 那是凑数，不是检验。
    ("只删动作留条件：mixin 自行改判 verified（绕过 is_verified 真源）",
     "agent_loop_mixin.py",
     "            _lp.verified = agent_loop.is_verified(ts)",
     "            _lp.verified = True  # 自行拍板，不查账本",
     ["LP11-10 行为级：阶段进 verify 且 verified=False（有未达标）"]),

    # ── 判定层退化 ──
    # 注意：门槛降到 1 后 LP3-1（零要求）**仍照绿** —— 0>=1 依然是 False。
    # 真正红的是 LP3-2（单要求被误当多要求）与 LP3-6（常量被改）。
    ("只删动作留条件：PLAN 门槛降到 1（单硬要求也注入计划，浪费一轮 prompt）",
     "agent_loop.py",
     "PLAN_MIN_REQUIREMENTS = 2",
     "PLAN_MIN_REQUIREMENTS = 1",
     ["LP3-2 单硬要求 → 不注入计划（步骤唯一，不浪费一轮）",
      "LP3-6 门槛常量=2"]),

    ("只删动作留条件：PLAN 门槛忽略步号（每步都注入计划）",
     "agent_loop.py",
     "        if int(step or 1) != 1:\n            return False   # 只在第一步注入一次",
     "        if False:\n            return False",
     ["LP3-5 非第一步 → 不注入"]),

    ("只删动作留条件：末阶段不再饱和（summarize 回退到 execute）",
     "agent_loop.py",
     "        return PHASE_ORDER[i + 1] if i + 1 < len(PHASE_ORDER) else PHASE_ORDER[i]",
     "        return PHASE_ORDER[(i + 1) % len(PHASE_ORDER)]",
     ["LP1-6 next_phase(summarize) 饱和不回退（不回 execute）",
      "LP1-11 连 advance 四次仍饱和在 summarize"]),

    ("只删动作留条件：enter 接受任意阶段名（非法阶段静默改状态）",
     "agent_loop.py",
     "            if phase in PHASE_ALL:\n                self.phase = phase\n"
     "                self.history.append((phase, int(step or 0)))",
     "            self.phase = phase\n            self.history.append((phase, int(step or 0)))",
     ["LP2-1 非法阶段名被忽略（phase 不变）",
      "LP2-2 非法阶段不入 history"]),

    ("只删动作留条件：小结不列未完成项（只剩好话）",
     "agent_loop.py",
     '        lines.append("未完成：%s" % ("；".join(unmet[:6]) if unmet else "无"))',
     '        lines.append("未完成：无")',
     ["LP8-3 小结含未完成项（write_file 没调）",
      "LP8-7 失败工具进未完成段"]),

    ("只删动作留条件：小结去掉「以本段为准」（默许模型幻觉自述）",
     "agent_loop.py",
     '        lines.append("（以上由系统按真实工具调用记录生成，与你的自述可能不一致；"\n'
     '                     "如有出入以本段为准。）")',
     "        pass",
     ["LP8-4 小结显式声明「以本段为准」（对抗模型幻觉自述）"]),

    # ⚠️ 曾长期是哑弹：早先 should_summarize 里还有一道「有工具调用」gate，
    # 删掉后被 build_summary 的「无事实→空串」兜住，红 0 条。已把那道**装饰性**
    # gate 从实现里删掉（登记了却从不改变结论 = 失效方式恰恰是「删了没人发现」），
    # 现在真门槛是 has_requirements + build_summary 非空。
    # 期望串用 LP7-3b（「有工具调用但没点名」）—— 它才是 has_requirements 的
    # 唯一抓手：纯咨询用例（LP7-3）同时也没事实，两道门一起失效也看不出来；
    # 而它与 LP7-3/LP8-5 不同，删掉这道门**只有它**会翻红。
    # 反过来说，LP7-3/LP8-5 在这条变异下仍照绿（被 build_summary 兜住）——
    # 把「本来就该绿」的断言写进期望 = 要求判据误报，那等于在逼判据说谎。
    ("只删动作留条件：SUMMARIZE 拆掉「有硬要求」这一条（点名核对被绕过）",
     "agent_loop.py",
     "        if not ts.has_requirements():\n            return False",
     "        pass",
     ["LP7-3b 有工具调用但用户没点名 → 仍不追加小结（点名核对才有意义）"]),

    # 空话兜底：build_summary 内的「无事实→空串」早返回
    ("只删动作留条件：build_summary 不再对零事实返回空串（空话小结）",
     "agent_loop.py",
     "        used = list(getattr(ts, \"used_tools\", None) or ())\n"
     "        if not used:\n            return \"\"\n        artifacts = list(getattr(ts, \"artifacts\", None) or ())",
     "        used = list(getattr(ts, \"used_tools\", None) or ())\n"
     "        artifacts = list(getattr(ts, \"artifacts\", None) or ())",
     ["LP7-4b 零事实时 build_summary 本身返回空串（兜底真在，非装饰 gate）",
      "LP11-16 行为级：零工具调用不追加小结"]),

    ("只删动作留条件：is_verified 忽略账本（永远说已核验通过）",
     "agent_loop.py",
     "        return bool(tstate.is_complete())",
     "        return True",
     ["LP6-11 is_verified：有未调用点名工具 → False",
      "LP11-10 行为级：阶段进 verify 且 verified=False（有未达标）"]),

    ("只删动作留条件：verify 记录不再列未调用工具（核验漏项）",
     "agent_loop.py",
     '            out.append("未满足：用户点名的工具 %s 从未被调用" % t)',
     "            pass",
     ["LP6-5 记录点名未调用的硬要求"]),

    ("只删动作留条件：计划文案去掉「不要复述清单」（弱模型拿复述当交差）",
     "agent_loop.py",
     '        lines = ["【任务计划】用户点名了以下要求，本轮必须逐条落实（这是给你的"\n'
     '                 "核对清单，**不要**把它复述一遍当完成）："]',
     '        lines = ["【任务计划】用户点名了以下要求："]',
     ["LP4-4 文案带「不要复述清单」反交差约束"]),

    ("只删动作留条件：build_plan 臆造步骤（写下「第一步/第二步」）",
     "agent_loop.py",
     '            out.append("必须完成：真实调用工具 %s" % t)',
     '            out.append("必须完成：第一步真实调用工具 %s" % t)',
     ["LP5-1 不臆造「第一步/第二步」步骤词"]),

    ("只删动作留条件：产物存在性检查放行（声称成功即算落地）",
     "agent_loop.py",
     '        landed = [a[0] for a in artifacts if len(a) >= 3 and a[0] and a[1]]',
     '        landed = [a[0] for a in artifacts if len(a) >= 3 and a[0]]',
     ["LP8-9 未落地产物不进证据段（claimed≠landed）"]),

    # ── 反向钉子：mixin 复制判定（判据漂移） ──
    ("反向钉子：mixin 自行实现 verified 判定（绕过 is_verified 真源）",
     "agent_loop_mixin.py",
     "            _lp.verified = agent_loop.is_verified(ts)",
     "            _lp.verified = not (list(ts.missing_tools() or ()) or\n"
     "                                list(ts.missing_artifacts() or ()))",
     ["LP12-6 mixin 全部判定都经 agent_loop. 前缀（无本地重实现）",
      "LP12-6b mixin 的 verified 真走 agent_loop.is_verified（单一真源）"]),
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