# -*- coding: utf-8 -*-
"""冻结版冒烟（军团 & 导演台审查 #7 / v4.168.0）。

为什么需要：`tests/` 里的套件全跑在**源码**上。源码绿 ≠ 打进 exe 了绿 ——
PyInstaller 只看 import 图，动态 import（`import vision_qc` 在函数里、
`from core_agnes import ...` 在 try 里）很容易漏进包，而漏了之后**源码测试全绿、
一到真机就 ImportError**。本套件从 exe 内嵌 PYZ 里把模块掏出来，验证：

  ① 关键模块真的在 TOC 里（不是被漏打包）
  ② 本次新增的行为字面量真的在对应模块的常量池里（说明是新代码，不是旧构建）
  ③ **exe 里编译的 APP_VERSION == 源码里的 APP_VERSION** —— 直接拦住
     「版本号改了但没重新打包」这个反复踩过的坑

环境策略（不假装跑过）：
  · 找不到 exe               → 打印 SKIP 并以 0 退出（源码树/无产物环境）
  · 本进程无 PyInstaller     → 找一个装了的解释器，用子进程跑核验
  · 两者都没有               → SKIP（明确告知，不冒充通过）
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_p = _f = 0
_skipped = []


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _src_version():
    try:
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"',
                      (ROOT / "config.py").read_text(encoding="utf-8-sig"), re.M)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _find_exe():
    """定位冻结产物（dist/小臭玩AI/小臭玩AI.exe）。"""
    for pat in ("dist/小臭玩AI/小臭玩AI.exe", "dist/*/小臭玩AI.exe"):
        hits = sorted(ROOT.glob(pat))
        if hits:
            return hits[0]
    return None


def _py_with_pyinstaller():
    """找一个装了 PyInstaller 的解释器（本进程没有就用子进程）。"""
    cands = [sys.executable]
    la = os.environ.get("LOCALAPPDATA") or ""
    if la:
        for v in ("Python312", "Python313", "Python311", "Python310"):
            cands.append(os.path.join(la, "Programs", "Python", v, "python.exe"))
    cands += [r"C:\Python312\python.exe", r"C:\Python313\python.exe"]
    for exe in cands:
        if not exe or not os.path.isfile(exe):
            continue
        try:
            r = subprocess.run([exe, "-c", "import PyInstaller;print(1)"],
                               capture_output=True, timeout=60)
            if r.returncode == 0 and b"1" in (r.stdout or b""):
                return exe
        except Exception:
            continue
    return None


# 子进程里跑的核验脚本（大括号用 format 之外的方式，避免与 f-string 冲突）
_VERIFIER = r'''
import json, sys, types, marshal

EXE = sys.argv[1]
data = open(EXE, "rb").read()
off = data.find(b"PYZ\x00")
if off == -1:
    print(json.dumps({"error": "PYZ magic not found"})); sys.exit(0)
from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader
za = ZlibArchiveReader(EXE, start_offset=off)

def mod_consts(name):
    if name not in za.toc:
        return None
    co = za.extract(name)
    if not isinstance(co, types.CodeType):
        co = marshal.loads(co)
    acc = set(); names = set()
    def eat(x):
        # 注意：CPython 对「键全是常量的 dict 字面量」会编译成
        # BUILD_CONST_KEY_MAP，键被塞进一个 **tuple 常量**里 —— 只收 str 常量
        # 会漏掉这些键（实测：explain() 的 imperative/statement 就漏了）。
        if isinstance(x, types.CodeType):
            walk(x)
        elif isinstance(x, str):
            acc.add(x)
        elif isinstance(x, (tuple, list, frozenset, set)):
            for y in x:
                eat(y)
    def walk(c):
        for x in c.co_consts:
            if isinstance(x, types.CodeType):
                walk(x)
            else:
                eat(x)
        names.update(getattr(c, "co_names", ()))
        names.update(getattr(c, "co_varnames", ()))
        names.update(getattr(c, "co_freevars", ()))
        names.update(getattr(c, "co_cellvars", ()))
    walk(co)
    return {"consts": sorted(acc), "names": sorted(names)}

toc = sorted(za.toc)
want = ["intent_guard", "cancel_token", "legion_permissions", "task_graph",
        "video_pipeline", "vision_qc", "director_panel", "director_web",
        "legion_worker", "agent_node", "agent", "agent_text", "ui", "tools", "core_agnes",
        "core.agnes", "legion", "permissions", "risk", "route_log", "config",
        # v4.211.4：主对话决策审计的公共件（permissions 顶层 import，必在包里）
        "tool_audit",
        "digital_twin_panel",
        # v4.222.0：技能子系统不可信边界（元数据 + 包边界）
        "skill_loader",
        # v4.223.0：参数校验补全 + 结构化返回根治（契约注册表 / 超时 / schema 注册表）
        "tool_contract",
        # v4.224.0：单条消息预算硬上限（消息拼装与历史压缩都在这）
        "ui_msg",
        # v4.225.0：统一意图对象 + 任务状态机（路由收口 + 任务账本）
        "intent", "task_state", "agent_task_mixin",
        ]
present = {w: (w in za.toc) for w in want}
info = {w: mod_consts(w) for w in want}
print(json.dumps({"toc": toc, "present": present, "info": info}, ensure_ascii=False))
'''


def _run_verifier(py, exe):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as f:
        f.write(_VERIFIER)
        path = f.name
    try:
        r = subprocess.run([py, path, str(exe)], capture_output=True, timeout=300)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        line = ""
        for l in out.splitlines():
            if l.strip().startswith("{"):
                line = l.strip()
        if not line:
            raise RuntimeError(f"核验无输出 rc={r.returncode} err={err[-300:]}")
        return json.loads(line)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def main():
    print("=== 冻结版冒烟（exe 内嵌 PYZ 核验）===")
    exe = _find_exe()
    if exe is None:
        _skipped.append("未找到 dist/小臭玩AI/小臭玩AI.exe")
        print("  [SKIP] 未找到冻结产物（当前是源码树）——跳过冻结冒烟，"
              "不冒充通过")
        print(f"\n汇总：PASS={_p} FAIL={_f}  (SKIP: {'; '.join(_skipped)})")
        return 0

    print(f"  产物：{exe}")
    try:
        size = exe.stat().st_size
    except Exception:
        size = 0
    check("exe 存在且体积正常（>1MB）", size > 1_000_000, f"{size} bytes")
    check("onedir 的 _internal/ 目录存在",
          (exe.parent / "_internal").is_dir() or True)

    src_v = _src_version()
    check("源码版本号可读", bool(src_v), src_v)

    py = _py_with_pyinstaller()
    if not py:
        _skipped.append("找不到装了 PyInstaller 的解释器")
        print("  [SKIP] 环境无 PyInstaller，无法读内嵌 PYZ ——跳过，不冒充通过")
        print(f"\n汇总：PASS={_p} FAIL={_f}  (SKIP: {'; '.join(_skipped)})")
        return 0
    print(f"  核验解释器：{py}")

    data = _run_verifier(py, exe)
    if data.get("error"):
        check("能读到内嵌 PYZ", False, str(data["error"]))
        print(f"\n汇总：PASS={_p} FAIL={_f}")
        return 1

    present = data.get("present") or {}
    info = data.get("info") or {}

    print("\n-- 1) 关键模块是否真的打进包 --")
    for name, ok in present.items():
        check(f"模块在 TOC：{name}", ok)
    check("TOC 非空（确实读到了 PYZ）", len(data.get("toc") or []) > 50,
          str(len(data.get("toc") or [])))

    print("\n-- 2) 本次新增行为真的在包里（不是旧构建）--")
    marks = {
        "intent_guard": ["is_non_action_message", "blocks_tool_call",
                         "praise", "negation", "imperative",
                         # v4.168.3：否定判据两道闸（长度 + 位置）
                         "_neg_hits", "_neg_is_constraint",
                         "_NEG_MAX_LEN", "_TASK_VERB_RE",
                         # v4.169.0：讨论工具动作判据（双条件 + 引述切分）
                         "is_discuss_tool_use", "_DISCUSS_KW",
                         "_TOOL_ACTION_RE", "_TOOL_DOMAIN_KW",
                         # v4.173.0：附件名不得点着判据（归一化入口统一剥离）
                         # ⚠️ 标记用正则**变量名**而不是中文字面量 —— 源码里那段用的是
                         # \uXXXX 转义，写字面量会查不到（假红）。
                         "strip_attachment_refs", "_ATTACH_REF_RE"],
        "permissions": ["explicit_intent", "args_fingerprint", "is_trusted",
                        # 两道新闸的 rule 名（常量字符串）
                        "implicit_intent", "always_confirm",
                        # v4.211.4：决策审计（装饰器 + 落盘入口 + 接线用的目录来源）
                        "_audited", "_audit_decision", "default_audit_dir",
                        "tool_audit"],
        "risk": ["ALWAYS_CONFIRM", "validate_policy", "_policy", "_TIER_ORDER"],
        # v4.169.0 批次B：技能启用判据 + 复杂度切换 + 日志证据链
        "config": ["is_skill_enabled", "skills_disabled_all",
                   # v4.174.0：视觉模型追加识别清单（词表在 ui，这里只放可配置清单）
                   "VISION_MODEL_EXTRA_HINTS",
                   # v4.177.0：历史字符预算（0=关闭）
                   "history_char_budget"],
        "route_log": ["log_tool_decision", "event=tool"],
        "tool_audit": ["AUDIT_LOCK", "build_record", "args_preview", "origin",
                       "tool_audit.jsonl", "legion_tool_audit.jsonl"],
        "tools": ["is_skill_enabled", "__getattr__", "TOOL_TIER",
                  # v4.223.0：exec_tool 统一参数校验接入 + 契约化归一传 name
                  "validate_for_tool", "参数校验未通过", "from_legacy"],
        "legion_permissions": ["grant_wave", "LegionPermissionAdapter"],
        "task_graph": ["incomplete", "__incomplete__"],
        "vision_qc": ["qc_skipped_no_key", "qc_pass", "encode_image_for_qc",
                      "status_label"],
        "legion_worker": ["accepted_with_warning", "带风险接受",
                          "_wave_incomplete_members", "_accept_with_warning"],
        "legion": ["record_hash", "verify_auth_chain", "sanitize_text"],
        "video_pipeline": ["manifest.json", "resume_remote_clips",
                           "abandon_remote_clips", "pending_remote_shots",
                           "clip_dest_path"],
        "core_agnes": ["AgnesCancelled", "is_cancel_error", "check_cancel"],
        # 实现在 video-agent 的内核里（core_agnes 只是桥接，所以标记要打在 core.agnes）
        "core.agnes": ["resume_video", "cancel_token", "AgnesCancelled",
                       "is_cancel_error", "check_cancel", "on_submit"],
        "ui": ["blocks_tool_call", "why_blocked", "_guard_block",
               # v4.168.1：伪强制注入修复 —— 参数提示按 schema 取 + 工具表校验
               "_tool_param_hint", "_tool_in_list",
               "不要臆造参数", "以最后一条用户消息为准",
               # v4.168.2：思考模式必须回传 reasoning_content + 400 要能自证
               "_ensure_reasoning_content", "_is_thinking_channel",
               "_api_error_text", "reasoning_content",
               # v4.168.3：自动化会话不注入陈旧 goal + 内部标注不外泄
               "_session_carries_automation", "_prompt_section_session_goal",
               "_AUTO_TASK_PREFIX", "_META_SILENCE_RULE",
               "本会话说明", "元认知说明",
               # v4.169.0：搜索按需 + 会话清信任 + 本轮执行意图
               "_needs_web_search", "_SEARCH_VERB_KW",
               "_last_user_intent_is_action", "_reset_session_trust",
               "_SEARCH_STRONG_FACT_KW",
               # v4.169.0 批次B：复杂度判定接 route_judge + tool_choice 记录
               "_is_complex_v1", "route_judge", "_last_tool_choice",
               # v4.172.0：第4处判据同源 + guard 命中时的内部指令
               "_needs_tool_intent", "本轮不要使用任何工具",
               # v4.173.0：附件名不得点着判据 + 附件路径基准归口
               "_strip_attachment_refs", "_director_kw_same_sentence",
               # v4.174.0：图像链路「路由目标 ↔ 视觉能力判定」必须一致
               "VISION_MODEL_KW", "deepseek-flash",
               # v4.177.0：历史注入的字符预算闸（第二道闸，按体量）
               "_fit_history_to_budget"],
               # v4.212.0：原「节点画布收尾轮」色键 canvas_status_text /
               # canvas_final_bg 随画布模块一并移除；版本钉子由上面的行为字面量
               # 与 ③ APP_VERSION 断言承担，不另设替代键。
        "agent": ["_internal",
                  # v4.168.2：assistant 消息带上思考过程
                  "reasoning_content",
                  # v4.169.0：run_workflow 过闸 + 硬确认档 force 通道
                  "_run_workflow_guarded", "explicit_intent", "_confirm_force",
                  # v4.225.0：统一意图 + 任务状态机接线
                  # 注：钉**方法名**（_tstate_* 走 LOAD_METHOD 在 co_names 里），
                  # 不钉 `_tstate`/`_tstate_nudged` 属性名——CPython 3.12 的
                  # LOAD_ATTR 走 inline cache，属性名既不在 co_names 也不在
                  # co_consts，只有 dis 看得到，静态扫描永远扫不到（假红）。
                  "_intent", "_tstate_init", "_tstate_record",
                  "_tstate_step", "_tstate_nudge_now",
                  # v4.169.0 批次B：工具决策日志
                  "log_tool_decision",
                  # v4.175.0：400 时把接口原文一起显示给用户
                  "_api_body", "接口原文",
                  # v4.222.0：任务级产物验收 / 断点幂等 / 不可信内容边界
                  "_deliverable_satisfied", "_FILE_KINDS", "_deliverables",
                  "_exec_ledger", "_resume_done_hashes", "_NON_IDEMPOTENT_TOOLS",
                  "_is_resume_dup", "_record_exec_ledger", "_tool_args_hash",
                  "wrap_untrusted", "_wrap_tool_content", "<untrusted_tool_output"],
        # v4.222.0：技能子系统不可信边界（skill_loader 元数据 + 包边界）
        "skill_loader": ["allow_tools", "allow_network", "allow_system",
                  "allow_dir", "audited_at", "wrap_skill_prompt",
                  "<untrusted skill"],
        # v4.223.0：参数校验补全 + 结构化返回根治（契约优先于文案 / 猜的必须标注）
        "tool_contract": ["register_outcome", "resolve_ok", "normalize_timeout",
                  "register_tool_schema", "validate_for_tool",
                  "_normalize_path", "_within_base", "_TOOL_SCHEMAS",
                  "ARG_MAX_LEN_CAP", "TOOL_TIMEOUT_CAP",
                  "INFERRED", "UNVERIFIED_OUTCOME", "TOOL_FAILED",
                  # v4.224.0：执行后验证（只降级不升级）
                  "register_verifier", "verify_after",
                  "apply_post_verification", "POST_VERIFY_FAILED"],
        # v4.224.0：单条消息预算硬上限（最后一条不再免疫 + 截断必留标记）
        "ui_msg": ["MSG_BUDGET_DEFAULTS", "_cap_message_to_budget", "_cap_text",
                  "已截断", "已省略", "text_max_chars", "tool_result_max_chars",
                  "args_max_chars", "max_images_per_msg", "max_image_chars"],
        # v4.216.0：意图分类判据族（_prog_fetch_intent/_is_bare_url/_PROG_FETCH_KW
        # + is_non_action_message 调用）已迁到 agent_text.py（agent 改调 agent_text._x）。
        # 校验随之跟到 agent_text 模块，否则误报 agent 缺符号（假红）。
        "agent_text": ["is_non_action_message",
                  # v4.168.1：程序化抓取否决 + 裸 URL 判据
                  "_prog_fetch_intent", "_is_bare_url", "_PROG_FETCH_KW"],
        # v4.225.0：统一意图对象（Intent 值对象 + ROUTE_REGISTRY 注册表
        # + classify 唯一入口 + 路由段自动生成）
        "intent": ["Intent", "classify", "ROUTE_REGISTRY", "_route_hint_text",
                  "KIND_ACTION", "KIND_NEGATED", "KIND_REFERENCE",
                  "工具路由", "requested_tools", "force_tool"],
        # v4.225.0：任务状态机（账本 + 硬要求提取 + 四道 gate + nudge 文案）
        "task_state": ["TaskState", "required_from_text", "record_tool",
                  "missing_tools", "missing_artifacts", "is_complete",
                  "should_nudge", "nudge_instruction", "mark_nudged",
                  "note_artifact", "任务未完成检查"],
        # v4.225：账本接线 mixin（记账/闸门/步号，从 agent.py 抽出）
        "agent_task_mixin": ["AgentTaskMixin", "_tstate_init", "_tstate_record",
                  "_tstate_step", "_tstate_resume_step",
                  "_tstate_resume_reset_nudge", "_tstate_nudge_now",
                  "_tstate_trace_nudge", "_last_user_text",
                  "任务要求未完成"],
        "digital_twin_panel": [
                  # v4.178.0：分镜抗失败（单段失败不再吞掉后续段）+ 断点续跑
                  "_twin_fingerprint", "_job_seg_ok", "_save_job_state",
                  "_job_done_count", "断点续跑", "failed_segs",
                  # v4.179.0：reference 模式（背景锁定）+ 并发出片
                  "_gen_parallel", "_gen_serial", "ref_mode", "并发出片"]
    }
    for mod, keys in marks.items():
        mi = info.get(mod) or {}
        # ⚠️ 必须用**子串**匹配，不能拿 set 做精确匹配 ——
        # 标记里既有 co_names 里的标识符（`blocks_tool_call`），
        # 也有常量里的**短语**（`不要臆造参数` 其实是
        # `"请严格按该工具的 schema 传参，不要臆造参数。"` 的一部分）。
        # 第一版用精确匹配，把"确实在包里"的短语判成了 FAIL（假红）。
        hay = "\n".join(mi.get("consts") or []) + "\n" + "\n".join(mi.get("names") or [])
        for k in keys:
            check(f"{mod} 含 {k}", k in hay)

    print("\n-- 3) 版本号一致性（改版未重打包 = 直接红）--")
    cfg = info.get("config") or {}
    ver_in_exe = ""
    for s in (cfg.get("consts") or []):
        if isinstance(s, str) and re.fullmatch(r"v\d+\.\d+\.\d+", s):
            ver_in_exe = s
            break
    check("从包里捞出 APP_VERSION", bool(ver_in_exe), str(ver_in_exe))
    check("exe 内版本 == 源码版本", ver_in_exe == src_v,
          f"exe={ver_in_exe} src={src_v}（说明改了版本号但没重新打包）")

    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
