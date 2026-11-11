# -*- coding: utf-8 -*-
"""v4.294.0：治「PM 找的技能牛头不对马嘴」—— 行为级判据。

背景（2026-11-11 大哥实机）：批准组建报告里 PM 明写
「🔧 差技能（需你决定是否去 GitHub 找）：漫剧节拍模板、种田设定百科」，
用户回一句「找技能」，结果系统回「⏳ 正在去 GitHub 找「技能」…」，
拉回 lean-mode-skill / ecommerce-skills 等通用 skill 仓库，跟缺口毫无关系。

根因三层：
  ① `parse_skill_intent("找技能")` -> ("search", "技能")：正则 `(?:技能)?`
     回溯后把**类别词**当成查询词；`github_search_skill_repos` 再拼成
     `"技能 SKILL.md"` 搜仓库，于是满屏通用 skill 库。
  ② 真实缺口没地方存：`new_recipe()` / project 都不存 missing_skills，
     `self._team_plan` 批准后置 None；`_skill_cmd("list")` 只读
     `worker.last_meta["skill_gaps"]`，军团没跑时 worker=None，
     实测回「当前没检测到技能缺口」—— v4.293.0 回执里那句
     「说『缺技能』看缺口清单」其实是假指令（只验了接线，没验数据）。
  ③ 报告里「已自动去 GitHub 找候选」的文案也没真发请求，是句空话。

本版按大哥选的「按缺口名去找」档：泛词改用 PM 报的真实缺口名逐个搜；
搜不到如实说，不拿通用仓库凑数；**只搜不装、不给 PM 放权、缺口只留会话内不落盘**。
判据五组：A 泛词解析 / B 按缺口搜索 / C 报告渲染 / D UI 接线与数据可及 / E 不变量。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_sbx_skillq294_")
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

GAPS = [
    {"name": "漫剧节拍模板", "for_role": "写手", "why": "定节拍"},
    {"name": "种田设定百科", "for_role": "策划", "why": "世界观一致"},
]
NAMES = ["漫剧节拍模板", "种田设定百科"]


# ============================================================
# A 组：泛词解析 —— 「找技能」不能再变成 search("技能")
# ============================================================
for _t in ("找技能", "找一下技能", "去找技能", "搜技能", "查技能",
           "去 GitHub 找技能", "找技能库", "找 相关技能"):
    check("A1 %r 归到 find 且不再把类别词当查询词" % _t,
          legion.parse_skill_intent(_t) == ("find", ""),
          legion.parse_skill_intent(_t))

check("A2 「找技能」的 arg 绝不是「技能」（防拼成 技能 SKILL.md）",
      legion.parse_skill_intent("找技能")[1] != "技能",
      legion.parse_skill_intent("找技能"))
check("A3 不变量：具体关键词仍走 search 原样传",
      legion.parse_skill_intent("去 GitHub 找 漫剧分镜节拍表")
      == ("search", "漫剧分镜节拍表")
      and legion.parse_skill_intent("找 漫剧节拍模板")
      == ("search", "漫剧节拍模板"))
check("A4 不变量：缺技能 / 差技能 仍是 list（看清单，不联网）",
      legion.parse_skill_intent("缺技能") == ("list", "")
      and legion.parse_skill_intent("差技能") == ("list", ""))
check("A5 不变量：装第N个 / owner-repo 口径不变",
      legion.parse_skill_intent("装第 2 个") == ("install", "2")
      and legion.parse_skill_intent("去GitHub找 short-video-scripter")
      == ("search", "short-video-scripter"))
check("A6 不变量：长句与无关短句仍不误判",
      legion.parse_skill_intent("建角色，找技能") == (None, "")
      and legion.parse_skill_intent("帮我做一个种田漫剧的完整方案")
      == (None, ""))


# ============================================================
# B 组：按缺口名搜索 —— 一次请求都不许用「技能」这种泛词
# ============================================================
_has_gaps_fn = hasattr(legion, "github_search_skill_gaps")
check("B1 存在 github_search_skill_gaps（按缺口名批量搜的纯函数）",
      _has_gaps_fn)

_seen = []
_orig_search = legion.github_search_skill_repos


def _fake_search(q, limit=8, timeout=20):
    _seen.append(q)
    if q == "漫剧节拍模板":
        return ([{"full_name": "a/manga-beat", "stars": 12,
                  "description": "漫剧节拍模板"}], None)
    return ([], None)


if _has_gaps_fn:
    legion.github_search_skill_repos = _fake_search
    try:
        _res = legion.github_search_skill_gaps(NAMES)
    finally:
        legion.github_search_skill_repos = _orig_search
    check("B2 请求词 == PM 报的两个真实缺口名（不是「技能」）",
          _seen == NAMES, _seen)
    check("B3 结果按缺口名分组，空结果保留 err/repos 结构",
          isinstance(_res, dict)
          and set(_res.keys()) == set(NAMES)
          and len(_res["漫剧节拍模板"].get("repos") or []) == 1
          and not _res["种田设定百科"].get("repos"),
          _res)
    _seen.clear()
    legion.github_search_skill_repos = _fake_search
    try:
        legion.github_search_skill_gaps([])
        legion.github_search_skill_gaps(["漫剧节拍模板", "漫剧节拍模板"])
    finally:
        legion.github_search_skill_repos = _orig_search
    check("B4 空缺口不发请求；重复缺口名只搜一次",
          _seen == ["漫剧节拍模板"], _seen)
else:
    check("B2 请求词 == PM 报的两个真实缺口名（不是「技能」）", False,
          "github_search_skill_gaps 不存在")
    check("B3 结果按缺口名分组，空结果保留 err/repos 结构", False,
          "github_search_skill_gaps 不存在")
    check("B4 空缺口不发请求；重复缺口名只搜一次", False,
          "github_search_skill_gaps 不存在")


# ============================================================
# C 组：报告渲染 —— 全局编号对齐「装第N个」，搜不到如实说
# ============================================================
_has_report = hasattr(legion, "skill_gap_search_report")
check("C1 存在 skill_gap_search_report（Qt-free 纯函数）", _has_report)

if _has_report:
    _demo = {
        "漫剧节拍模板": {"repos": [
            {"full_name": "a/manga-beat", "stars": 12, "description": "节拍模板"},
            {"full_name": "b/beat-kit", "stars": 8, "description": "分镜节拍"}],
            "err": None},
        "种田设定百科": {"repos": [], "err": None},
    }
    _flat, _txt = legion.skill_gap_search_report(_demo)
    check("C2 扁平候选列表按报告里的全局编号排好（装第N个不会装错）",
          len(_flat) == 2
          and _flat[0]["full_name"] == "a/manga-beat"
          and "1) a/manga-beat" in _txt and "2) b/beat-kit" in _txt,
          (_flat, _txt[:200]))
    check("C3 搜不到的缺口如实说没搜到，不拿通用仓库凑数",
          "种田设定百科" in _txt
          and ("没搜到" in _txt or "没有" in _txt)
          and "lean-mode-skill" not in _txt, _txt[:240])
    check("C4 报告写明只搜不装、装要用户点头（不给 PM 放权）",
          "只搜不装" in _txt and ("点头" in _txt or "装第N个" in _txt),
          _txt[:240])
    check("C5 报告不出现把「技能」当查询词的标题",
          "「技能」候选" not in _txt and "「技能」" not in _txt,
          _txt[:160])
else:
    for _n in ("C2 扁平候选列表按报告里的全局编号排好（装第N个不会装错）",
               "C3 搜不到的缺口如实说没搜到，不拿通用仓库凑数",
               "C4 报告写明只搜不装、装要用户点头（不给 PM 放权）",
               "C5 报告不出现把「技能」当查询词的标题"):
        check(_n, False, "skill_gap_search_report 不存在")


# ============================================================
# D 组：UI 接线与数据可及（缺口不能随 _team_plan 一起丢）
# ============================================================
_ui = read("legion_ui.py")
_chat = read("legion_chat.py")
check("D1 批准组建时把 missing_skills 留在会话属性里",
      "self._last_skill_gaps" in _ui, "批准分支没保存缺口")
check("D2 GHWorker 有 find 模式（一次批量搜，不搞多 worker 状态机）",
      'self.mode == "find"' in _ui)
check("D3 _skill_cmd 有 find 分支，且优先读会话内缺口",
      'kind == "find"' in _ui and "getattr(self, \"_last_skill_gaps\"" in _ui)
check("D4 set_skill_helper 文档登记 find 这种 kind",
      "find" in _chat[_chat.find("def set_skill_helper"):
                       _chat.find("def set_skill_helper") + 400])
check("D5 无缺口时不联网、回执不给假希望",
      "当前没有" in _ui or "没有 PM 报的技能缺口" in _ui)

_idler = read("legion_ui.py")
check("D6 v4.293.0 军团未运行回执改成指向真的能用的「找技能」",
      "找技能：直接说「找技能」" in _idler,
      "回执还在指一句没数据/没入口的指令")
check("D7 回执仍保留 C3 判据要的两条字面量（去 GitHub 找 / 启动军团）",
      "去 GitHub 找" in _idler and "启动军团" in _idler)


# ---- 行为级：假 window 跑 _skill_cmd("find")，看它到底拿什么词去搜 ----
import legion_ui  # noqa: E402


class _FakeSignal:
    def connect(self, *a, **kw):
        pass


class _FakeGH:
    created = []

    def __init__(self, mode, parent=None, **kw):
        self.mode = mode
        self.kw = kw
        self.done = _FakeSignal()
        _FakeGH.created.append((mode, kw))

    def start(self):
        pass


class _FakeWin:
    def __init__(self, gaps):
        self.worker = None
        self.said = []
        self._last_skill_gaps = gaps
        self._last_skill_cands = []

    def _skill_say(self, t):
        self.said.append(str(t))

    def _on_skill_find(self, payload, err):
        # 真窗口上这是 QThread.done 的槽；假对象只需让 connect 拿到可调用对象，
        # 本组判据验的是「发了什么查询词」，不验异步回包。
        pass


_orig_gh = legion_ui.GHWorker
legion_ui.GHWorker = _FakeGH
try:
    _FakeGH.created = []
    _w = _FakeWin(GAPS)
    legion_ui.LegionWindow._skill_cmd(_w, "find", "")
    _made = list(_FakeGH.created)
    _said = list(_w.said)
finally:
    legion_ui.GHWorker = _orig_gh

check("D8 行为级：find 真的发一次批量搜索，queries 来自会话内真实缺口",
      len(_made) == 1 and _made[0][0] == "find"
      and _made[0][1].get("queries") == NAMES,
      _made)
check("D9 行为级：没有任何一次请求用「技能」当查询词",
      all("技能" not in str(kw) for _, kw in _made), _made)
check("D10 行为级：等待回执里点明是按缺口名找（不是「正在找技能」）",
      any("漫剧节拍模板" in s and "种田设定百科" in s for s in _said),
      _said)


# ============================================================
# E 组：不越权 / 不变量（既有判据不得翻）
# ============================================================
check("E1 PM 组队 prompt 仍禁止自己编 slug / 自己装技能",
      "不要编造 slug" in read("legion.py") and "必须请示" in read("legion.py"))
check("E2 find 分支不触发安装（_skill_install 只在 install 分支）",
      _ui.count("self._skill_install(") <= 2
      and 'kind == "install"' in _ui)
check("E3 泛词识别仍限短句（>60 字不判技能指令）",
      legion.parse_skill_intent("找技能" + "啊" * 60) == (None, ""))
check("E4 v4.293.0 的 D6/D7 接线字面量仍在",
      "set_skill_helper(self._skill_cmd)" in _ui
      and 'GHWorker("search", self, query=q)' in _ui)
check("E5 缺口不落盘：new_recipe 仍不含 missing_skills 字段",
      "missing_skills" not in (_src_new_recipe := read("legion.py")
                               .split("def new_recipe(")[1].split("def save_team_recipe(")[0]),
      "用户选的是「只留会话内」，别顺手改数据层")
check("E6 _skill_cmd 的既有 list/search/install 三个分支都还在",
      all('kind == "%s"' % k in _ui for k in ("list", "search", "install")))

# ============================================================
# F 组：v4.293.0 回执那句「缺技能看缺口清单」补成真的（同一根因）
# ============================================================
_orig_gh2 = legion_ui.GHWorker
legion_ui.GHWorker = _FakeGH
try:
    _FakeGH.created = []
    _w2 = _FakeWin(GAPS)       # worker=None，但批准组建留下的缺口在
    legion_ui.LegionWindow._skill_cmd(_w2, "list", "")
    _said2 = list(_w2.said)
    _made2 = list(_FakeGH.created)
finally:
    legion_ui.GHWorker = _orig_gh2

check("F1 军团未运行时「缺技能」列出真实缺口名，不再说「没检测到」",
      len(_said2) == 1 and "漫剧节拍模板" in _said2[0]
      and "种田设定百科" in _said2[0] and "没检测到" not in _said2[0],
      _said2)
check("F2 list 不联网、也不谎称「已自动去 GitHub 找候选」",
      not _made2
      and all("已自动去 GitHub 找候选" not in s for s in _said2),
      (_made2, _said2))
check("F3 不变量：worker 跑批真搜过候选时仍走原报告（不误伤引擎路径）",
      "skill_candidates_report_text(gaps, res)" in _ui)

print("")
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
