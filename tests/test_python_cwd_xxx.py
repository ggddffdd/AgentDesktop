# 判据：run_python cwd 统一 + traceback 末行保留 + read_xlsx_json 结构化（根治 2026-10-11 读附件空转/甩锅）
# 风格：源码契约断言（A 类）+ 纯函数行为（A5，tools 不可导入则 SKIP）+ 防回归（B 类）。
# 失败打印 [FAIL] name（兼容 _perturb_*.py 的 (?:\[FAIL\]|FAIL)\s+ 抓取）；零哑弹。
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "tools.py")
sys.path.insert(0, REPO)


def _read_tools():
    with open(TOOLS, encoding="utf-8") as f:
        return f.read()


results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    mark = "[PASS] " if cond else "[FAIL] "
    print(mark + name + (("  -- " + detail) if (detail and not cond) else ""))


def _fn_block(src, fn):
    """抓取 def fn( ... 到下一个 \\ndef 之间的源码块。"""
    m = re.search(r"def " + fn + r"\(.*?\n(?=\ndef )", src, re.S)
    return m.group(0) if m else ""


src = _read_tools()
run_py_src = _fn_block(src, "tool_run_python")
rf_src = _fn_block(src, "tool_read_file")
rx_src = _fn_block(src, "tool_read_xlsx_json")

# ---- A 类：根治契约 ----
# A1：run_python 执行 cwd 统一为 WORKSPACE_DIR（不再 cwd=ws=USER_DATA_DIR/workspace）
check("A1 run_python cwd 统一为 WORKSPACE_DIR",
      ("cwd=run_cwd" in run_py_src
       and "run_cwd = WORKSPACE_DIR" in run_py_src
       and 'cwd=os.path.join(USER_DATA_DIR, "workspace")' not in run_py_src),
      "Popen 的 cwd 必须为 run_cwd(=WORKSPACE_DIR)，不能再用 USER_DATA_DIR/workspace")

# A2：报错末行保留（函数定义 + 调用）
check("A2 _preserve_traceback_tail 定义且被调用",
      "def _preserve_traceback_tail(" in src
      and "out = _preserve_traceback_tail(raw, out)" in src)

# A3：read_xlsx_json 注册到 TOOL_REGISTRY
check('A3 read_xlsx_json 注册到 TOOL_REGISTRY',
      '@register_tool("read_xlsx_json")' in src and "def tool_read_xlsx_json(" in src)

# A4：read_xlsx_json 路径解析继承 WORKSPACE_DIR 统一根
check("A4 read_xlsx_json 路径解析用 WORKSPACE_DIR",
      "os.path.join(WORKSPACE_DIR, path)" in rx_src)

# A5（行为）：_preserve_traceback_tail 纯函数——报错末行钉在尾部且相对路径不脱敏
try:
    import tools  # 本地/CI 均可导入（binary guard 已证 import tools 在 CI 成功）
    raw = ("Traceback (most recent call last):\n  File 'x.py', line 2\n"
           "FileNotFoundError: [Errno 2] No such file or directory: "
           "'incoming/理论题库.xlsx'\n").encode("utf-8")
    out = tools._preserve_traceback_tail(raw, "head text")
    ok_tail = ("FileNotFoundError: [Errno 2] No such file or directory: "
               "'incoming/理论题库.xlsx'") in out
    ok_unmasked = "<路径已脱敏>" not in out
    check("A5 报错末行保留且相对路径不脱敏", ok_tail and ok_unmasked)
except Exception as e:
    print("[SKIP] A5 (tools 不可导入: %r)" % e)

# ---- B 类：防回归（不误拦） ----
# B1：tool_read_file 解析根未退化（仍 WORKSPACE_DIR）
check("B1 tool_read_file 仍用 WORKSPACE_DIR 解析根",
      "os.path.join(WORKSPACE_DIR, path)" in rf_src)

# B2：run_python 危险命令拦截未退化（不能因改 cwd 把安全闸弄丢）
check("B2 run_python 危险命令拦截保留",
      "_dangerous_command_check(code)" in run_py_src)

fails = [n for n, c in results if not c]
print("\n>>> 判据 A/B: PASS=%d FAIL=%d" % (len(results) - len(fails), len(fails)))
sys.exit(1 if fails else 0)
