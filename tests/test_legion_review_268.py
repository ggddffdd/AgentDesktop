# -*- coding: utf-8 -*-
"""v4.268.0 军团审阅高优先级：授权时机 / 多技能全文 / 组队展示与精确匹配。

行为级判据（先红后绿）。不依赖真实军团目录。
"""
import ast
import logging
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_SBX = tempfile.mkdtemp(prefix="xc_legion_268_")
os.environ["XC_LEGION_DIR"] = _SBX
os.environ.setdefault("XC_USER_DATA_DIR", os.path.join(_SBX, "userdata"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import legion  # noqa: E402
import legion_permissions as lp  # noqa: E402

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [PASS] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + ((" —— " + detail) if detail else ""))


def _write_skill(root, slug, name, body, requires="", description=""):
    d = os.path.join(root, slug)
    os.makedirs(d, exist_ok=True)
    fm = ["---", "name: %s" % name, "description: %s" % (description or name)]
    if requires:
        fm.append("requires_tools: %s" % requires)
    fm.append("---")
    fm.append(body)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(fm) + "\n")


def _func_node(path, class_name, func_name):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == func_name:
                    return item
        if class_name is None and isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    return None


def _call_lines(fn, attr):
    lines = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            func = n.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name == attr:
                lines.append(n.lineno)
    return lines


def test_grant_before_dispatch():
    print("\n=== 1) human 本波派发前授予 exec，高危仍二次确认 ===")
    tmp = tempfile.mkdtemp(prefix="legion_grant268_")
    ws = os.path.join(tmp, "ws")
    os.makedirs(ws, exist_ok=True)
    a = lp.LegionPermissionAdapter(
        run_id="r", project_id="p", project_name="t",
        scope_paths=[ws], audit_dir=tmp, gate_mode="human")
    d = a.check("run_command", {"command": "echo hi"}, wave=2)
    check("未授予时 run_command 被 needs_auth 挡住",
          (not d.allowed) and d.rule == "needs_auth" and d.needs_auth and not d.needs_reconfirm,
          d.rule)
    fresh = lp.grant_for_wave_dispatch(a, 2, "human")
    check("human 派发前授予本波（不是 grant_all）", fresh is True)
    check("只授了第 2 波，没有星号全局",
          a.is_granted("exec", 2) and not a.is_granted("exec", 3)
          and "*" not in a.granted_waves(),
          str(a.state().get("granted")))
    d = a.check("run_command", {"command": "echo hi"}, wave=2)
    check("授予后普通 run_command 放行", d.allowed and d.rule == "grant", d.rule)
    d = a.check("run_python", {"code": "print(1)"}, wave=2)
    check("授予后普通 run_python 放行", d.allowed and d.rule == "grant", d.rule)
    d = a.check("run_command", {"command": "rm -rf ./build"}, wave=2)
    check("高危命令仍要 command_danger_level 二次确认",
          (not d.allowed) and d.rule == "high_risk_exec" and d.needs_reconfirm,
          "%s %s" % (d.rule, d.reason))
    d = a.check("db_delete", {"table": "notes"}, wave=2)
    check("ALWAYS_CONFIRM 不因本波授予而免确认",
          (not d.allowed) and d.rule == "always_confirm" and d.needs_reconfirm,
          d.rule)
    d = a.check("run_command", {"command": "echo hi"}, wave=3)
    check("下一波仍未授予", (not d.allowed) and d.rule == "needs_auth", d.rule)
    again = lp.grant_for_wave_dispatch(a, 2, "human")
    check("同一波重复派发不改成另一套授权", again is False)
    b = lp.LegionPermissionAdapter(
        run_id="r2", project_id="p", project_name="t",
        scope_paths=[ws], audit_dir=tmp, gate_mode="off")
    check("非 human 不走这条逐波预授（避免改成第二套全局策略）",
          lp.grant_for_wave_dispatch(b, 1, "off") is False)
    check("off 适配器并未因此拿到 exec", not b.is_granted("exec", 1))

    worker = os.path.join(ROOT, "legion_worker.py")
    fn = _func_node(worker, "LegionWorker", "_run_wave")
    grant_lines = _call_lines(fn, "_grant_wave_before_dispatch")
    run_lines = _call_lines(fn, "run")
    create_lines = _call_lines(fn, "create")
    check("_run_wave 在派发前调用逐波授予", bool(grant_lines), str(grant_lines))
    if grant_lines and run_lines:
        check("授予调用早于 tg.run", min(grant_lines) < min(run_lines),
              "grant=%s run=%s" % (grant_lines, run_lines))
    else:
        check("授予调用早于 tg.run", False, "grant=%s run=%s" % (grant_lines, run_lines))
    if grant_lines and create_lines:
        check("授予调用早于 tg.create", min(grant_lines) < min(create_lines),
              "grant=%s create=%s" % (grant_lines, create_lines))
    else:
        check("授予调用早于 tg.create", False, "grant=%s create=%s" % (grant_lines, create_lines))


