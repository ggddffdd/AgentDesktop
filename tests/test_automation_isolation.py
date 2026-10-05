# -*- coding: utf-8 -*-
"""v4.215.0 判据：自动化任务**独立会话 + 独立工具权限**（外部审核 P1 修复）。

背景（审核原文）：定时任务直接把内容追加到当前活动会话 —— 任务和用户聊天
混流、任务读用户聊天上下文、用户不易区分哪些是自己发的、任务继承当前会话
的（工具）权限。

守的东西（每条都能被 `_perturb_automation_isolation.py` 单独翻红）：

  A 组  filter_tools_safe 纯函数（真实 RISK_MAP）：
    A1  READ 保留；A2 semi 档 WRITE_LOCAL 保留；
    A3  EXEC / EXTERNAL / 未登记（MCP）全滤；
    A4  ALWAYS_CONFIRM（硬确认）滤 —— 无人值守弹确认框=挂起；
    A5  manual 档本地写（write_file/db_insert/db_update/schedule）滤 ——
        交互模式它们要人工确认（permissions.decide 第 7 步），同样会挂起；
    A6  不改入参、坏元素不崩；A7 risk 不可用时全滤（fail-closed）；
    A8  new_task full_tools 默认 False。
  B 组  ui.py AST：
    B1  _fire_automation_run 不再写 store.active()（不混入用户当前会话）；
    B2  _automation_session get-or-create（get + Session 构造 + 字典赋值）；
    B3  专属 sid 前缀 auto_；
    B4  消息 append 到专属会话；B5 store.switch 到专属会话；
    B6  _agent_run 的受限工具分支（_auto_task_active + filter_tools_safe）；
    B7  full_tools=True 才跳过过滤（守卫含 not ...full_tools）；
    B8  _on_agent_done 收口清理 _auto_task_active。
  C 组  行为（offscreen import ui，不碰真盘）：
    C1  _automation_session 首次建会话（sid/title/folder）+ 二次取同一个；
    C2  任务缺 id 返回 None（防 sid=auto_ 空后缀撞车）。

扰动用环境变量 UI_PATH / AUTO_PATH 指向变异副本（不动真源码）。
用法：python tests/test_automation_isolation.py
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print(f"  [PASS] {name}")
        _p += 1
    else:
        print(f"  [FAIL] {name}  {detail}")
        _f += 1


def _src(path):
    return io.open(path, "r", encoding="utf-8-sig", newline="").read()


UI_PATH = os.environ.get("UI_PATH", os.path.join(ROOT, "ui.py"))
AUTO_PATH = os.environ.get("AUTO_PATH", os.path.join(ROOT, "automation.py"))


def _fn(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def group_a_pure():
    print("== A 组：filter_tools_safe 纯函数（真实 RISK_MAP） ==")
    if os.environ.get("AUTO_PATH"):
        # 扰动模式：从变异副本加载 automation（不 import 真模块）
        import importlib.util
        spec = importlib.util.spec_from_file_location("automation_mut", AUTO_PATH)
        automation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(automation)
    else:
        import automation

    def T(n):
        return {"function": {"name": n}}

    tools = [T(n) for n in [
        "web_search", "read_file", "browser_open", "browser_read",     # READ
        "image_gen", "video_gen", "remember", "use_skill", "chart_gen",  # semi 写
        "write_file", "db_insert", "db_update", "schedule",             # manual 写
        "db_delete", "skill_install", "create_skill", "delete_automation",  # 硬确认
        "run_command", "run_python", "browser_click", "mouse_click",    # EXEC
        "process_kill", "app_launch",
        "send_email", "webhook_start",                                  # EXTERNAL
        "mcp_unknown_tool",                                             # 未登记 → EXTERNAL
    ]]
    kept = [t["function"]["name"] for t in automation.filter_tools_safe(tools)]
    ks = set(kept)

    check("A1 READ 工具保留（web_search/read_file/browser_open/browser_read）",
          {"web_search", "read_file", "browser_open", "browser_read"} <= ks,
          f"实际保留={kept}")
    check("A2 semi 档本地写保留（image_gen/video_gen/remember/use_skill/chart_gen）",
          {"image_gen", "video_gen", "remember", "use_skill", "chart_gen"} <= ks,
          f"实际保留={kept}")
    check("A3 EXEC/EXTERNAL/未登记全滤（run_command/browser_click/send_email/mcp_*）",
          not ({"run_command", "run_python", "browser_click", "mouse_click",
                "process_kill", "app_launch", "send_email", "webhook_start",
                "mcp_unknown_tool"} & ks),
          f"漏滤={sorted({'run_command','browser_click','send_email','mcp_unknown_tool'} & ks)}")
    check("A4 硬确认档滤（db_delete/skill_install/create_skill/delete_automation）",
          not ({"db_delete", "skill_install", "create_skill",
                "delete_automation"} & ks),
          f"漏滤={sorted({'db_delete','skill_install','create_skill'} & ks)}")
    check("A5 manual 档本地写滤（write_file/db_insert/db_update/schedule）——无人值守不卡确认框",
          not ({"write_file", "db_insert", "db_update", "schedule"} & ks),
          f"漏滤={sorted({'write_file','db_insert','db_update','schedule'} & ks)}")
    check("A6 不改入参 + 坏元素不崩",
          automation.filter_tools_safe([]) == []
          and automation.filter_tools_safe(None) == []
          and automation.filter_tools_safe([None, "x", {}, T("web_search")])
          and T("web_search") in automation.filter_tools_safe(
              [None, "x", {}, T("web_search")]),
          "空/None/坏元素路径崩了或入参被改")
    _orig = tools[0]
    automation.filter_tools_safe(tools)
    check("A7 过滤幂等（两次结果一致）且入参列表原样",
          [t["function"]["name"] for t in automation.filter_tools_safe(tools)] == kept
          and tools[0] is _orig and len(tools) == len(tools))
    t = automation.new_task("n", automation.ACT_RUN, "m", automation.SCHED_DAILY)
    check("A8 new_task full_tools 默认 False（保守侧）",
          t.get("full_tools") is False, f"实际={t.get('full_tools')!r}")
    t2 = automation.new_task("n", automation.ACT_RUN, "m", automation.SCHED_DAILY,
                             full_tools=True)
    check("A8b new_task full_tools=True 可显式放开", t2.get("full_tools") is True)


def group_b_ast():
    print("== B 组：ui.py AST（独立会话 + 受限工具接线） ==")
    tree = ast.parse(_src(UI_PATH))

    fire = _fn(tree, "_fire_automation_run")
    check("B0a _fire_automation_run 存在", fire is not None)
    sess_fn = _fn(tree, "_automation_session")
    check("B0b _automation_session 存在", sess_fn is not None,
          "get-or-create 助手没了")
    run_fn = _fn(tree, "_agent_run")
    check("B0c _agent_run 存在", run_fn is not None)
    done_fn = _fn(tree, "_on_agent_done")
    check("B0d _on_agent_done 存在", done_fn is not None)

    if fire:
        src_seg = ast.get_source_segment(_src(UI_PATH), fire) or ""
        # AST 判真实调用（防被注释里的 store.active 字样骗——v4.215.0 实踩）
        calls = [c for c in ast.walk(fire)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                 and c.func.attr == "active"]
        check("B1 _fire 不再调用 store.active()（不混入用户当前会话）",
              not calls, f"fire 里有 {len(calls)} 处 .active() 调用")
        check("B4 消息 append 到 _automation_session 返回的会话",
              "self._automation_session(task)" in src_seg,
              "fire 里没有取专属会话")
        check("B5 store.switch 到专属会话（可见分流，非静默）",
              "self.store.switch(session.sid)" in src_seg,
              "没有 switch 到专属会话")

    if sess_fn:
        seg = ast.get_source_segment(_src(UI_PATH), sess_fn) or ""
        check("B2 get-or-create 三件套（sessions.get + Session( + sessions[sid]=）",
              ".sessions.get(" in seg and "Session(" in seg
              and ".sessions[sid] = s" in seg,
              "get-or-create 逻辑不完整")
        check("B3 专属 sid 用 SESSION_SID_PREFIX 单源常量（auto_ 前缀）",
              "automation.SESSION_SID_PREFIX" in seg,
              "sid 不再走单源常量（回退到内联 f-string 也算回归）")

    if run_fn:
        seg = ast.get_source_segment(_src(UI_PATH), run_fn) or ""
        check("B6 _agent_run 有受限工具分支（_auto_task_active → filter_tools_safe）",
              "_auto_task_active" in seg and "automation.filter_tools_safe(" in seg,
              "过滤接线丢了")
        check("B7 full_tools=True 才放开全部（守卫含 not ...full_tools）",
              'not _auto_t.get("full_tools")' in seg,
              "full_tools 逃生口丢了")

    if done_fn:
        seg = ast.get_source_segment(_src(UI_PATH), done_fn) or ""
        check("B8 _on_agent_done 收口清理 _auto_task_active",
              "_auto_task_active = None" in seg,
              "受限标记不随轮次清理（会泄漏到用户下一轮）")


def group_c_behavior():
    print("== C 组：行为（offscreen import ui，_automation_session） ==")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import ui  # noqa: F401
        from session import Session
    except Exception as e:
        print(f"  [FAIL] C 组环境 import ui 失败: {e}")
        globals()["_f"] = globals()["_f"] + 1
        return

    class FakeStore:
        def __init__(self):
            self.sessions = {}
            self.active_sid = None
            self.saved = 0

        def switch(self, sid):
            self.active_sid = sid

        def save(self):
            self.saved += 1

    class FakeApp:
        pass

    app = FakeApp()
    app.store = FakeStore()
    task = {"id": "abc123", "name": "每日简报"}

    s1 = ui.ChatWindow._automation_session(app, task)
    check("C1a 首次调用建会话（sid=auto_abc123）",
          s1 is not None and s1.sid == "auto_abc123",
          f"sid={getattr(s1, 'sid', None)}")
    check("C1b 会话带标题与「自动化」分组",
          s1.title == "⚙️ 每日简报" and s1.folder == "自动化",
          f"title={s1.title!r} folder={s1.folder!r}")
    s2 = ui.ChatWindow._automation_session(app, task)
    check("C1c 二次调用取同一会话（任务历史累积）", s2 is s1)
    check("C1d 不同任务不同会话（按 id 隔离）",
          ui.ChatWindow._automation_session(
              app, {"id": "xyz789", "name": "B"}) .sid == "auto_xyz789")
    check("C2 任务缺 id 返回 None（防 auto_ 空后缀）",
          ui.ChatWindow._automation_session(app, {"name": "无id"}) is None)


def main():
    for g in (group_a_pure, group_b_ast, group_c_behavior):
        try:
            g()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {g.__name__} 组崩溃: {e}")
            globals()["_f"] = globals()["_f"] + 1
    n_ok, n_fail = _p, _f
    print(f"\n汇总：{n_ok} OK / {n_fail} FAIL   PASS={n_ok} FAIL={n_fail}")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
