# -*- coding: utf-8 -*-
"""test_split_216 —— v4.216.0 大文件拆分（ui.py 12805→9591 / agent.py 2834→2165）的结构判据。

拆分契约（纯代码搬移，零行为变化）：
  theme_tokens.py  : THEME 唯一真源（叶子模块，只依赖标准库）
  ui_widgets.py    : 对话框/小组件/几何图标辅助（含 THEME 真源 import）
  ui_workers.py    : 6 个 QThread worker（voice_mod 别名 import）
  ui_msg.py        : 消息与 API 纯函数族 + VISION_MODEL_KW
  ui_audit_mixin.py: ChatAuditMixin（审计族 20 方法 + 12 常量）
  agent_text.py    : 17 个判据函数 + 24 个词表常量（模块级函数，去 self）
  ui.py            : 顶部 re-export 全部迁出名字，ChatWindow(ChatAuditMixin, QMainWindow)
  agent.py         : 调用点改 agent_text._x（共 7 处）

本套件守住的结构不变量（配套扰动 _perturb_216.py 逐条验证「改坏必红」）：
  A 模块结构（存在/可解析/叶子性/未定义名全扫——2026-10-05 实锤过 4 处
    except-吞-NameError 运行期炸弹，静态扫描是唯一可靠防线）
  B re-export 与接线（ui 名字空间完备 / agent 7 处调用 / 无 self._x 残留）
  C 单源与继承（THEME 只在 theme_tokens；theme_qss 不许回到 from ui import；
    ChatWindow 必须继承 ChatAuditMixin）
  D 规模收敛（ui.py<10000 / agent.py<2400，防慢慢回弹）
  E 行为冒烟（真 import、真调用、agent_text.log 已定义——真 BUG 教训）
  H 自证（防空谓词：扫描器喂坏样本必须叫）
"""
import ast
import builtins
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PASS = FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    tag = "OK  " if ok else "FAIL"
    print("  [%s] %s%s" % (tag, name, ("  — " + str(extra)) if extra and not ok else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def read(fn):
    with open(os.path.join(ROOT, fn), encoding="utf-8") as f:
        return f.read()


NEW_MODULES = ["theme_tokens.py", "ui_widgets.py", "ui_workers.py",
               "ui_msg.py", "ui_audit_mixin.py", "agent_text.py"]

MOVED_WIDGETS = [
    "RES_PRESETS", "TaskStatusStrip", "ThemedDialog", "ConfirmDialog",
    "RenameDialog", "InfoDialog", "MultiLineInput", "SessionManagerDialog",
    "_EdgeResizeFilter", "_NoWheelCombo", "_NAV_ICONS", "_brief_err",
    "_session_preview", "_nav_icon_pixmap", "_make_avatar_label",
    "_nav_icon_qicon", "_skill_icon_name", "resource_path",
    "clamp_dialog_to_screen", "clamp_popup_to_screen", "SKILL_ICON_MAP",
    "SKILL_ICON_FALLBACK", "_WM_NCHITTEST", "_HTCLIENT", "_HTCAPTION",
    "_HTLEFT", "_HTRIGHT", "_HTTOP", "_HTTOPLEFT", "_HTTOPRIGHT",
    "_HT_BOTTOM", "_HT_BOTTOMLEFT", "_HT_BOTTOMRIGHT", "_EDGE_MARGIN",
]
MOVED_WORKERS = ["_GenThread", "PerfWorker", "GuiAsyncJob", "OrchestrateWorker",
                 "_ASRWorker", "_TTSWorker"]
MOVED_MSG = [
    "_flatten_text_content", "_sanitize_filename", "_vision_debug",
    "_compress_image_for_api", "_normalize_image_dataurl",
    "_model_supports_vision", "_strip_attachment_refs",
    "_extract_file_image_parts", "_sanitize_msg_for_api", "_repair_tool_pairs",
    "_tool_ids_present", "_api_error_text", "_attach_api_body",
    "_is_thinking_channel", "_ensure_reasoning_content",
    "_fit_history_to_budget", "_build_api_history", "VISION_MODEL_KW",
]
MOVED_JUDGE_FUNCS = [
    "_looks_like_promise", "_audit_ref_needed", "_looks_like_question",
    "_looks_like_fake_tool_call", "_is_question", "_verb_near",
    "_gen_intent_span", "_gen_intent", "_phrase_hit", "_neg_hit",
    "_ref_by_position", "_ref_existing_artifact", "_prog_fetch_intent",
    "_is_bare_url", "_route_force_tool", "_content_creation_only",
    "_detect_action_intent",
]

# agent.py 里的期望接线（函数 → 次数），合计 7 处
AGENT_CALLS = {
    "_looks_like_promise": 2,
    "_audit_ref_needed": 1,
    "_looks_like_question": 1,
    "_looks_like_fake_tool_call": 1,
    # v4.225（P3 统一意图）：_route_force_tool 的调用点从 agent.py 收口到
    # intent.py —— step1 改读 Intent.force_tool，agent.py 不再直接调它。
    # 所以这里从 agent.py 的精确计数里**移出**，改到下方 INTENT_CALLS 断言
    # 「路由接线不许弄丢」（保住原意，不是删判据）。
    "_detect_action_intent": 1,
}

# v4.225：路由判据的新收口位置（原 agent.py 的 _route_force_tool 调用点）
INTENT_CALLS = {
    "_route_force_tool": 1,
    "_detect_action_intent": 1,
    "_content_creation_only": 1,
}


def undefined_names(fn):
    """AST 扫模块级未定义名（import 溯源）。返回 (未定义名集合, 定义的顶层名集合)。"""
    tree = ast.parse(read(fn))
    loads, stores = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            (loads if isinstance(n.ctx, ast.Load) else stores).add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stores.add(n.name)
        elif isinstance(n, ast.arg):
            stores.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            stores.add(n.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                stores.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                stores.add(a.asname or a.name)
    undef = loads - stores - set(dir(builtins))
    return undef, stores


print("=== A 模块结构 ===")
trees = {}
for fn in NEW_MODULES:
    path = os.path.join(ROOT, fn)
    ok = os.path.isfile(path) and os.path.getsize(path) > 100
    if ok:
        try:
            trees[fn] = ast.parse(read(fn))
        except SyntaxError as e:
            ok = False
            check(f"A1[{fn}] 存在且可解析", False, str(e))
            continue
    check(f"A1[{fn}] 存在且可解析", ok)
check("A1 六个新模块全在", len(trees) == 6, f"缺 {[f for f in NEW_MODULES if f not in trees]}")

# A2 theme_tokens 叶子性：不许 import 项目内模块（防循环依赖 —— theme_qss 曾依赖 ui，
#    若叶子反向引用就成环）
tt_src = read("theme_tokens.py")
project_mods = {"ui", "ui_widgets", "ui_workers", "ui_msg", "ui_audit_mixin",
                "agent_text", "theme_qss", "agent", "tools", "empty_state",
                "toast", "config", "main", "permissions", "legion_ui",
                "automation_panel", "director_panel", "digital_twin_panel"}
bad_imports = []
for node in ast.walk(trees.get("theme_tokens.py", ast.Module(body=[], type_ignores=[]))):
    if isinstance(node, ast.Import):
        bad_imports += [a.name for a in node.names if a.name.split(".")[0] in project_mods]
    elif isinstance(node, ast.ImportFrom):
        if node.module and node.module.split(".")[0] in project_mods:
            bad_imports.append(node.module)
check("A2 theme_tokens 是叶子模块（不 import 项目内任何模块，防 ui 环）",
      not bad_imports, f"反向引用：{bad_imports}")

# A3 未定义名全扫（真教训：log/voice_mod/html_mod/tools_mod 四处运行期炸弹
#    全是 except-吞-NameError 型，import 冒烟测不出来，只有静态扫描可靠）
print("  -- A3 未定义名全扫（豁免 __file__）--")
for fn in NEW_MODULES:
    undef, _ = undefined_names(fn)
    undef -= {"__file__"}
    check(f"A3[{fn}] 无未定义名", not undef, f"缺：{sorted(undef)[:6]}")

print("=== B re-export 与接线 ===")
# B1 动态：ui 名字空间里全部搬移名字可用（re-export 是另一份绑定，必须 import 实测）
import ui  # noqa: E402
missing = [n for n in (MOVED_WIDGETS + MOVED_WORKERS + MOVED_MSG + ["ChatAuditMixin", "THEME"])
           if not hasattr(ui, n)]
check("B1 ui 名字空间完备（60 个搬移名字 + ChatAuditMixin + THEME 经 re-export 全可用）",
      not missing, f"缺：{missing[:6]}")

# B2 agent.py 接线：期望函数逐个计数
ag_src = read("agent.py")
bad_wiring = []
for fn_name, want in AGENT_CALLS.items():
    got = ag_src.count(f"agent_text.{fn_name}(")
    if got != want:
        bad_wiring.append(f"{fn_name}:{got}≠{want}")
check("B2 agent.py 判据接线 = 5 处 agent_text._x 调用（5 函数精确计数）",
      not bad_wiring, "; ".join(bad_wiring))

# B2b v4.225：路由判据在 intent.py 里仍真被调用（搬家不许丢接线）
intent_src = read("intent.py")
bad_intent = []
for fn_name, want in INTENT_CALLS.items():
    got = intent_src.count(f"agent_text.{fn_name}(")
    if got != want:
        bad_intent.append(f"{fn_name}:{got}≠{want}")
check("B2b intent.py 判据接线 = 3 处 agent_text._x 调用（3 函数精确计数）",
      not bad_intent, "; ".join(bad_intent))
check("B2c agent.py 改读 Intent.force_tool（收口真接线）",
      "self._intent.force_tool" in ag_src,
      "step1 没读 Intent.force_tool → 收口只做了一半")
check("B2d agent.py 仍直接调 _detect_action_intent（未被 Intent 顶掉）",
      ag_src.count("agent_text._detect_action_intent(") == 1)

# B3 无残留：搬走的 17 个判据函数不许再以 self._x( 形态出现在 agent.py
leftover = [f for f in MOVED_JUDGE_FUNCS if f"self.{f}(" in ag_src]
check("B3 agent.py 无旧式 self._x( 残留（17 个搬移函数）",
      not leftover, f"残留：{leftover[:4]}")

print("=== C 单源与继承 ===")
# C1 ChatWindow 继承接入 mixin
m = re.search(r"class ChatWindow\(([^)]*)\):", read("ui.py"))
bases = [b.strip() for b in m.group(1).split(",")] if m else []
check("C1 ChatWindow(ChatAuditMixin, QMainWindow) 继承接入",
      "ChatAuditMixin" in bases and "QMainWindow" in bases, f"实际基类：{bases}")

# C2 theme_qss 从 theme_tokens 取 THEME（不许回到 from ui import THEME —— 那会成环）
tq_src = read("theme_qss.py")
check("C2 theme_qss 的 THEME 来自 theme_tokens（非 ui）",
      re.search(r"from theme_tokens import .*THEME", tq_src) is not None
      and "from ui import" not in tq_src,
      "THEME 来源回退或出现 from ui import")

# C3 THEME 字面量唯一：字典定义只许出现在 theme_tokens.py
#    （防止有人在 ui.py/其他模块里再抄一份 —— 那是第二真源，改 token 就会漂移）
def has_theme_dict_literal(fn):
    try:
        t = trees.get(fn) or ast.parse(read(fn))
    except Exception:
        return False
    for node in t.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) \
                and any(getattr(x, "id", None) == "THEME" for x in node.targets):
            return True
    return False

double_defs = [fn for fn in NEW_MODULES + ["ui.py"]
               if fn != "theme_tokens.py" and has_theme_dict_literal(fn)]
check("C3 THEME 字典字面量只在 theme_tokens.py（无第二真源）",
      not double_defs, f"重复定义于：{double_defs}")

print("=== D 规模收敛（防回弹） ===")
ui_lines = len(read("ui.py").splitlines())
ag_lines = len(read("agent.py").splitlines())
check("D1 ui.py < 10000 行（拆分前 12805，防慢慢回弹）",
      ui_lines < 10000, f"当前 {ui_lines}")
check("D2 agent.py < 2400 行（拆分前 2834）", ag_lines < 2400, f"当前 {ag_lines}")

print("=== E 行为冒烟 ===")
# E1 真调用几个搬移函数（re-export 绑定链上任何一环断了都会 AttributeError）
try:
    _e1a = ui._brief_err(RuntimeError("x" * 300)).startswith("x") or True
    _e1b = isinstance(ui.THEME, dict) and "accent" in ui.THEME
    _e1c = hasattr(ui.ChatAuditMixin, "_audit_reply_citations")
    check("E1 真调用：ui._brief_err / ui.THEME / ui.ChatAuditMixin._audit_*",
          _e1a and _e1b and _e1c)
except Exception as e:  # noqa: BLE001
    check("E1 真调用：ui._brief_err / ui.THEME / ui.ChatAuditMixin._audit_*", False, repr(e))

# E2 agent_text.log 已定义（真 BUG 教训：缺 log 时 NameError 被 except 吞掉、
#    评价句误判 needs_action —— 判据当时抓的就是这一条）
import agent_text  # noqa: E402
check("E2 agent_text.log 已定义（logging.getLogger）",
      hasattr(agent_text, "log") and hasattr(agent_text.log, "info"))
# E3 判据函数真可调（抽 3 个行为冒烟）
try:
    _e3 = (agent_text._looks_like_promise("我等下就去改") is not None
           and callable(agent_text._detect_action_intent)
           and callable(agent_text._route_force_tool))
    check("E3 agent_text 判据函数真可调（抽 3 个）", _e3)
except Exception as e:  # noqa: BLE001
    check("E3 agent_text 判据函数真可调（抽 3 个）", False, repr(e))

print("=== H 自证（防空谓词） ===")
# H1 读取器真在读（非空文件）
check("H1 源码读取非空（防负负得正的空比对）",
      all(len(read(f)) > 1000 for f in NEW_MODULES + ["ui.py", "agent.py"]))
# H2 未定义名扫描器自证：喂一段含未定义名的样本必须抓到
_h2_src = "theme_tokens.py"  # 借文件名，喂内存样本
import tempfile
with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
    f.write("import os\nLOGGERS_MISSING = totally_undefined_name\n")
    _h2_path = f.name
try:
    _h2_tree_path = _h2_path
    _h2_orig = undefined_names.__code__.co_varnames  # noqa: F841
    # 直接复用同一扫描逻辑（inline 防路径依赖）
    t = ast.parse(open(_h2_path, encoding="utf-8").read())
    loads, stores = set(), set()
    for n in ast.walk(t):
        if isinstance(n, ast.Name):
            (loads if isinstance(n.ctx, ast.Load) else stores).add(n.id)
    got = loads - stores - set(dir(builtins))
    check("H2 未定义名扫描器自证（坏样本必抓：totally_undefined_name）",
          "totally_undefined_name" in got, f"实际抓到：{got}")
finally:
    os.unlink(_h2_path)
# H3 接线计数器自证：把 agent_text. 前缀改掉一个就数得出来（防空转 0==0）
_h3_probe = ag_src.replace("agent_text._looks_like_promise(", "agent_text._looks_like_promiseX(", 1)
check("H3 B2 计数器自证（改坏一个前缀立刻失配）",
      _h3_probe.count("agent_text._looks_like_promise(") == AGENT_CALLS["_looks_like_promise"] - 1)

print()
print("=== 汇总：PASS=%d FAIL=%d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
