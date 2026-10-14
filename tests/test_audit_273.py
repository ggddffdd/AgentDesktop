# -*- coding: utf-8 -*-
"""v4.273.0 判据：本地审核里三处会悄悄做错的行为，以及聊天页空状态。

A1  自动化接手不算完成。推脱回复失败；失败不写 last_run，冷却后还能补跑。
A2  回写钉在启动时的会话，不跟着后来的 active() 走。
A3  证据编号接着库里的最大号，重启后 get(新号) 不是旧网页。
E   聊天页连续加载失败发出一次放弃信号，文案带「重试」。
B   外部工具缺命令或目录时跳过，并说人话。
C   朗读失败不报域名；设置把五档和总是启用工具放进高级选项。

扰动用环境变量指向变异副本（不动真源码）。
用法：python tests/test_audit_273.py
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import shutil
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print("  [PASS] %s" % name)
        _p += 1
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        _f += 1


def load_mod(env_key, filename):
    path = os.environ.get(env_key) or os.path.join(ROOT, filename)
    name = "audit273_" + os.path.splitext(os.path.basename(filename))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_src(env_key, filename):
    path = os.environ.get(env_key) or os.path.join(ROOT, filename)
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def group_a1(auto):
    print("== A1 接手不等于做完 ==")
    refusal = "请开启 Agent 模式后再试"
    check("A1a 推脱回复不算成功",
          not auto.reply_is_real_success(refusal)
          and not auto.reply_is_real_success("请切到『助手做事』让我执行")
          and not auto.reply_is_real_success("")
          and not auto.reply_is_real_success("好的", tools_ran=False))
    check("A1b 够长或用过工具才算成功",
          auto.reply_is_real_success("已整理好今天的热榜，条目如下。" + ("项" * 80))
          and auto.reply_is_real_success("好的", tools_ran=True)
          and not auto.reply_is_real_success("已整理好。" + ("项" * 80), stopped=True))
    reason = auto.failure_reason(refusal)
    check("A1d 失败原因不含内部词",
          "Agent" not in reason and "干活" in reason, reason)

    now = datetime(2026, 10, 14, 10, 0, 0)
    task = {"schedule_type": auto.SCHED_DAILY, "at_time": "09:00",
            "last_run": 0.0, "enabled": True}
    check("A1 当天到点且没跑过会到期", auto.is_due(task, now))
    auto.mark_failed(task, now, reason=reason)
    check("A1c 失败不写 last_run", float(task.get("last_run") or 0) == 0.0,
          "last_run=%r" % task.get("last_run"))
    check("A1e 冷却期内不到期",
          not auto.is_due(task, now + timedelta(seconds=30)))
    check("A1f 冷却结束后可补跑",
          auto.is_due(task, now + timedelta(seconds=auto.RETRY_COOLDOWN_SEC + 5)))

    blocked = {"schedule_type": auto.SCHED_DAILY, "at_time": "09:00",
               "last_run": 0.0, "enabled": True}
    for _ in range(auto.MAX_FAILS_PER_DAY):
        auto.mark_failed(blocked, now, reason="没做完")
    later = now + timedelta(seconds=auto.RETRY_COOLDOWN_SEC * 4)
    check("A1g 一天失败三次后当天不再跑", not auto.is_due(blocked, later))
    nxt = datetime(2026, 10, 15, 10, 0, 0)
    check("A1h 第二天重新允许", auto.is_due(blocked, nxt))


class _Sess(object):
    def __init__(self, sid):
        self.sid = sid
        self.messages = []


class _Store(object):
    def __init__(self):
        self.sessions = {
            "auto_github": _Sess("auto_github"),
            "auto_news": _Sess("auto_news"),
        }
        self._active = "auto_news"

    def active(self):
        return self.sessions[self._active]


def group_a2(auto, ui_src):
    print("== A2 会话不串、同时只跑一个 ==")
    store = _Store()
    got = auto.bound_session(store, "auto_github")
    check("A2a 钉住的会话不是当前会话",
          got is not None and got.sid == "auto_github"
          and store.active().sid == "auto_news",
          "got=%r" % getattr(got, "sid", None))
    check("A2b 流式回写走钉住的会话",
          "msgs = self._agent_write_session().messages" in ui_src)
    check("A2c 工具记录走钉住的会话",
          "self._agent_write_session().messages.append(rec)" in ui_src)


def group_a2_runtime(ui_mod):
    print("== A2 运行时回写 ==")

    class _Sess(object):
        def __init__(self, sid):
            self.sid = sid
            self.messages = []

    github = _Sess("auto_github")
    news = _Sess("auto_news")

    class _St(object):
        def __init__(self):
            self.sessions = {"auto_github": github, "auto_news": news}

        def active(self):
            return news

    class _Wkr(object):
        _bound_sid = "auto_github"

    win = type("_W", (object,), {
        "store": _St(),
        "_agent_worker": _Wkr(),
        "_save_throttled": lambda s: None,
        "_render_throttled": lambda s: None,
    })()
    win._agent_write_session = lambda: ui_mod.ChatWindow._agent_write_session(win)
    try:
        ui_mod.ChatWindow._on_tool_log(
            win, {"name": "web_search", "args": "q", "result": "fresh"})
        landed = len(github.messages) == 1 and not news.messages
        detail = "github=%d news=%d" % (len(github.messages), len(news.messages))
    except Exception as e:
        landed = False
        detail = "%s: %s" % (type(e).__name__, e)
    check("A2d 工具记录运行时进钉住的会话", landed, detail)


def group_a3(ev):
    print("== A3 证据编号不从 1 重来 ==")
    tmp = tempfile.mkdtemp(prefix="ev273_")
    try:
        ev.set_dir(tmp)
        ev._DB_READY = False
        if not ev._init_db():
            check("A3a 重启后编号接着最大号", False, "证据库初始化失败")
            check("A3b 新编号取回的是新页面", False, "跳过")
            check("A3c 旧编号仍是旧页面", False, "跳过")
            return
        dbp = os.path.join(tmp, "evidence.db")
        conn = sqlite3.connect(dbp)
        try:
            conn.execute(
                "INSERT INTO evidence (id, ts, ts_str, tool, args, raw, n_chars, "
                "n_lines, ok, err, sha) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (1, 1.0, "2026-07-01 00:00:00", "web_search", "{}",
                 "STALE PAGE", 10, 1, 1, None, "stale"))
            conn.commit()
        finally:
            conn.close()
        # 模拟新进程：内存里没有任何递增计数。
        ev._DB_READY = False
        eid = ev.register("web_search", {"q": "ai"}, "FRESH PAGE")
        check("A3a 重启后编号接着最大号",
              isinstance(eid, int) and eid > 1, "eid=%r" % eid)
        fresh = ev.get(eid) if isinstance(eid, int) else None
        check("A3b 新编号取回的是新页面",
              bool(fresh) and fresh.get("raw") == "FRESH PAGE",
              "raw=%r" % (None if not fresh else fresh.get("raw")))
        old = ev.get(1)
        check("A3c 旧编号仍是旧页面",
              bool(old) and old.get("raw") == "STALE PAGE"
              and (not fresh or fresh.get("raw") != old.get("raw")),
              "old=%r" % (None if not old else old.get("raw")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def group_e(chat_mod, focus_mod):
    print("== E 聊天页加载失败空状态 ==")
    title, hint, action = focus_mod.chat_load_failure_copy()
    check("E4 按钮文案是重试",
          action == "重试" and "重试" in hint and bool(title),
          "action=%r" % action)
    try:
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        view = chat_mod.ChatWebView({})
        try:
            view._probe_timer.stop()
        except Exception:
            pass
        hits = []
        view.loadGaveUp.connect(lambda: hits.append(1))
        view._ever_ready = False
        view._ready = False
        view._load_fail_count = 0
        view._load_gave_up = False
        for _ in range(6):
            view._on_load_finished(False)
        check("E1 连续六次失败只发一次放弃", len(hits) == 1, "hits=%d" % len(hits))
        view._on_load_finished(False)
        check("E2 第七次不再发", len(hits) == 1, "hits=%d" % len(hits))
        view.retry_from_user()
        check("E3 重试清零",
              view._load_fail_count == 0 and not view._load_gave_up)
    except Exception as e:
        check("E1 连续六次失败只发一次放弃", False, "%s: %s" % (type(e).__name__, e))
        check("E2 第七次不再发", False, "跳过")
        check("E3 重试清零", False, "跳过")


def group_b(mcp, cfg_mod):
    print("== B 启动失败要跳过 ==")
    # 用当前解释器当「命令存在」。/bin/true 在 Windows 上不存在，会先被判成程序找不到。
    ok, hint = mcp.preflight(sys.executable, ["/no/such/audit273/july-dir"])
    check("B1 目录不在就跳过",
          ok is False and "文件夹已经不在了" in hint and "初始化失败" not in hint,
          "ok=%r hint=%r" % (ok, hint))
    real_which = mcp.shutil.which
    mcp.shutil.which = lambda _c: None
    try:
        ok2, hint2 = mcp.preflight("npx", ["-y", "@scope/server"])
    finally:
        mcp.shutil.which = real_which
    check("B2 没有 Node 就跳过",
          ok2 is False and "Node.js" in hint2 and "初始化失败" not in hint2,
          "ok=%r hint=%r" % (ok2, hint2))

    cfg_mod.mcp_init_attempted = False
    cfg_mod.mcp_skip_hints = []
    cfg_mod.mcp_clients = []
    cfg = {"mcp_servers": [{
        "enabled": True,
        "name": "filesystem",
        "command": "npx",
        "args": ["-y", "pkg", "/no/such/audit273/july-dir"],
    }]}
    cfg_mod.init_mcp_clients(cfg)
    n1 = len(cfg_mod.mcp_skip_hints)
    cfg_mod.init_mcp_clients(cfg)
    check("B3 同一进程只提示一次",
          n1 == 1 and len(cfg_mod.mcp_skip_hints) == 1 and not cfg_mod.mcp_clients,
          "hints=%r" % (cfg_mod.mcp_skip_hints,))


def group_c(voice, ui_src):
    print("== C 白话与设置 ==")
    exc = RuntimeError("403 Client Error for url: https://speech.platform.bing.com/consumer/speech")
    msg = voice.tts_user_message(exc)
    check("C1 朗读失败不说域名",
          voice.tts_failure_is_quota(exc) and "朗读" in msg
          and "speech.platform" not in msg and "403" not in msg,
          msg)
    check("C2 设置页不把开关叫 Agent 模式",
          '_kv("Agent 模式"' not in ui_src and '_kv("总是启用工具"' in ui_src)
    check("C3 高级选项写在按钮上",
          "高级选项：五档执行 / 总是启用工具" in ui_src)
    check("C4 提示词不教模型说请开启 Agent 模式",
          "请开启 Agent 模式" not in ui_src
          and "请切到『助手做事』" in ui_src)


def group_memory():
    print("== B 记忆备份 ==")
    import memory_store
    base = tempfile.mkdtemp(prefix="mem273_")
    try:
        memory_store._configure(base)
        memory_store._backup_dir._warned = False
        bd = os.path.join(base, "backups")
        os.makedirs(bd)
        os.makedirs(os.path.join(bd, "memory_0000_blocked"))
        os.makedirs(os.path.join(bd, "legion_checkpoints_test_x"))
        for i in range(1, 70):
            with open(os.path.join(bd, "memory_%04d.md" % i), "w", encoding="utf-8") as f:
                f.write("old")
        memory_store._prune_backups(bd)
        check("B4 删不掉的备份目录被跳过",
              os.path.isdir(os.path.join(bd, "memory_0000_blocked"))
              and os.path.isdir(os.path.join(bd, "legion_checkpoints_test_x")))
        with open(memory_store.MEMORY_PATH, "w", encoding="utf-8") as f:
            f.write("今天的记忆")
        memory_store._snapshot_memory("audit")
        names = os.listdir(bd)
        check("B5 快照仍然写出来",
              any(n.startswith("memory_") and n.endswith(".md") and "audit" in n
                  for n in names),
              "names=%s" % names[:8])
        orig = memory_store._dir_writable
        memory_store._dir_writable = lambda _p: False
        try:
            fallback = memory_store._backup_dir()
        finally:
            memory_store._dir_writable = orig
        check("B6 备份目录不可写时改到临时目录",
              "xiaochou_memory_backups" in fallback and fallback != bd,
              fallback)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def group_denial():
    print("== C 拒绝说明 ==")
    import tools
    msg = tools.user_denial("write_file")
    denied = tools.user_denial("write_file", "denied")
    check("C5 写文件的拒绝是白话",
          msg.startswith("写文件需要你先允许")
          and "没有被允许" in denied
          and "exec_tool" not in msg and "审查报告" not in msg
          and "闸门" not in msg,
          msg)


def main():
    print("==== 审核修复 v4.273 判据开始 ====")
    auto = load_mod("AUTOMATION_PATH", "automation.py")
    ev = load_mod("EVIDENCE_PATH", "evidence.py")
    chat_mod = load_mod("CHAT_WEB_PATH", "chat_web.py")
    focus_mod = load_mod("UI_FOCUS_PATH", "ui_focus.py")
    mcp = load_mod("MCP_PATH", "mcp_client.py")
    voice = load_mod("VOICE_PATH", "voice.py")
    ui_src = read_src("UI_PATH", "ui.py")
    ui_mod = load_mod("UI_PATH", "ui.py")
    import config as cfg_mod
    group_a1(auto)
    group_a2(auto, ui_src)
    group_a2_runtime(ui_mod)
    group_a3(ev)
    group_e(chat_mod, focus_mod)
    group_b(mcp, cfg_mod)
    group_c(voice, ui_src)
    group_memory()
    group_denial()
    print("\n==== 审核修复 v4.273 结果：PASS=%d  FAIL=%d ====" % (_p, _f))
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