def test_multi_skill_full_body():
    print("\n=== 2) 技能数 >3 时关键技能走 skill_full_body_block ===")
    root = tempfile.mkdtemp(prefix="skills268_")
    _write_skill(root, "script-exec", "脚本执行",
                 "UNIQUE_BODY_EXEC 先写脚本再调用 run_command。",
                 requires="run_command, run_python",
                 description="跑命令")
    _write_skill(root, "xhs-topic", "小红书选题",
                 "UNIQUE_BODY_TOPIC 先列十个标题再筛。",
                 description="选题方法")
    _write_skill(root, "other-a", "无关甲",
                 "UNIQUE_BODY_A 不该进全文。", description="别的")
    _write_skill(root, "other-b", "无关乙",
                 "UNIQUE_BODY_B 不该进全文。", description="别的")
    role = {
        "name": "写手",
        "skills": ["script-exec", "xhs-topic", "other-a", "other-b"],
        "tools": ["run_command"],
    }
    plan = legion.skill_disclosure_plan(role, task="做小红书选题", skills_dir=root)
    check("披露计划含 requires_tools 技能", "script-exec" in plan["full"], str(plan))
    check("披露计划含任务命中技能", "xhs-topic" in plan["full"], str(plan))
    check("未命中技能留在摘要",
          "other-a" in plan["summary"] and "other-b" in plan["summary"], str(plan))
    called = []
    real = legion.skill_full_body_block

    def _wrap(role_, slug, skills_dir=None):
        called.append(slug)
        return real(role_, slug, skills_dir)

    legion.skill_full_body_block = _wrap
    logs = []

    class _H(logging.Handler):
        def emit(self, record):
            logs.append(record.getMessage())

    lg = logging.getLogger("legion")
    h = _H()
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    try:
        prompt = legion.build_role_prompt(role, skills_dir=root, task="做小红书选题")
    finally:
        legion.skill_full_body_block = real
        lg.removeHandler(h)
    check("确实调用了 skill_full_body_block",
          set(called) >= {"script-exec", "xhs-topic"}, str(called))
    check("全文方法论进了 prompt",
          "UNIQUE_BODY_EXEC" in prompt and "UNIQUE_BODY_TOPIC" in prompt)
    check("未命中技能正文不进 prompt",
          "UNIQUE_BODY_A" not in prompt and "UNIQUE_BODY_B" not in prompt,
          prompt[-400:])
    check("日志说明全文与摘要",
          any(("全文" in m and "摘要" in m) for m in logs), " | ".join(logs)[:300])
    check("未审技能附 skill_meta 提示（军团不走拒用闸）",
          "技能来源提示" in prompt or "未经审计" in prompt)

    # ≤3 仍全文，且不依赖「命中」
    role3 = {"name": "写手", "skills": ["other-a", "other-b", "xhs-topic"]}
    p3 = legion.build_role_prompt(role3, skills_dir=root, task="完全无关的任务")
    check("技能数 ≤3 仍注入全文",
          "UNIQUE_BODY_A" in p3 and "UNIQUE_BODY_B" in p3 and "UNIQUE_BODY_TOPIC" in p3)

    # 一个都不命中时至少保住一条方法论
    role_fb = {
        "name": "写手",
        "skills": ["other-a", "other-b", "xhs-topic", "script-exec"],
    }
    # script-exec 有 requires_tools，会命中。换四个都没有 requires、任务也不命中。
    _write_skill(root, "plain-1", "普通一", "UNIQUE_FB_1 方法论一。", description="一")
    _write_skill(root, "plain-2", "普通二", "UNIQUE_FB_2 方法论二。", description="二")
    _write_skill(root, "plain-3", "普通三", "UNIQUE_FB_3 方法论三。", description="三")
    _write_skill(root, "plain-4", "普通四", "UNIQUE_FB_4 方法论四。", description="四")
    role_fb = {"name": "写手", "skills": ["plain-1", "plain-2", "plain-3", "plain-4"]}
    plan_fb = legion.skill_disclosure_plan(role_fb, task="没有对应技能的杂务", skills_dir=root)
    check("全不命中时至少一条全文", len(plan_fb["full"]) >= 1, str(plan_fb))
    pfb = legion.build_role_prompt(role_fb, skills_dir=root, task="没有对应技能的杂务")
    check("兜底全文进了 prompt", "UNIQUE_FB_1" in pfb)


