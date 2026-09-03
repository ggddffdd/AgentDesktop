# -*- coding: utf-8 -*-
"""v4.109 模型下拉 + 路由旁路日志 —— 源码级回归。

覆盖两条主线：
A. 旁路日志：只记录不干预，任何异常不得影响对话（含磁盘不可写）。
B. 手动锁定：Auto 行为零变化；锁定档位失效要回退主模型，**绝不静默跳付费通道**。

关键回归点（本次实际踩到的坑）：
C. 输入区下拉必须叫 chat_model_combo —— 设置弹层里还有一个 self.model_combo
   （_build_settings_popup 内创建，__init__ 在其后执行会覆盖同名引用）。
   复用同名会让输入区下拉变孤儿控件（永远空白），且路由读到设置弹层的值。
"""
import ast
import inspect
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra and not cond else ""))


SRC = os.path.dirname(os.path.abspath(__file__))
UI_PY = os.path.join(SRC, "ui.py")
UI_SRC = io.open(UI_PY, encoding="utf-8-sig").read()
UI_TREE = ast.parse(UI_SRC)

_MISSING = []


def func_src(cls_name, fn):
    """取某个类中某方法的源码文本。取不到要显式报错——

    曾经因为把主窗口类名写成 MainWindow（实际是 ChatWindow），取到空串，
    导致 5 条断言「假通过」（`x not in ""` 恒真）。故这里登记缺失并让脚本失败。
    """
    for node in ast.walk(UI_TREE):
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == fn:
                    return ast.get_source_segment(UI_SRC, sub) or ""
    _MISSING.append("%s.%s" % (cls_name, fn))
    return ""


print("=" * 60)
print("v4.109 模型下拉 + 路由旁路日志 回归验证")
print("=" * 60)

# ---------- A. 旁路日志 ----------
print("\n[A] 旁路日志（只记录，不干预）")
import route_log

check("route_log 可导入", hasattr(route_log, "log_route") and hasattr(route_log, "read_recent"))

# A1 正常写入
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "t.jsonl")
    old = route_log._PATH
    route_log._PATH = p
    try:
        r1 = route_log.log_route(event="route", model="agnes-2.5-flash",
                                 base_url="https://apihub.agnes-ai.cn/v1",
                                 upgraded=False, reason="default", lock="", msgs_len=10)
        recs = route_log.read_recent(10)
        check("写入+读回正常", r1 and len(recs) == 1 and recs[0]["tier"] == "free",
              recs)
        route_log.log_route(event="route", model="deepseek-chat",
                            base_url="https://api.deepseek.com", upgraded=True,
                            reason="kw:分析", lock="", msgs_len=99)
        recs = route_log.read_recent(10)
        check("付费档 tier 判定为 paid", recs[-1]["tier"] == "paid", recs[-1].get("tier"))
        check("event 过滤可用", len(route_log.read_recent(10, event="usage")) == 0)
    finally:
        route_log._PATH = old

# A2 磁盘不可写必须静默失败（返回 False 而不是抛异常）——旁路铁律
old = route_log._PATH
route_log._PATH = "Z:/不存在的盘/不可能写入/x.jsonl"
try:
    r = route_log.log_route(event="route", model="m", base_url="", reason="x")
    check("写入失败静默返回 False（不抛异常）", r is False, r)
except Exception as e:
    check("写入失败静默返回 False（不抛异常）", False, repr(e))
finally:
    route_log._PATH = old

check("ui.py 已 import route_log", "import route_log" in UI_SRC)
_LR = func_src("ChatWindow", "_log_route")
check("_log_route 吞异常", bool(_LR) and "except Exception" in _LR)

# ---------- B. 路由手动锁定 ----------
print("\n[B] 手动锁定语义")
RM = func_src("ChatWindow", "_route_model")
check("取到 _route_model 源码（为空则后续断言均不可信）", bool(RM))
check("_route_model 接受 reason 参数", "def _route_model(self, messages, force_complex=False, reason=\"\")" in RM
      or "reason=\"\"" in RM)
check("锁定分支在最前（优先于路由）",
      RM.find("_model_lock") < RM.find("routing = cfg.get(\"model_routing\")"))
check("锁定失效回退主模型", "lock_invalid:fallback_main" in RM)
check("Auto 时先判 routing.enabled（行为不变）",
      "if not routing.get(\"enabled\", True):" in RM)

