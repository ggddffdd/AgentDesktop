# -*- coding: utf-8 -*-
"""PYZ 硬核验证 v4.110：技能使用体检埋点 已编译进冻结 exe。

不是查源码——是从 dist exe 里把 PYZ 解出来，确认：
1. route_log 里有 log_skill 函数，且它写的 event 常量是 "skill"。
2. tools 里有 _log_skill_hit 函数，且 source 常量是 "auto"（模型自动命中）。
3. ui 的 _on_skill_pick 里有 "manual" 常量（用户手动点选）。
4. 防御式 import 的 `route_log = None` 兜底语义在包里（模块缺失不许拖垮主程序）。

判定铁律（沿用 v4.108/109 踩过的坑）：
- 模块查找必须 `k.split(".")[-1] == name`，禁用 endswith。
- 字符串要展开 tuple const。
- 形参在 co_varnames / 函数名·属性在 co_names / 字面量在 co_consts。
- **常量归属要落到具体函数**：只查模块级 all_names 会被别处的同名字符串混过去，
  因此关键常量一律定位到对应 code object 的 co_consts 再断言。
"""
import io
import types

from PyInstaller.archive.readers import ZlibArchiveReader

EXE = "dist/小臭玩AI/小臭玩AI.exe"
_FAIL = []


def chk(name, cond, extra=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra and not cond else ""))
    if not cond:
        _FAIL.append(name)


data = open(EXE, "rb").read()
off = data.find(b"PYZ\x00")
if off < 0:
    print("未找到 PYZ 魔数，打包结构异常")
    raise SystemExit(1)
zr = ZlibArchiveReader(EXE, start_offset=off)


def find_module(name):
    """完整模块名精确匹配优先，再退回末段匹配。

    ⚠️ v4.110 踩坑：只写 `k.split(".")[-1] == name` 会命中一堆同名子模块——
    PYZ 里 `tools` 就有 7 个候选（tools / comtypes.tools / pandas.core.tools /
    pydantic.tools / pydantic.v1.tools / pydantic.deprecated.tools /
    pandas.plotting._matplotlib.tools），toc 是 dict、顺序不定，
    取到第一个就直接拿错模块，后面所有断言全废。
    """
    for k in zr.toc.keys():
        if k == name:
            return k
    for k in zr.toc.keys():
        if k.split(".")[-1] == name:
            return k
    return None


def collect(mod_name):
    key = find_module(mod_name)
    if not key:
        return [], set()
    raw = zr.extract(key)
    if not isinstance(raw, types.CodeType):
        return [], set()
    codes, names = [], set()

    def walk(co):
        codes.append(co)
        for n in co.co_names:
            names.add(str(n))
        for c in co.co_consts:
            if isinstance(c, types.CodeType):
                walk(c)
            elif isinstance(c, str):
                names.add(c)
            elif isinstance(c, tuple):
                for t in c:
                    if isinstance(t, str):
                        names.add(t)
                    elif isinstance(t, types.CodeType):
                        walk(t)
    walk(raw)
    return codes, names


def fn_consts(codes, fn):
    """取指定函数的全部字符串常量（含嵌套 code）。找不到返回 None。"""
    for c in codes:
        if c.co_name == fn:
            out = set()

            def w(co):
                for x in co.co_consts:
                    if isinstance(x, types.CodeType):
                        w(x)
                    elif isinstance(x, str):
                        out.add(x)
                    elif isinstance(x, tuple):
                        for t in x:
                            if isinstance(t, str):
                                out.add(t)
            w(c)
            return out
    return None


def has_fn(codes, fn):
    return any(c.co_name == fn for c in codes)


print("=" * 60)
print("PYZ 硬核验证 v4.110（解冻结 exe）")
print("=" * 60)

# ---- 1. route_log.log_skill ----
print("\n[1] route_log 模块：log_skill")
rl_codes, rl_names = collect("route_log")
chk("route_log 模块在 PYZ 中", bool(rl_codes))
if rl_codes:
    chk("有 log_skill 函数", has_fn(rl_codes, "log_skill"))
    chk("log_route 仍在（v4.109 未回归）", has_fn(rl_codes, "log_route"))
    chk("read_recent 仍在（v4.109 未回归）", has_fn(rl_codes, "read_recent"))
    c = fn_consts(rl_codes, "log_skill")
    chk("log_skill 常量可解析", c is not None)
    if c:
        chk('log_skill 写 event="skill"', "skill" in c, sorted(c)[:12])
        chk("log_skill 带 ok 字段", "ok" in c)
        chk("log_skill 带 source 字段", "source" in c)

# ---- 2. tools._log_skill_hit ----
print("\n[2] tools 模块：自动埋点")
tl_codes, tl_names = collect("tools")
chk("tools 模块在 PYZ 中", bool(tl_codes))
# 防「拿错同名子模块」：主 tools 必然含 tool_use_skill，子模块绝不会含
chk("取到的是主 tools 而非同名子模块", "tool_use_skill" in tl_names,
    "拿到的是别的 xxx.tools —— find_module 匹配写错了")
if tl_codes:
    chk("有 _log_skill_hit 函数", has_fn(tl_codes, "_log_skill_hit"))
    c = fn_consts(tl_codes, "_log_skill_hit")
    chk("_log_skill_hit 常量可解析", c is not None)
    if c:
        chk("source 常量 auto", "auto" in c, sorted(c)[:12])
    us = fn_consts(tl_codes, "tool_use_skill")
    chk("tool_use_skill 常量可解析", us is not None)
    if us:
        chk("未找到分支文案仍在", any("未找到技能" in x for x in us))
    chk("防御式 import 兜底（route_log 缺失不拖垮工具）",
        "route_log" in tl_names)

# ---- 3. ui._on_skill_pick ----
print("\n[3] ui 模块：手动埋点")
ui_codes, ui_names = collect("ui")
chk("ui 模块在 PYZ 中", bool(ui_codes))
if ui_codes:
    chk("有 _on_skill_pick", has_fn(ui_codes, "_on_skill_pick"))
    c = fn_consts(ui_codes, "_on_skill_pick")
    chk("_on_skill_pick 常量可解析", c is not None)
    if c:
        chk("source 常量 manual", "manual" in c, sorted(c)[:12])
    chk("v4.109 下拉未回归（chat_model_combo 仍在）", "chat_model_combo" in ui_names)
    chk("v4.109 锁定语义未回归（__main__ 仍在）", "__main__" in ui_names)

print("\n" + "=" * 60)
if _FAIL:
    print("PYZ_V4110_FAIL: %d 项" % len(_FAIL))
    for x in _FAIL:
        print("  - " + x)
    raise SystemExit(1)
print("PYZ_V4110_OK")
