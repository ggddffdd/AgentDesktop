# -*- coding: utf-8 -*-
"""v4.225.0 进包核验：结构化执行（P3 统一意图 Intent + P2 主 Agent 任务状态机）。

本轮改动（审查报告第三阶段 a 批）：
  · P3 统一意图 Intent：路由规则此前散在五处（agent_text._route_force_tool /
    _detect_action_intent / _content_creation_only、intent_guard 散词表、
    agent.py 系统提示里手写的路由句）。新增 intent.py 收成「一个 Intent 对象 +
    一张 ROUTE_REGISTRY」；系统提示的【工具路由】段由 _route_hint_text() 自动生成。
    纪律：只做壳不改行为 —— force_tool / needs_action / text_only 三结论逐条
    等于既有判据原始输出。
  · P2 主 Agent 轻量任务状态机：新增 task_state.py 任务账本（子目标/当前步/
    已完成/失败/待验证产物），在 _handle_tool_result 里记账，收尾 break 前做
    四道 gate 的定向 nudge。四道 gate 任一不满足 → 完全按旧路径收尾。

核验重点：① 版本号 v4.225.0 且不含 v4.224.0；② intent / task_state 两个新模块
真进包（PYZ toc 里有）；③ 注册表与系统提示生成符号在位；④ 状态机记账与
gate 符号在位；⑤ agent 真引用两个新模块（接线没丢）；⑥ ui.py 真调
_route_hint_text（系统提示不再手写）；⑦ 复用关键历史钉子（v4.221硬确认 /
v4.222 验收与边界 / v4.223 契约 / v4.224 验证与预算 / tool_contract /
系统控制 15 tool_* / 画布反向 / 测试不随包）防重打包回归。
"""
import hashlib
import marshal
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXE = ROOT / "dist" / "小臭玩AI" / "小臭玩AI.exe"

MODULES = ["tools", "agent", "agent_node", "risk", "permissions",
           "system_control_tools", "software_control_tools", "config",
           "tool_contract", "skill_loader",
           "ui_msg"]

_n_pass = _n_fail = 0


def check(name, ok, detail=""):
    global _n_pass, _n_fail
    if ok:
        _n_pass += 1
        print(f"  [PASS] {name}")
    else:
        _n_fail += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _prim(x):
    if isinstance(x, (str, int, float, bool, bytes, type(None))):
        return x
    if isinstance(x, tuple):
        return tuple(_prim(v) for v in x)
    if isinstance(x, frozenset):
        return ("<frozenset>", tuple(sorted((repr(_prim(v)) for v in x))))
    if isinstance(x, types.CodeType):
        return ("<code>", x.co_name)
    return f"<{type(x).__name__}>"


def _fingerprint(co):
    acc = hashlib.sha256()

    def walk(c, depth=0):
        acc.update((c.co_name + "\x00").encode("utf-8"))
        acc.update(c.co_code)
        for seq in (c.co_names, c.co_varnames, c.co_freevars, c.co_cellvars):
            acc.update(("\x01".join(map(str, seq)) + "\x02").encode("utf-8"))
        for k in c.co_consts:
            if isinstance(k, types.CodeType):
                walk(k, depth + 1)
            else:
                acc.update(repr(_prim(k)).encode("utf-8"))
            acc.update(b"\x03")
        acc.update(b"\x04")

    walk(co)
    return acc.hexdigest()


def _code_iter(c):
    yield c
    for k in c.co_consts:
        if isinstance(k, types.CodeType):
            yield from _code_iter(k)


def _str_consts(co):
    out = set()
    for c in _code_iter(co):
        for x in c.co_consts:
            if isinstance(x, str):
                out.add(x)
            elif isinstance(x, (tuple, frozenset)):
                out.update(y for y in x if isinstance(y, str))
    return out


def _code_names(co):
    out = set()
    for c in _code_iter(co):
        out |= set(c.co_names)
        out.add(c.co_name)
    return out


def _func_codes(co, prefix):
    return {c.co_name: c for c in _code_iter(co) if c.co_name.startswith(prefix)}


def _load(za, name):
    got = za.extract(name)
    return got if isinstance(got, types.CodeType) else marshal.loads(got)


