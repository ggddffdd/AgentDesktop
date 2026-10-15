# -*- coding: utf-8 -*-
"""v4.279.0 判据：定时任务不得因「多功能二选一」澄清而停住。

事故：打包版跑 GitHub trending 简报时，正文同时碰到「浏览器」和「写入」，
澄清闸门当成两个方向，直接回复「请先确认你想要哪一个」，任务没有执行。
定时任务没有人回答。交互会话里真正含糊的多功能指令仍可以问。

本组同时钉住三件事：
  1. 「无需浏览器」这类否定不算打开网页；
  2. 自动化会话里，同时写了「打开网页」和「写文件」也不澄清，并且会继续跑；
  3. 任务正文声明了 HTTP + 落盘时，工具集里有 web_fetch / write_file，
     范围内的 write_file 不再弹确认框。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = 0
FAIL = 0

CLARIFY_MARK = "这条指令可能指向多个功能"
CLARIFY_TAIL = "请补充说明后我再执行"

# 主人这次没跑起来的任务正文（约）。
OWNER = (
    "【自动化任务】抓取今天最新的 GitHub trending，整理成 8 条以内要点简报"
    "（每条：仓库名 + 一句话说明 + Star数/语言/来源），"
    "写入 ~/Documents/小臭玩AI/产物/github_trending_latest.md，"
    "并在回复里展示简报内容。\n"
    "【通道策略】T0 首选 requests 直连 https://github.com/trending?since=daily "
    "（纯 HTTP，无需浏览器）; Star 补全用 GitHub API；不足 8 条按实际条数，禁止编造。"
)

# 自动化口吻，同时点名两个功能。交互会话仍应澄清；定时任务必须接着跑。
BOTH = (
    "【自动化任务】请打开网页查看今天的 GitHub trending，并写文件保存简报。"
    "纯 HTTP requests 直连，无需浏览器。"
)


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s%s" % (name, ("  <%s>" % detail) if detail else ""))


print("=" * 64)
print("v4.279.0 定时任务不因多功能澄清停住")
print("=" * 64)

import intent  # noqa: E402


def _nc(text):
    it = intent.classify(text)
    try:
        return bool(it.needs_clarification), it
    except AttributeError:
        return None, it


print("\n--- C1 否定浏览器不再当成第二个功能 ---")
nc_owner, it_owner = _nc(OWNER)
check("C1 主人这条 trending 任务不澄清",
      nc_owner is False,
      "clarify=%r tools=%r reason=%r" % (
          nc_owner,
          getattr(it_owner, "requested_tools", None),
          getattr(it_owner, "clarify_reason", None)))
check("C1b 仍认出要写文件",
      it_owner is not None and "write_file" in (it_owner.requested_tools or ()),
      repr(getattr(it_owner, "requested_tools", None)))
check("C1c 不把「无需浏览器」算成打开网页",
      it_owner is not None and "browser_open" not in (it_owner.requested_tools or ()),
      repr(getattr(it_owner, "requested_tools", None)))

nc_both, it_both = _nc(BOTH)
check("C1d 真的同时写了打开网页和写文件，分类层仍标澄清",
      nc_both is True and "browser_open" in (it_both.requested_tools or ())
      and "write_file" in (it_both.requested_tools or ()),
      "clarify=%r tools=%r" % (nc_both, getattr(it_both, "requested_tools", None)))


print("\n--- C2 工具集：声明了 HTTP + 落盘才把 write_file 加回来 ---")
try:
    import automation

    def T(n):
        return {"type": "function", "function": {"name": n}}

    catalog = [T(n) for n in (
        "web_fetch", "web_search", "read_file", "browser_open",
        "write_file", "db_insert", "schedule", "run_python", "run_command",
    )]
    task = {"id": "t279", "message": OWNER, "full_tools": False}
    bare = [t["function"]["name"] for t in automation.filter_tools_safe(catalog)]
    check("C2 不传任务时 write_file 仍滤掉（无人值守默认不卡确认框）",
          "write_file" not in bare and "run_command" not in bare,
          repr(bare))
    kept = [t["function"]["name"]
            for t in automation.filter_tools_safe(catalog, task)]
    check("C2b 任务声明写入 + HTTP → 有 write_file 和 web_fetch",
          "write_file" in kept and "web_fetch" in kept,
          repr(kept))
    check("C2c 不加回 db_insert / schedule / run_command / run_python",
          not ({"db_insert", "schedule", "run_command", "run_python"} & set(kept)),
          repr(kept))
    quiet = automation.filter_tools_safe(
        catalog, {"id": "q", "message": "只提醒我喝水"})
    quiet_names = [t["function"]["name"] for t in quiet]
    check("C2d 没说落盘的任务不加回 write_file",
          "write_file" not in quiet_names, repr(quiet_names))
    ui_src = open(os.path.join(ROOT, "ui.py"), encoding="utf-8").read()
    check("C2e 定时任务把任务正文传进工具过滤",
          "automation.filter_tools_safe(all_tools, _auto_t)" in ui_src)
except Exception as e:
    check("C2 工具集恢复可调用", False, "%s: %s" % (type(e).__name__, e))


print("\n--- C3 范围内写文件：定时任务不弹确认，越界仍拒绝 ---")
try:
    import permissions

    home = os.path.expanduser("~")
    eng = permissions.PermissionEngine("interactive", set(), [home], set())
    path = os.path.join(home, "Documents", "小臭玩AI", "产物",
                        "github_trending_latest.md")
    interactive = eng.decide("write_file", {"path": path, "content": "hi"})
    check("C3 交互模式写文件仍要确认",
          interactive.allowed and interactive.needs_user,
          "allowed=%s needs=%s rule=%s" % (
              interactive.allowed, interactive.needs_user, interactive.rule))
    permissions.set_unattended(True)
    try:
        auto = eng.decide("write_file", {"path": path, "content": "hi"})
        check("C3b 定时任务范围内写文件直接放行",
              auto.allowed and not auto.needs_user
              and auto.rule == "auto:task_write",
              "allowed=%s needs=%s rule=%s" % (
                  auto.allowed, auto.needs_user, auto.rule))
        outside = eng.decide("write_file", {"path": "/etc/passwd", "content": "x"})
        check("C3c 越界写入仍拒绝",
              not outside.allowed and not outside.needs_user,
              "allowed=%s rule=%s" % (outside.allowed, outside.rule))
        cmd = eng.decide("run_command", {"command": "echo hi"})
        check("C3d 定时任务不放行 run_command",
              cmd.needs_user or not cmd.allowed,
              "allowed=%s needs=%s rule=%s" % (
                  cmd.allowed, cmd.needs_user, cmd.rule))
        db = eng.decide("db_insert", {"table": "t"})
        check("C3e 定时任务不放行 db_insert",
              db.needs_user or not db.allowed,
              "allowed=%s needs=%s rule=%s" % (db.allowed, db.needs_user, db.rule))
        fetch = eng.decide("web_fetch", {"url": "https://github.com/trending"})
        check("C3f web_fetch 仍可直接执行",
              fetch.allowed and not fetch.needs_user,
              "allowed=%s needs=%s rule=%s" % (
                  fetch.allowed, fetch.needs_user, fetch.rule))
        discuss = permissions.PermissionEngine("discuss", set(), [home], set())
        blocked = discuss.decide("write_file", {"path": path, "content": "hi"})
        check("C3g 仅讨论模式仍拒绝写文件",
              not blocked.allowed, "rule=%s" % blocked.rule)
    finally:
        permissions.set_unattended(False)
except Exception as e:
    check("C3 权限可调用", False, "%s: %s" % (type(e).__name__, e))


print("\n--- C4 真跑 Agent：自动化接着执行，交互仍澄清 ---")


class _Sess(object):
    sid = "test_clarify_279"
    messages = []


class _Store(object):
    def active(self):
        return _Sess()


class _Dec(object):
    allowed = True
    needs_user = False
    reason = ""
    rule = "test"


class _Engine(object):
    def decide(self, name, args=None, explicit_intent=True, task_risk=None):
        return _Dec()


class _FakeMw(object):
    def __init__(self, script, auto=False):
        self.script = list(script)
        self.i = 0
        self.calls = []
        self.cfg = {}
        self.store = _Store()
        self.permission_engine = _Engine()
        self.app_dir = ROOT
        self._auto_task_active = (
            {"id": "t279", "message": BOTH, "full_tools": False} if auto else None)

    def _agent_call(self, messages, tool_defs, on_delta=None,
                    force_required=False, force_tool=None, force_complex=False):
        self.calls.append(force_tool)
        if self.i < len(self.script):
            content, tcs = self.script[self.i]
            self.i += 1
        else:
            content, tcs = "简报在上面。", []
        return {"content": content, "tool_calls": tcs,
                "reasoning_content": "", "usage": {"total_tokens": 8}}


def _run(text, auto):
    from agent import AgentWorker
    tcs = [
        {"id": "c1", "type": "function",
         "function": {"name": "web_fetch",
                      "arguments": "{\"url\":\"https://github.com/trending\"}"}},
        {"id": "c2", "type": "function",
         "function": {"name": "write_file",
                      "arguments": "{\"path\":\"brief.md\",\"content\":\"x\"}"}},
    ]
    mw = _FakeMw([("先抓再写。", tcs), ("简报如下：1. 示例", [])], auto=auto)
    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": text},
    ]
    w = AgentWorker(mw, messages, [], isolated=True)
    w._sync_to_session = lambda mw_: None
    w._sync_agent_checkpoint = lambda mw_: None
    w._auto_remember = lambda mw_: None
    w._persist_task_memory = lambda mw_, duration_s=0, steps=0: None
    executed = []

    def _fake_exec(tool_calls, mw_, app_dir):
        for tc in tool_calls or []:
            fn = tc.get("function", {})
            executed.append(fn.get("name", ""))
            try:
                w._handle_tool_result(tc, fn.get("name", ""), fn, "OK", [], None)
            except Exception:
                pass

    w._exec_tool_calls = _fake_exec
    chunks = []
    w.stream_commit.connect(lambda t: chunks.append(str(t)))
    w.render.connect(lambda *a: None)
    w.run()
    return mw, executed, "\n".join(chunks)


try:
    import permissions
    _flags = []
    _real_set = permissions.set_unattended

    def _spy_unattended(on):
        _flags.append(bool(on))
        _real_set(on)

    permissions.set_unattended = _spy_unattended
    mw_auto, ran_auto, txt_auto = _run(BOTH, auto=True)
    check("C4f 自动化运行标记过无人值守", True in _flags, repr(_flags))
    _flags.clear()
    check("C4 自动化不出现多功能澄清",
          CLARIFY_MARK not in txt_auto and CLARIFY_TAIL not in txt_auto,
          txt_auto[-300:])
    check("C4b 自动化真的调用了模型",
          len(mw_auto.calls) >= 1, "calls=%d" % len(mw_auto.calls))
    check("C4c 自动化真的跑了 web_fetch 和 write_file",
          "web_fetch" in ran_auto and "write_file" in ran_auto,
          repr(ran_auto))
    mw_chat, ran_chat, txt_chat = _run(BOTH, auto=False)
    check("C4g 交互运行没有标成无人值守", True not in _flags, repr(_flags))
    check("C4d 交互会话仍澄清，不直接跑",
          CLARIFY_MARK in txt_chat and CLARIFY_TAIL in txt_chat
          and len(mw_chat.calls) == 0 and not ran_chat,
          "calls=%d ran=%r text=%r" % (len(mw_chat.calls), ran_chat, txt_chat[-200:]))
    mw_owner, ran_owner, txt_owner = _run(OWNER, auto=True)
    check("C4e 主人原文在自动化里也不澄清并继续跑",
          CLARIFY_MARK not in txt_owner and len(mw_owner.calls) >= 1
          and "web_fetch" in ran_owner and "write_file" in ran_owner,
          "calls=%d ran=%r text=%r" % (
              len(mw_owner.calls), ran_owner, txt_owner[-240:]))
except Exception as e:
    import traceback
    traceback.print_exc()
    check("C4 Agent 真跑得起来", False, "%s: %s" % (type(e).__name__, e))
finally:
    try:
        permissions.set_unattended = _real_set
        _real_set(False)
    except Exception:
        pass


print("\n" + "=" * 64)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 64)
sys.exit(1 if FAIL else 0)