def test_team_plan_gaps_and_match():
    print("\n=== 3) 缺角展示 / 精确匹配 / 空波报告 / 空草稿 ===")
    plan = {
        "understanding": "先取证再写",
        "name": "小队",
        "waves": [{"members": [
            {"name": "写手", "why": "成稿", "skills": []},
        ]}],
        "reason": "就这些",
        "missing_roles": [{"name": "合规官", "why": "过审"}],
        "missing_skills": [{"name": "kalodata", "for_role": "写手", "why": "取数"}],
    }
    text = legion.format_team_plan(plan, [{"name": "写手", "emoji": "✍️", "tools": ["write_file"]}])
    check("批准前能看见缺角", "合规官" in text and "缺角" in text)
    check("批准前能看见缺技能", "kalodata" in text and "缺技能" in text)
    check("理解仍在阵容之前", text.find("理解") < text.find("阵容"))

    lib = [
        {"name": "写手", "emoji": "✍️", "tools": ["write_file"], "skills": [],
         "aliases": ["文案"]},
        {"name": "短视频写手", "emoji": "🎬", "tools": [], "skills": []},
        {"name": "竞品分析师", "emoji": "🔎", "tools": [], "skills": []},
        {"name": "数据分析师", "emoji": "📊", "tools": [], "skills": []},
    ]
    hit = legion.match_role_by_name("写手", lib)
    check("精确名命中写手", hit and hit.get("name") == "写手")
    hit = legion.match_role_by_name("文案", lib)
    check("别名精确命中写手", hit and hit.get("name") == "写手")
    hit = legion.match_role_by_name("✍️写手", lib)
    check("去掉 emoji 后精确命中", hit and hit.get("name") == "写手")
    bad = legion.match_role_by_name("分析", lib)
    check("子串不再把「分析」配到分析师", bad is None, str(bad))
    bad = legion.match_role_by_name("视频", lib)
    check("子串不再把「视频」配到短视频写手", bad is None, str(bad))

    proj = legion.new_project(name="草稿")
    plan2 = {
        "name": "落地小队",
        "waves": [
            {"members": [{"name": "分析", "why": "", "skills": []},
                         {"name": "不存在的角色", "why": "", "skills": []}]},
            {"members": [{"name": "写手", "why": "成稿", "skills": []}]},
        ],
        "missing_roles": [],
        "missing_skills": [],
    }
    proj, rep = legion.apply_team_plan(proj, plan2, lib)
    names = []
    for w in proj.get("waves") or []:
        names.append([m.get("name") for m in w.get("members") or []])
    check("模糊子串没有把分析师塞进第一波", names == [["写手"]], str(names))
    check("整波被抽空的报告写明删除并前移",
          ("整波" in rep) and ("前移" in rep), rep)

    data = {"projects": [], "role_library": list(lib)}
    draft = legion.new_project(name="新军团")
    data["projects"].append(draft)
    projects, removed = legion.drop_empty_team_draft(data["projects"], draft)
    check("解析失败清掉还没有成员的草稿项目", removed and draft not in projects)
    data["projects"] = projects
    kept = legion.new_project(name="已有人")
    kept["waves"] = [{"members": [{"name": "写手"}]}]
    data["projects"].append(kept)
    projects, removed = legion.drop_empty_team_draft(data["projects"], kept)
    check("已经有成员的项目不会被清掉", (not removed) and kept in projects)

    plan3 = {
        "name": "补角小队",
        "waves": [{"members": [
            {"name": "写手", "why": "", "skills": []},
            {"name": "合规官", "why": "过审", "skills": []},
        ]}],
        "missing_roles": [{
            "name": "合规官",
            "why": "过审",
            "draft": {"mission": "核对平台规则。", "emoji": "⚖️"},
        }],
        "missing_skills": [],
    }
    proj3 = legion.new_project(name="待批")
    data3 = {"role_library": [dict(lib[0])], "projects": [proj3]}
    proj3, rep3 = legion.approve_team_plan(data3, proj3, plan3, adopt_missing=True)
    got = [m.get("name") for w in proj3.get("waves") or [] for m in w.get("members") or []]
    lib_names = [r.get("name") for r in data3["role_library"]]
    check("对话批准先采纳缺角再落地", "合规官" in got and "合规官" in lib_names,
          "waves=%s lib=%s rep=%s" % (got, lib_names, rep3[:180]))


