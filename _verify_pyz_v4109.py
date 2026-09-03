# -*- coding: utf-8 -*-
"""PYZ 硬核验证 v4.109：模型下拉 + 路由旁路日志 已编译进冻结 exe。

不是查源码——是从 dist exe 里把 PYZ 解出来，确认：
1. route_log 模块进了包（spec hiddenimports 生效）。
2. log_route / _tier / read_recent 真的在里面。
3. ui 模块里有 chat_model_combo（**且不是** 被设置弹层的 model_combo 顶掉的版本）。
4. 手动锁定语义在字节码里：manual_lock / lock_invalid:fallback_main。
5. 归因字符串 kw: / len: 在包里。

判定铁律（沿用 v4.108 踩过的坑）：
- 模块查找必须 `k.split(".")[-1] == name`，禁用 endswith（会命中 xxx_tools 等 30+ 模块）。
- 字符串要展开 tuple const（`return "a","b"` 会被编译期 fold 成 tuple）。
- 三处符号分清：形参在 co_varnames / 函数名·属性在 co_names / 字面量在 co_consts。
  因此「符号是否出现」一律用 all_names = co_names ∪ co_consts 字符串 判定。
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

    ⚠️ 禁止 endswith（会命中 browser_control_tools 等 30+ 模块）；
    也**不能只写** `k.split(".")[-1] == name` —— v4.110 实测：PYZ 里 `tools`
    有 7 个同名候选（comtypes.tools / pandas.core.tools / pydantic.tools …），
    toc 是 dict、顺序不定，退回末段匹配前必须先试全名精确匹配。
    """
    for k in zr.toc.keys():
        if k == name:
            return k
    for k in zr.toc.keys():
        if k.split(".")[-1] == name:
            return k
    return None


def collect(mod_name):
    """返回 (code_objects, all_names)。all_names = co_names ∪ co_consts 里的字符串。"""
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
            elif isinstance(c, tuple):      # 编译期 fold：return "a","b"
                for t in c:
                    if isinstance(t, str):
                        names.add(t)
                    elif isinstance(t, types.CodeType):
                        walk(t)
    walk(raw)
    return codes, names


def has_fn(codes, fn):
    return any(c.co_name == fn for c in codes)


print("=" * 60)
print("PYZ 硬核验证 v4.109（解冻结 exe）")
print("=" * 60)

# ---- 1. route_log 模块 ----
print("\n[1] route_log 模块入包")
rl_codes, rl_names = collect("route_log")
chk("route_log 模块在 PYZ 中", bool(rl_codes), "模块缺失（spec hiddenimports 未生效？）")
if rl_codes:
    chk("有 log_route", has_fn(rl_codes, "log_route"))
    chk("有 _tier（付费/免费档判定）", has_fn(rl_codes, "_tier"))
    chk("有 read_recent（复盘读取）", has_fn(rl_codes, "read_recent"))
    chk("有 _log_path", has_fn(rl_codes, "_log_path"))
    chk("落盘文件名 route_log.jsonl", "route_log.jsonl" in rl_names)
    chk("付费档标记 paid", "paid" in rl_names)
    chk("免费档标记 free", "free" in rl_names)

# ---- 2. ui 模块：下拉与锁定语义 ----
print("\n[2] ui 模块：下拉 + 锁定语义")
ui_codes, ui_names = collect("ui")
chk("ui 模块在 PYZ 中", bool(ui_codes))
chk("输入区下拉 chat_model_combo 在包里", "chat_model_combo" in ui_names)
chk("仍保留设置弹层的 model_combo（未被误删）", "model_combo" in ui_names)
chk("Auto 文案在包里", any("Auto" in n for n in ui_names))
chk("主模型锁定标记 __main__", "__main__" in ui_names)
chk("手动锁定归因 manual_lock:", any(str(n).startswith("manual_lock") for n in ui_names))
chk("锁定失效回退 lock_invalid:fallback_main",
    any("lock_invalid" in str(n) for n in ui_names))
chk("关键词归因 kw:", "kw:" in ui_names)
# 注意：是运行时格式化，包里存的是完整格式串 "len:%d>%s"，不存在裸 "len:" 常量
chk("长度归因 len:%d>%s", "len:%d>%s" in ui_names)
chk("AgentWorker 归因 agent_worker", "agent_worker" in ui_names)
chk("流式锁定归因 stream_lock", "stream_lock" in ui_names)
chk("_fill_model_combo 方法在包里", has_fn(ui_codes, "_fill_model_combo"))
chk("_on_model_combo_changed 方法在包里", has_fn(ui_codes, "_on_model_combo_changed"))
chk("_log_route 方法在包里", has_fn(ui_codes, "_log_route"))
chk("_complexity_reason 方法在包里", has_fn(ui_codes, "_complexity_reason"))

# ---- 3. 路由方法本体 ----
print("\n[3] _route_model 本体")
rm = [c for c in ui_codes if c.co_name == "_route_model"]
chk("_route_model 在包里", bool(rm))
if rm:
    f = rm[0]
    chk("签名含 reason 形参", "reason" in f.co_varnames, f.co_varnames[:8])

# ---- 4. config 默认项 ----
print("\n[4] config 默认 model_lock")
cfg_codes, cfg_names = collect("config")
chk("config 模块在 PYZ 中", bool(cfg_codes))
chk("config 含 model_lock 键", "model_lock" in cfg_names)

print("\n" + "=" * 60)
if _FAIL:
    print("PYZ_V4109_FAIL: %d 项" % len(_FAIL))
    for x in _FAIL:
        print("  - " + x)
    raise SystemExit(1)
print("PYZ_V4109_OK")