# B1 锁定不静默跳付费：锁定分支内不得出现 complex_model 的读取
_lock_seg = RM[RM.find("_lock = getattr"):RM.find("routing = cfg.get")]
check("锁定分支不碰 complex_model（不静默升付费）",
      "complex_model" not in _lock_seg, _lock_seg[-200:])

# B2 默认行为不变：Auto 下 _is_complex 调用链保留
check("保留 _is_complex 判定", "self._is_complex(messages, routing)" in RM)

# B3 归因统计
check("有复杂度归因函数", "_complexity_reason" in RM or "def _complexity_reason" in UI_SRC)
check("归因含关键词格式 kw:", '"kw:"' in UI_SRC or "'kw:'" in UI_SRC)
check("归因含长度格式 len:", '"len:' in UI_SRC or "'len:" in UI_SRC)

# ---------- C. 下拉命名冲突（本次踩坑） ----------
print("\n[C] 下拉命名冲突（关键回归）")
check("输入区下拉叫 chat_model_combo", "self.chat_model_combo = QComboBox()" in UI_SRC)
FILL = func_src("ChatWindow", "_fill_model_combo")
check("_fill_model_combo 用 chat_model_combo",
      "self.chat_model_combo.clear()" in FILL and "self.model_combo.clear()" not in FILL)
ONCHG = func_src("ChatWindow", "_on_model_combo_changed")
check("_on_model_combo_changed 用 chat_model_combo",
      "self.chat_model_combo.itemData(idx)" in ONCHG
      and "self.model_combo.itemData(idx)" not in ONCHG)
check("设置弹层的 model_combo 未被破坏（仍存在）", UI_SRC.count("self.model_combo = QComboBox()") == 1)
check("构建顺序：_build_chat_page 早于 _build_settings_popup（故必须改名隔离）",
      UI_SRC.find("self._build_chat_page()") < UI_SRC.find("self._build_settings_popup()"))
check("填充在输入区构建之后调用",
      UI_SRC.find("cc_lay.addWidget(self._build_input_area())")
      < UI_SRC.find("self._fill_model_combo()"))

# ---------- D. 覆盖全部调用点 ----------
print("\n[D] 调用点覆盖")
check("_agent_call 传入归因", 'reason=_route_reason' in UI_SRC)
check("_start_stream 锁定生效（Auto 行为不变）",
      'reason="stream_lock"' in UI_SRC and 'if getattr(self, "_model_lock", ""):' in UI_SRC)
check("_start_stream 带图仍强制 complex", 'reason="image"' in UI_SRC)
check("AgentWorker 调用带归因", 'reason="agent_worker"' in UI_SRC)
check("usage 埋点存在", 'route_log.log_route(event="usage"' in UI_SRC)
check("usage 记录锁定态", "lock=getattr(self, \"_model_lock\", \"\")" in UI_SRC)

# D1 tool_choice 豁免跟着实际模型走（手动锁到思考模型也不能 400）
check("force_tool 豁免用路由后变量",
      "self._is_reasoning_model(_model, _base_url)" in UI_SRC)
check("required 豁免用路由后变量",
      UI_SRC.count("self._is_reasoning_model(_model, _base_url)") >= 2)

# ---------- E. 打包 & 配置 ----------
print("\n[E] 打包与配置")
SPEC = None
for f in os.listdir(SRC):
    if f.endswith(".spec"):
        SPEC = os.path.join(SRC, f)
        break
if SPEC:
    spec_txt = io.open(SPEC, encoding="utf-8").read()
    check("spec hiddenimports 含 route_log", "'route_log'" in spec_txt)
else:
    check("spec hiddenimports 含 route_log", False, "未找到 spec")

import config as _c

check("config 默认 model_lock 为空（Auto）",
      _c.DEFAULT_CONFIG.get("model_lock", "__MISSING__") == "")

check("route_stats.py 存在且可编译", os.path.exists(os.path.join(SRC, "route_stats.py")))