def _load_entry_script(exe: Path, name: str = "main"):
    try:
        from PyInstaller.archive.readers import CArchiveReader
        r = CArchiveReader(str(exe))
        if name not in r.toc:
            return None
        raw = r.extract(name)
        co = marshal.loads(raw)
        return co if isinstance(co, types.CodeType) else None
    except Exception:
        return None


def main():
    print("v4.221.0 进包核验（硬确认 / 续跑记失败 / 路由去幽灵名 + 关键历史钉子复验）")
    print("-" * 62)
    if not EXE.is_file():
        print(f"未找到产物：{EXE}")
        return 1

    data = EXE.read_bytes()
    off = data.find(b"PYZ\x00")
    check("exe 内含 PYZ 段", off != -1, f"offset={off}")
    if off == -1:
        print("\n找不到 PYZ 段，后续核验无意义，提前退出。")
        print(f"汇总：PASS={_n_pass} FAIL={_n_fail}")
        return 1
    print(f"  产物：{EXE.name}  {len(data)/1048576:.1f} MB")

    sys.path.insert(0, str(ROOT))
    from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader
    za = ZlibArchiveReader(str(EXE), start_offset=off)
    names = set(za.toc)
    check("PYZ 模块表可读（模块数 > 50）", len(names) > 50, f"{len(names)} 个模块")

    print("\n-- 1) 本轮改动的模块：字节码指纹（源码 compile() == 包内 code object）--")
    for mod in MODULES:
        if mod not in names:
            check(f"{mod} 在 PYZ 模块表里", False, "模块缺失 → 运行必 ImportError")
            continue
        src_file = ROOT / f"{mod}.py"
        if not src_file.is_file():
            check(f"{mod} 源码存在", False, str(src_file))
            continue
        src = src_file.read_bytes()
        co_src = compile(src.decode("utf-8-sig"), f"{mod}.py", "exec")
        co_pkg = _load(za, mod)
        same = _fingerprint(co_src) == _fingerprint(co_pkg)
        check(f"{mod} 字节码指纹一致（包内 == 当前源码）", same,
              f"src={_fingerprint(co_src)[:12]} pkg={_fingerprint(co_pkg)[:12]}")

    print("\n-- 2) 版本一致性 --")
    if "config" in names:
        consts = _str_consts(_load(za, "config"))
        check("PYZ 内 config 版本常量 == v4.225.0",
              "v4.225.0" in consts, f"包内版本串={sorted(s for s in consts if s.startswith('v4.22'))}")
        check("PYZ 内不含上一版旧版本常量 v4.224.0",
              "v4.224.0" not in consts, "残留旧版本串（可能是增量打包旧模块）")
    else:
        check("config 在 PYZ 里", False, "缺失 → 启动崩")

    print("\n-- 3) 本轮钉子：P1#1 四工具进 ALWAYS_CONFIRM（硬确认不可绕过会话信任）--")
    _hard = {"process_kill", "app_kill", "app_close", "clean_recycle_bin"}
    try:
        from risk import ALWAYS_CONFIRM
        check("★ 源码 risk.ALWAYS_CONFIRM 含四工具",
              _hard <= set(ALWAYS_CONFIRM),
              f"missing={sorted(_hard - set(ALWAYS_CONFIRM))}")
    except Exception as e:
        check("★ 源码 risk.ALWAYS_CONFIRM 可导入并断言", False, f"导入失败：{e!r}")
    if "risk" in names:
        co_risk = _load(za, "risk")
        rcn = _code_names(co_risk)
        check("★ 包内 risk 引用 ALWAYS_CONFIRM（硬确认档）",
              "ALWAYS_CONFIRM" in rcn, "ALWAYS_CONFIRM 没编进去")
        rcs = _str_consts(co_risk)
        check("★ 包内 risk 常量表含四工具名（process_kill/app_kill/app_close/clean_recycle_bin）",
              _hard <= rcs, f"缺={sorted(_hard - rcs)}")

    print("\n-- 3b) 本轮钉子：P2#3 路由表去 software_run 幽灵名 --")
    if "config" in names:
        ccs = _str_consts(_load(za, "config"))
        # 路由表工具名嵌在提示词字符串里（子串），故用子串搜索而非完整字符串成员判断
        check("★ 包内 config 常量表不含幽灵名 software_run",
              not any("software_run" in s for s in ccs),
              "路由表仍写着不存在的 tool_software_run → 小臭会调幽灵工具")
        for real in ("app_launch", "app_close", "app_click", "app_type",
                     "app_window_state", "app_list_controls"):
            check(f"★ 包内 config 路由含真实工具 {real}", any(real in s for s in ccs),
                  f"{real} 未进路由表")

    print("\n-- 3c) 本轮钉子：P1#2 续跑 API 异常记失败 + 保留断点 --")
    if "agent" in names:
        co_agent = _load(za, "agent")
        an = _code_names(co_agent)
        check("★ 包内 agent 引用 _note_exit（续跑失败标记）",
              "_note_exit" in an, "_note_exit 没编进去 → 续跑异常不记失败")
        check("★ 包内 agent 引用 _resumable_stop（续跑保留断点）",
              "_resumable_stop" in an, "_resumable_stop 没编进去 → 断点被误删")
    else:
        check("agent 在 PYZ 里", False, "缺失 → 主流程崩")

    print("\n-- 3h) 本轮钉子：P1 任务验收（产物级结局判定）--")
    if "agent" in names:
        co_agent = _load(za, "agent")
        an = _code_names(co_agent)
        acs = _str_consts(co_agent)
        check("★ 包内 agent 定义 _deliverable_satisfied（产物级验收谓词）",
              "_deliverable_satisfied" in an, "_deliverable_satisfied 没编进去")
        check("★ 包内 agent 定义 _FILE_KINDS（产物类型白名单）",
              "_FILE_KINDS" in an, "_FILE_KINDS 没编进去")
        check("★ 包内 agent 引用 _deliverables（累积声明交付物）",
              "_deliverables" in an, "_deliverables 没编进去 → 验收无数据")
    else:
        check("agent 在 PYZ 里", False, "缺失 → 主流程崩")

    print("\n-- 3i) 本轮钉子：P2 断点幂等（恢复不重复执行副作用）--")
    if "agent" in names:
        co_agent = _load(za, "agent")
        an = _code_names(co_agent)
        for sym in ("_exec_ledger", "_resume_done_hashes", "_NON_IDEMPOTENT_TOOLS",
                    "_is_resume_dup", "_record_exec_ledger", "_tool_args_hash"):
            check(f"★ 包内 agent 含断点幂等符号 {sym}",
                  sym in an, f"{sym} 没编进去 → 恢复会重复执行副作用")

    print("\n-- 3j) 本轮钉子：P1 不可信内容边界（工具/技能产出显式包边界）--")
    if "agent" in names:
        co_agent = _load(za, "agent")
        an = _code_names(co_agent)
        acs = _str_consts(co_agent)
        check("★ 包内 agent 定义 wrap_untrusted（不可信内容包装）",
              "wrap_untrusted" in an, "wrap_untrusted 没编进去")
        check("★ 包内 agent 定义 _wrap_tool_content（工具产出包边界）",
              "_wrap_tool_content" in an, "_wrap_tool_content 没编进去")
        check("★ 包内 agent 常量含 <untrusted_tool_output 边界标签",
              any("<untrusted_tool_output" in s for s in acs),
              "不可信工具边界标签没编进去")
    if "skill_loader" in names:
        co_sl = _load(za, "skill_loader")
        sln = _code_names(co_sl)
        sls = _str_consts(co_sl)
        for sym in ("allow_tools", "allow_network", "allow_system",
                    "allow_dir", "audited_at", "wrap_skill_prompt"):
            check(f"★ 包内 skill_loader 含技能元数据/边界符号 {sym}",
                  (sym in sln) or any(sym in s for s in sls),
                  f"{sym} 没编进去 → 技能边界缺元数据")
        check("★ 包内 skill_loader 常量含 <untrusted skill 边界标签",
              any("<untrusted skill" in s for s in sls),
              "技能边界标签没编进去")

    print("\n-- 3k) 本轮钉子：P2 参数校验补全 + P2 结构化返回根治（v4.223）--")
    if "tool_contract" in names:
        co_tc2 = _load(za, "tool_contract")
        tcn = _code_names(co_tc2)
        tcs = _str_consts(co_tc2)
        # 结构化返回根治：契约注册表 + 「猜的」标注 + 机器可读错误码
        for sym in ("register_outcome", "resolve_ok"):
            check(f"★ 包内 tool_contract 定义 {sym}（显式结局契约）",
                  sym in tcn, f"{sym} 没编进去 → 成败仍会退回按文案猜")
        for mark in ("INFERRED", "UNVERIFIED_OUTCOME", "TOOL_FAILED"):
            check(f"★ 包内 tool_contract 常量含 {mark}",
                  any(mark in s for s in tcs),
                  f"{mark} 没编进去 → 猜出来的结论会冒充已验证")
        for sym in ("error_code", "retryable", "verified"):
            check(f"★ 包内 tool_contract 含 ToolResult 机器可读字段 {sym}",
                  (sym in tcn) or any(sym in s for s in tcs),
                  f"{sym} 没编进去 → 结构化返回补全缺失")
        # 参数校验补全：路径/长度/未知字段/超时 + schema 注册表
        for sym in ("normalize_timeout", "register_tool_schema",
                    "validate_for_tool", "_normalize_path", "_within_base",
                    "_TOOL_SCHEMAS"):
            check(f"★ 包内 tool_contract 定义 {sym}（参数校验补全）",
                  sym in tcn, f"{sym} 没编进去 → 参数校验补全没进包")
        check("★ 包内 tool_contract 常量含未知字段策略串 reject/strip",
              any("reject" in s and "strip" in s for s in tcs),
              "未知字段策略没编进去")
        check("★ 包内 tool_contract 常量含参数校验报错串（越界/超过上限）",
              any(("越界" in s) or ("超过上限" in s) for s in tcs),
              "路径围栏/长度上限报错串没编进去")
    if "tools" in names:
        co_ts = _load(za, "tools")
        tsn = _code_names(co_ts)
        tss = _str_consts(co_ts)
        check("★ 包内 tools 调用 validate_for_tool（exec_tool 统一校验接入）",
              "validate_for_tool" in tsn,
              "validate_for_tool 没被调用 → 参数校验只覆盖老 3 处")
        check("★ 包内 tools 常量含「参数校验未通过」拦截串",
              any("参数校验未通过" in s for s in tss),
              "exec_tool 拦截串没编进去")

    print("\n-- 3l) 本轮钉子：P2 执行后验证 + P2 单条消息预算硬上限（v4.224）--")
    if "tool_contract" in names:
        co_tc3 = _load(za, "tool_contract")
        tcn3 = _code_names(co_tc3)
        tcs3 = _str_consts(co_tc3)
        for sym in ("register_verifier", "verify_after",
                    "apply_post_verification"):
            check(f"★ 包内 tool_contract 定义 {sym}（执行后验证）",
                  sym in tcn3, f"{sym} 没编进去 → 副作用没人回头验")
        check("★ 包内 tool_contract 常量含 POST_VERIFY_FAILED",
              any("POST_VERIFY_FAILED" in s for s in tcs3),
              "验证失败错误码没编进去 → 降级无法被机器识别")
        check("★ 包内 tool_contract 常量含执行后验证证据串",
              any("执行后验证未通过" in s for s in tcs3),
              "证据尾巴没编进去 → 模型看不到验证结论")
    else:
        check("tool_contract 在 PYZ 里", False, "缺失 → 契约层空转")
    if "system_control_tools" in names:
        co_sc = _load(za, "system_control_tools")
        scn = _code_names(co_sc)
        scs = _str_consts(co_sc)
        for sym in ("_verify_process_kill", "_verify_clean_recycle_bin",
                    "register_verifier"):
            check(f"★ 包内 system_control_tools 定义 {sym}（真信号验证器）",
                  sym in scn, f"{sym} 没编进去 → 验证器没接上")
        check("★ 包内 system_control_tools 常量含验证证据串（残留/已空）",
              any(("残留" in s) or ("已空" in s) for s in scs),
              "验证证据串没编进去")
    if "ui_msg" in names:
        co_um = _load(za, "ui_msg")
        umn = _code_names(co_um)
        ums = _str_consts(co_um)
        for sym in ("MSG_BUDGET_DEFAULTS", "_cap_message_to_budget",
                    "_cap_text"):
            check(f"★ 包内 ui_msg 定义 {sym}（单条消息预算）",
                  sym in umn, f"{sym} 没编进去 → 最后一条仍能绕过预算")
        for mark in ("已截断", "已省略", "text_max_chars",
                     "tool_result_max_chars", "args_max_chars",
                     "max_images_per_msg", "max_image_chars"):
            check(f"★ 包内 ui_msg 常量含 {mark}",
                  any(mark in s for s in ums),
                  f"{mark} 没编进去 → 预算维度缺失")
    else:
        check("ui_msg 在 PYZ 里", False, "缺失 → 消息拼装崩")

    print("\n-- 3d) 关键历史钉子：tool_contract 契约真进包（v4.220 收口）--")
    if "tool_contract" in names:
        co_tc = _load(za, "tool_contract")
        tcn = _code_names(co_tc)
        tcs = _str_consts(co_tc)
        check("★ 包内 tool_contract 定义 ToolResult（结构化返回 dataclass）",
              "ToolResult" in tcn, "ToolResult 不在包内 co_names → 契约没编进去")
        check("★ 包内 tool_contract 定义 _validate_args（入口参数校验）",
              "_validate_args" in tcn, "_validate_args 不在包内 co_names")
        check("★ 包内 tool_contract 定义 compute_impact_scope（影响范围计算）",
              "compute_impact_scope" in tcn, "compute_impact_scope 不在包内")
        check("★ 包内 tool_contract 含脱敏逻辑 _mask_sensitive/_mask_recursive",
              ("_mask_sensitive" in tcn) or ("_mask_recursive" in tcn),
              "脱敏函数没编进去 → 敏感路径会明文泄漏")
        check("★ 包内 ToolResult 含 ok/msg/deliverables/schedule 字段语义",
              any("deliverables" in s for s in tcs) and any("schedule" in s for s in tcs),
              "结构化返回字段语义缺失")
    else:
        check("tool_contract 在 PYZ 里", False, "缺失 → 全链路契约崩")

    print("\n-- 3e) 关键历史钉子：系统/软件控制工具返回 ToolResult + 注入影响范围 --")
    if "system_control_tools" in names:
        co_sc = _load(za, "system_control_tools")
        scn = _code_names(co_sc)
        check("★ 包内 system_control_tools 引用 ToolResult（结构化返回）",
              "ToolResult" in scn, "ToolResult 引用缺失 → 仍返回旧三元组")
        check("★ 包内 system_control_tools 引用 _validate_args（入口校验）",
              "_validate_args" in scn, "_validate_args 没被编进去")
        check("★ 包内 system_control_tools 接线影响范围预检 register_impact_scope（P1#5）",
              "register_impact_scope" in scn,
              "register_impact_scope 没被编进去 → 影响范围预检未接入")
        tools_co = _func_codes(co_sc, "tool_")
        n_tools = len(tools_co)
        _want15 = {"screenshot", "mouse_move", "mouse_click", "mouse_scroll",
                   "keyboard_type", "keyboard_press", "clipboard_read",
                   "clipboard_write", "window_list", "window_focus",
                   "window_get_info", "process_list", "process_kill",
                   "process_start", "clean_recycle_bin"}
        lack = sorted(n for n, c in tools_co.items()
                      if not {"progress", "stop_event", "should_stop"} <= set(c.co_varnames))
        check(f"system_control 共 15 个 tool_*（实际 {n_tools}）", n_tools == 15,
              f"漏={sorted(_want15 - set(tools_co))}")
        check("★ 全部 15 个 tool_* 形参表都含 should_stop", not lack, f"缺形参的={lack}")
        scl = _str_consts(co_sc)
        check("★ clean_recycle_bin 工具 + 路由表登记都在包内",
              "tool_clean_recycle_bin" in scn and "clean_recycle_bin" in scl,
              "清空回收站真实工具没编进去")
        check("★ 关键进程黑名单 9 名仍在包内常量表",
              {"lsass.exe", "csrss.exe", "winlogon.exe", "wininit.exe", "smss.exe",
               "services.exe", "svchost.exe", "explorer.exe", "dwm.exe"} <= scl,
              "关键进程名单不全 → 系统保护缺口")
    else:
        check("system_control_tools 在 PYZ 里", False, "缺失 → 系统控制工具全不可用")

    if "software_control_tools" in names:
        co_sw = _load(za, "software_control_tools")
        swn = _code_names(co_sw)
        check("★ 包内 software_control_tools 引用 ToolResult（app_kill 结构化返回）",
              "ToolResult" in swn, "app_kill 没走 ToolResult")
        check("★ 包内 software_control_tools 引用 _validate_args（app_kill 校验）",
              "_validate_args" in swn, "_validate_args 没编进去")
        check("★ 包内 software_control_tools 接线影响范围预检 register_impact_scope（P1#5）",
              "register_impact_scope" in swn,
              "register_impact_scope 没被编进去 → 影响范围预检未接入")
    else:
        check("software_control_tools 在 PYZ 里", False, "缺失 → 软件控制工具全不可用")

    print("\n-- 3f) 关键历史钉子：agent 确认弹窗注入『影响范围』段 --")
    if "agent" in names:
        co_agent = _load(za, "agent")
        an = _code_names(co_agent)
        acs = _str_consts(co_agent)
        check("★ 包内 agent 引用 compute_impact_scope（确认弹窗取影响范围）",
              "compute_impact_scope" in an, "compute_impact_scope 没被编进去")
        check("★ 包内 agent._build_confirm_detail 文案含『影响范围：』注入（P1#5）",
              any("影响范围" in s for s in acs), "确认弹窗影响范围文案没编进去")
    else:
        check("agent 在 PYZ 里", False, "缺失 → 主流程崩")

    print("\n-- 3m) 本轮钉子：P3 统一意图 Intent + P2 任务状态机（v4.225）--")
    if "intent" in names:
        co_it = _load(za, "intent")
        itn = _code_names(co_it)
        its = _str_consts(co_it)
        for sym in ("Intent", "classify", "ROUTE_REGISTRY", "_route_hint_text"):
            check(f"★ 包内 intent 定义 {sym}（统一意图对象）",
                  sym in itn or sym in its,
                  f"{sym} 没编进去 → 路由收口失效")
        for k in ("KIND_ACTION", "KIND_NEGATED", "KIND_REFERENCE"):
            check(f"★ 包内 intent 常量 {k}",
                  any(k in s for s in its), f"{k} 缺失 → 意图分类降级")
        check("★ 包内 intent 含系统提示路由段标题",
              any("工具路由" in s for s in its),
              "路由段标题没编进去 → 系统提示不会自动生成路由说明")
        check("★ 包内 intent 真调 agent_text 的既有路由判据（只做壳不改行为）",
              "_route_force_tool" in itn,
              "intent 没引用 _route_force_tool → 不是收口是另起炉灶")
    else:
        check("intent 在 PYZ 里", False, "缺失 → 统一意图层不存在，exe 会 ModuleNotFoundError")
    if "task_state" in names:
        co_ts = _load(za, "task_state")
        tsn = _code_names(co_ts)
        tss = _str_consts(co_ts)
        for sym in ("TaskState", "required_from_text", "record_tool",
                    "should_nudge", "nudge_instruction", "missing_artifacts"):
            check(f"★ 包内 task_state 定义 {sym}（任务状态机）",
                  sym in tsn, f"{sym} 没编进去 → 任务账本残缺")
        check("★ 包内 task_state 常量含补做提示串",
              any("任务未完成检查" in s for s in tss),
              "补做提示串没编进去 → 模型收不到定向指令")
    else:
        check("task_state 在 PYZ 里", False, "缺失 → 任务状态机不存在，exe 会 ModuleNotFoundError")
    if "agent_task_mixin" in names:
        co_mx = _load(za, "agent_task_mixin")
        mxn = _code_names(co_mx)
        mxs = _str_consts(co_mx)
        for sym in ("AgentTaskMixin", "_tstate_init", "_tstate_record",
                    "_tstate_step", "_tstate_resume_step",
                    "_tstate_resume_reset_nudge", "_tstate_nudge_now",
                    "_tstate_trace_nudge", "_last_user_text"):
            check(f"★ 包内 agent_task_mixin 定义 {sym}（账本接线）",
                  sym in mxn, f"{sym} 没编进去 → 接线方法缺失")
        check("★ 包内 agent_task_mixin 含补做状态提示串",
              any("任务要求未完成" in s for s in mxs),
              "补做状态串没编进去 → 用户看不到为何中断")
    else:
        check("agent_task_mixin 在 PYZ 里", False,
              "缺失 → 账本接线不存在，exe 会 ModuleNotFoundError")
    if "agent" in names:
        co_ag2 = _load(za, "agent")
        ag2n = _code_names(co_ag2)
        ag2s = _str_consts(co_ag2)
        check("★ 包内 agent 引用 intent.classify（统一意图接线）",
              "intent" in ag2n and "classify" in ag2n,
              "agent 没引用 intent → 主循环仍在用分散路由")
        check("★ 包内 agent 引用 task_state（任务状态机接线）",
              "task_state" in ag2n, "task_state 没被编进去 → 账本不记账")
        # 注意：这里**钉方法名而不是属性名**。CPython 3.12 的 `LOAD_ATTR`
        # 走 inline cache —— `self._tstate` / `self._tstate_nudged` 这类
        # 属性名既不在 `co_names` 也不在 `co_consts`（实测源码字节码同样
        # 扫不到），只有 `dis` 能看到。方法名 `_tstate_init` 等走的是
        # `LOAD_METHOD` → 在 `co_names` 里，稳定可扫。
        for _m in ("_tstate_init", "_tstate_record", "_tstate_step",
                   "_tstate_resume_step", "_tstate_resume_reset_nudge",
                   "_tstate_nudge_now"):
            check(f"★ 包内 agent 真调 {_m}（账本接线方法）",
                  _m in ag2n,
                  f"{_m} 没编进去 → 该处接线是死引用（任务账本形同虚设）")
        check("★ 包内 agent 真 mixin 了 AgentTaskMixin（接线不是死引用）",
              "AgentTaskMixin" in ag2n,
              "agent 没引用 AgentTaskMixin → 记账/闸门全是死代码")
        check("★ 包内 agent 调 _tstate_nudge_now（收尾闸门真接线）",
              "_tstate_nudge_now" in ag2n,
              "_tstate_nudge_now 没编进去 → 任务账本形同虚设")
    if "ui" in names:
        co_ui2 = _load(za, "ui")
        ui2n = _code_names(co_ui2)
        check("★ 包内 ui 真调 intent._route_hint_text（系统提示路由段自动生成）",
              "_route_hint_text" in ui2n,
              "ui 没调 _route_hint_text → 仍在用手写路由句（加工具会静默失配）")
    else:
        check("ui 在 PYZ 里", False, "缺失 → 主界面崩")

    print("\n-- 3g) 关键历史钉子：画布模块不得随包 + 测试不随包 --")
    for _cm in ("canvas_graph", "canvas_panel", "canvas_export", "executors"):
        check(f"★ 包内不含已移除画布模块 {_cm}", _cm not in names, "死代码进包")
    leaked = sorted(n for n in names
                    if n.startswith("test_") or "perturb" in n or "pep701" in n)
    check("PYZ 里没有 tests/ 判据套件与根目录扰动脚本",
          not leaked, f"泄漏的模块={leaked}")

    co_main = _load_entry_script(EXE, "main")
    check("main 从 CArchive 取到（入口脚本不在 PYZ 是正常结构）", co_main is not None)

    print(f"  PYZ 模块数：{len(names)}")
    print("\n" + "=" * 62)
    print(f"汇总：PASS={_n_pass} FAIL={_n_fail}")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
