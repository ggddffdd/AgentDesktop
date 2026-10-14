# -*- coding: utf-8 -*-
"""军团使用体验（v4.269.0）：少弹窗、组队一眼能批、跑团一句话进度。

行为级判据。信息/成功不再走 QMessageBox；表单错误落在控件上；
阵容缺角缺技能醒目；技术日志收进详情，失败能点开。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SBX = tempfile.mkdtemp(prefix="xc_sbx_ux269_")
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


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _plan_text():
    return json.dumps({
        "understanding": "做一条带货短视频",
        "name": "带货小队",
        "waves": [
            {"members": [{"name": "研究员", "why": "找竞品"},
                         {"name": "写手", "why": "写脚本"}]},
        ],
        "missing_roles": [{"name": "剪辑", "why": "库里没有"}],
        "missing_skills": [{"name": "seo", "for_role": "写手", "why": "没装"}],
    }, ensure_ascii=False)


def test_progress_sentence():
    from legion_feedback import progress_sentence
    nodes = {
        "w1_m1": {"role": "写手", "wave": 1, "status": "error"},
        "w1_m2": {"role": "研究员", "wave": 1, "status": "done"},
    }
    text = progress_sentence(nodes, 1, 3)
    check("进度·失败点名", "写手" in text and "未通过" in text and "1/3" in text, text)
    running = {
        "w2_m1": {"role": "研究员", "wave": 2, "status": "running"},
    }
    text2 = progress_sentence(running, 2, 3)
    check("进度·进行中", "研究员" in text2 and "进行中" in text2 and "2/3" in text2, text2)
    check("进度·没开工", progress_sentence({}, 0, 0) == "还没开工")
    gate = {"gate_w1": {"role": "验收", "wave": 1, "status": "await_approval"}}
    text3 = progress_sentence(gate, 1, 2)
    check("进度·等验收", "等你验收" in text3, text3)


def test_lineup_and_fold():
    from legion_feedback import team_lineup_brief, fold_log_line, failure_oneline
    brief = team_lineup_brief({
        "understanding": "做一条带货短视频",
        "waves": [{"members": [{"name": "研究员"}, {"name": "写手"}]}],
        "missing_roles": [{"name": "剪辑", "why": "没有"}],
        "missing_skills": [{"name": "seo", "for_role": "写手"}],
    })
    check("阵容·波次人名", "研究员" in brief["headline"] and "写手" in brief["headline"])
    check("阵容·缺角醒目", "剪辑" in brief["gap_line"] and brief["missing_roles"] == ["剪辑"])
    check("阵容·缺技能醒目", "seo" in brief["gap_line"] and "写手" in brief["gap_line"])
    quiet = team_lineup_brief({
        "waves": [{"members": [{"name": "写手"}]}],
        "missing_roles": [],
        "missing_skills": [],
    })
    check("阵容·齐", "没有缺角" in quiet["gap_line"] and not quiet["missing_roles"])
    tool_line = "  🧰 写手 本波工具集：run_command, write_file"
    fail_line = "  ⚠️ 成员 写手 执行失败（节点 failed）—— 本波交付不完整"
    check("日志·工具集折叠", fold_log_line(tool_line) is True)
    check("日志·失败不折叠", fold_log_line(fail_line) is False)
    check("日志·失败一句", "执行失败" in failure_oneline(fail_line) and "\n" not in failure_oneline(fail_line))


def test_status_strip_sentence():
    _app()
    from legion_status_widget import LegionStatusStrip
    s = LegionStatusStrip()
    s.set_total_waves(2)
    s.on_board("pid", "w1_m1", "写手", 1, "error")
    check("状态条·一句未通过",
          "未通过" in s._lbl_now.text() and "写手" in s._lbl_now.text(),
          s._lbl_now.text())
    s.note_log("  ⚠️ 成员 写手 执行失败（节点 failed）—— 本波交付不完整，需要看原因\n")
    s._detail_btn.click()
    check("状态条·失败可展开",
          "failed" in s.detail_text() and not s._detail.isHidden())
    # 旧计数口径仍在（不把技术计数删掉，只是不再当唯一反馈）
    check("状态条·异常计数仍在", "异常1" in s._lbl_members.text())
    s.deleteLater()


def test_chat_team_bar_and_log():
    _app()
    from legion_chat import LegionChatPanel
    p = LegionChatPanel()
    got = {}
    p.set_team_approver(lambda intent, text: got.update(intent=intent, text=text))
    p.on_team_plan(_plan_text(), [])
    check("组队条可见", not p.team_bar.isHidden())
    check("组队·缺角在条上", "剪辑" in p.team_gap.text(), p.team_gap.text())
    check("组队·缺技能在条上", "seo" in p.team_gap.text())
    check("方案长文不刷主时间线", "② 阵容编排" not in p.log.toPlainText())
    check("方案长文在详情", "阵容" in p.detail_text() or "研究员" in p.detail_text())
    p.btn_team_approve.click()
    check("一键批准", got.get("intent") == "pass")
    check("批准后收起", p.team_bar.isHidden())

    got.clear()
    p.on_team_plan(_plan_text(), [])
    p.team_advice.clear()
    p.btn_team_reject.click()
    check("空打回不送出", got.get("intent") != "reject")
    check("空打回有提示", "一句" in p.team_gap.text() or "不对" in p.team_gap.text(),
          p.team_gap.text())
    p.team_advice.setText("把写手换成审校")
    p.btn_team_reject.click()
    check("一句打回送出", got.get("intent") == "reject" and "审校" in (got.get("text") or ""))

    p.on_log("  🧰 写手 本波工具集：run_command, write_file\n")
    check("技术日志不进主时间线", "本波工具集" not in p.log.toPlainText())
    check("技术日志进详情", "本波工具集" in p.detail_text())
    p.on_log("  ⚠️ 成员 写手 执行失败（节点 failed）—— 本波交付不完整，日志很长" + ("。" * 40) + "\n")
    check("失败进主时间线", "执行失败" in p.log.toPlainText())
    check("失败原文在详情", "failed" in p.detail_text())
    check("主时间线没有被失败长文刷屏", ("。" * 12) not in p.log.toPlainText())
    p.deleteLater()


def test_quiet_forms():
    _app()
    import legion_ui
    from PySide6.QtWidgets import QMessageBox
    calls = []
    orig = QMessageBox.information
    QMessageBox.information = lambda *a, **k: calls.append(a)
    legion_ui.QMessageBox.information = QMessageBox.information
    try:
        dlg = legion_ui.TeamBuildDialog(None, {"id": "p", "name": "t"}, [], {})
        dlg.ed.setPlainText("短")
        dlg._build()
        check("短需求不弹窗", calls == [], str(calls))
        check("短需求写在状态上", "6" in dlg.status.text(), dlg.status.text())

        notes = []
        legion_ui._notice = lambda msg, kind="info", detail=None: notes.append((msg, kind))
        dlg.plan = {
            "name": "小队",
            "waves": [{"members": [{"name": "写手", "why": "", "skills": []}]}],
            "missing_roles": [],
            "missing_skills": [],
        }
        dlg.gap_list.clear()
        dlg.ed.setPlainText("做一条带货短视频脚本")
        dlg._approve()
        check("批准成功不弹信息框", calls == [], str(calls))
        check("批准成功走提示", any("入职" in (m or "") for m, _k in notes), str(notes))

        notes.clear()
        inst = legion_ui.SkillInstallDialog()
        inst.ed_slug.setText("seo-audit")
        inst._on_result("install", {"ok": True, "msg": "已写入技能库", "slug": "seo-audit"}, None)
        check("装好记下 slug", inst.installed_slug == "seo-audit")
        check("装好不弹信息框", calls == [], str(calls))
        check("装好走提示", bool(notes) and notes[-1][1] == "success", str(notes))

        editor = legion_ui.RoleEditor({"name": ""})
        editor._on_ok()
        check("缺角色名内联", "名字" in editor._err.text(), editor._err.text())
        from PySide6.QtWidgets import QDialog
        check("缺角色名不关闭", editor.result() != QDialog.DialogCode.Accepted)

        kept = legion_ui.RoleEditor({
            "name": "写手",
            "tools": ["write_file", "not_a_real_tool_zz"],
        })
        check("名单外工具有提示", "not_a_real_tool_zz" in kept._keep_note.text(),
              kept._keep_note.text())
        check("保存不丢名单外工具", "not_a_real_tool_zz" in kept.get_role()["tools"])
        bare = legion_ui.RoleEditor({"name": "甲"})
        check("没填模型质量时更多设定默认收起", bare._more.isChecked() is False)
        rich = legion_ui.RoleEditor({"name": "乙", "quality": "要有出处"})
        check("已有质量标准则展开更多", rich._more.isChecked() is True)
    finally:
        QMessageBox.information = orig
        legion_ui.QMessageBox.information = orig


if __name__ == "__main__":
    test_progress_sentence()
    test_lineup_and_fold()
    test_status_strip_sentence()
    test_chat_team_bar_and_log()
    test_quiet_forms()
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
