# -*- coding: utf-8 -*-
"""v4.293.0：治「PM 不做事」体感的三处断链 —— 行为级判据。

背景（2026-11-11 大哥实机）：对话里说「建角色，找技能」，PM 零回应。
实地核查发现 PM 其实没偷懒，是四个环节串起来把话吞了，本版只修三处可修的：

  ① 假成功：批准组建时 adopt_missing_roles() 把缺角加进内存，UI 回「✅ 新增 1 个角色」，
     但 _ui_save_legion() 走三方合并，_merge_val(new, _MISSING, _MISSING) 把新增元素
     当成「盘删了」丢掉 → 角色库里根本没有，重启即无。
  ② 自相矛盾：同一条批准回复里，「✅ 新增 1 个角色」后面还跟着
     「🆕 缺角（库里没有，需你决定是否补角色）」——用户自然再下一句指令，又掉进 ③。
  ③ 指令黑洞：parse_intent / parse_skill_intent / parse_team_build_intent /
     parse_manage_intent / _parse_launch_command 全不认「建角色，找技能」，
     落到「普通留言→PM」；军团没在跑时只写技术日志框，对话时间线零回显。

本版**不给 PM 放权**：建角色仍是批准时自动入库、装技能仍要用户点头。
判据四组：A 合并落盘 / B 批准回复 / C 对话回执 / D 不变量（越权与既有判据不得翻）。
"""
import os
import sys
import copy
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_sbx_pmnoop293_")
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

M = legion._MISSING


def _role(rid, name):
    return {"id": rid, "name": name, "emoji": "", "tools": [], "skills": []}


# ============================================================
# A 组：三方合并不得吞掉 UI 新增（① 假成功）
# ============================================================
_r_new = _role("brand-new", "漫剧成片师")

check("A1 _merge_val(新增, 快照无, 盘无) 保留而不是丢",
      legion._merge_val(_r_new, M, M) is not M,
      "返回 _MISSING = 新增被当删除吞掉")

_base = [_role("a", "策划"), _role("b", "写手")]
_new = _base + [_r_new]
_cur = copy.deepcopy(_base)
_merged = legion._merge_by_key(_new, _base, _cur)
check("A2 真实场景 base=21/new=22/cur=21 合并后仍是 22",
      len(_merged) == 3 and any(r.get("name") == "漫剧成片师" for r in _merged),
      "实际 %d 个: %s" % (len(_merged), [r.get("name") for r in _merged]))

# 完整落盘：写一次基准 → 追加角色 → 合并保存 → 读盘
_d0 = {"role_library": copy.deepcopy(_base), "projects": []}
legion.save_legion(_d0)
_d1 = copy.deepcopy(_d0)
_d1["role_library"].append(copy.deepcopy(_r_new))
_ok, _mm = legion.save_legion_merged(_d1, _d0)
try:
    _disk = json.loads(Path(legion.LEGION_PATH).read_text(encoding="utf-8"))
    _names = [r.get("name") for r in _disk.get("role_library") or []]
except Exception as e:
    _names = []
    print("  (读盘异常 %s)" % e)
check("A3 save_legion_merged 落盘后新增角色真的在盘上",
      _ok and "漫剧成片师" in _names,
      "盘上 %d 个角色，含成片师=%s" % (len(_names), "漫剧成片师" in _names))

# 新增字典键（顶层键 / 嵌套键）同样不得被吞
_deep = legion._merge_val_deep({"a": 1, "fresh": 2}, {"a": 1}, {"a": 1})
check("A4 新增字典键保留", _deep.get("fresh") == 2, _deep)

# 反向不变量：修复不得放过「盘删了 + UI 没动」——否则 worker 删除会被 UI 复活
check("A5 不变量：base 有 / 盘删了 / UI 没动 → 仍尊重删除",
      legion._merge_val({"id": "a", "name": "策划"}, {"id": "a", "name": "策划"}, M) is M)
check("A6 不变量：base 有 / 盘删了 / UI 改过 → UI 为准",
      legion._merge_val({"id": "a", "name": "策划改"}, {"id": "a", "name": "策划"}, M)
      == {"id": "a", "name": "策划改"})
check("A7 不变量：UI 显式删除仍返回 _MISSING",
      legion._merge_val(M, {"id": "a", "name": "策划"}, {"id": "a", "name": "策划"}) is M)

# ============================================================
# B 组：批准回复不得自相矛盾（②）
# ============================================================
def _plan():
    return {
        "name": "漫剧团", "emoji": "🎞️", "description": "做漫剧",
        "understanding": "做一部动态漫剧", "reason": "三岗覆盖全链路",
        "waves": [
            {"members": [{"name": "策划", "why": "定需求"}]},
            {"members": [{"name": "写手", "why": "写脚本"},
                         {"name": "主图策划", "why": "出分镜"}]},
        ],
        "missing_roles": [{
            "name": "漫剧成片师", "why": "缺图生视频成片位",
            "draft": {"emoji": "🎞️", "category": "视频创作",
                      "mission": "把分镜与主图合成漫剧成片"},
        }],
        "missing_skills": [{
            "name": "漫剧分镜节拍表", "for_role": "写手", "why": "节拍表",
        }],
    }


def _data():
    return {"role_library": [
        _role("a", "策划"), _role("b", "写手"), _role("c", "主图策划")],
        "projects": []}


_d = _data()
_proj = legion.new_project(name="新军团")
_p, _rep = legion.approve_team_plan(_d, _proj, _plan(), _d["role_library"],
                                    adopt_missing=True)
