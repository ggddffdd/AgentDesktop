# -*- coding: utf-8 -*-
"""v4.271.0：审阅剩余项。设置一页、高成本波拦住、导演台花费确认、审计合并。

无网络。角色编辑用离屏 Qt。
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS]", name)
    else:
        FAIL += 1
        print("  [FAIL]", name, ("| " + str(extra)) if extra else "")


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def test_capability_and_skills():
    print("\n=== 高成本波拦住；拒用技能不退回裸正文 ===")
    import legion
    check("高成本波 Critical 会拦住",
          legion.capability_gap_blocks_wave(True, True, "生成一段视频") is True)
    check("文本波不拦",
          legion.capability_gap_blocks_wave(True, False, "写一篇") is False)
    check("仍要开工不拦",
          legion.capability_gap_blocks_wave(True, True, "仍要开工，继续生视频") is False)
    worker = _read("legion_worker.py")
    check("工人在高成本波调用拦住",
          "capability_gap_blocks_wave(" in worker and "_pause_for_capability_gap(" in worker)

    import skill_meta
    slug = "reject-followup-271"
    body = "RAW_SHOULD_NOT_LEAK_271"
    tmp = tempfile.mkdtemp(prefix="skills271_")
    try:
        folder = os.path.join(tmp, slug)
        os.makedirs(folder)
        with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("---\nname: Reject Me\ndescription: 缺来源\n---\n%s\n" % body)
        orig = skill_meta.strict_from_config
        skill_meta.strict_from_config = lambda cfg=None: True
        try:
            loaded = legion._legion_loader_body(slug, tmp)
        finally:
            skill_meta.strict_from_config = orig
        check("拒用不得退回裸正文",
              loaded == "" and body not in str(loaded), repr(loaded)[:180])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_role_editor_keeps_tags():
    print("\n=== 角色编辑保留职责标签、别名和编号 ===")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from legion_ui import RoleEditor
    ed = RoleEditor({
        "id": "role-keep-271",
        "name": "画师",
        "capability_tags": ["生图", "检索"],
        "aliases": ["画师甲"],
        "focus": "画面",
        "completion_criteria": "有成图",
        "tools": ["write_file"],
        "skills": [],
    })
    role = ed.get_role()
    check("保存后职责标签还在",
          role.get("capability_tags") == ["生图", "检索"], role.get("capability_tags"))
    check("保存后别名还在", role.get("aliases") == ["画师甲"], role.get("aliases"))
    check("保存后编号还在", role.get("id") == "role-keep-271", role.get("id"))
    check("保存后专注和完成标准还在",
          role.get("focus") == "画面" and role.get("completion_criteria") == "有成图")


def test_director_prompts_and_steps():
    print("\n=== 导演台花费确认、跳过步骤、缺镜合成、出片后续跑 ===")
    import director_panel as dp
    import tools
    check("预审关掉时整批视频要问",
          "将生成" in (dp.paid_generation_prompt("clip", 3, False) or ""))
    check("预审开着时整批不再加短确认",
          dp.paid_generation_prompt("clip", 3, True) is None)
    check("单镜视频无论预审都问",
          "这一镜" in (dp.paid_generation_prompt("clip", 1, True, single=True) or ""))
    check("口播跳过人物不当完成",
          dp.step_chip_state(2, 5, True) == "skipped")
    check("非口播已走过的步骤是完成",
          dp.step_chip_state(2, 5, False) == "done")
    check("只能回看已经走到的步骤",
          dp.step_view_allowed(3, 5) and not dp.step_view_allowed(6, 5))
    partial = dp.clips_done_notice(1, 3, ["a.mp4", None, None])
    check("缺一部分镜可以合成",
          partial and partial.get("error") is None and "可以合成" in partial.get("status", ""),
          partial)
    none = dp.clips_done_notice(0, 3, [None, None, None])
    check("一镜都没有才记失败",
          none and none.get("error") and "没有可用片段" in none["error"], none)
    check("出片后续跑横幅",
          "上次已出片" in dp.resume_banner_text({"step": 6, "finished": True}))
    msg = tools.video_gen_breaker_message("director")
    check("导演台熔断不提 HTML", "HTML" not in msg and "代码" not in msg, msg)
    check("主对话熔断仍可建议 HTML",
          "HTML" in tools.video_gen_breaker_message(None))
    panel = _read("director_panel.py")
    check("导演台高级拍摄项默认收起", "更多拍摄选项" in panel)


def test_settings_copy_audit_scan():
    print("\n=== 设置一页、失败句、审计合并、设备名 ===")
    ui = _read("ui.py")
    check("设置页不再单独弹层", "打开设置弹层" not in ui)
    check("设置页写明复杂路由回退费钱", "route_complex_v1 是费钱的调试回退" in ui)
    check("旧设置入口改去设置页", "_mount_settings_form" in ui)
    twin = _read("digital_twin_panel.py")
    check("数字人增强项收进更多选项", "更多选项" in twin and "twin_more_box" in twin)
    steps = _read("onboarding.py")
    check("引导写三档和省略号菜单",
          "只聊天" in steps and "⋯" in steps and "Ctrl+Alt+X" in steps)
    import task_status
    sentence = task_status.failure_sentence("文件解析", "编码不支持", True)
    check("失败句带原因和可重试",
          sentence == "文件解析失败：编码不支持。 可以重试。" or (
              "文件解析" in sentence and "编码不支持" in sentence and "可以重试" in sentence),
          sentence)
    stored = task_status.TaskRegistry()
    tid = stored.begin("file", "解析")
    stored.fail(tid, "编码不支持", retryable=True)
    check("账本失败原因仍是原文",
          stored.snapshot()["recent"][0]["detail"] == "编码不支持")

    import tool_audit
    d = tempfile.mkdtemp(prefix="audit271_")
    try:
        main_p = os.path.join(d, "tool_audit.jsonl")
        leg_p = os.path.join(d, "legion_tool_audit.jsonl")
        with open(main_p, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"ts": 2, "tool": "run_command", "origin": "main"}) + "\n")
            f.write("not-json\n")
        with open(leg_p, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"ts": 1, "tool": "write_file"}) + "\n")
        rows = tool_audit.merge_ledgers(main_p, leg_p)
        check("两本账按时间合并并补 origin",
              [r.get("tool") for r in rows] == ["write_file", "run_command"]
              and rows[0].get("origin") == "legion" and rows[1].get("origin") == "main",
              rows)
        check("合并不写盘",
              open(leg_p, encoding="utf-8").read().count("origin") == 0)
        check("核心字段含 origin", "origin" in tool_audit.CORE_FIELDS)
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    import release_check
    check("跳过 Windows 设备名",
          release_check._is_device_name("nul") and release_check._is_device_name("NUL.txt")
          and not release_check._is_device_name("readme.md"))


def test_agent_node_allowlist():
    print("\n=== AgentNode 调用点允许名单 ===")
    allow = {"agent.py", "agent_node.py", "legion_worker.py", "legion_ui.py"}
    found = set()
    skip_dirs = {"tests", "_perturb", "_dev_history", ".git", "dist", "build"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and d != "__pycache__"
                       and not release_check_device(d)]
        for fn in filenames:
            if not fn.endswith(".py") or release_check_device(fn):
                continue
            path = os.path.join(dirpath, fn)
            try:
                text = open(path, encoding="utf-8").read()
            except Exception:
                continue
            if "AgentNode(" in text:
                found.add(fn)
    extra = found - allow
    check("AgentNode 只出现在允许的源文件", not extra, sorted(extra))
    check("允许名单里的文件都还在调用", allow <= found, sorted(allow - found))


def release_check_device(name):
    base = os.path.splitext(str(name or ""))[0].lower()
    return base in {"nul", "con", "prn", "aux"}


def main():
    test_capability_and_skills()
    test_role_editor_keeps_tags()
    test_director_prompts_and_steps()
    test_settings_copy_audit_scan()
    test_agent_node_allowlist()
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
