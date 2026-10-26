# -*- coding: utf-8 -*-
"""v4.289.3：第三波测试门禁与资源管理判据。

覆盖：release fail-closed、扰动护栏收尾、CI 全文件密钥扫描、子进程输出限额、
录音 busy 丢弃、数据库后验、技能白名单/全禁用、browser 页面绑定、
SessionStore 路径注入、PDF 依赖声明。
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

_SBX = tempfile.mkdtemp(prefix="xc_293_guards_")
os.environ["XC_USER_DATA_DIR"] = os.path.join(_SBX, "user")
os.environ["XC_LOG_DIR"] = os.path.join(_SBX, "logs")
os.environ["XC_LEGION_DIR"] = os.path.join(_SBX, "legion")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import browser_runner
import config
import database_tools
import release_check as rc
import session as session_mod
import skill_loader
import tool_contract as tc
import tools
import ui as UI
import voice

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def group(name, fn):
    print("\n" + name)
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        check(name + " 执行异常", False, "%s: %s" % (type(e).__name__, e))


def _release_failclosed():
    checks = []
    warns = []
    tmp = Path(_SBX) / "relroot"
    dist = tmp / "dist" / "小臭玩AI"
    dist.mkdir(parents=True)
    (dist / "小臭玩AI.exe").write_bytes(b"fake")
    (tmp / "config.py").write_text('APP_VERSION = "v9.9.9"\n', encoding="utf-8")
    (tmp / "README.md").write_text("当前版本 **v9.9.9**\n", encoding="utf-8")
    with mock.patch.object(rc, "ROOT", tmp), \
         mock.patch.object(rc, "_scan_secrets", return_value=[]), \
         mock.patch.object(rc, "_scan_pyz_secrets", return_value=(None, "PYZ损坏")), \
         mock.patch.object(rc, "_module_version_drift", return_value=[]), \
         mock.patch.object(rc, "_read_readme_version", return_value="v9.9.9"), \
         mock.patch.object(rc, "_read_exe_version", return_value=(None, "版本损坏")), \
         mock.patch.object(rc, "check",
                           side_effect=lambda n, ok, d="": checks.append((n, ok, d))), \
         mock.patch.object(rc, "warn", side_effect=lambda n, d="": warns.append((n, d))):
        rc.check_secrets(True)
        rc.check_version(True)
    pyz = [c for c in checks if "PYZ" in c[0]]
    ver = [c for c in checks if "exe 内版本" in c[0]]
    check("A1 PYZ 解析失败必须判红", pyz and pyz[-1][1] is False, pyz)
    check("A2 exe 版本读取失败必须判红", ver and ver[-1][1] is False, ver)


def _runall_guard():
    import tests.run_all as ra
    fn = getattr(ra, "guard_failure_row", None)
    check("B1 扰动收尾失败有统一失败行", fn is not None)
    if fn is not None:
        row = fn(1, "残留变异")
        check("B2 非零护栏结果生成 FAILED 行",
              row is not None and row[3] == "FAILED" and "残留" in row[5], row)
        check("B3 正常/不可用护栏不误伤", fn(0, "") is None and fn(None, "") is None)
    src = (ROOT / "tests" / "run_all.py").read_text(encoding="utf-8-sig")
    check("B4 后置护栏结果真被并入 p_rows",
          "_grow = guard_failure_row(_grc, _gout)" in src
          and "p_rows.append(_grow)" in src)


def _ci_all_files():
    src = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    check("C1 CI 不再只筛 .py",
          "git diff --name-only" in src and "-- '*.py'" not in src)
    check("C2 CI 传全部新增/修改/改名文件",
          "--diff-filter=ACMR" in src and "全部变更文件" in src)


def _bounded_subprocess():
    marker = "后续输出已丢弃"
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys;sys.stdout.write('x'*5000000)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err, outcome = tools._stream_collect(proc, 30)
    check("D1 5MB 输出读取完成", outcome == "done", outcome)
    check("D2 收集结果被 4MB 上限约束",
          len(out) <= tools._RAW_OUTPUT_HARD_LIMIT + 2048, len(out))
    check("D3 超限有明确丢弃标记",
          marker.encode("utf-8") in out, len(out))


def _recording_drop():
    class Rec:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    win = type("W", (), {})()
    win._busy = True
    win._recording = True
    win.rec_wav = "x.wav"
    win.recorder = Rec()
    win.talk_btn = type("B", (), {"setText": lambda s, t: None})()
    win.status_label = type("L", (), {"setText": lambda s, t: None})()
    UI.ChatWindow._stop_talk(win)
    check("E1 busy 丢弃录音时必须关闭采集资源", win.recorder.closed == 1,
          win.recorder.closed)
    check("E2 丢弃后录音状态复位",
          win._recording is False and win.rec_wav is None)


def _db_verifier():
    dbp = os.path.join(_SBX, "db", "probe.db")
    os.makedirs(os.path.dirname(dbp), exist_ok=True)
    old = getattr(tools, "db_tools", None)
    try:
        tools.db_tools = database_tools.DatabaseTools(dbp)
        r = tools.db_tools.insert("notes", {"title": "取证标题", "content": "内容"})
        rid = (r.get("data") or {}).get("id") if isinstance(r, dict) else None
        if rid is None:
            rid = r.get("id")
        check("F1 测试记录插入成功", rid is not None, r)
        v1 = tc.verify_after(
            "db_insert", {"table": "notes", "data": {"title": "取证标题"}}, None)
        check("F2 db_insert 后验真正能连到库并表态",
              v1 is not None and v1[0] is True, v1)
        v2 = tc.verify_after(
            "db_update", {"table": "notes", "record_id": rid}, None)
        check("F3 db_update 后验可回查记录", v2 is not None and v2[0] is True, v2)
        tools.db_tools.delete("notes", rid)
        v3 = tc.verify_after(
            "db_delete", {"table": "notes", "record_id": rid}, None)
        check("F4 db_delete 后验确认已删除", v3 is not None and v3[0] is True, v3)
    finally:
        tools.db_tools = old


def _skill_filter():
    sd = Path(_SBX) / "skill"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("# 测试技能\n说明", encoding="utf-8")
    cfgp = Path(_SBX) / "config.json"
    with mock.patch.object(config, "get_skill_scan_dirs", return_value=[str(sd)]), \
         mock.patch.object(skill_loader, "get_available_skills",
                           return_value=[{"name": "测试技能", "description": "说明"}]):
        cfgp.write_text(json.dumps({"enabled_skills": ["不存在的"]}), encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", str(cfgp)):
            txt = config.load_dynamic_skills(compact=False)
        check("G1 白名单无匹配时结果为空（不回落全部）", "测试技能" not in txt, txt[:120])
        cfgp.write_text(json.dumps({"skills_disabled_all": True}), encoding="utf-8")
        with mock.patch.object(config, "CONFIG_PATH", str(cfgp)):
            txt2 = config.load_dynamic_skills(compact=False)
        check("G2 显式全禁用对清单生效", "测试技能" not in txt2, txt2[:120])


def _browser_page_binding():
    check("H1 URL 规范化函数存在", hasattr(browser_runner, "_norm_url")
          and hasattr(browser_runner, "_same_url")
          and hasattr(browser_runner, "_pick_page"))
    if not hasattr(browser_runner, "_norm_url"):
        return
    check("H2 尾斜杠/片段视为同一页",
          browser_runner._same_url("https://A.com/x/#f", "https://a.com/x"))
    check("H3 前缀相似但不同页不得误判",
          not browser_runner._same_url("https://a.com/x", "https://a.com/xyz"))
    class P:
        def __init__(self, u):
            self.url = u
    pages = [P("https://a.com/home"), P("https://a.com/x")]
    picked = browser_runner._pick_page(pages, "https://a.com/x/")
    check("H4 多标签页选择精确匹配而不是永远第 0 个",
          picked is pages[1], getattr(picked, "url", None))


def _session_path():
    d = Path(_SBX) / "sess"
    s = session_mod.SessionStore(path=str(d / "sessions.json"))
    check("I1 SessionStore 支持显式路径注入", str(s.PATH).startswith(str(d)))
    check("I2 会话文件落在注入目录", (d / "sessions.json").is_file())
    check("I3 默认路径来自统一用户数据目录",
          os.path.normcase(str(session_mod.SessionStore.PATH)).startswith(
              os.path.normcase(config.USER_DATA_DIR)))


def _requirements():
    txt = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    check("J1 PDF 后端 pdfplumber 已锁版本声明", "pdfplumber==" in txt)


def main():
    try:
        group("[A] 发布门禁 fail-closed", _release_failclosed)
        group("[B] 扰动护栏收尾", _runall_guard)
        group("[C] CI 密钥扫描覆盖", _ci_all_files)
        group("[D] 子进程输出有界", _bounded_subprocess)
        group("[E] 录音资源释放", _recording_drop)
        group("[F] 数据库后验", _db_verifier)
        group("[G] 技能白名单", _skill_filter)
        group("[H] 浏览器标签页绑定", _browser_page_binding)
        group("[I] 会话目录", _session_path)
        group("[J] 依赖声明", _requirements)
    finally:
        shutil.rmtree(_SBX, ignore_errors=True)
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