def test_role_editor_tools():
    print("\n=== 4) RoleEditor 工具列表对齐 tool_pool，保存保留列表外工具 ===")
    pool = legion.tool_pool()
    names = legion.role_editor_tool_names(pool)
    pool_names = [n for n, _d, _c in pool if n and not str(n).startswith("legion_")]
    check("编辑器列表覆盖 tool_pool 全量（不含 legion_）",
          set(pool_names) <= set(names) and "run_command" in names,
          "pool=%d listed=%d" % (len(pool_names), len(names)))
    check("列表本身不含 legion_ 调度工具",
          all(not n.startswith("legion_") for n in names))
    merged = legion.merge_saved_role_tools(
        ["run_command", "write_file"],
        ["run_command", "legion_list_outputs", "write_file"],
        names)
    check("列表没有的原工具被保留", "legion_list_outputs" in merged, str(merged))
    check("用户取消勾选的列表内工具不复活",
          "web_search" not in legion.merge_saved_role_tools(
              ["run_command"], ["web_search", "run_command"], names))

    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from legion_ui import RoleEditor
    ed = RoleEditor({
        "name": "研究员",
        "tools": ["run_command", "legion_list_outputs"],
        "skills": [],
    })
    listed = [ed.e_tools.item(i).text() for i in range(ed.e_tools.count())]
    selected = [i.text() for i in ed.e_tools.selectedItems()]
    check("界面列出 run_command 且原卡勾上",
          "run_command" in listed and "run_command" in selected, str(selected))
    role = ed.get_role()
    check("保存后 run_command 还在", "run_command" in (role.get("tools") or []))
    check("保存后列表外的 legion_list_outputs 还在",
          "legion_list_outputs" in (role.get("tools") or []), str(role.get("tools")))


def test_dead_capability_and_audit_wording():
    print("\n=== 5) 停喂能力死链；审计「拦截」与行为一致 ===")
    worker = open(os.path.join(ROOT, "legion_worker.py"), encoding="utf-8").read()
    check("开工提示不再调用 team_capability_for",
          "team_capability_for(" not in worker)
    # 函数还在，避免外部引用直接炸；只是不再喂给 PM
    check("team_capability_for 仍可调用（停喂不是删库）",
          callable(getattr(legion, "team_capability_for", None)))
    audit_src = legion._capability_audit_report_text(
        "写一篇", {"coverage_pct": 0.2},
        [{"cap": "浏览器取证", "need_tools": ["browser_open"], "need_skills": []}],
        [], [], 0.95, False)
    check("Critical 文案不再写成程序会拦住开工",
          "不许开工" not in audit_src and "不自动开工" not in audit_src,
          audit_src)
    check("文案写明高成本波会因此暂停",
          "高成本波会因此暂停" in audit_src, audit_src)
    check("高成本波日志不再把提示写成拦截",
          "能力审计：⛔ 拦截" not in worker)


def main():
    test_grant_before_dispatch()
    test_multi_skill_full_body()
    test_team_plan_gaps_and_match()
    test_role_editor_tools()
    test_dead_capability_and_audit_wording()
    print("\nPASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
