# -*- coding: utf-8 -*-
"""回归期日志改道（#2 工程卫生 ⑧）—— 回归判据

背景
----
套件子进程里 `config.app_log_path()` 缺省落 `WORKSPACE_DIR/debug.log`，而那**是用户的
真实诊断日志**。实测 2026-10-04：该文件单日 1032 条 WARNING 里 828 条（80%）出自判据
套件（empty_state 桩按钮 414 / permissions 白名单用例 372 / system_control 模拟
RuntimeError 116 / tool_audit 失败用例 99 / legion_chat 的 Temp\\lc_probe_* 22），
且全部集中在回归窗口 14:00–17:10。测试噪音灌进真实日志 = 把线索池搅浑、「今天有多少
告警」这类读数失真。

修复 = `tests/run_all.py` 的 `run_one` 给子进程 env 注入 `XC_LOG_DIR`（临时目录）；
`tests/test_workspace_routing.py` 因要验「默认路由」而自行 pop 掉该变量。

本套件自己也要 pop（见下方模块级那行）：它验的是「run_one **自己**会不会注入」，
是**源码行为**；带着容器注入的 `XC_LOG_DIR` 跑，`setdefault` 永不生效 → D 组哑掉
（扰动 PH1/PH2 实测就栽在这里 —— 拆掉改道后 D2 仍绿，两条假命中清零）。

本套件钉四层
------------
A 源码契约：注入代码还在、`TEST_LOG_DIR` 仍由 `tempfile.gettempdir()` 派生、
  `test_workspace_routing` 的 pop 仍在 `import config` **之前**。
B config 行为：`app_log_path()` 确实尊重 `XC_LOG_DIR`（含「空串 = 回落默认」边界）。
C 注入行为：`run_one` 传给子进程的 env 含 `XC_LOG_DIR`；外部已设时**不覆盖**。
D 端到端：真跑一次探针 —— 探针的 WARNING 只落临时日志、真实日志零命中。

用法：python tests/test_log_redirect_hyg.py
"""
import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUN_ALL = HERE / "run_all.py"
WR = HERE / "test_workspace_routing.py"
PY = sys.executable

# 本套件验的是「run_all.run_one **自己**会不会注入 XC_LOG_DIR」——那是**源码行为**。
# 但 run_all 会给每个套件子进程注入该变量（正是被验的那个机制），于是不清掉它就会：
#   · 作为套件跑时：C 组测到的成了「外部已设」而非「run_one 自己注入」；D 组的探针
#     被外部值改道，看不见 run_one 的行为。
#   · 作为扰动目标跑时（`_perturb_log_redirect_hyg`）：更致命 —— PH1 删掉注入行、
#     PH2 把 TEST_LOG_DIR 改成真实目录，D2「真实日志零新增」都**仍然绿** → 两条哑弹。
# 故与 `test_workspace_routing` 同理：先 pop，把「默认环境」还原出来再验。
os.environ.pop("XC_LOG_DIR", None)

PASS = 0
FAIL = 0

PROBE_MARKER = "HYG-PROBE-MARKER"
USER_DOCS = Path(os.path.expanduser("~")) / "Documents" / "小臭玩AI"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS]", name)
    else:
        FAIL += 1
        print("  [FAIL]", name, ("| " + str(extra)) if extra else "")


# ---------------- A 源码契约 ----------------
def group_A():
    src = RUN_ALL.read_text(encoding="utf-8")
    tree = ast.parse(src)

    fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_one"]
    check("A1 run_all 里 run_one 函数唯一", len(fns) == 1, "找到 %d 个" % len(fns))
    seg = ast.get_source_segment(src, fns[0]) if fns else ""
    check("A2 run_one 注入 XC_LOG_DIR（setdefault）",
          'setdefault("XC_LOG_DIR"' in seg or "setdefault('XC_LOG_DIR'" in seg)
    check("A3 run_one 仍设 QT_QPA_PLATFORM=offscreen",
          'env["QT_QPA_PLATFORM"] = "offscreen"' in seg)

    assigns = [n for n in tree.body
               if isinstance(n, ast.Assign)
               and any(getattr(t, "id", None) == "TEST_LOG_DIR" for t in n.targets)]
    check("A4 模块级 TEST_LOG_DIR 唯一", len(assigns) == 1, "找到 %d 个" % len(assigns))
    if assigns:
        val = ast.unparse(assigns[0].value)
        check("A5 TEST_LOG_DIR 落在临时目录（tempfile.gettempdir）",
              "tempfile.gettempdir" in val, val)
    imports = [n for n in tree.body if isinstance(n, ast.Import)]
    names = {a.name for n in imports for a in n.names}
    check("A6 顶层 import tempfile", "tempfile" in names, str(sorted(names)))

    wsrc = WR.read_text(encoding="utf-8")
    i_pop = wsrc.find('os.environ.pop("XC_LOG_DIR"')
    if i_pop < 0:
        i_pop = wsrc.find("os.environ.pop('XC_LOG_DIR'")
    i_cfg = wsrc.find("\nimport config")
    check("A7 test_workspace_routing 有 pop XC_LOG_DIR", i_pop >= 0)
    check("A8 该 pop 位于 import config 之前（否则测的就不是默认路由）",
          0 <= i_pop < i_cfg, "pop@%d import@%d" % (i_pop, i_cfg))


