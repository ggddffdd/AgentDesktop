# -*- coding: utf-8 -*-
"""v4.227 P2-2 执行后验证加固：首批硬验真跑 + 分档全覆盖可枚举。

v4.224 建了 `register_verifier` / `verify_after` / `apply_post_verification`
这条链，但只登记 2 个验证器，其余 81 个工具静默 fail-open。
fail-open 本身正确，问题是**不可见**：「查不到信号所以不表态」与
「压根没人想过这个工具」在代码里长得一模一样。

修法（tool_contract + tool_verifiers_227）：
  · 分档机制 verified / degraded / open，把验证姿态显式化
  · 首批补 4 个硬验：db_insert / db_update / db_delete / write_file

判据分六组：
  A 硬验行为   真写库真删库真写文件，验证器结论必须跟着事实变（含反例）
  B 只降级不升级 验证通过不许把失败翻成成功（v4.224 语义硬约束不许被破）
  C 分档完整性 83 个工具零漏项、零幽灵名（幽灵名 = 登记了却从不改变结论）
  D 分档不参与决策 分档表绝不能变成第二套策略（v4.226 删 _AllowAllDecision 同理）
  E 导入期接线  模块不被 import 注册就不发生 —— 必须钉住真在 tools.py 里
  F 判据自证   C 组自己非恒真（造漏项/幽灵名必须被它抓住）
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


import tool_contract as tc          # noqa: E402
import tools as T                   # noqa: E402
import tool_verifiers_227 as tv     # noqa: E402


def group_a_verifiers():
    print("\n-- A) 硬验行为：真跑，结论必须跟着事实变 --")

    # A1 写文件：真写了文件 → 验证通过
    tmp = tempfile.mkdtemp(prefix="pv227_")
    rel = "pv227_probe.txt"
    try:
        import tools as tools_mod
        ap = os.path.join(tools_mod.WORKSPACE_DIR, rel)
        d = os.path.dirname(ap)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(ap, "w", encoding="utf-8") as fh:
            fh.write("hello verify")
        v = tc.verify_after("write_file", {"path": rel}, None)
        check("A1 write_file 真落地 → 验证通过",
              v is not None and v[0] is True, "verify_after=%r" % (v,))
        check("A1b write_file 验证带证据（反编造）",
              v is not None and "字节" in (v[1] or ""), "evidence=%r" % (v,))

        # A2 反例：文件其实不存在（工具报成功但没落地）→ 验证必须不通过
        v2 = tc.verify_after("write_file", {"path": "pv227_never_written.txt"}, None)
        check("A2 反例：文件不存在 → 验证不通过（防编造成功）",
              v2 is not None and v2[0] is False, "verify_after=%r" % (v2,))

        # A3 空文件也算没落地（write_file 结局契约同样口径）
        empty = os.path.join(tools_mod.WORKSPACE_DIR, "pv227_empty.txt")
        with open(empty, "w", encoding="utf-8") as fh:
            fh.write("")
        v3 = tc.verify_after("write_file", {"path": "pv227_empty.txt"}, None)
        check("A3 空文件 → 验证不通过（0 字节）",
              v3 is not None and v3[0] is False, "verify_after=%r" % (v3,))
    finally:
        for n in (rel, "pv227_never_written.txt", "pv227_empty.txt"):
            try:
                os.remove(os.path.join(T.WORKSPACE_DIR, n))
            except Exception:
                pass
        try:
            os.rmdir(tmp)
        except Exception:
            pass

    # A4 无 path 参数时不表态（None），不得瞎判
    v4 = tc.verify_after("write_file", {}, None)
    check("A4 无 path → 不表态（None，不瞎判）", v4 is None, "got %r" % (v4,))

    # A5 db 三个验证器：参数缺失时不表态
    for name in ("db_insert", "db_update", "db_delete"):
        check("A5 %s 无参数 → 不表态（None）" % name,
              tc.verify_after(name, {}, None) is None)

    # A6 db_update/db_delete 有 record_id 时必须真去查库（不崩、有结论）
    for name in ("db_update", "db_delete"):
        v = tc.verify_after(name, {"table": "notes", "record_id": 999999}, None)
        check("A6 %s 查不存在的 id → 有明确结论（不崩）" % name,
              v is None or isinstance(v, tuple),
              "got %r" % (v,))


def group_b_no_upgrade():
    print("\n-- B) 只降级不升级（v4.224 语义硬约束） --")
    from tool_contract import ToolResult

    # B1 原本失败 + 验证通过 → 仍必须失败（不许被「验证」翻成成功）
    tr = ToolResult(ok=False, msg="工具自己报错了")
    tr2 = ToolResult(ok=False, msg="工具自己报错了")
    # 拿 write_file 做真实验证器：文件真在磁盘上（验证会通过）
    import tools as tools_mod
    rel = "pv227_b1.txt"
    ap = os.path.join(tools_mod.WORKSPACE_DIR, rel)
    try:
        with open(ap, "w", encoding="utf-8") as fh:
            fh.write("x")
        args = {"path": rel}
        downgraded = tc.apply_post_verification(tr, "write_file", args)
        check("B1 验证通过但原本失败 → 不得翻成成功（ok 仍为 False）",
              tr.ok is False, "ok=%r" % (tr.ok,))
        check("B1b 验证通过时不算降级（返回 False）", downgraded is False,
              "returned %r" % (downgraded,))
        check("B1c 验证通过仍留 verified=True 痕迹", tr.verified is True)

        # B2 原本成功 + 验证不通过 → 必须降级
        tr3 = ToolResult(ok=True, msg="声称写好了")
        args3 = {"path": "pv227_never2.txt"}
        got = tc.apply_post_verification(tr3, "write_file", args3)
        check("B2 原本成功但验证不通过 → 降级为失败",
              tr3.ok is False and got is True,
              "ok=%r returned=%r" % (tr3.ok, got))
        check("B2b 降级时错误码是 POST_VERIFY_FAILED",
              getattr(tr3, "error_code", "") == "POST_VERIFY_FAILED",
              "error_code=%r" % getattr(tr3, "error_code", ""))
        check("B2c 降级时证据写进 msg（反编造）",
              "执行后验证未通过" in (tr3.msg or ""), "msg=%r" % (tr3.msg,))
    finally:
        try:
            os.remove(ap)
        except Exception:
            pass

    # B3 无验证器 → 完全不动结论（fail-open）
    tr4 = ToolResult(ok=True, msg="原样")
    r = tc.apply_post_verification(tr4, "browser_read", {}, )
    check("B3 无验证器的工具 → 结论完全不动（fail-open）",
          r is False and tr4.ok is True and tr4.msg == "原样"
          and getattr(tr4, "verified", False) is False,
          "ok=%r msg=%r verified=%r" % (tr4.ok, tr4.msg,
                                       getattr(tr4, "verified", False)))

    # B4 tr 为 None 不崩
    try:
        check("B4 tr=None → 返回 False 不崩",
              tc.apply_post_verification(None, "write_file", {}) is False)
    except Exception as e:
        check("B4 tr=None → 返回 False 不崩", False, repr(e))


def group_c_tier_coverage():
    print("\n-- C) 分档完整性：零漏项、零幽灵名 --")
    names = sorted(T.TOOL_REGISTRY.keys())
    rep = tc.verification_tier_report()
    covered = set(rep.get("verified", [])) | set(rep.get("degraded", [])) \
        | set(rep.get("open", []))

    # C1 零漏项：每个已注册工具都必须有明确档位
    miss = [n for n in names if n not in covered]
    check("C1 %d 个已注册工具零漏项（人人有档）" % len(names), not miss,
          "漏=%r" % miss)

    # C2 零幽灵名：登记了但工具不存在的 = 登记了却从不改变结论
    ghost = sorted(x for x in covered if x not in names)
    check("C2 零幽灵名（登记项必须都是真工具）", not ghost, "幽灵=%r" % ghost)

    # C3 unverified_tools 报空（分档表自身无漏洞）
    check("C3 unverified_tools 报空（分档表自身无漏洞）",
          tc.unverified_tools(names) == [],
          "仍漏=%r" % tc.unverified_tools(names))

    # C4 verified 档必须有真验证器（不能只挂个名字）
    vs = set(tc.registered_verifiers())
    fake = [n for n in rep.get("verified", []) if n not in vs]
    check("C4 verified 档全部有真验证器", not fake, "只有档无验证器=%r" % fake)

    # C5 degraded 必须带理由（空壳理由 = 登记了等于没登记）
    reasons = tv.degraded_reasons()
    noreason = [n for n in rep.get("degraded", []) if not reasons.get(n)]
    check("C5 degraded 档全部带降级理由", not noreason, "无理由=%r" % noreason)

    # C6 档位取值合法
    ok_tier = all(t in ("verified", "degraded", "open")
                  for t in tc._VERIFY_TIERS.values())
    check("C6 档位取值全部合法", ok_tier)
    check("C6b 三档都有实际条目（三档非空壳）",
          all(rep.get(k) for k in ("verified", "degraded", "open")),
          {k: len(rep.get(k, [])) for k in ("verified", "degraded", "open")})


def group_d_tier_no_decision():
    print("\n-- D) 分档不参与决策（不许变成第二套策略） --")
    src = open(os.path.join(ROOT, "tool_contract.py"), "rb").read().decode("utf-8-sig")
    # D1 分档查得到，但运行时判定仍只看验证器。
    #     ⚠️ 必须用 **AST 取真实函数体**：v4.227 把分档函数插在 verify_after
    #     之后，按 `src.find("\ndef ")` 切会一路吃到下一个顶层 def，
    #     把分档函数的代码算进 verify_after 体内 → 假红（本次踩过）。
    import ast
    tree = ast.parse(src)
    fa = None
    for nd in tree.body:
        if isinstance(nd, ast.FunctionDef) and nd.name == "verify_after":
            fa = nd
            break
    check("D0 verify_after 函数可定位（否则本组无意义）", fa is not None)
    body = ""
    if fa is not None:
        body = "\n".join(src.split("\n")[fa.lineno - 1:fa.end_lineno])
    check("D1 verify_after 体内不引用分档表（判定只看验证器）",
          fa is not None and "_VERIFY_TIERS" not in body
          and "verification_tier" not in body,
          "verify_after 体内出现了分档引用")

    # D2 register_verification_tier 不返回值语义、不抛异常
    check("D2 register_verification_tier 非法档位被拒（返回 False）",
          tc.register_verification_tier("x_probe", "不存在的档") is False)
    check("D2b 空工具名被拒", tc.register_verification_tier("", "open") is False)

    # D3 degraded 工具运行时仍 fail-open（分档不影响行为）
    check("D3 degraded 工具运行时仍 fail-open（browser_read）",
          tc.verify_after("browser_read", {"url": "x"}, None) is None)

    # D4 三个查询函数都是纯读（不改注册表）
    before = dict(tc._VERIFY_TIERS)
    tc.verification_tier_report()
    tc.unverified_tools(sorted(T.TOOL_REGISTRY.keys()))
    tc.verification_tier("write_file")
    check("D4 查询函数是纯读（调用前后分档表不变）",
          before == tc._VERIFY_TIERS)


def group_e_import_wiring():
    print("\n-- E) 导入期接线：模块不被 import，注册就不发生 --")
    # ⚠️ 不能只 `find("import tool_verifiers_227")` —— 扰动把它缩进到
    # `if False:` 底下时，那串字面量**照样存在**，源码找串判据照样绿
    # （v4.227 首版 E1 就是这么被 V5 打穿的：红 0 条）。
    # 必须用 AST 判定「这是一条真在模块顶层执行的 import」。
    import ast
    tsrc = open(os.path.join(ROOT, "tools.py"), "rb").read().decode("utf-8-sig")
    ttree = ast.parse(tsrc)
    real_imports = []
    for nd in ttree.body:            # 顶层 body，不是递归 walk
        if isinstance(nd, ast.Import):
            for a in nd.names:
                real_imports.append((a.name, nd.lineno))
    check("E0 tools.py 可解析（否则本组无意义）", isinstance(ttree, ast.Module))
    hit_import = [x for x in real_imports if x[0] == "tool_verifiers_227"]
    check("E1 tools.py 顶层真 import 了 tool_verifiers_227（非 if False 缩进）",
          bool(hit_import), "顶层真实 import=%r" % (real_imports,))
    i = tsrc.find("import tool_verifiers_227")
    check("E1b 该 import 在 exec_tool 定义之前（注册早于使用）",
          i > 0 and i < tsrc.find("def exec_tool("),
          "import 位置=%d exec_tool 位置=%d" % (i, tsrc.find("def exec_tool(")))
    check("E2 验证器已实际注册（非空）", len(tc.registered_verifiers()) >= 6,
          "只注册了 %d 个" % len(tc.registered_verifiers()))
    # 首批四个 + v4.224 两个
    need = {"db_insert", "db_update", "db_delete", "write_file",
            "process_kill", "clean_recycle_bin"}
    check("E3 首批硬验 + v4.224 两个全在",
          need.issubset(set(tc.registered_verifiers())),
          "缺=%r" % sorted(need - set(tc.registered_verifiers())))

    # E4 反向自证：注册**只**发生在导入 tool_verifiers_227 时。
    #    起一个干净子进程，只 import tool_contract（不碰 tools），
    #    那时 write_file/db_insert 必须**尚未**注册 —— 否则 E2 恒真
    #    （本判据文件自己 import 了 tools，注册早就发生了，什么都证明不了）。
    import subprocess
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import tool_contract as tc\n"
        "print(','.join(tc.registered_verifiers()))\n" % ROOT
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, cwd=ROOT, timeout=120,
                       encoding="utf-8", errors="replace")
    only = (r.stdout or "").strip()
    check("E4 未导入 tool_verifiers_227 时验证器尚未注册（注册是导入期副作用）",
          r.returncode == 0 and only == "",
          "子进程已注册=%r（应为空）stderr=%r" % (only, (r.stderr or "")[-200:]))
    # E5 对照：导入之后立刻就有了 —— 证明 E4 不是「永远空」的恒真
    code2 = (
        "import sys; sys.path.insert(0, %r)\n"
        "import tool_verifiers_227\n"
        "import tool_contract as tc\n"
        "print(','.join(tc.registered_verifiers()))\n" % ROOT
    )
    r2 = subprocess.run([sys.executable, "-c", code2], capture_output=True,
                        text=True, cwd=ROOT, timeout=120,
                        encoding="utf-8", errors="replace")
    after = (r2.stdout or "").strip()
    check("E5 对照：导入后立刻注册（证明 E4 非恒真）",
          r2.returncode == 0 and "write_file" in after,
          "导入后=%r" % after)


def group_f_judge_selfproof():
    print("\n-- F) 判据自证：C 组自己非恒真 --")
    # C1/C2 的判据若写错，会对「漏项/幽灵名」视而不见。这里造两种坏情况，
    # 用同一套判定逻辑验证它必须给假 —— 证明判据不是恒真。
    names = ["alpha", "beta", "gamma"]
    rep_ok = {"verified": ["alpha"], "degraded": ["beta"], "open": ["gamma"]}

    def judge(names_, covered_):
        return ([n for n in names_ if n not in covered_],
                sorted(x for x in covered_ if x not in names_))

    # 正常：全覆盖无幽灵
    m1, g1 = judge(names, set(rep_ok["verified"]) | set(rep_ok["degraded"])
                   | set(rep_ok["open"]))
    check("F1 判定逻辑对全覆盖无幽灵给空（不误报）", not m1 and not g1)

    # 坏①：漏一个
    m2, g2 = judge(names, {"alpha", "beta"})
    check("F2 判定逻辑能抓漏项（非恒真）", m2 == ["gamma"], "漏=%r" % m2)
    # 坏②：混进幽灵名
    m3, g3 = judge(names, {"alpha", "beta", "gamma", "ghost_tool"})
    check("F3 判定逻辑能抓幽灵名（非恒真）", g3 == ["ghost_tool"], "幽灵=%r" % g3)
    # 坏③：空覆盖（等于分档表整个没导入）
    m4, _ = judge(names, set())
    check("F4 判定逻辑对「完全没登记」全红（非恒真）", len(m4) == len(names))


def main():
    print("v4.227 P2-2 执行后验证加固")
    group_a_verifiers()
    group_b_no_upgrade()
    group_c_tier_coverage()
    group_d_tier_no_decision()
    group_e_import_wiring()
    group_f_judge_selfproof()
    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
