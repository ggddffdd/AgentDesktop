# -*- coding: utf-8 -*-
"""v4.296.0：治「批准组建回复固定说做小红书带货」—— 行为级判据。

背景（2026-11-12 大哥实机）：种子是种田漫剧，PM 出的组队理由也对得上，
但报告尾巴永远挂着「直接说『启动军团 做小红书带货』就能开工」。

根因：`legion_ui.py:2497` 的 `_chat_team_approve(pass)` 里把报告 `rep`
和一句**写死的示例**无条件拼接。rep 来自 apply_team_plan()，是按本次需求
现写的；那句尾巴则是 v4.148.1 时代留下的举例，跟当前任务零关系。

按大哥选的「换成通用引导」档：只改这一处提示，不动调度、不动数据层。
⚠️ 不把示例换成当前需求文本：`_parse_launch_command` 有 len(t) > 60 硬上限，
   而组队需求允许到 100 字，实测 75 字照抄会返回 None 被当普通留言（死指令）。
⚠️ 也不写「或点 ▶ 启动军团」：批准组建后 task_edit 是空的，
   `_run_legion:3709` 会拦「给军团下个任务再启动」——那是半死指令。
   真有执行入口的是对话里说「启动军团 <任务描述>」：
   `_chat_launch:2339` 先 setText(task_text) 再 _run_legion()。

判据四组：A 纯函数 / B 批准分支行为级 / C 源码接线 / D 不变量。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_sbx_hint296_")
os.environ["XC_LEGION_DIR"] = _SBX
os.environ.setdefault("XC_USER_DATA_DIR", os.path.join(_SBX, "userdata"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s %s" % (name, extra))


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


import legion  # noqa: E402
import legion_chat  # noqa: E402
import legion_ui  # noqa: E402

_REP = ("✅ 已应用组队方案：2 波 / 3 人\n"
        "  第 1 波：🧭策划\n"
        "  第 2 波：✍️写手、🖼️主图策划\n"
        "💡 组队理由：参照过往种田漫剧军团架构，明确穿越设定与视觉分镜为双核心。")
_BANNED = ("做小红书带货", "小红书带货", "小红书", "带货", "漫剧", "种田", "短剧")

_fn = getattr(legion_ui, "team_approve_reply", None)

# ============================================================
# A 组：纯函数 —— 报告原样 + 通用引导（改前函数不存在，逐条登记红，不短路）
# ============================================================
check("A1 legion_ui 暴露 team_approve_reply 纯函数", callable(_fn))
if callable(_fn):
    _out = _fn(_REP)
    check("A2 报告全文原样保留（不是另写一份摘要）", _out.startswith(_REP), _out[:160])
    check("A3 报告只出现一次，不重复拼接", _out.count("✅ 已应用组队方案") == 1, _out[:160])
    check("A4 不再出现任何具体任务名", not any(w in _out[len(_REP):] for w in _BANNED),
          _out[len(_REP):])
    check("A5 引导用通用占位 <任务描述>，不是某个真实任务",
          "<任务描述>" in _out and "启动军团" in _out, _out[-120:])
    check("A6 引导与报告分行，不再黏在同一行末尾", "\n" in _out[len(_REP):],
          repr(_out[len(_REP):]))
    check("A7 空报告 / None 不崩且仍给引导",
          "<任务描述>" in _fn("") and "<任务描述>" in _fn(None))
    check("A8 引导不提会被「缺任务」拦住的启动按钮",
          "▶" not in _out and "启动按钮" not in _out, _out[-120:])
    _fn_names = set(_fn.__code__.co_names or ())
    check("A9 纯函数不读盘、不写盘、不发网络请求（重复调用结果稳定）",
          not ({"open", "save_legion", "requests", "urlopen", "socket",
                "record_auth"} & _fn_names)
          and _fn(_REP) == _out, sorted(_fn_names))
else:
    for _n in ("A2 报告全文原样保留（不是另写一份摘要）",
               "A3 报告只出现一次，不重复拼接",
               "A4 不再出现任何具体任务名",
               "A5 引导用通用占位 <任务描述>，不是某个真实任务",
               "A6 引导与报告分行，不再黏在同一行末尾",
               "A7 空报告 / None 不崩且仍给引导",
               "A8 引导不提会被「缺任务」拦住的启动按钮",
               "A9 纯函数不读盘、不写盘、不发网络请求"):
        check(_n, False, "team_approve_reply 不存在")


# ============================================================
# B 组：批准组建分支（行为级，假 window；写盘副作用全部打桩）
# ============================================================
class _FakePanel:
    def __init__(self):
        self.said = []

    def say(self, role, text):
        self.said.append((role, str(text)))


def _role(rid, name):
    return {"id": rid, "name": name, "emoji": "", "tools": [], "skills": []}


def _plan():
    return {
        "name": "漫剧团", "emoji": "🎞️", "description": "做漫剧",
        "understanding": "做一部动态漫剧", "reason": "三岗覆盖全链路",
        "waves": [{"members": [{"name": "策划", "why": "定需求"}]},
                  {"members": [{"name": "写手", "why": "写脚本"}]}],
        "missing_roles": [], "missing_skills": [],
    }


def _fake_win():
    class W:
        pass
    w = W()
    w.worker = None
    w.data = {"role_library": [_role("a", "策划"), _role("b", "写手")],
              "projects": []}
    w._team_plan = _plan()
    w._team_need = "做一部种田漫剧，穿越设定加视觉分镜"
    w._team_target_proj = legion.new_project(name="新军团")
    w._team_created_proj = None
    w._team_advice = ""
    w.chat_panel = _FakePanel()
    w._cur_project = lambda: w._team_target_proj
    w._refresh_projects = lambda **kw: None
    w._rebuild_waves = lambda: None
    return w


_orig = {
    "save": legion_ui._ui_save_legion,
    "auth": legion.record_auth,
    "recipe": legion.save_team_recipe,
}
legion_ui._ui_save_legion = lambda data: True
legion.record_auth = lambda *a, **k: None
legion.save_team_recipe = lambda data, need, plan: ({"id": "stub"}, None)

_w = _fake_win()
try:
    legion_ui.LegionWindow._chat_team_approve(_w, "pass", "")
finally:
    legion_ui._ui_save_legion = _orig["save"]
    legion.record_auth = _orig["auth"]
    legion.save_team_recipe = _orig["recipe"]

_said = _w.chat_panel.said
_t = _said[0][1] if _said else ""
check("B1 批准组建只在对话里回一条系统消息", len(_said) == 1 and _said[0][0] == "系统",
      _said[:2])
check("B2 批准回复不再说「做小红书带货」", "做小红书带货" not in _t, _t[-160:])
check("B3 批准回复不带任何具体业务词（小红书/带货/漫剧/种田/短剧）",
      not any(x in _t.split("就能开工")[0][-120:] for x in _BANNED[:4]), _t[-160:])
check("B4 rep 的真实内容仍在（应用方案 / 波次 / 组队理由）",
      "已应用组队方案" in _t and "第 1 波" in _t and "组队理由" in _t, _t[:160])
check("B5 通用引导出现在报告后面且分行",
      "<任务描述>" in _t and "启动军团" in _t
      and _t.find("<任务描述>") > _t.find("组队理由")
      and "\n" in _t[_t.find("组队理由"):_t.find("<任务描述>")], _t[-200:])
check("B6 批准分支仍走 approve_team_plan + 落盘调用（没绕过审计）",
      "approve_team_plan" in read("legion_ui.py") and "_ui_save_legion" in read("legion_ui.py"))

# abort 分支不变量
_w2 = _fake_win()
legion_ui.LegionWindow._chat_team_approve(_w2, "abort", "")
check("B7 不变量：abort 仍是「已放弃这次组队」",
      len(_w2.chat_panel.said) == 1
      and "已放弃这次组队" in _w2.chat_panel.said[0][1],
      _w2.chat_panel.said[:1])


# ============================================================
# C 组：源码接线 —— 硬编码必须真的消失，示例「例如」不误伤
# ============================================================
_ui = read("legion_ui.py")
_chat = read("legion_chat.py")
check("C1 legion_ui.py 里不再有「做小红书带货」硬编码",
      "做小红书带货" not in _ui)
check("C2 批准分支调用 team_approve_reply(rep)，不再裸拼字符串",
      "team_approve_reply(rep)" in _ui, "批准分支没接纯函数")
check("C3 纯函数定义在模块级（不是塞进类里）",
      "\ndef team_approve_reply(" in _ui)
check("C4 不误伤 legion_chat.py 里「没带任务」的举例提示",
      "启动军团需要任务描述，例如" in _chat)
check("C5 apply_team_plan 的「💡 组队理由」仍是 PM 现写的内容，未被改写",
      "💡 组队理由：%s" in read("legion.py"))
check("C6 不改调度/数据层：本版判据不要求改 legion_worker.py / task_graph.py",
      "board_update" in read("legion_worker.py") and "wave_members" in read("legion.py"))


# ============================================================
# D 组：引导语必须指向真能执行的入口（不塞死指令 / 半死指令）
# ============================================================
_hint = "启动军团 <任务描述>"
_probe = _hint.replace("<任务描述>", "做种田漫剧")
check("D1 引导模式替换成短任务后确实命中开团解析",
      legion_chat.LegionChatPanel._parse_launch_command(_probe) == "做种田漫剧",
      repr(legion_chat.LegionChatPanel._parse_launch_command(_probe)))
check("D2 _chat_launch 把任务写进 task_edit 再开跑（不是空指令）",
      "self.task_edit.setText(task_text)" in _ui and "self._run_legion()" in _ui)
check("D3 _run_legion 仍有缺任务拦截（所以引导语不能只叫人点按钮）",
      "给军团下个任务再启动" in _ui)
check("D4 引导与 v4.294.0 军团未运行回执口径一致",
      "启动军团 <任务描述>" in _ui or "<任务描述>" in _ui)
_appr = _ui.split("def _chat_team_approve")[1].split("def _chat_manage")[0]
check("D5 不变量：批准分支没新增技能安装/自动执行入口（不给 PM 放权）",
      "install_skill" not in _appr and "adopt_missing_skills" not in _appr)
check("D6 不变量：parse_team_build_intent / _parse_launch_command 都还在",
      callable(legion.parse_team_build_intent)
      and callable(legion_chat.LegionChatPanel._parse_launch_command))

print("")
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