# ---------------- B config 行为 ----------------
def _ws_and_log(extra_env, drop=()):
    """起子进程 import config，返回 (WORKSPACE_DIR, app_log_path())（config 纯标准库）。"""
    code = ("import sys; sys.path.insert(0, r'%s')\n"
            "import config\n"
            "print(config.WORKSPACE_DIR)\n"
            "print(config.app_log_path())\n" % ROOT)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    for k in drop:
        env.pop(k, None)
    env.update(extra_env)
    p = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                       env=env, cwd=str(ROOT), timeout=120)
    lines = [l for l in (p.stdout or "").splitlines() if l.strip()]
    if len(lines) < 2:
        return "", ""
    return lines[-2], lines[-1]


def group_B():
    tmp = os.path.join(tempfile.gettempdir(), "dsb_hyg_b_probe")
    _, got = _ws_and_log({"XC_LOG_DIR": tmp})
    check("B1 设 XC_LOG_DIR 时 app_log_path 落该目录",
          os.path.normcase(os.path.dirname(got)) == os.path.normcase(tmp), got)

    ws2, got2 = _ws_and_log({}, drop=["XC_LOG_DIR"])
    check("B2 不设时回落 WORKSPACE_DIR/debug.log",
          os.path.normcase(got2) == os.path.normcase(os.path.join(ws2, "debug.log")),
          "%s vs %s" % (got2, ws2))

    _, got3 = _ws_and_log({"XC_LOG_DIR": ""})
    check("B3 空串 = 回落默认（or 语义，防误以为能关掉日志）",
          os.path.normcase(got3) == os.path.normcase(os.path.join(ws2, "debug.log")), got3)


# ---------------- C 注入行为（mock subprocess.run） ----------------
class _FakeP:
    returncode = 0
    stdout = b"PASS=1 FAIL=0"
    stderr = b""


def _capture_run_one(module, env_preset=None):
    captured = {}
    real = module.subprocess.run

    def fake(cmd, **kw):
        captured["env"] = dict(kw.get("env") or {})
        return _FakeP()

    saved = None
    if env_preset is not None:
        saved = os.environ.get("XC_LOG_DIR")
        os.environ["XC_LOG_DIR"] = env_preset
    module.subprocess.run = fake
    try:
        module.run_one(Path(__file__))
    finally:
        module.subprocess.run = real
        if env_preset is not None:
            if saved is None:
                os.environ.pop("XC_LOG_DIR", None)
            else:
                os.environ["XC_LOG_DIR"] = saved
    return captured.get("env", {})


def group_C():
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import run_all as R

    env = _capture_run_one(R)
    check("C1 run_one 注入的 env 含 XC_LOG_DIR",
          "XC_LOG_DIR" in env, str(sorted(env))[:200])
    check("C2 注入值 = TEST_LOG_DIR",
          os.path.normcase(env.get("XC_LOG_DIR", "")) == os.path.normcase(R.TEST_LOG_DIR),
          env.get("XC_LOG_DIR"))
    check("C3 TEST_LOG_DIR 本身在临时目录下",
          os.path.normcase(R.TEST_LOG_DIR).startswith(os.path.normcase(tempfile.gettempdir())),
          R.TEST_LOG_DIR)
    check("C4 保留 QT_QPA_PLATFORM=offscreen", env.get("QT_QPA_PLATFORM") == "offscreen")
    check("C5 保留 PYTHONIOENCODING=utf-8", env.get("PYTHONIOENCODING") == "utf-8")

    env2 = _capture_run_one(R, env_preset="X:/preset-not-overwritten")
    check("C6 外部已设 XC_LOG_DIR 时不被覆盖（setdefault 语义）",
          env2.get("XC_LOG_DIR") == "X:/preset-not-overwritten", env2.get("XC_LOG_DIR"))


# ---------------- D 端到端 ----------------
def _count(path):
    try:
        if not path.is_file():
            return 0
        return open(path, encoding="utf-8", errors="replace").read().count(PROBE_MARKER)
    except Exception:
        return 0


def group_D():
    import run_all as R
    probe = Path(tempfile.gettempdir()) / "dsb_probe_log_redirect.py"
    probe.write_text(
        "import os, sys\n"
        "sys.path.insert(0, os.getcwd())\n"
        "import config\n"
        "import logging\n"
        "logging.getLogger('probe_hyg').warning('%s')\n"
        "print('PASS=1 FAIL=0')\n" % PROBE_MARKER, encoding="utf-8")

    real_log = USER_DOCS / "debug.log"
    tmp_log = Path(R.TEST_LOG_DIR) / "debug.log"
    try:
        b_real, b_tmp = _count(real_log), _count(tmp_log)
        name, npass, nfail, status, secs, out = R.run_one(probe)
        check("D1 探针子进程跑成功", status == "ok",
              "%s PASS=%d FAIL=%d" % (status, npass, nfail))
        a_real, a_tmp = _count(real_log), _count(tmp_log)
        check("D2 真实 debug.log 零新增探针标记（核心）",
              a_real == b_real, "before=%d after=%d" % (b_real, a_real))
        check("D3 临时 debug.log 收到探针标记",
              a_tmp > b_tmp, "before=%d after=%d（%s）" % (b_tmp, a_tmp, tmp_log))
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def _run_part(fn):
    try:
        fn()
    except Exception as ex:
        global FAIL
        FAIL += 1
        print("  [FAIL] 组 %s 抛异常未跑完：%s: %s" % (fn.__name__, type(ex).__name__, ex))
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("== A 源码契约 ==")
    _run_part(group_A)
    print("== B config 行为 ==")
    _run_part(group_B)
    print("== C 注入行为 ==")
    _run_part(group_C)
    print("== D 端到端 ==")
    _run_part(group_D)
    print()
    print("结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    sys.exit(0 if FAIL == 0 else 1)
