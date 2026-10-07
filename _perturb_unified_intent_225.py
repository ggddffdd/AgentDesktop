# -*- coding: utf-8 -*-
"""扰动验证 P3 统一意图：抽掉注册表 / 绕过统一对象 / 改一票否决，看判据是否翻红。

手法：备份原字节 → 整段删（注册表/判据整块失效）+ 只删动作留条件
（接线还在但被短路）→ 跑判据 → 期望对应条目翻红 → 恢复原字节。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TESTS = [
    os.path.join(ROOT, "tests", "test_unified_intent_225.py"),
    os.path.join(ROOT, "tests", "test_task_state_machine_225.py"),
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
        except Exception as e:
            reds.append("崩溃:%s" % e)
    return reds


sys.path.insert(0, ROOT)
import _perturb_guard as _guard  # noqa: E402
_guard.arm()

# (名称, 文件, 原串, 新串, 期望翻红的判据片段)
CASES = [
    # ── 整段删 ──
    ("整段删：系统提示不再由注册表生成（退回手写维护的老路）",
     "ui.py",
     "            base = \"\\n\\n\" + _intent_mod._route_hint_text() + \"\\n\"",
     "            base = \"\"",
     ["IN4-9 ui.py 调 _route_hint_text",
      "IN4-10b 注册表生成逻辑在非注释代码里"]),

    ("整段删：step1 不再读统一 Intent（绕回分散路由）",
     "agent.py",
     "                _ft = self._intent.force_tool",
     "                _ft = agent_text._route_force_tool(_cur_user, _prev_user)",
     ["IN6-4 step1 强制路由读 Intent.force_tool",
      "IN6-5 不再在主循环直接调 _route_force_tool"]),

    ("整段删：硬要求提取换成关键词命中（逼模型干不存在的活）",
     "agent_task_mixin.py",
     "                _t, [n for n, _w in task_state.required_from_text(_t)])",
     "                _t, list(getattr(self._intent, 'requested_tools', ()) or ()))",
     ["TS7-3 账本用 required_from_text 取硬要求"]),

    # ── 只删动作留条件 ──
    ("只删动作留条件：注入后不置 nudged 标记（闸门失效→无限补做）",
     "agent_task_mixin.py",
     "            self._tstate_nudged = True",
     "            pass",
     ["TS7-8 注入后置 nudged 标记"]),

    # 注意：变异把调用包进 `if False:`，源码串 `_ts.record_tool(` **仍在**
    # → 源码层断言 TS7-15 必然照绿；真正能抓它的是**行为级**断言
    # TS7-22/23（真跑一遍 mixin，看工具到底有没有进账本）。这也验证了
    # 「为什么必须有第四层行为判据」：前两层钉字面，抓不住短路/掏空。
    ("只删动作留条件：记账不登记工具（账本永远空）",
     "agent_task_mixin.py",
     "            _ts.record_tool(name, _args, result_str, ok=_ok,\n"
     "                            step=_ts.current_step)",
     "            if False:  # 不记账\n"
     "                _ts.record_tool(name, _args, result_str, ok=_ok,\n"
     "                                step=_ts.current_step)",
     ["TS7-22 行为级：_tstate_record 真把工具记进账本",
      "TS7-23 行为级：失败判据被真正采用（记进 failed_tools）"]),

    # 注意：这条只期望 IN3-1（kind 分类层）翻红，**不**期望 IN2-1 ——
    # `_route_force_tool` 自己内部也有一道否定否决，所以绕过 classify 这一层
    # 并不会让 force_tool 变成 video_gen（force 仍为 None）。
    # 这恰好证明两层否决是独立的：判据不该把「kind 退化」误判成「行为退化」。
    ("只删动作留条件：否定一票否决放行（kind 退化：别生成→不再标 negated）",
     "intent.py",
     "        if agent_text._neg_hit(text):\n"
     "            return Intent(kind=KIND_NEGATED, confidence=0.60, explicit=False,",
     "        if False and agent_text._neg_hit(text):\n"
     "            return Intent(kind=KIND_NEGATED, confidence=0.60, explicit=False,",
     ["IN3-1 否定 → kind=negated"]),

    ("只删动作留条件：词边界退化（子串误伤）",
     "task_state.py",
     '        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(nm.lower())\n'
     '                         + r"(?![A-Za-z0-9_])")',
     '        pat = re.compile(re.escape(nm.lower()))',
     ["TS2-1 my_image_genner 不命中 image_gen",
      "TS2-2 image_genner 不命中 image_gen",
      "TS2-3 xweb_search 不命中 web_search"]),

    ("只删动作留条件：产物存在性判定放行（声称成功即算成功）",
     "task_state.py",
     "                self.artifacts.append((p, self._exists(p), name))",
     "                self.artifacts.append((p, True, name))",
     ["TS3-8 产物不存在 → exists=False",
      "TS3-10 is_complete=False（声称成功但文件不存在）"]),

    # 这条变异只跳过 `should_nudge` 的返回判断，**注入副作用照做** →
    # 首次调用仍返回 True（TS7-24/25 照常绿），红的是「重复注入 / 步数耗尽
    # 仍注入」这类**后果**断言。源码层字符串仍在（`_ts.should_nudge(` 还在），
    # 只有行为级判据能抓到 —— 这正是第四层判据存在的理由。
    ("只删动作留条件：收尾闸门跳过判断（可重复注入/耗尽仍注入）",
     "agent_task_mixin.py",
     "            if not _ts.should_nudge(step, max_steps,",
     "            if False and not _ts.should_nudge(step, max_steps,",
     ["TS7-29 行为级：同轮第二次被闸拦住（不重复注入）",
      "TS7-31 行为级：步数耗尽时不注入",
      "TS7-33 行为级：续跑补完后再调被拦住"]),

    # ⚠️ 原串必须**逐字等于**当前类声明（扰动框架是纯字符串替换，不支持正则）。
    # v4.226 给 AgentWorker 加了第二个 mixin（AgentLoopMixin）→ 原串同步更新。
    # 框架若日后支持正则，这里应改成 r"class AgentWorker\([^)]*AgentTaskMixin[^)]*\):"
    # ——否则再加 mixin 时这条变异会静默 SKIP（哑弹），是本脚本唯一的失效方式。
    ("整段删：AgentWorker 不再 mixin（接线全成死引用）",
     "agent.py",
     "class AgentWorker(AgentTaskMixin, AgentLoopMixin, QThread):",
     "class AgentWorker(QThread):",
     ["TS7-1c AgentWorker 真的 mixin 了 AgentTaskMixin"]),
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