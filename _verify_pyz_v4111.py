# -*- coding: utf-8 -*-
"""PYZ 硬核验证 v4.111：enabled_tools 工具白名单 已编译进冻结 exe。

不是查源码——是从 dist exe 里把 PYZ 解出来，确认：
1. config 里有 _load_enabled_tools / _filter_enabled_tools，且 get_all_tools 真调用了它。
2. tool_manager_ui **整个模块进了包**（它是函数内延迟导入，静态分析扫不到，
   漏了 hiddenimports 的话点菜单直接 ModuleNotFoundError）。
3. ui 的托盘入口 _open_tool_manager 在包里。
4. v4.110 的埋点未回归。

判定铁律（v4.108~v4.110 三次踩坑的积累）：
- 模块查找 **先全名精确匹配 `k == name`，再退回末段匹配**——PYZ 里 `tools`
  有 7 个同名候选、`config` 也有多个，只做末段匹配会拿错模块，断言全废。
- 字符串常量要**展开 tuple const**（`return "A","B"` 会被 fold 成 tuple）。
- 形参在 co_varnames / 函数名·属性在 co_names / 字面量在 co_consts。
- **常量归属要落到具体函数**：只查模块级 names 会被别处同名字符串混过去。
"""
import types

from PyInstaller.archive.readers import ZlibArchiveReader

EXE = "dist/小臭玩AI/小臭玩AI.exe"
_FAIL = []


def chk(name, cond, extra=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name
          + (("  -> " + str(extra)) if extra and not cond else ""))
    if not cond:
        _FAIL.append(name)


data = open(EXE, "rb").read()
off = data.find(b"PYZ\x00")
if off < 0:
    print("未找到 PYZ 魔数，打包结构异常")
    raise SystemExit(1)
zr = ZlibArchiveReader(EXE, start_offset=off)


def find_module(name):
    """完整模块名精确匹配优先，再退回末段匹配。"""
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
        return [], set(), None
    raw = zr.extract(key)
    if not isinstance(raw, types.CodeType):
        return [], set(), None
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
                    if isinstance(t, types.CodeType):
                        walk(t)
                    elif isinstance(t, str):
                        names.add(t)
    walk(raw)
    return codes, names, key


def fn_consts(codes, fn):
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


def fn_names(codes, fn):
    """取指定函数直接引用的名字（co_names）——用于验证调用关系。"""
    for c in codes:
        if c.co_name == fn:
            return {str(n) for n in c.co_names}
    return None


def has_fn(codes, fn):
    return any(c.co_name == fn for c in codes)


print("=" * 62)
print("PYZ 硬核验证 v4.111（解冻结 exe）")
print("=" * 62)

# ---- 1. config ----
print("\n[1] config 模块：enabled_tools 白名单")
cf_codes, cf_names, cf_key = collect("config")
chk("config 模块在 PYZ 中", bool(cf_codes))
# 防呆：主 config 必然含 get_all_tools，同名子模块绝不会含
chk("取到的是主 config 而非同名子模块", "get_all_tools" in cf_names,
    "find_module 匹配写错了，拿到的是别的 xxx.config")
if cf_codes:
    chk("有 _load_enabled_tools 函数", has_fn(cf_codes, "_load_enabled_tools"))
    chk("有 _filter_enabled_tools 函数", has_fn(cf_codes, "_filter_enabled_tools"))
    c = fn_consts(cf_codes, "_filter_enabled_tools")
    chk("_filter_enabled_tools 常量可解析", c is not None)
    if c:
        chk("读的是 enabled_tools 键", "enabled_tools" in c, sorted(c)[:12])
        chk("匹配不到名字时告警（不静默吞掉）",
            any("enabled_tools 里有" in x for x in c), sorted(c)[:12])
    g = fn_names(cf_codes, "get_all_tools")
    chk("get_all_tools 常量可解析", g is not None)
    if g:
        chk("get_all_tools 真调用了 _filter_enabled_tools",
            "_filter_enabled_tools" in g, sorted(g)[:20])
    chk("模块级含 enabled_tools（DEFAULT_CONFIG 已登记）",
        "enabled_tools" in cf_names)
    chk("save_config 仍在（管理器存盘要用）", has_fn(cf_codes, "save_config"))

# ---- 2. tool_manager_ui（新增模块，最怕漏打包）----
print("\n[2] tool_manager_ui 模块：工具管理器（延迟导入，必须显式登记）")
tm_codes, tm_names, tm_key = collect("tool_manager_ui")
chk("tool_manager_ui 模块在 PYZ 中", bool(tm_codes),
    "没进包 → 点托盘菜单会 ModuleNotFoundError；检查 spec 的 hiddenimports")
if tm_codes:
    chk("有 ToolManagerWindow 类方法集", has_fn(tm_codes, "load_tools"))
    chk("有 open_tool_manager 入口函数", has_fn(tm_codes, "open_tool_manager"))
    chk("有 _apply_preset（一键档位）", has_fn(tm_codes, "_apply_preset"))
    chk("有 _tool_usage_counts（读历史使用次数）",
        has_fn(tm_codes, "_tool_usage_counts"))
    chk("有 _save_config（勾选即存盘）", has_fn(tm_codes, "_save_config"))
    lt = fn_consts(tm_codes, "load_tools")
    chk("load_tools 常量可解析", lt is not None)
    if lt:
        chk("⚠ load_tools 会清空白名单拿全量（防自锁）",
            "enabled_tools" in lt, sorted(lt)[:12])
    ap = fn_consts(tm_codes, "_apply_preset")
    chk("_apply_preset 常量可解析", ap is not None)
    if ap:
        chk("三档预设齐全（全开/均衡/精简）",
            all(k in ap for k in ("全开", "均衡", "精简")), sorted(ap)[:12])

# ---- 3. ui 托盘入口 ----
print("\n[3] ui 模块：托盘「工具管理器」入口")
ui_codes, ui_names, ui_key = collect("ui")
chk("ui 模块在 PYZ 中", bool(ui_codes))
if ui_codes:
    chk("有 _open_tool_manager 方法", has_fn(ui_codes, "_open_tool_manager"))
    c = fn_consts(ui_codes, "_open_tool_manager")
    chk("_open_tool_manager 常量可解析", c is not None)
    if c:
        chk("托盘文案「工具管理器」在包里", "工具管理器" in c, sorted(c)[:12])
    n = fn_names(ui_codes, "_open_tool_manager")
    if n:
        chk("入口真调用了 open_tool_manager", "open_tool_manager" in n, sorted(n)[:20])
    chk("v4.109 未回归：_on_skill_pick 仍在", has_fn(ui_codes, "_on_skill_pick"))
    chk("v4.104 未回归：ChatWebView 仍在", "ChatWebView" in ui_names)

# ---- 4. v4.110 回归 ----
print("\n[4] v4.110 回归：技能埋点未被破坏")
rl_codes, rl_names, _ = collect("route_log")
chk("route_log 模块在 PYZ 中", bool(rl_codes))
if rl_codes:
    chk("log_skill 仍在", has_fn(rl_codes, "log_skill"))
tl_codes, tl_names, _ = collect("tools")
chk("tools 模块在 PYZ 中", bool(tl_codes))
if tl_codes:
    chk("取到的是主 tools 而非同名子模块", "tool_use_skill" in tl_names)
    chk("tools._log_skill_hit 仍在", has_fn(tl_codes, "_log_skill_hit"))

print()
print("=" * 62)
if _FAIL:
    print("FAILED %d 项：" % len(_FAIL))
    for f in _FAIL:
        print("   - %s" % f)
    raise SystemExit(1)
print("ALL_OK")