check("B1 批准回复报了「已新增角色」", "新增 1 个角色" in _rep, _rep[:120])
check("B2 批准回复不再同时说「需你决定是否补角色」",
      "需你决定是否补角色" not in _rep, _rep[:200])
check("B3 差技能属真实缺口，诚实保留", "差技能" in _rep and "GitHub" in _rep,
      _rep[:200])
check("B4 角色确实进了 data.role_library（不是只改文案）",
      any(r.get("name") == "漫剧成片师" for r in _d["role_library"]),
      [r.get("name") for r in _d["role_library"]])
check("B5 阵容与波次照常落地", "已应用组队方案" in _rep and len(_p.get("waves") or []) == 2,
      _rep[:120])

# 弹窗路径（不自动采纳）必须保持原文案 —— 那里用户要自己勾选草案
_p2, _rep2 = legion.apply_team_plan(legion.new_project(name="弹窗"), _plan(), _data()["role_library"])
check("B6 不变量：apply_team_plan 直接调用（弹窗路径）仍提示补角色",
      "需你决定是否补角色" in _rep2, _rep2[:160])
_d3 = _data()
_p3, _rep3 = legion.approve_team_plan(_d3, legion.new_project(name="不自采"),
                                      _plan(), _d3["role_library"],
                                      adopt_missing=False)
check("B7 不变量：adopt_missing=False 时仍提示补角色",
      "需你决定是否补角色" in _rep3, _rep3[:160])

# ============================================================
# C 组：军团未运行时，普通留言要在对话时间线给回执（③）
# ============================================================
class _FakePanel:
    def __init__(self):
        self.said = []

    def say(self, role, text):
        self.said.append((role, text))


class _FakeLog:
    def __init__(self):
        self.lines = []

    def append(self, s):
        self.lines.append(s)


class _RunningWorker:
    def __init__(self):
        self.sent = []

    def isRunning(self):
        return True

    def send_message(self, text, urgent=False):
        self.sent.append((text, urgent))
        return True


def _fake_win(running=False):
    class W:
        pass
    w = W()
    w.worker = _RunningWorker() if running else None
    w.log_view = _FakeLog()
    w.chat_panel = _FakePanel()
    return w


import legion_ui  # noqa: E402

_w_idle = _fake_win(running=False)
legion_ui.LegionWindow._chat_send_pm(_w_idle, "建角色，找技能")
check("C1 军团未运行时对话时间线收到系统回执（不再只写技术日志框）",
      len(_w_idle.chat_panel.said) == 1 and _w_idle.chat_panel.said[0][0] == "系统",
      _w_idle.chat_panel.said)
_c1_text = (_w_idle.chat_panel.said[0][1] if _w_idle.chat_panel.said else "")
check("C2 回执说明没送达、不谎称已发送",
      ("没在跑" in _c1_text or "未运行" in _c1_text or "没送达" in _c1_text)
      and "已发送" not in _c1_text, _c1_text[:160])
check("C3 回执给出真正可执行的指令（不是又一句空话）",
      "去 GitHub 找" in _c1_text and "启动军团" in _c1_text, _c1_text[:200])
check("C4 回执点明缺角已在批准时自动入库，不用再建",
      "自动入库" in _c1_text or "不用再建" in _c1_text, _c1_text[:200])

_w_run = _fake_win(running=True)
legion_ui.LegionWindow._chat_send_pm(_w_run, "给 PM 的普通留言")
check("C5 不变量：军团运行中仍照常投送 worker.send_message",
      len(_w_run.worker.sent) == 1 and _w_run.worker.sent[0][0] == "给 PM 的普通留言"
      and not _w_run.chat_panel.said,
      (_w_run.worker.sent, _w_run.chat_panel.said))

# ============================================================
# D 组：不越权 / 意图识别口径不变
# ============================================================
check("D1 「建角色，找技能」仍不被误判为授权/组队/技能指令",
      legion.parse_skill_intent("建角色，找技能") == (None, "")
      and legion.parse_team_build_intent("建角色，找技能") is None
      and legion.parse_manage_intent("建角色，找技能") is None)
check("D2 原有可执行指令口径不变",
      legion.parse_skill_intent("缺技能") == ("list", "")
      and legion.parse_skill_intent("去 GitHub 找 漫剧分镜节拍表")
      == ("search", "漫剧分镜节拍表"))
check("D3 PM 组队 prompt 仍禁止自己编 slug / 自己装技能",
      "不要编造 slug" in read("legion.py") and "必须请示" in read("legion.py"))
check("D4 未改 _pending_team_plan 审批路由（待审批态仍只认 批准/打回/放弃）",
      "if intent in (\"pass\", \"reject\", \"abort\"):" in read("legion_chat.py"))

# 回执里指给用户的两条指令必须有真实执行入口，不能又是一句空话
import legion_chat  # noqa: E402

check("D5 「启动军团 <任务>」确实命中开团解析",
      legion_chat.LegionChatPanel._parse_launch_command("启动军团 做小红书带货")
      == "做小红书带货")
check("D6 技能指令与开团入口都已接线（不是只写了句提示文案）",
      "set_skill_helper(self._skill_cmd)" in read("legion_ui.py")
      and "set_launcher(self._chat_launch)" in read("legion_ui.py"))
check("D7 「去 GitHub 找 X」有联网执行分支（GHWorker search）",
      "GHWorker(\"search\", self, query=q)" in read("legion_ui.py"))

print("")
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
