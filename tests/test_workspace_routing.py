# -*- coding: utf-8 -*-
"""工作区归口回归测试

纯标准库（config / tools 只依赖标准库 + PySide6 不在本链路上）：
    python tests/test_workspace_routing.py
退出码 0=全过，1=有失败。

背景（为什么值得钉住）
----------------------
`APP_DIR` 就是 exe 所在目录，而它**同时是分发源（dist）**。历史上有十几 MB 运行数据
（incoming / output(s) / notes / pages / temp / multi_platform / rag_data / orchestrate /
avatars / log / director_session.json / debug.log）全落在那里，**打包分发时会连同
用户内容一起发出去**；更严重的是曾把 1.2GB 浏览器 profile（51 条已存登录）打包分发。

修复后这些「用户数据类」路径统一走 `config.WORKSPACE_DIR`（默认 =
`~/Documents/小臭玩AI`，与 `USER_DATA_DIR` 同值）。

本测试钉住三件事：
1. 工作区路径确实不在 APP_DIR 之内；
2. Agent 的文件工具「相对路径」以 WORKSPACE_DIR 为基准，且越界写入被拒绝；
3. 源码里不再出现「用户数据类 + APP_DIR」的组合，而**资源类（skills/icon）仍必须留在 APP_DIR**。
"""
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# 本套件要验的是「**默认**路由」—— app_log_path() 在没有任何 XC_* 改道时落 WORKSPACE_DIR。
# 而回归入口 run_all 会给子进程注入 XC_LOG_DIR（防套件日志污染用户真实 debug.log，
# 见 run_all 顶部注释）。不 pop 掉的话，下面那条断言测的就成了「改道后」而非「默认」。
os.environ.pop("XC_LOG_DIR", None)

import config
import tools

PASS = 0
FAIL = 0

# 用户数据类目录名（不允许再与 APP_DIR 组合）
USER_DATA_DIRS = ["incoming", "output", "outputs", "notes", "temp", "multi_platform",
                  "pages", "rag_data", "orchestrate", "avatars", "log", "gen"]


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def test_config_paths():
    print("== 工作区路径定义 ==")
    ws = config.WORKSPACE_DIR
    app = config.APP_DIR
    check("WORKSPACE_DIR 存在", bool(ws), str(ws))
    check("WORKSPACE_DIR 是绝对路径", os.path.isabs(ws), ws)
    check("★ WORKSPACE_DIR 不在 APP_DIR 之内",
          not os.path.abspath(ws).startswith(os.path.abspath(app) + os.sep), f"{ws} vs {app}")
    check("WORKSPACE_DIR 默认与 USER_DATA_DIR 同值（同一份用户目录）",
          os.path.normcase(ws) == os.path.normcase(config.USER_DATA_DIR),
          f"{ws} != {config.USER_DATA_DIR}")
    check("PRODUCTS_DIR 落在工作区之内（产物也应可见于用户目录）",
          os.path.abspath(config.PRODUCTS_DIR).startswith(os.path.abspath(ws) + os.sep),
          config.PRODUCTS_DIR)

    # 日志是运行数据，必须跟着工作区走（这里也是曾经踩 NameError 的点）
    lp = config.app_log_path()
    check("★ 日志落 WORKSPACE_DIR，不落 APP_DIR",
          os.path.abspath(lp).startswith(os.path.abspath(ws) + os.sep)
          and not os.path.abspath(lp).startswith(os.path.abspath(app) + os.sep), lp)

    check("workspace_path 拼接正确",
          config.workspace_path("a", "b") == os.path.join(ws, "a", "b"))
    check("is_under_app_dir 判定正确",
          config.is_under_app_dir(os.path.join(app, "x")) is True
          and config.is_under_app_dir(os.path.join(ws, "x")) is False)


def test_tool_roots():
    print("== 文件工具根目录 ==")
    roots = tools._tool_roots(config.APP_DIR)
    check("含 WORKSPACE_DIR", os.path.normcase(config.WORKSPACE_DIR) in
          [os.path.normcase(r) for r in roots], str(roots))
    check("含 APP_DIR（历史绝对路径兼容）", os.path.normcase(config.APP_DIR) in
          [os.path.normcase(r) for r in roots], str(roots))
    check("无重复", len(roots) == len({os.path.normcase(r) for r in roots}), str(roots))
    check("_within_roots 判定正确",
          tools._within_roots(os.path.join(config.WORKSPACE_DIR, "a.txt"), roots) is not None
          and tools._within_roots(os.path.join(tempfile.gettempdir(), "x", "a.txt"),
                                  roots) is None)


