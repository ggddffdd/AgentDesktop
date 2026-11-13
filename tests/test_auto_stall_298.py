# -*- coding: utf-8 -*-
"""v4.298.0：治「自动化任务转半天不出结果 / 一直卡着」+ 一条 400 参数错。

背景（2026-11-12 下午大哥实机，14:01 重启到 14:26 才算完，约 24 分钟）：
两个定时任务最后都写出了产物、last_result=ok，但前台过程气泡刷个不停、
"一直卡着"。日志实锤三件事：

① **核验空转**（"一直卡着"的主体）：`四阶段循环：核验 阶段=verify 计划0项
   核验未过` 连续 20 步 → 触发「单轮步数耗尽，自动续跑」又 20 步 → 再 20 步
   （14:13:42 一路到 14:26:08）。账本结论一字未变，补做毫无进展，却没有任何
   止损，只能靠 20 步上限 + token 熔断兜底。
② **v4.66 连续工具失败护栏没生效**：`_FAIL_MARKERS` 只收「文件不存在 /
   命令执行失败」这类本地错误。今天真实的工具回执是
   `⚠ 本次调用失败：[WORKFLOW_INCOMPLETE]`、`[UNVERIFIED_OUTCOME]`、
   `未找到搜索结果（最后错误：<urlopen error [SSL: UNEXPECTED_EOF...]>）`、
   Brave `HTTP Error 422`，一个都不认识 → `_fail_steps` 永远是 0。
③ **一条真 400**：14:09:56 `tool_choice is only allowed when tools are
   specified`。`_run_workflow` 的 analyst 节点 `tools=set()` → my_tools=[]，
   但 `_agent_call` 仍设 `body["tool_choice"]="required"`，通道按参数错误拒绝。

按大哥选的 A 档，全部是「护栏加固 + 说真话」，不新建模块、不动信号签名、
不删落盘数据、不改搜索后端（Brave+Serper 照旧）：
① agent_text.tool_result_backend_down 纯函数识别「资料源/后端不可用」；
   agent.py:_step_tools_all_failed 接进去，让已有的 v4.66 护栏真正生效。
② 主循环加「核验结论原地不动连续 N 步」早停，登记可续真因。
③ 早停不再只说「检查路径/命令」，automation.STOP_REASON_TEXT 说真因。
④ ui_focus.guard_tool_choice 发送前护栏：工具表为空就不带 tool_choice。

判据五组：A 后端不可用纯函数 / B 连续失败护栏接线 / C 核验停滞早停 /
          D tool_choice 护栏 / E 不变量。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_sbx_stall298_")
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


def fn_seg(src, name):
    """取 `    def name(` 到下一个同级 def 之间的源码（不 import Qt 也能查接线）。"""
    key = "    def %s(" % name
    i = src.find(key)
    if i < 0:
        return ""
    j = src.find("\n    def ", i + len(key))
    return src[i:j if j >= 0 else len(src)]


import agent_text  # noqa: E402
import automation  # noqa: E402
import ui_focus  # noqa: E402

AG = read("agent.py")
AT = read("agent_text.py")
UI = read("ui.py")
FOCUS = read("ui_focus.py")
AUTO = read("automation.py")

# 真实回执样本（来自 sessions.json / tools.py，不是杜撰）
FAIL_SEARCH = ("未找到搜索结果（最后错误：<urlopen error "
               "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in "
               "violation of protocol (_ssl.c:1010)>）")
FAIL_WF = ("[EV#1445] 证据登记（本次调用失败）\n"
           "  工具 run_workflow · 参数 {\"type\": \"multi_search\"}\n"
           "  ⚠ 本次调用失败：[WORKFLOW_INCOMPLETE] —— 禁止据此补全任何事实，"
           "只能如实报告失败\n⚠ 任务图未完整达成：2 个节点未成功")
FAIL_UNVERIFIED = ("[EV#1443] 证据登记（本次调用失败）\n"
                   "  ⚠ 本次调用失败：[UNVERIFIED_OUTCOME] —— 禁止据此补全任何事实")
FAIL_HTTP = "Brave 搜索失败（回落免费引擎）: HTTP Error 422: Unprocessable Entity"
FAIL_OFFSHORE = ("⚠️ 境外主题搜索无法获取：当前未开 VPN 且未配置 Serper/Brave key，"
                 "DuckDuckGo 被墙、国内引擎已禁用")
OK_SEARCH = "搜索「AI 新闻」结果（来源：brave）：\n1. 每日 AI 简报\n   https://a.com"
OK_FILE = "已写入 1761 字符到 <路径已脱敏>"
OLD_FILE_ERR = "文件不存在：C:\\temp\\nope.txt"


# ============================================================
# A 组：后端/资料源不可用判定（改前整个函数不存在 → 逐条登记红）
# ============================================================
_bd = getattr(agent_text, "tool_result_backend_down", None)
check("A1 agent_text 暴露 tool_result_backend_down 纯函数", callable(_bd))
_A_REST = (
    "A2 搜索全链失败带异常 → 命中",
    "A3 工作流未完整达成回执 → 命中",
    "A4 UNVERIFIED_OUTCOME 失败回执 → 命中",
    "A5 HTTP 4xx 后端错 → 命中",
    "A6 境外模式明确报不可用 → 命中",
    "A7 正常搜索结果不误杀",
    "A8 裸「未找到搜索结果」不误杀（可能只是这词真没有）",
    "A9 普通文本里出现「失败」二字不误杀",
    "A10 成功写文件回执不误杀",
    "A11 旧式本地文件错误不归它管（仍由 _FAIL_MARKERS 负责）",
    "A12 空 / None / 数字 / 坏档一律 False",
    "A13 纯函数不读盘不写盘不联网",
)
if callable(_bd):
    check(_A_REST[0], _bd(FAIL_SEARCH) is True, repr(FAIL_SEARCH[:40]))
    check(_A_REST[1], _bd(FAIL_WF) is True)
    check(_A_REST[2], _bd(FAIL_UNVERIFIED) is True)
    check(_A_REST[3], _bd(FAIL_HTTP) is True)
    check(_A_REST[4], _bd(FAIL_OFFSHORE) is True)
    check(_A_REST[5], _bd(OK_SEARCH) is False)
    check(_A_REST[6], _bd("未找到搜索结果") is False)
    check(_A_REST[7], _bd("这次尝试失败了，我们换个思路再来") is False)
    check(_A_REST[8], _bd(OK_FILE) is False)
    check(_A_REST[9], _bd(OLD_FILE_ERR) is False)
    check(_A_REST[10], all(_bd(x) is False for x in ("", None, 123, [], {}, b"xx")))
    _names = set(_bd.__code__.co_names or ())
    check(_A_REST[11], not ({"open", "urlopen", "save", "remove", "read_text"} & _names),
          sorted(_names))
else:
    for _n in _A_REST:
        check(_n, False, "tool_result_backend_down 不存在")


# ============================================================
# B 组：v4.66 连续失败护栏接入后端不可用判定
# ============================================================
_seg_fail = fn_seg(AG, "_step_tools_all_failed")
check("B1 _step_tools_all_failed 仍存在（改判据不删判据）", bool(_seg_fail))
check("B2 函数体接了 tool_result_backend_down",
      "tool_result_backend_down" in _seg_fail, repr(_seg_fail[:120]))
check("B3 旧 _FAIL_MARKERS 一个没删",
      all(k in AG for k in ("文件不存在", "命令执行失败", "不是内部或外部命令")))
check("B4 接进去而非替换（旧标志仍在判定里）",
      "_FAIL_MARKERS" in _seg_fail)

# 行为级：用假 self 调未绑定方法，不 import Qt
class _Fake:
    pass


def _fake_self(messages):
    f = _Fake()
    f.messages = messages
    f._FAIL_MARKERS = (
        "文件不存在", "命令执行失败", "读取失败", "写入失败", "拒绝：",
        "命令执行超时", "Out-File", "FileOpenFailure", "NotSupportedException",
        "不是内部或外部命令", "未能找到路径", "找不到路径", "无法找到路径",
    )
    return f


def _msgs(*contents):
    out = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}]
    for c in contents:
        out.append({"role": "tool", "content": c})
    return out


# agent.py 顶层 import 会拉 Qt，这里用 ast 抽函数体做等价执行
import ast  # noqa: E402
import types  # noqa: E402

_tree = ast.parse(AG)
_fn_node = None
for _n in ast.walk(_tree):
    if isinstance(_n, ast.FunctionDef) and _n.name == "_step_tools_all_failed":
        _fn_node = _n
        break
check("B5 能从 AST 取到 _step_tools_all_failed", _fn_node is not None)

_behavior_ready = False
if _fn_node is not None:
    try:
        _mod = ast.Module(body=[_fn_node], type_ignores=[])
        ast.fix_missing_locations(_mod)
        _ns = {"agent_text": agent_text}
        exec(compile(_mod, "<agent_fail>", "exec"), _ns)
        _probe = _ns["_step_tools_all_failed"]
        _behavior_ready = True
    except Exception as _e:
        print("  [warn] AST 执行失败: %s" % _e)

_B_REST = (
    "B6 本步全是后端不可用 → 判失败（护栏能命中今天这种卡法）",
    "B7 混一条正常结果 → 不判全失败（不误杀）",
    "B8 旧式本地错误仍判全失败（旧行为不回退）",
    "B9 无 tool 消息 → False",
    "B10 无带 tool_calls 的 assistant → False",
    "B11 判定会记下「是不是后端挂了」供文案分流",
)
if _behavior_ready:
    _r = _probe(_fake_self(_msgs(FAIL_SEARCH, FAIL_WF)))
    check(_B_REST[0], _r is True, repr(_r))
    _r = _probe(_fake_self(_msgs(FAIL_SEARCH, OK_SEARCH)))
    check(_B_REST[1], _r is False, repr(_r))
    _r = _probe(_fake_self(_msgs(OLD_FILE_ERR, OLD_FILE_ERR)))
    check(_B_REST[2], _r is True, repr(_r))
    check(_B_REST[3], _probe(_fake_self([{"role": "assistant", "content": "x", "tool_calls": [{"id": "c"}]}])) is False)
    check(_B_REST[4], _probe(_fake_self([{"role": "tool", "content": FAIL_SEARCH}])) is False)
    _f = _fake_self(_msgs(FAIL_SEARCH))
    _probe(_f)
    check(_B_REST[5], getattr(_f, "_last_fail_backend_down", False) is True,
          repr(getattr(_f, "_last_fail_backend_down", None)))
else:
    for _n in _B_REST:
        check(_n, False, "AST 等价执行未就绪")


# ============================================================
# C 组：核验停滞早停 + 失败说真因
# ============================================================
check("C1 主循环有核验停滞阈值常量", "MAX_VERIFY_STALL_STEPS = 8" in AG)
check("C2 阈值不放宽到 20 步以上",
      "MAX_VERIFY_STALL_STEPS = 8" in AG and "MAX_VERIFY_STALL_STEPS = 999" not in AG)
check("C3 按「未满足项签名原地不动」计数",
      "_verify_stall_steps" in AG and "_last_verify_sig" in AG
      and 'startswith("未满足")' in AG)
check("C4 早停登记可续真因 verify_stalled", '_mark_resumable("verify_stalled")' in AG)
check("C5 早停不再只说「检查路径/命令」",
      "核验一直没有进展" in AG or "一直卡在核验" in AG)
check("C6 工具失败早停按是否后端挂分流登记真因",
      '_mark_resumable("tool_backend_down"' in AG
      and '_mark_resumable("tool_fail"' in AG)
check("C7 两处早停都先落盘再退出（断点不丢）",
      AG.count("_sync_to_session(mw)") >= 7,
      "当前 %d 处" % AG.count("_sync_to_session(mw)"))
check("C8 STOP_REASON_TEXT 三条新人话都在",
      all(k in AUTO for k in ("verify_stalled", "tool_backend_down", "tool_fail")))
check("C9 failure_reason 对 verify_stalled 说真话",
      automation.failure_reason("", stopped=True, stop_reason="verify_stalled")
      != "中途停了，这次不算完成"
      and "核验" in automation.failure_reason("", stopped=True, stop_reason="verify_stalled")
      if "verify_stalled" in AUTO else False,
      repr(automation.failure_reason("", stopped=True, stop_reason="verify_stalled")))
check("C10 failure_reason 对 tool_backend_down 说真话",
      automation.failure_reason("", stopped=True, stop_reason="tool_backend_down")
      != "中途停了，这次不算完成" if "tool_backend_down" in AUTO else False)
check("C11 未知 reason 仍 fail-closed 回退旧文案",
      automation.failure_reason("", stopped=True, stop_reason="__nope__")
      == "中途停了，这次不算完成")
check("C12 未停止的旧判定一字不改",
      automation.failure_reason("") == "没有给出结果"
      and automation.failure_reason("随便写了点东西") == "没有产出可用结果")
check("C13 旧 stop_reason 映射一条没删",
      all(k in AUTO for k in ("token_budget", "timeout", "total_budget",
                              "tool_timeout", "model_error", "max_steps",
                              "resume_exhausted")))
check("C14 agent.py 仍守住 <2400 行红线", len(AG.splitlines()) < 2400,
      "当前 %d 行" % len(AG.splitlines()))


# ============================================================
# D 组：tool_choice 发送前护栏（空工具表绝不携带 tool_choice）
# ============================================================
_g = getattr(ui_focus, "guard_tool_choice", None)
check("D1 ui_focus 暴露 guard_tool_choice 纯函数", callable(_g))
_D_REST = (
    "D2 空 tools + required → 摘掉",
    "D3 空 tools + 指定函数 → 摘掉",
    "D4 有 tools → 原样保留 required",
    "D5 None 工具表 → 摘掉",
    "D6 非 dict 坏档安全返回",
    "D7 纯函数不读盘不写盘不联网",
)
if callable(_g):
    _b = {"tools": [], "tool_choice": "required"}
    _g(_b)
    check(_D_REST[0], "tool_choice" not in _b, repr(_b))
    _b = {"tools": [], "tool_choice": {"type": "function", "function": {"name": "sys_info"}}}
    _g(_b)
    check(_D_REST[1], "tool_choice" not in _b, repr(_b))
    _b = {"tools": [{"type": "function"}], "tool_choice": "required"}
    _g(_b)
    check(_D_REST[2], _b.get("tool_choice") == "required", repr(_b))
    _b = {"tools": None, "tool_choice": "none(guard)"}
    _g(_b)
    check(_D_REST[3], "tool_choice" not in _b, repr(_b))
    check(_D_REST[4], _g(None) is None and _g("bad") == "bad")
    _names = set(_g.__code__.co_names or ())
    check(_D_REST[5], not ({"open", "urlopen", "save", "read_text"} & _names),
          sorted(_names))
else:
    for _n in _D_REST:
        check(_n, False, "guard_tool_choice 不存在")

_ac = fn_seg(UI, "_agent_call")
check("D8 _agent_call 确实调用了护栏", "ui_focus.guard_tool_choice(body)" in _ac)
check("D9 护栏在 payload 序列化之前",
      _ac.find("guard_tool_choice(body)") >= 0
      and _ac.find("guard_tool_choice(body)") < _ac.find("payload = json.dumps(body"))
check("D10 护栏在所有 tool_choice 赋值之后（兜底覆盖四个分支）",
      _ac.rfind('body["tool_choice"]') < _ac.find("guard_tool_choice(body)"))
_gseg = ""
if "def guard_tool_choice(" in FOCUS:
    _gi = FOCUS.index("def guard_tool_choice(")
    _gj = FOCUS.find("\ndef ", _gi + 5)
    _gseg = FOCUS[_gi:_gj if _gj >= 0 else len(FOCUS)]
check("D11 非空谓词：护栏里确实有 pop（改成 no-op 必红）",
      callable(_g) and 'pop("tool_choice"' in _gseg, repr(_gseg[:120]))


# ============================================================
# E 组：不变量 / 版本传导
# ============================================================
check("E1 搜索后端一个字没动（Brave + Serper 照旧）",
      "def tool_web_search(" in read("tools.py")
      and "def search_brave(" in read("search.py")
      and "def search_serper(" in read("search.py"))
check("E2 tool_choice 四个分支仍在（只加兜底，不删路由）",
      _ac.count('body["tool_choice"]') >= 4, "实际 %d 处" % _ac.count('body["tool_choice"]'))
check("E3 落盘格式与信号签名不动",
      "def history_for_agent(" in AUTO and "board_update" not in FOCUS)
check("E4 版本已传导到 v4.301.0",
      'APP_VERSION = "v4.301.0"' in read("config.py")
      and 'VERSION = "v4.301.0"' in read("agent_loop.py")
      and "## v4.301.0" in read("CHANGELOG.md"))
check("E5 早停不用「跳页」解决（上一波成果不回退）",
      "def page_after_render(" in FOCUS
      and 'setCurrentIndex(1)' not in fn_seg(UI, "_render_messages"))


if __name__ == "__main__":
    print("")
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
