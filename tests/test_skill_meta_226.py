# -*- coding: utf-8 -*-
"""tests/test_skill_meta_226.py —— v4.226 技能元数据强制化校验判据

核心不变量（改坏必红）：
  SM1  分级：关键字段（description/source）vs 非关键（version/hash/audited_at）
      vs 能力声明（allow_*）
  SM2  **默认不拒用**：strict=False 时缺关键字段只降级为 degraded，
      绝不 reject —— 否则 v4.226 之前写的 ~30 个老技能会当场全部失效
  SM3  strict=True 且缺关键字段 → reject，且理由**可执行**（写清缺什么怎么补）
  SM4  元数据齐全 → ok
  SM5  fail-open：校验器自身抛异常 → 放行（失效方向必须是放行，不是拦死）
  SM6  unverified_note 只挂在 degraded 上，ok/reject 都不挂
  SM7  接线钉子：skill_loader.find_skill 存在、load_skill_prompt 支持
      strict_meta、tools.tool_use_skill 真查校验并真拒用
  SM8  端到端（行为级）：造真技能目录 → load_skill_prompt → strict 拒用 /
      降级放行 + 提示；tool_use_skill 在 strict 下返回拒用理由
  SM9  回归护栏：现有 ~30 个真实技能目录在默认档下**全部仍可加载**
      （这是「不能一刀切」的实测证据，不是口头承诺）
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] %s" % name)
    else:
        FAIL += 1
        print("[FAIL] %s%s" % (name, ("  <%s>" % detail) if detail else ""))


print("=" * 62)
print("v4.226 技能元数据强制化判据")
print("=" * 62)

import skill_meta  # noqa: E402


def _code_lines(path):
    """剔除注释文本后的代码（保留「代码 + 行尾注释」那行的代码部分）。

    ⚠️ 不能按「注释所在整行」剔除 —— `import agent_loop  # v4.226 xxx`
    这种「代码 + 行尾注释」是本项目的主力写法，整行删会把真调用点一起删掉。
    ⚠️ 也不能按 `#` 裸切 —— 会误伤字符串里的 `#`（如 `'#标题'`）。
    用 tokenize 拿 COMMENT token 的精确列号，只挖掉那几列。
    """
    import io
    import tokenize
    with open(os.path.join(ROOT, path), "rb") as f:
        src = f.read().decode("utf-8-sig")
    try:
        lines = src.split("\n")
        spans = {}
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            for ln in range(tok.start[0], tok.end[0] + 1):
                spans.setdefault(ln, []).append((tok.start[1], tok.end[1]))
        out = []
        for i, line in enumerate(lines, 1):
            sp = spans.get(i)
            if not sp:
                out.append(line)
                continue
            for (c0, c1) in sorted(sp, reverse=True):
                line = line[:c0] + line[c1:]
            out.append(line)
        return "\n".join(out)
    except Exception:
        return src


def _full(**kw):
    """一份元数据齐全的技能（各用例只减要测的那几个字段）。

    ⚠️ 四个 allow_* 必须都给**非空值**：元数据里写 `allow_dir: ""` 与
    完全不写，对模型的意义都是「我不知道这份技能允许什么」→ 判degraded。
    「齐全」的定义就是八个字段都有实质内容。
    """
    d = {
        "name": "demo",
        "description": "演示技能",
        "source": "user",
        "version": "1.0.0",
        "hash": "abc123",
        "audited_at": "2026-10-07",
        "allow_tools": "run_python,read_file",
        "allow_dir": "C:/workspace",
        "allow_network": "false",
        "allow_system": "false",
    }
    d.update(kw)
    return d


# ============================================================
# SM1  字段分级
# ============================================================
print("\n--- SM1 字段分级 ---")
check("SM1-1 关键字段 = description + source",
      set(skill_meta.REQUIRED_FIELDS) == {"description", "source"},
      str(skill_meta.REQUIRED_FIELDS))
check("SM1-2 非关键字段 = version/hash/audited_at",
      set(skill_meta.OPTIONAL_FIELDS) == {"version", "hash", "audited_at"},
      str(skill_meta.OPTIONAL_FIELDS))
check("SM1-3 能力声明字段 = allow_tools/allow_dir/allow_network/allow_system",
      set(skill_meta.CAPABILITY_FIELDS) == {"allow_tools", "allow_dir",
                                            "allow_network", "allow_system"},
      str(skill_meta.CAPABILITY_FIELDS))
check("SM1-4 三档判定常量齐全",
      (skill_meta.VERDICT_OK, skill_meta.VERDICT_DEGRADED,
       skill_meta.VERDICT_REJECT) == ("ok", "degraded", "reject"))

# ============================================================
# SM4  元数据齐全 → ok
# ============================================================
print("\n--- SM4 齐全即ok ---")
_v = skill_meta.check_skill(_full())
check("SM4-1 元数据齐全 → ok", _v.verdict == "ok", _v.verdict)
check("SM4-2 ok 时 ok 属性为 True", _v.ok is True)
check("SM4-3 ok 时 degraded 属性为 False", _v.degraded is False)
check("SM4-4 ok 时无任何缺失字段", _v.missing_all() == [], str(_v.missing_all()))
check("SM4-5 to_dict 可序列化且含 verdict",
      isinstance(_v.to_dict(), dict) and _v.to_dict()["verdict"] == "ok")

# ============================================================
# SM2  默认不拒用（最重要的一条：不能一刀切打死老技能）
# ============================================================
print("\n--- SM2 默认降级不拒用 ---")
_bare = {"name": "老技能", "description": "写得很早的老技能"}   # 无 source/version/...
_vb = skill_meta.check_skill(_bare, strict=False)
check("SM2-1 strict=False + 缺关键 → degraded（非 reject）",
      _vb.verdict == "degraded", _vb.verdict)
check("SM2-2 degraded 时 ok 仍为 True（**照常放行**）", _vb.ok is True)
check("SM2-3 degraded 时 degraded=True", _vb.degraded is True)
check("SM2-4 缺失字段被完整列出",
      "source" in _vb.missing_required
      and "version" in _vb.missing_optional
      and "allow_tools" in _vb.missing_capability,
      str(_vb.missing_all()))
# 只缺非关键 → 也是 degraded（不是 ok）
_vc = skill_meta.check_skill(_full(version="", hash="", audited_at=""))
check("SM2-5 只缺非关键字段 → degraded（非 ok：模型仍须知会未审）",
      _vc.verdict == "degraded", _vc.verdict)
check("SM2-6 只缺非关键时 required 为空",
      _vc.missing_required == [], str(_vc.missing_required))
# 只缺能力声明 → degraded（不填=没声明，模型必须知道）
_vd = skill_meta.check_skill(_full(allow_tools="", allow_dir="",
                                   allow_network="", allow_system=""))
check("SM2-7 能力声明全缺 → degraded（声明即安全边界，不填=没声明）",
      _vd.verdict == "degraded", _vd.verdict)
check("SM2-8 能力声明缺失被完整列出",
      len(_vd.missing_capability) == 4, str(_vd.missing_capability))

# ============================================================
# SM3  strict=True → reject，理由可执行
# ============================================================
print("\n--- SM3 strict拒用 ---")
_vr = skill_meta.check_skill({"name": "来路不明", "description": "有描述"},
                              strict=True)
check("SM3-1 strict=True + 缺 source → reject",
      _vr.verdict == "reject", _vr.verdict)
check("SM3-2 reject 时 ok=False", _vr.ok is False)
check("SM3-3 拒用理由点名了技能名", "来路不明" in _vr.reason, _vr.reason)
check("SM3-4 拒用理由点名了缺失字段", "source" in _vr.reason, _vr.reason)
check("SM3-5 拒用理由给出**怎么补**（可执行）",
      "frontmatter" in _vr.reason, _vr.reason)
# strict + 齐全 → 仍ok
check("SM3-6 strict=True + 元数据齐全 → ok（strict 不误伤）",
      skill_meta.check_skill(_full(), strict=True).verdict == "ok")
# strict + 只缺非关键 → 不拒用（非关键缺失不构成拒用理由）
_vr2 = skill_meta.check_skill(_full(version="", hash="", audited_at=""),
                              strict=True)
check("SM3-7 strict=True 但只缺非关键 → 不拒用（仍 degraded）",
      _vr2.verdict == "degraded", _vr2.verdict)
# strict + 缺description
_vr3 = skill_meta.check_skill({"name": "x", "source": "user"}, strict=True)
check("SM3-8 strict + 缺 description → reject", _vr3.verdict == "reject",
      _vr3.verdict)
check("SM3-9 多项缺失全被列出",
      set(_vr3.missing_required) == {"description"},
      str(_vr3.missing_required))
# 空技能对象
_vr4 = skill_meta.check_skill({}, strict=True)
check("SM3-10 空对象 + strict → reject（不崩）",
      _vr4.verdict == "reject", _vr4.verdict)
check("SM3-11 空对象 + 非strict → degraded（不崩）",
      skill_meta.check_skill({}, strict=False).verdict == "degraded")

# ============================================================
# SM5  fail-open：校验器自身出错必须放行
# ============================================================
print("\n--- SM5 fail-open ---")


class _Boom(object):
    def __getattr__(self, n):
        raise RuntimeError("boom")


_vb2 = skill_meta.check_skill(_Boom(), strict=True)
check("SM5-1 属性访问全抛异常 + strict=True → 仍放行（失效方向=放行）",
      _vb2.verdict == "ok", _vb2.verdict)
_vb3 = skill_meta.check_skill(None, strict=True)
check("SM5-2 None → 放行不崩", _vb3.verdict == "ok", _vb3.verdict)
_vb4 = skill_meta.check_skill(12345, strict=True)
check("SM5-3 非 dict 非对象（int）→ 放行不崩", _vb4.verdict == "ok", _vb4.verdict)
check("SM5-4 unverified_note(None) → 空串不抛",
      skill_meta.unverified_note(None) == "")
# SM5-0（静态）：fail-open 有**三层**纵深防御，缺任何一层都要能看出来。
# 为什么必须静态断言：三层里单拆一层，行为层红不了（另两层照样兜住）——
# 实测单拆探针/单拆字段级/单拆函数级 except 全部红 0 条。
# 「三层都在」这件事只能由源码断言守住，行为断言只能守「崩了会放行」。
_sm = _code_lines("skill_meta.py")
check("SM5-0a 第①层：函数级 except 兜底放行",
      "except Exception as e:" in _sm
      and _sm.count("log.warning(\"技能元数据校验异常") >= 1)
check("SM5-0b 第②层：探针先探字段能不能取到",
      "_probe, _got = _get(skill, \"description\")" in _sm
      and "if not _got:" in _sm)
check("SM5-0c 第③层：字段级 `if not ok` 兜底（3 个字段组各一道）",
      _sm.count("if not ok:") >= 3, "实际 %d 处" % _sm.count("if not ok:"))
check("SM5-5 rejection_text(None) → 空串不抛",
      skill_meta.rejection_text(None) == "")
check("SM5-6 unverified_note 对非 degraded → 空串",
      skill_meta.unverified_note(skill_meta.check_skill(_full())) == "")

# ============================================================
# SM6  unverified_note 只挂在 degraded 上
# ============================================================
print("\n--- SM6 未审提示 ---")
_note = skill_meta.unverified_note(_vb)
check("SM6-1 degraded → 产出未审提示", bool(_note.strip()), _note)
check("SM6-2 提示里列出了缺失字段", "source" in _note, _note)
check("SM6-3 提示要求把技能当参考而非权威",
      ("参考" in _note and "权威" in _note), _note)
check("SM6-4 提示对破坏性操作要求先确认",
      "确认" in _note, _note)
check("SM6-5 reject 判决不产出未审提示（该走拒用文案）",
      skill_meta.unverified_note(_vr) == "")
check("SM6-5b **ok** 判决也不产出未审提示（齐全技能零行为变化）",
      skill_meta.unverified_note(skill_meta.check_skill(_full())) == "")
check("SM6-5c 只有 degraded 才产出提示（三档逐一确认）",
      (skill_meta.unverified_note(skill_meta.check_skill(_full())) == ""
       and skill_meta.unverified_note(_vr) == ""
       and bool(skill_meta.unverified_note(_vb))))
# rejection_text
_rj = skill_meta.rejection_text(_vr)
check("SM6-6 reject → 拒用文案非空", bool(_rj.strip()), _rj)
check("SM6-7 拒用文案与 verdict.reason 同源",
      _rj == _vr.reason)
check("SM6-8 非 reject → 拒用文案空串",
      skill_meta.rejection_text(_vb) == "")

# ============================================================
# is_trusted_builtin
# ============================================================
print("\n--- 内置信任 ---")
for s in ("builtin", "internal", "app"):
    check("SM6-9 source=%s 视为内置可信" % s,
          skill_meta.is_trusted_builtin({"source": s}) is True)
check("SM6-10 source=user 非内置",
      skill_meta.is_trusted_builtin({"source": "user"}) is False)
check("SM6-11 source=BUILTIN 大写也认（归一化）",
      skill_meta.is_trusted_builtin({"source": "BUILTIN"}) is True)
check("SM6-12 无 source → 非内置",
      skill_meta.is_trusted_builtin({}) is False)
check("SM6-13 None → 非内置不崩",
      skill_meta.is_trusted_builtin(None) is False)

# ============================================================
# strict_from_config
# ============================================================
print("\n--- strict_from_config ---")
check("SM6-14 dict cfg 缺字段 → False（默认不拒用）",
      skill_meta.strict_from_config({}) is False)
check("SM6-15 dict cfg 显式 False → False",
      skill_meta.strict_from_config({"skill_meta_strict": False}) is False)
check("SM6-16 dict cfg 显式 True → True",
      skill_meta.strict_from_config({"skill_meta_strict": True}) is True)
check("SM6-17 cfg=None → False（不崩）",
      skill_meta.strict_from_config(None) is False)


# ============================================================
# SM7  接线钉子
# ============================================================
print("\n--- SM7 接线钉子 ---")


_sl = _code_lines("skill_loader.py")
_tl = _code_lines("tools.py")
check("SM7-1 skill_loader 提供 find_skill（拿得到元数据字段）",
      "def find_skill(" in _sl)
check("SM7-2 load_skill_prompt 支持 strict_meta 参数",
      "def load_skill_prompt(name, skills_dir, strict_meta=False)" in _sl)
check("SM7-3 load_skill_prompt 真调 check_skill（校验真接线）",
      "skill_meta.check_skill(" in _sl)
# ⚠️ v4.227：范围必须限定在 `load_skill_prompt` **函数体内**。
# 原写法是全文 `_sl.find(...)` 取第一个出现位置，而
# `wrap_skill_prompt_text`（v4.227 新增的技能包装转发）里那句
# `return wrap_skill_prompt_text(text, name)` 以 `return wrap_skill_prompt`
# 为前缀，排在文件前部 → 全文 find 命中的是它，不是 load_skill_prompt 的返回点，
# 于是「拒用分支在返回之前」这条真约束被一个无关函数的字符串顶掉了。
# 这类「全文 find 比较两个位置」的顺序断言，第二个串必须加尾随分隔符
# （`(` 或空格），否则任何以它为前缀的新名字都会造成假红。
def _fn_body(code, name):
    """取 `def <name>(` 那一行起到下一个顶层 def 之间的代码文本。"""
    key = "def %s(" % name
    i = code.find(key)
    if i < 0:
        return ""
    j = code.find("\ndef ", i + 1)
    return code[i:j] if j > 0 else code[i:]


_lsp = _fn_body(_sl, "load_skill_prompt")
check("SM7-4 reject 时返回 None（真拒用，不是照常返回）",
      ("VERDICT_REJECT" in _lsp
       and _lsp.find("VERDICT_REJECT") < _lsp.find("return wrap_skill_prompt")),
      "reject 分支必须在返回 prompt 之前")
check("SM7-5 tools.tool_use_skill 真查元数据",
      "check_skill(" in _tl)
check("SM7-6 tools 真用 config 的 strict 口径",
      "strict_from_config(" in _tl)
check("SM7-7 tools 真有拒用分支且在返回前",
      ("reject_reason" in _tl and "if reject_reason:" in _tl
       and _tl.find("if reject_reason:") < _tl.find("if prompt is None:")),
      "拒用分支必须在「未找到」分支之前")
check("SM7-8 tools 降级放行时追加未审提示",
      "unverified_note(" in _tl)

# ============================================================
# SM8  端到端：造真技能目录
# ============================================================
print("\n--- SM8 端到端（真目录真解析）---")
try:
    import skill_loader

    _tmp = tempfile.mkdtemp(prefix="sm226_")
    # ① 元数据齐全
    _ok_dir = os.path.join(_tmp, "ok_skill")
    os.makedirs(_ok_dir)
    with open(os.path.join(_ok_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: ok_skill\ndescription: 元数据齐全的演示技能\n"
                "source: user\nversion: 1.0.0\nhash: h1\n"
                "audited_at: 2026-10-07\nallow_tools: read_file\n"
                "allow_dir: C:/workspace\nallow_network: false\n"
                "allow_system: false\n"
                "---\n\n# 内容\n请严格按此执行。\n")
    # ② 无 source/version（老技能形态）
    _old_dir = os.path.join(_tmp, "old_skill")
    os.makedirs(_old_dir)
    with open(os.path.join(_old_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: old_skill\ndescription: 早期写法，没声明来源\n"
                "---\n\n# 老技能\n照此执行。\n")
    skill_loader.invalidate_skill_cache()

    _p_ok = skill_loader.load_skill_prompt("ok_skill", _tmp)
    check("SM8-1 齐全技能正常加载", _p_ok is not None and "照此" not in _p_ok,
          repr(_p_ok)[:120])
    check("SM8-2 加载文本仍包不可信边界（v4.222 语义未丢）",
          _p_ok is not None and "<untrusted skill=" in _p_ok, repr(_p_ok)[:80])

    _p_old = skill_loader.load_skill_prompt("old_skill", _tmp)
    check("SM8-3 老技能在默认档**仍可加载**（不误伤）", _p_old is not None)
    check("SM8-4 老技能 strict=False 加载成功",
          skill_loader.load_skill_prompt("old_skill", _tmp,
                                         strict_meta=False) is not None)
    check("SM8-5 老技能 strict=True → 返回 None（拒用）",
          skill_loader.load_skill_prompt("old_skill", _tmp,
                                         strict_meta=True) is None)
    check("SM8-6 齐全技能 strict=True 仍可加载（不误伤）",
          skill_loader.load_skill_prompt("ok_skill", _tmp,
                                         strict_meta=True) is not None)
    _sk_old = skill_loader.find_skill("old_skill", _tmp)
    check("SM8-7 find_skill 返回 Skill 对象（带元数据字段）",
          _sk_old is not None and getattr(_sk_old, "source", "") == "",
          repr(_sk_old))
    # v4.226 实测修掉的设计缺陷：初版把核验结论挂 sk.meta_verdict，而
    # scan_skills 返回**缓存共享**对象 → 先 strict=True 再 strict=False 时
    # 挂上去的是后者的结论，调用方读到别人的判断。现在改为纯函数自取。
    check("SM8-8 Skill 对象上**不挂** meta_verdict（无共享可变副作用）",
          not hasattr(_sk_old, "meta_verdict"),
          repr(getattr(_sk_old, "meta_verdict", None)))
    check("SM8-8b 核验结论由调用方自取（check_skill 是纯函数）",
          skill_meta.check_skill(_sk_old, strict=False).verdict == "degraded")
    check("SM8-8c 同一对象两次不同 strict 判定互不污染",
          (skill_meta.check_skill(_sk_old, strict=True).verdict == "reject"
       and skill_meta.check_skill(_sk_old, strict=False).verdict == "degraded"),
      "挂字段的实现会在这里读到同一个值")
    # 名字归一：emoji/大小写/空格
    check("SM8-9 find_skill 走名字归一（大小写/连字符）",
          skill_loader.find_skill("OK_SKILL", _tmp) is not None)
    check("SM8-10 找不到时 find_skill 返回 None",
          skill_loader.find_skill("不存在的技能xyz", _tmp) is None)
    check("SM8-11 空名 → None 不抛",
          skill_loader.find_skill("", _tmp) is None)

    # 真实端到端：tools.tool_use_skill 在 strict 下拒用
    import tools as _tools
    _orig_dirs = None
    try:
        import config as _cfg
        _orig_dirs = _cfg.get_skill_scan_dirs
        _cfg.get_skill_scan_dirs = lambda: [_tmp]
        import config as _cfg2
        _old_enabled = _cfg2.is_skill_enabled
        _cfg2.is_skill_enabled = lambda n: True
        _res_old = _tools.tool_use_skill({}, _tmp, "old_skill")
        check("SM8-12 端到端：默认档老技能仍能用（返回已加载技能）",
              "已加载技能" in _res_old, repr(_res_old)[:160])
        check("SM8-13 端到端：默认档老技能被标注未审来源",
              "元数据不完整" in _res_old or "未经审计" in _res_old,
              repr(_res_old)[:220])
        _res_ok = _tools.tool_use_skill({}, _tmp, "ok_skill")
        check("SM8-14 端到端：齐全技能返回不含未审提示（零行为变化）",
              "未经审计" not in _res_ok and "元数据不完整" not in _res_ok,
              repr(_res_ok)[:220])
        check("SM8-15 端到端：齐全技能仍正常注入专家指令",
              "照此" not in _res_ok and "专家指令" in _res_ok,
              repr(_res_ok)[:160])
        # strict 打开 → 拒用
        _cfg2.is_skill_enabled = _old_enabled
        _cfg2.get_skill_scan_dirs = lambda: [_tmp]
        import skill_meta as _sm
        _real_strict = _sm.strict_from_config
        try:
            _sm.strict_from_config = lambda cfg=None: True
            _res_rej = _tools.tool_use_skill({}, _tmp, "old_skill")
            check("SM8-16 端到端：strict 打开 → 老技能被拒用",
                  "拒绝" in _res_rej or "缺少必填元数据" in _res_rej,
                  repr(_res_rej)[:220])
            check("SM8-17 端到端：拒用文案含可执行补救指引",
                  "frontmatter" in _res_rej or "补齐" in _res_rej,
                  repr(_res_rej)[:260])
            _res_ok2 = _tools.tool_use_skill({}, _tmp, "ok_skill")
            check("SM8-18 端到端：strict 下齐全技能不受影响",
                  "已加载技能" in _res_ok2, repr(_res_ok2)[:160])
        finally:
            _sm.strict_from_config = _real_strict
    finally:
        if _orig_dirs is not None:
            _cfg.get_skill_scan_dirs = _orig_dirs
        try:
            import shutil
            shutil.rmtree(_tmp, ignore_errors=True)
        except Exception:
            pass
except Exception as _e:
    import traceback
    traceback.print_exc()
    check("SM8-0 端到端：真目录解析真可跑", False,
          "%s: %s" % (type(_e).__name__, _e))

# ============================================================
# SM9  现有真实技能目录在默认档下全部仍可加载（实测证据）
# ============================================================
print("\n--- SM9 存量技能不误伤（实测）---")
try:
    import skill_loader as _sl2
    try:
        from config import get_skill_scan_dirs as _gsd
        _dirs = _gsd()
    except Exception:
        _dirs = [os.path.join(ROOT, "skills")]
    # v4.239.1（CI 红 → 修）：SM9 的本意是「扫真目录实测不误伤」，扫描目录来自
    # config.get_skill_scan_dirs()（用户数据目录）。CI/干净环境一个真目录都没有时
    # _total 必为 0，这是环境空、不是误伤证据 → SKIP，不假红。
    if not any(os.path.isdir(_d) for _d in _dirs):
        print("  [SKIP] 无任何真实技能目录（CI/干净环境）——SM9 实测段跳过，不冒充通过")
    else:
        _total = 0
        _degraded = 0
        _okc = 0
        _rej = 0
        for _d in _dirs:
            if not os.path.isdir(_d):
                continue
            for _sk in _sl2.scan_skills(_d):
                _total += 1
                _v = skill_meta.check_skill(_sk, strict=False)
                if _v.verdict == "degraded":
                    _degraded += 1
                elif _v.verdict == "ok":
                    _okc += 1
                # 默认档一律不得 reject
                if skill_meta.check_skill(_sk, strict=False).verdict == "reject":
                    _rej += 1
                # 端到端：默认档必须仍能加载出文本
                if _sl2.load_skill_prompt(_sk.name, _d, strict_meta=False) is None:
                    check("SM9-x 存量技能默认档可加载：%s" % _sk.name, False)
        check("SM9-1 扫到存量技能（>0，证明确实在扫真目录）", _total > 0,
              "共 %d 个" % _total)
        check("SM9-2 默认档下存量技能 **零拒用**（不误伤）", _rej == 0,
              "拒用 %d 个" % _rej)
        print("       （实测：共 %d 个技能，degraded %d / ok %d）"
              % (_total, _degraded, _okc))
except Exception as _e:
    check("SM9-0 存量技能实测可跑", False, "%s: %s" % (type(_e).__name__, _e))

print("\n" + "=" * 62)
print("PASS=%d FAIL=%d" % (PASS, FAIL))
print("=" * 62)
sys.exit(1 if FAIL else 0)