def test_relative_path_behavior():
    print("== 相对路径以工作区为基准（真实读写）==")
    tmp_ws = tempfile.mkdtemp(prefix="wsroute_")
    orig = tools.WORKSPACE_DIR
    tools.WORKSPACE_DIR = tmp_ws          # 只改本模块的全局，不碰真实工作区
    try:
        app = config.APP_DIR
        r = tools.tool_write_file(app, os.path.join("sub", "note.txt"), "hello-workspace")
        check("相对路径写入成功", r.startswith("已写入"), r)

        target = os.path.join(tmp_ws, "sub", "note.txt")
        check("★ 文件真的落在 WORKSPACE_DIR 下（不在 APP_DIR）", os.path.isfile(target), target)
        check("★ 没有在 APP_DIR 下生成同名文件",
              not os.path.isfile(os.path.join(app, "sub", "note.txt")),
              os.path.join(app, "sub", "note.txt"))

        got = tools.tool_read_file(app, os.path.join("sub", "note.txt"))
        check("相对路径可读回（读写基准一致）", "hello-workspace" in got, got[:80])

        # 越界写入必须被拒
        bad = tools.tool_write_file(app, os.path.join("..", "..", "..", "evil.txt"), "x")
        check("★ 越界写入被拒", bad.startswith("拒绝"), bad[:80])
        bad2 = tools.tool_write_file(app, os.path.join(tempfile.gettempdir(), "evil2.txt"), "x")
        check("★ 写到根目录之外被拒", bad2.startswith("拒绝"), bad2[:80])

        # 只给文件名时，应从 incoming/ 找（incoming 现在也在工作区）
        inc = os.path.join(tmp_ws, "incoming")
        os.makedirs(inc, exist_ok=True)
        with open(os.path.join(inc, "photo.txt"), "w", encoding="utf-8") as f:
            f.write("from-incoming")
        got2 = tools.tool_read_file(app, "photo.txt")
        check("只给文件名时能在 incoming/ 命中", "from-incoming" in got2, got2[:80])
    finally:
        tools.WORKSPACE_DIR = orig
        import shutil
        shutil.rmtree(tmp_ws, ignore_errors=True)


def test_source_has_no_user_data_app_dir():
    print("== 源码静态守卫 ==")
    files = ["ui.py", "tools.py", "config.py", "director_panel.py",
             "browser_control_tools.py", "digital_twin_panel.py"]
    pat = re.compile(
        r'os\.path\.join\(\s*APP_DIR\s*,\s*["\'](?:%s)["\']' % "|".join(USER_DATA_DIRS))
    offenders = []
    for fn in files:
        src = (HERE / fn).read_text(encoding="utf-8")
        for m in pat.finditer(src):
            # digital_twin_panel 的 _migrate_legacy_avatars 是**刻意的遗留迁移源**，豁免
            line = src[:m.start()].count("\n") + 1
            if fn == "digital_twin_panel.py" and "legacy" in src.splitlines()[line - 1]:
                continue
            offenders.append(f"{fn}:{line}")
    check("★ 源码中无「用户数据类 + APP_DIR」组合", not offenders, ", ".join(offenders))

    tsrc = (HERE / "tools.py").read_text(encoding="utf-8")
    check("★ tools.py 中无 cwd=app_dir（Agent 不再以 dist 为工作目录）",
          "cwd=app_dir" not in tsrc, "仍存在")
    check("tool_run_command 用 WORKSPACE_DIR 作 cwd", "cwd=_ws" in tsrc)

    # 资源类必须仍锚在 APP_DIR，否则打包后技能/图标会找不到
    csrc = (HERE / "config.py").read_text(encoding="utf-8")
    check("资源类 ICON_PATH 仍在 APP_DIR", 'ICON_PATH = os.path.join(APP_DIR, "icon.ico")' in csrc)
    check("内置技能兜底仍在 APP_DIR（资源类，未误改）",
          'os.path.join(app_dir, "skills")' in tsrc)

    # WORKSPACE_DIR 只能定义一次（曾因定义在后、日志初始化在前而 NameError）
    check("config.WORKSPACE_DIR 只定义一次",
          len(re.findall(r"^WORKSPACE_DIR\s*=", csrc, re.M)) == 1,
          str(len(re.findall(r"^WORKSPACE_DIR\s*=", csrc, re.M))))
    idx_def = csrc.index("WORKSPACE_DIR = os.path.expanduser")
    idx_use = csrc.index("def app_log_path")
    check("★ WORKSPACE_DIR 定义早于 app_log_path（防 NameError）",
          idx_def < idx_use, f"def@{idx_def} use@{idx_use}")


if __name__ == "__main__":
    try:
        test_config_paths()
        test_tool_roots()
        test_relative_path_behavior()
        test_source_has_no_user_data_app_dir()
    except Exception:
        import traceback
        FAIL += 1
        traceback.print_exc()
    print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
