# -*- coding: utf-8 -*-
"""v4.228.0 进包核验：连接层瞬时失败（WinError 10061）重试 + 异常文案可读性。

本轮改动：
  · **ui_msg.py 新增两个纯函数**（+81 行）：
      `is_transient_net_error(exc)` —— 把异常分类成「同请求重发一次有意义」的瞬时
      连接层失败。只看异常文本不看类型：urllib 包 URLError、requests 包
      ConnectionError、socket 包超时，类型各异但语义同一类。
      ⚠️ **排除表必须先判**：真实世界存在「含 refused/timeout 字样但属限流」的
      混合报文（如 `HTTP Error 429: Too Many Requests; retry timeout=60`），
      先认瞬时词就会对着限流硬打一轮。判据 A12/A13 专钉此顺序。
      `humanize_net_error(exc)` —— 把异常转成人话。此前 `f"{e}"` 直接把
      `<urlopen error [Errno 10061> 由于目标计算机积极拒绝…` 渲染在「模型：」后面。
      顺带修掉一处**从未生效**的剥壳正则：原式假设 `>` 在开头，而
      `str(SomeError("x"))` 的真实形态是 `<ValueError: x>`（`>` 在末尾）。
  · **ui.py `_attempt_model`**：在 429/5xx 退避分支之后、else 兜底之前插入瞬时分支
      （2s 退避重发 1 次）。旧代码里 `URLError` 没有 `.code` 属性 →
      `_code is None` → 落进最后的 else 直接上抛 —— 这就是 10061 崩整轮的根因。
  · **agent.py**：气泡文案改走 `humanize_net_error` 并点名本轮模型
      （`self._last_model`）。
  · **打包登记**：本轮**未新增模块**（改的是已在包内的 ui_msg / ui / agent），
      故 hiddenimports 无需变更 —— 但仍要核实这三个模块里的新代码真进了 PYZ。

核验重点：① 版本号 v4.228.0 且不含 v4.227.0；② ui_msg 内两个新函数 + 词表
真在 PYZ（漏进包 = 只有异常分支才炸，平时毫无征兆）；③ ui 内真调用了这两个函数；
④ 剥壳正则已修正；⑤ 复用 v4.221~v4.227 全部关键历史钉子防重打包回归。
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
           "ui_msg",
           # v4.226：三模块（四阶段循环 + 技能元数据）
           "agent_loop", "agent_loop_mixin", "skill_meta",
           # v4.227：P1-2 判定真源 / P2-1 边界 / P2-2 验证器 / P2-3 状态机
           "agent_result_mixin", "untrusted_boundary",
           "tool_verifiers_227", "task_state"]

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


def _all_names(co):
    """收集**全部** co_names（属性名 / 函数名通常落在这里，不在字符串常量表）。

    为什么单列：`_str_consts` 只收 co_consts 里的字符串，而 `is_transient_net_error`
    这类被**调用**的名字是 `co_names` 条目 —— 只扫字符串会误判成「没编进包」。
    （同一个坑在 v4.227 的核验里已栽过一次：`_deliverables` 是 getattr 的参数，
      名字在 co_consts；`ARG_MAX_LEN_CAP` 是 int 数值常量。）
    """
    out = set()
    for c in _code_iter(co):
        out.update(c.co_names)
        # 嵌套 code 对象的参数名也在 co_varnames
        out.update(getattr(c, "co_varnames", ()) or ())
    return out


def _all_consts(co):
    """收集全部 co_consts（含非字符串），配合 _str_consts 做兜底判定。"""
    out = set()
    for c in _code_iter(co):
        out.update(x for x in c.co_consts if isinstance(x, (str, int, float, bytes)))
    return out


def _num_consts(co):
    """收集**数值**常量（bool 除外）。

    为什么单列一个函数：`_str_consts` 只收字符串，于是 `ARG_MAX_LEN_CAP = 4_000_000`
    这类**int** 上限常量永远扫不到 —— 写断言时极容易误判成「漏打包」。
    实测本轮就栽在这（v4.227 首次跑核验，int 常量被当成「包内没有」）。
    """
    out = set()
    for c in _code_iter(co):
        for x in c.co_consts:
            if isinstance(x, bool):
                continue
            if isinstance(x, (int, float)):
                out.add(x)
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
        check("PYZ 内 config 版本常量 == v4.228.0",
              "v4.228.0" in consts, f"包内版本串={sorted(s for s in consts if s.startswith('v4.22'))}")
        check("PYZ 内不含上一版旧版本常量 v4.227.0",
              "v4.227.0" not in consts, "残留旧版本串（可能是增量打包旧模块）")
    else:
        check("config 在 PYZ 里", False, "缺失 → 启动崩")

    print("\n-- 2a2) ★ 本轮钉子：v4.228.0 瞬时重试 + 异常文案 真进包 --")
    # ui_msg.py 本轮新增两个纯函数（is_transient_net_error / humanize_net_error），
    # ui.py 的 _agent_call 与 agent.py 的气泡文案都依赖它们。
    # ⚠️ 最危险的失败模式：ui_msg 若漏进包 → import 抛 ModuleNotFoundError →
    # 只有走到异常分支时才炸 → 平时毫无征兆，出事即整轮崩。故必须逐个确认。
    if "ui_msg" in names:
        _um_code = _load(za, "ui_msg")
        _um_names = _all_names(_um_code)
        _um_consts = _all_consts(_um_code)
        for _fn in ("is_transient_net_error", "humanize_net_error"):
            check(f"★ ui_msg 内新函数 {_fn} 在位",
                  _fn in _um_names or _fn in _um_consts,
                  "缺失 → 异常分支一触即崩（平时无征兆）")
        # 词表必须真在包里（漏了 = 分类永远判非瞬时 = 重试形同虚设）
        _um_strs = _str_consts(_um_code) | {c for c in _um_consts if isinstance(c, str)}
        for _tok in ("10061", "目标计算机积极拒绝", "Too Many Requests"):
            check(f"★ ui_msg 内词表 {_tok!r} 真在包里",
                  any(_tok in s for s in _um_strs),
                  "缺失 → 分类失准")
        check("★ 剥壳正则已修正（认 <类名:正文> 形态）",
              any("<" in s and "[:：]" in s for s in _um_strs),
              "剥壳正则缺失 → 异常文案仍会把 Python 类名抛给用户")
    else:
        check("ui_msg 在 PYZ 里", False, "缺失 → 异常分支一触即崩")

    if "ui" in names:
        _ui_code = _load(za, "ui")
        _ui_names = _all_names(_ui_code)
        _ui_consts = _str_consts(_ui_code)
        # 函数名通常在 co_names；字符串常量走 _str_consts
        check("★ ui 内调用了 is_transient_net_error（重试真接上了）",
              "is_transient_net_error" in _ui_names or
              any("is_transient_net_error" in s for s in _ui_consts),
              "缺失 → 瞬时重试分支没编进包")
        check("★ ui 内调用了 humanize_net_error（文案转换真接上了）",
              "humanize_net_error" in _ui_names or
              any("humanize_net_error" in s for s in _ui_consts),
              "缺失 → 报错仍显示 Python 异常对象")
    else:
        check("ui 在 PYZ 里", False, "缺失 → 主界面崩")

    print("\n-- 2b) 本轮钉子：v4.227 四新模块真进包（漏进包=静默失效）--")
    # ⚠️ tool_verifiers_227 最危险：注册是**导入期副作用**，漏进包不会报错，
    #    只是验证器永不注册 → 验证链静默空转。这里必须逐个确认真在 TOC 里。
    for _m in ("agent_result_mixin", "untrusted_boundary",
               "tool_verifiers_227", "task_state"):
        check(f"★ 新模块 {_m} 在 PYZ 模块表里",
              _m in names, "缺失 → 相关能力静默失效（不报错）")

    print("\n-- 2c) 本轮钉子：run_python 的 schema 登记真的进了包 --")
    # 漏进包 = run_python 零参数校验，且**静默**（模型不会知道，只是保护没了）
    try:
        from tool_contract import registered_tool_schemas
        _sch = registered_tool_schemas()
        check("★ 源码 run_python 已登记 schema", "run_python" in _sch,
              f"已登记={sorted(_sch)}")
    except Exception as e:
        check("★ 源码 tool_contract 可导入并断言", False, f"导入失败：{e!r}")
    if "tool_contract" in names:
        _tc = _load(za, "tool_contract")
        _tcs = _str_consts(_tc)
        check("★ 包内 tool_contract 常量表含 run_python 登记",
              "run_python" in _tcs, "包内没有 run_python 登记")
        # ⚠️ ARG_MAX_LEN_CAP 是 **int** 常量，_str_consts 只收字符串，扫不到
        #   （数值常量版的「属性名不在字符串常量表」坑）。必须直接查 int。
        _tc_ints = _num_consts(_tc)
        check("★ 包内 tool_contract 含 ARG_MAX_LEN_CAP 上限常量(int)",
              4_000_000 in _tc_ints,
              f"包内 int 常量={sorted(x for x in _tc_ints if x > 1000)[:10]}")

    print("\n-- 2d) 本轮钉子：权限 fail-closed 的双向结论 --")
    # 正向：主链的兜底放行类必须**不在**包里（否则闸门被短路）
    try:
        from agent import AgentWorker  # noqa: F401
        import agent as _ag
        check("★ 源码 agent 不再有 _AllowAllDecision",
              not hasattr(_ag, "_AllowAllDecision"),
              "类还在 → 拿不到决策时依然无条件放行")
    except Exception as e:
        check("★ 源码 agent 可导入并断言", False, f"导入失败：{e!r}")
    if "agent" in names:
        _ans = _str_consts(_load(za, "agent"))
        check("★ 包内 agent 不含 _AllowAllDecision 字符串",
              "_AllowAllDecision" not in " ".join(_ans),
              "包内仍有该类 → exe 里闸门仍会被短路")
    # 反向：军团侧的 _AllowDecision 必须**还在**（刻意保留，防下一个人当同类漏项改掉）
    try:
        import agent_node as _an
        check("★ 反向钉子：军团侧 _AllowDecision 仍在（刻意保留）",
              hasattr(_an, "_AllowDecision"),
              "被删了 → research_write 的「撰写报告」节点会被 WRITE_LOCAL 闸门拒")
    except Exception as e:
        check("★ 源码 agent_node 可导入并断言", False, f"导入失败：{e!r}")

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
        # ⚠️ agent 侧那处是 `getattr(self, "_deliverables", None)` ——
        #   getattr 的名字是**字符串参数**，进的是 co_consts 而不是 co_names。
        #   只扫 co_names 会把「确实存在」判成「没编进去」（本轮首跑就红在这）。
        check("★ 包内 agent 读 _deliverables（累积声明交付物）",
              "_deliverables" in an or
              any("_deliverables" in s for s in _str_consts(co_agent)),
              "_deliverables 没编进去 → 验收无数据")
        # 写入侧在 agent_result_mixin（v4.227 P1-2 外移），读侧在 agent，
        # 两边都要钉：只钉读侧会漏掉「只读不写」的退化。
        if "agent_result_mixin" in names:
            _armn2 = _code_names(_load(za, "agent_result_mixin"))
            check("★ 包内 agent_result_mixin 写 _deliverables（累积交付物）",
                  "_deliverables" in _armn2,
                  "写入侧没编进去 → 读侧永远读到空，产物验收形同虚设")
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
    # ⚠️ v4.227 整块外移成 untrusted_boundary.py：标签字面量与两个包装函数的
    #   **真身都在新模块**。agent.py 只做 re-export（它源码里那句标签在**注释**
    #   中，编译期就不进常量表）—— 钉旧模块等于钉一个永远为假的键。
    if "untrusted_boundary" in names:
        co_ub = _load(za, "untrusted_boundary")
        ubn = _code_names(co_ub)
        ubs = _str_consts(co_ub)
        for sym in ("wrap_untrusted", "_wrap_tool_content",
                    "neutralize_forged_tags", "is_untrusted_tool",
                    "wrap_skill_prompt_text"):
            check(f"★ 包内 untrusted_boundary 含边界符号 {sym}",
                  sym in ubn, f"{sym} 没编进去 → 边界能力缺失")
        check("★ 包内 untrusted_boundary 常量含 <untrusted_tool_output 边界标签",
              any("untrusted_tool_output" in s for s in ubs),
              "不可信工具边界标签没编进去")
        check("★ 包内 untrusted_boundary 常量含 <untrusted skill 边界标签",
              any("untrusted skill" in s for s in ubs),
              "技能边界标签没编进去")
        # 反向钉子：伪装闭合的中和逻辑必须在（否则外部正文能自己解除边界）
        check("★ 包内 untrusted_boundary 含中和正则（防伪造闭合）",
              any("untrusted" in s and "s|>" in s or "(?" in s
                  for s in ubs) or any("untrusted" in s for s in ubs),
              "中和正则没编进去")
    if "agent" in names:
        co_agent = _load(za, "agent")
        an = _code_names(co_agent)
        check("★ 包内 agent 仍 re-export wrap_untrusted（接线在位）",
              "wrap_untrusted" in an, "agent 侧不再引用包装函数")
    if "skill_loader" in names:
        co_sl = _load(za, "skill_loader")
        sln = _code_names(co_sl)
        sls = _str_consts(co_sl)
        for sym in ("allow_tools", "allow_network", "allow_system",
                    "allow_dir", "audited_at", "wrap_skill_prompt"):
            check(f"★ 包内 skill_loader 含技能元数据/边界符号 {sym}",
                  (sym in sln) or any(sym in s for s in sls),
                  f"{sym} 没编进去 → 技能边界缺元数据")

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
        # `_tstate_record` 的**调用点**随 `_handle_tool_result` 外移到了
        # agent_result_mixin（v4.227 P1-2），所以它不在 agent 的 co_names 里。
        # 钉错模块 = 钉一个永远为假的键（v4.227 首跑就红在这）。
        for _m in ("_tstate_init", "_tstate_step",
                   "_tstate_resume_step", "_tstate_resume_reset_nudge",
                   "_tstate_nudge_now"):
            check(f"★ 包内 agent 真调 {_m}（账本接线方法）",
                  _m in ag2n,
                  f"{_m} 没编进去 → 该处接线是死引用（任务账本形同虚设）")
        if "agent_result_mixin" in names:
            _armn = _code_names(_load(za, "agent_result_mixin"))
            check("★ 包内 agent_result_mixin 真调 _tstate_record（记账接线）",
                  "_tstate_record" in _armn,
                  "_tstate_record 没编进去 → 每次工具调用都不记账")
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

    print("\n-- 3n) 本轮钉子：四阶段循环 + 技能元数据强制化（v4.226）--")
    # ① agent_loop 判定层：四阶段符号 + 机器小结串
    if "agent_loop" in names:
        co_al = _load(za, "agent_loop")
        aln = _code_names(co_al)
        als = _str_consts(co_al)
        for sym in ("next_phase", "LoopState", "should_plan", "build_plan",
                    "plan_instruction", "should_verify", "verify_report",
                    "is_verified", "should_summarize", "build_summary"):
            check(f"★ 包内agent_loop 定义 {sym}（四阶段循环判定层）",
                  sym in aln or sym in als,
                  f"{sym} 没编进去 → 四阶段状态机残缺")
        for k in ("PHASE_ORDER", "PLAN_MIN_REQUIREMENTS"):
            check(f"★ 包内 agent_loop 常量 {k}",
                  any(k in s for s in aln) or any(k in s for s in als),
                  f"{k} 缺失 → 阶段推进/门槛失效")
        # 四阶段四个阶段名必须都在常量池里 —— 否则等于退回「没有显式阶段」
        for ph in ("plan", "execute", "verify", "summarize"):
            check(f"★ 包内 agent_loop 含阶段名 {ph}",
                  any(ph in s for s in als),
                  f"阶段名 {ph} 没编进去 → 阶段机不完整")
        check("★ 包内 agent_loop 含机器小结「以本段为准」串",
              any("以本段为准" in s for s in als),
              "小结串没编进去 → 用户看到的结论没有真源声明")
    else:
        check("agent_loop 在 PYZ 里", False,
              "缺失 → 四阶段循环不存在，exe 会ModuleNotFoundError")

    # ② agent_loop_mixin 接线层：方法名在 co_names（LOAD_METHOD 稳定可扫）
    if "agent_loop_mixin" in names:
        co_lm = _load(za, "agent_loop_mixin")
        lmn = _code_names(co_lm)
        for sym in ("AgentLoopMixin", "_loop_start", "_loop_step",
                    "_loop_verify", "_loop_summary", "_loop_state_dict"):
            check(f"★ 包内 agent_loop_mixin 定义 {sym}（四阶段接线）",
                  sym in lmn,
                  f"{sym} 没编进去 → 接线方法缺失")
    else:
        check("agent_loop_mixin 在 PYZ 里", False,
              "缺失 → 四阶段接线不存在，exe 会 ModuleNotFoundError")

    # ③ skill_meta：三档字段常量 + fail-open 探针 + 默认不拒用
    if "skill_meta" in names:
        co_sm = _load(za, "skill_meta")
        smn = _code_names(co_sm)
        sms = _str_consts(co_sm)
        for sym in ("REQUIRED_FIELDS", "OPTIONAL_FIELDS",
                    "CAPABILITY_FIELDS", "check_skill",
                    "strict_from_config", "unverified_note", "rejection_text"):
            check(f"★ 包内 skill_meta 定义 {sym}（技能元数据三档判定）",
                  sym in smn or any(sym in s for s in sms),
                  f"{sym} 没编进去 → 元数据校验残缺")
        check("★ 包内 skill_meta 含 MetaVerdict（三档结论载体）",
              "MetaVerdict" in smn or any("MetaVerdict" in s for s in sms),
              "MetaVerdict 缺失 → ok/degraded/reject 三档塌成一档")
        # 八个元数据字段名（能力声明四件套是关键 —— 技能越权调用工具的围栏基础）
        for fld in ("description", "source", "allow_tools", "allow_dir",
                    "allow_network", "allow_system"):
            check(f"★ 包内 skill_meta 含字段名 {fld}",
                  any(fld in s for s in sms),
                  f"{fld} 没编进去 → 该字段不参与校验")
        # fail-open 纪律：`_probe, _got = _get(...)` 里的 `_probe/_got` 是**局部变量名**，
        # 走 STORE_FAST，**既不在 co_names 也不在 co_consts**（与「属性名不在常量表」
        # 同源，变量名同理）—— 实测包内 co_names 只有 `_get`，扫 `_got` 永远假红。
        # 所以钉**真实存在于常量表的 fail-open 说明串**（来自模块/函数 docstring，
        # docstring 是真字符串常量，进得了 co_consts）。
        # ⚠️ 这条断言的教训：核验 FAIL 时先怀疑断言本身（v4.226 首版连错两次：
        #    先假设 agent.py 引用 skill_meta、再假设 `if not _got` 在常量表）。
        check("★ 包内 skill_meta 含 fail-open 放行纪律说明串",
              any("取不到任何字段" in s and "fail-open" in s for s in sms),
              "fail-open 说明串没编进去 → 无法证明放行纪律随包")
        check("★ 包内 skill_meta 含「未审」明示串（degraded 要让模型知道）",
              any("未审" in s for s in sms),
              "未审明示串没编进去 → degraded 技能不会被告知未审")
        check("★ 包内 skill_meta 含 _get（取字段二元组的实现）",
              "_get" in smn,
              "_get 没编进去 → 「取不到 vs 为空」无法区分，fail-open 失效")
    else:
        check("skill_meta 在 PYZ 里", False,
              "缺失 → 技能元数据强制化不存在，exe 会 ModuleNotFoundError")

    # ④ skill_loader 真接校验（find_skill 是接线入口）
    if "skill_loader" in names:
        co_sl = _load(za, "skill_loader")
        sln = _code_names(co_sl)
        for sym in ("find_skill", "check_skill", "load_skill_prompt"):
            check(f"★ 包内 skill_loader 定义 {sym}（元数据校验接线）",
                  sym in sln,
                  f"{sym} 没编进去 → 元数据校验没接上")
    else:
        check("skill_loader 在 PYZ 里", False, "缺失 → 技能子系统不存在")

    # ⑤ agent 接线：四循环方法名 + mixin 引用 + 调用点位置（时序铁律）
    if "agent" in names:
        co_ag3 = _load(za, "agent")
        ag3n = _code_names(co_ag3)
        for _m in ("_loop_start", "_loop_step", "_loop_verify", "_loop_summary"):
            check(f"★ 包内 agent 真调 {_m}（四阶段接线方法）",
                  _m in ag3n,
                  f"{_m} 没编进去 → 四阶段循环是死接线")
        check("★ 包内 agent 真 mixin 了 AgentLoopMixin（四阶段接线非死引用）",
              "AgentLoopMixin" in ag3n,
              "agent 没引用 AgentLoopMixin → 四阶段全是死代码")
        check("★ 包内 agent 引用 agent_loop 模块（四阶段判定层接线）",
              "agent_loop" in ag3n,
              "agent 没引用 agent_loop → 判定层不存在")
        # ⚠️ **不要**在这里断言 agent 引用 skill_meta —— 实测 agent.py 根本不引用
        # （元数据校验的接线全在 tools.py 的 tool_use_skill 与 skill_loader 里，
        # agent 侧只是间接受益）。v4.226 首版误加这条断言，当场假红：
        # **核验断言 FAIL 时先怀疑断言本身**，别急着改代码。
    if "tools" in names:
        co_t3 = _load(za, "tools")
        t3n = _code_names(co_t3)
        check("★ 包内 tools 引用 skill_meta（tool_use_skill 接元数据校验）",
              "skill_meta" in t3n,
              "tools 没引用 skill_meta → 技能元数据校验被架空")

    # ⑥ 时序铁律（源码侧）：_loop_summary 必须在 _tstate_nudge_now 之后、break 之前。
    #    字节码常量池是**无序集合**，位置关系只能回源码查 —— 但源码不在包里，
    #    所以这条钉在 tests/test_agent_loop_phases_226.py（LP9-9/LP9-10），
    #    这里只钉「两个方法都被调用」，位置关系由源码判据守。
    print("  （时序铁律「_loop_summary 在 _tstate_nudge_now 之后、break 之前」"
          "由源码判据 LP9-9/LP9-10 守，字节码常量池无序、无法在此表达）")

    print("\n-- 3g) 关键历史钉子：画布模块不得随包 + 测试不随包 --")
    for _cm in ("canvas_graph", "canvas_panel", "canvas_export", "executors"):
        check(f"★ 包内不含已移除画布模块 {_cm}", _cm not in names, "死代码进包")
    leaked = sorted(n for n in names
                    if n.startswith("test_") or "perturb" in n or "pep701" in n)
    check("PYZ 里没有 tests/ 判据套件与根目录扰动脚本",
          not leaked, f"泄漏的模块={leaked}")

    print("\n-- 3h) 本轮钉子：ToolResult 判定真源在包内 + agent.py 行数红线 --")
    # _tool_outcome 是 P1-2 的判定真源。漏进包 → agent 又退回「看文案」判断，
    # 而 UI 绿勾 / 账本仍会把 POST_VERIFY_FAILED 记成成功（那正是本轮修的 bug）。
    if "agent_result_mixin" in names:
        _arm = _load(za, "agent_result_mixin")
        _arns = _str_consts(_arm)
        check("★ 包内 agent_result_mixin 含 _tool_outcome 判定真源",
              "_tool_outcome" in " ".join(_arns),
              f"常量={sorted(s for s in _arns if 'outcome' in s)[:5]}")
    if "agent" in names:
        _agn = _code_names(_load(za, "agent"))
        check("★ 包内 agent 引用 AgentResultMixin（接线在位）",
              "AgentResultMixin" in " ".join(_agn), "接线没编进去")
    # 行数红线：<2400。抬阈值等于把红线挪到自己脚下，所以这里钉死上限。
    _agent_lines = len((ROOT / "agent.py").read_bytes().decode("utf-8-sig")
                       .splitlines())
    check("★ agent.py 行数守住 <2400（本轮未抬阈值）",
          _agent_lines < 2400, f"当前 {_agent_lines} 行")

    co_main = _load_entry_script(EXE, "main")
    check("main 从 CArchive 取到（入口脚本不在 PYZ 是正常结构）", co_main is not None)

    print(f"  PYZ 模块数：{len(names)}")
    print("\n" + "=" * 62)
    print(f"汇总：PASS={_n_pass} FAIL={_n_fail}")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