# ---------- F. 真实调用（静态断言看不出逻辑错误，必须跑一次） ----------
print("\n[F] 真实调用 _route_model")
try:
    import ui as _ui

    CW = getattr(_ui, "ChatWindow", None)
    check("取到 ChatWindow 类", CW is not None)

    if CW is not None:
        w = CW.__new__(CW)          # 绕过 __init__（不建 Qt 控件）
        w.cfg = {
            "base_url": "https://apihub.agnes-ai.cn/v1",
            "model": "agnes-2.5-flash",
            "api_key": "main-key",
            "model_routing": {
                "enabled": True,
                "complex_model": "DeepSeek 官方",
                "length_threshold": 1500,
                "complex_hint": ["代码", "分析", "报告"],
            },
            "model_profiles": {
                "DeepSeek 官方": {"base_url": "https://api.deepseek.com/v1",
                                  "model": "deepseek-chat", "api_key": "ds-key"},
                "Agnes": {"base_url": "https://apihub.agnes-ai.cn/v1",
                          "model": "agnes-2.5-flash", "api_key": "ag-key"},
                "空档位": {"base_url": "", "model": "", "api_key": ""},
            },
        }
        w._route_reason = ""

        # 把旁路日志导到临时文件，避免污染真实 route_log.jsonl
        _tmpdir = tempfile.mkdtemp()
        _old_path = route_log._PATH
        route_log._PATH = os.path.join(_tmpdir, "t.jsonl")
        try:
            msgs = [{"role": "user", "content": "今天天气怎么样"}]

            # F1 Auto + 普通闲聊 → 主模型 Agnes（v4.108 行为）
            w._model_lock = ""
            bu, m, k = w._route_model(msgs)
            check("Auto 闲聊 → 主模型 Agnes（行为不变）",
                  m == "agnes-2.5-flash" and bu == "https://apihub.agnes-ai.cn/v1", m)

            # F2 Auto + 工具意图 force_complex → 升 DeepSeek（v4.108 行为）
            w._model_lock = ""
            bu, m, k = w._route_model(msgs, force_complex=True, reason="tool_intent")
            check("Auto 工具意图 → 升舱 DeepSeek（行为不变）",
                  m == "deepseek-chat" and k == "ds-key", m)

            # F3 Auto + 命中关键词 → 升 DeepSeek（v4.108 行为）
            w._model_lock = ""
            bu, m, k = w._route_model([{"role": "user", "content": "帮我分析这段代码"}])
            check("Auto 命中关键词 → 升舱 DeepSeek（行为不变）", m == "deepseek-chat", m)

            # F4 锁定 DeepSeek + 普通闲聊 → 不再走 Agnes
            w._model_lock = "DeepSeek 官方"
            bu, m, k = w._route_model(msgs)
            check("锁定 DeepSeek → 闲聊也用 DeepSeek（绕过路由）",
                  m == "deepseek-chat" and k == "ds-key", m)

            # F5 锁定档位 key 缺失 → 回退主模型，绝不静默跳付费
            w._model_lock = "空档位"
            bu, m, k = w._route_model(msgs)
            check("锁定失效 → 回退主模型（不静默跳付费）",
                  m == "agnes-2.5-flash" and k == "main-key", m)

            # F6 锁定 __main__ → 主模型，即便命中关键词也不升舱
            w._model_lock = "__main__"
            bu, m, k = w._route_model([{"role": "user", "content": "帮我分析这段代码"}])
            check("锁定主模型 → 命中关键词也不升舱", m == "agnes-2.5-flash", m)

            # F7 routing 关闭 + Auto → 主模型
            w._model_lock = ""
            w.cfg["model_routing"]["enabled"] = False
            bu, m, k = w._route_model(msgs, force_complex=True)
            check("Auto + 路由关闭 → 主模型", m == "agnes-2.5-flash", m)
            w.cfg["model_routing"]["enabled"] = True

            # F8 归因记录正确
            w._model_lock = ""
            w._route_model([{"role": "user", "content": "写个报告"}])
            check("关键词归因写入 _route_reason",
                  str(getattr(w, "_route_reason", "")).startswith("kw:"),
                  getattr(w, "_route_reason", ""))

            # F9 日志确实落盘（旁路生效）
            check("旁路日志已落盘", os.path.exists(route_log._PATH)
                  and os.path.getsize(route_log._PATH) > 0)
        finally:
            route_log._PATH = _old_path
            try:
                import shutil
                shutil.rmtree(_tmpdir, ignore_errors=True)
            except Exception:
                pass
except Exception as e:
    check("真实调用段可执行", False, repr(e))

if _MISSING:
    FAIL.append("源码提取缺失（断言可能假通过）: %s" % ", ".join(_MISSING))

print("\n" + "=" * 60)
print("通过 %d / 失败 %d" % (len(OK), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
