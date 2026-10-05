# -*- coding: utf-8 -*-
"""v4.214.0 判据：证据库入库脱敏（外部审核 P1-3 可采纳部分）。

设计边界（与审核建议的对账）：
  - 采纳：入库前对凭据类内容打码（token/cookie/authorization/api key）。
  - 不采纳：「默认只存摘要」—— 那会摧毁 v4.195 证据链「完整原文可回验」的设计目的。
  - 规则全部带上下文（键名/前缀/结构），禁止裸「N 位字母数字」泛匹配（L233）。

零副作用：evidence.set_dir(%TEMP%) 隔离，不碰真实证据库。
支持 EV_PATH 变异注入（扰动脚本用）。
"""
import ast
import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  [OK]  " if cond else "  [FAIL] ") + name + (f"  ({detail})" if detail and not cond else ""))


def load_ev():
    """EV_PATH 指向变异副本时从那里加载（扰动注入），否则真仓。"""
    ev_path = os.environ.get("EV_PATH")
    if ev_path:
        spec = importlib.util.spec_from_file_location("ev_mut", ev_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    sys.path.insert(0, ROOT)
    import evidence
    return evidence


def group_a_pure():
    print("== A 组：_mask_secrets 纯函数行为 ==")
    ev = load_ev()
    m = ev._mask_secrets
    # A1 sk- key
    r = m("key 是 sk-abc123def456ghi789xyz 请保管好")
    check("A1  sk- API key 打码", "sk-abc123def456ghi789xyz" not in r and "***" in r, r[:60])
    # A2 Bearer（值长于 12）
    r = m("Authorization: Bearer abcdef1234567890abcd")
    check("A2a Bearer 凭据打码、方案名保留", "Bearer ***" in r and "abcdef1234567890abcd" not in r, r)
    r = m("Proxy-Authorization: Basic dXNlcjpwYXNzd29yZA==")
    check("A2b Basic 凭据打码", "dXNlcjpwYXNzd29yZA==" not in r and "Basic ***" in r, r)
    # A3 键值上下文（JSON / url）
    r = m('{"api_key": "abcdef12345678", "city": "昆明"}')
    check("A3a JSON api_key 值打码、键保留", "api_key" in r and "abcdef12345678" not in r and "昆明" in r, r)
    r = m("https://x.com/api?token=abcdef12345678&page=2")
    check("A3b url token= 值打码、参数结构保留", "token=***" in r and "page=2" in r, r)
    # A4 JWT 三段
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N"
    r = m(f"返回了 jwt: {jwt} 过期时间 1h")
    check("A4  JWT 三段结构打码", jwt not in r and "jwt: ***" in r, r[:70])
    # A5 Cookie 头行
    r = m("Set-Cookie: sessionid=abc123def456ghi789; Path=/; HttpOnly")
    check("A5  Cookie/Set-Cookie 头行值打码", "sessionid=abc" not in r and "Set-Cookie: ***" in r, r)
    # A6 短值/普通内容不误伤（防过度打码 = 防证据失效）
    r = m("token: 5 retries left; password 重置已完成")
    check("A6a 短值（<8 位）不打码", "token: 5" in r, r)
    r = m("今天昆明的天气很好。api-kitten 是个词组，secret garden 也是。")
    check("A6b 普通文本一字不动", r == "今天昆明的天气很好。api-kitten 是个词组，secret garden 也是。", r)
    r = m("sk-short 太短不打码；sk-abcdefgh12345678 打码")
    check("A6c sk- 短于 16 位不打码", "sk-short" in r and "sk-abcdefgh12345678" not in r, r)
    # A7 幂等（打两遍 = 一遍）
    once = m("Authorization: Bearer abcdef1234567890 key=zzzz99998888")
    twice = m(once)
    check("A7  幂等：二次打码不变形", once == twice, f"{once!r} vs {twice!r}")
    # A8 空值/None 容错
    check("A8  空串与 None 原样返回", m("") == "" and m(None) is None)


def group_b_register():
    print("== B 组：register 端到端（隔离目录） ==")
    ev = load_ev()
    tmp = tempfile.mkdtemp(prefix="evmask_")
    ev.set_dir(tmp)
    try:
        # B1 raw 里的凭据不落明文
        eid = ev.register("web_fetch", {"url": "https://x.com/a?token=abcdef12345678"},
                          "页面返回 Authorization: Bearer zz12ab34cd56ef78 内容")
        row = ev.get(eid)
        check("B1a register 返回有效 eid", eid is not None and row is not None)
        check("B1b raw 落库前已打码", "zz12ab34cd56ef78" not in (row or {}).get("raw", "")
              and "Bearer ***" in (row or {}).get("raw", ""), (row or {}).get("raw", "")[:60])
        # B2 sha 与库内文本自洽（回验链不受影响）
        raw = (row or {}).get("raw", "")
        sha_expect = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]
        check("B2  sha 基于打码后文本（库内自洽）", row.get("sha") == sha_expect,
              f"{row.get('sha')} vs {sha_expect}")
        # B3 args 里的凭据也打码
        eid2 = ev.register("api_call", {"api_key": "sk-abcdefgh1234567890", "q": "天气"},
                           "ok 普通结果")
        row2 = ev.get(eid2)
        check("B3  args 落库前已打码", "sk-abcdefgh1234567890" not in (row2 or {}).get("args", "")
              and "api_key" in (row2 or {}).get("args", ""), (row2 or {}).get("args", "")[:60])
        # B4 无凭据证据逐字保留（证据链可用性）
        plain = "第 3 行：负载率 87.5%，无越限。\n第 4 行：正常。"
        eid3 = ev.register("read_file", {"path": "d:/a.txt"}, plain)
        check("B4  无凭据证据逐字保留", ev.raw_text(eid3) == plain)
        # B5 err 文本打码
        eid4 = ev.register("tool_x", {}, "部分输出", ok=False,
                           err="HTTP 401 token=abcdef12345678 unauthorized")
        row4 = ev.get(eid4)
        check("B5  err 落库前已打码", "abcdef12345678" not in str((row4 or {}).get("err", ""))
              and "token=***" in str((row4 or {}).get("err", "")), str((row4 or {}).get("err", ""))[:60])
    finally:
        ev.set_dir(None) if hasattr(ev, "set_dir") else None
        shutil.rmtree(tmp, ignore_errors=True)


def group_c_contract():
    print("== C 组：源码契约（AST） ==")
    src = None
    ev_path = os.environ.get("EV_PATH") or os.path.join(ROOT, "evidence.py")
    with open(ev_path, "r", encoding="utf-8-sig") as f:
        src = f.read()
    tree = ast.parse(src)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    # C1 单源：_mask_secrets 定义在 evidence.py
    check("C1  _mask_secrets 定义存在（单源）", "_mask_secrets" in funcs)
    # C2 register 内对 raw 与 args_s 都调用 _mask_secrets
    reg = funcs.get("register")
    mask_targets = []
    if reg is not None:
        for n in ast.walk(reg):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_mask_secrets" and n.args):
                a = n.args[0]
                if isinstance(a, ast.Name):
                    mask_targets.append(a.id)
    check("C2a register 内对 raw 打码", "raw" in mask_targets, str(mask_targets))
    check("C2b register 内对 args_s 打码", "args_s" in mask_targets, str(mask_targets))
    # C3 规则上下文齐备：sk- / bearer|basic / cookie
    check("C3a 规则含 sk- 前缀", "sk-" in src)
    check("C3b 规则含 bearer|basic 方案", "bearer" in src.lower() and "basic" in src.lower())
    check("C3c 规则含 cookie 头", "cookie" in src.lower())
    # C4 打码函数挂在 register 数据流上（赋值形式 raw = _mask_secrets(raw)）
    if reg is not None:
        assign_form = any(
            isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name)
            and n.value.func.id == "_mask_secrets"
            and n.targets and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in ("raw", "args_s")
            for n in ast.walk(reg))
        check("C4  打码以赋值形式生效（防『调了不用』）", assign_form)


def main():
    for g in (group_a_pure, group_b_register, group_c_contract):
        try:
            g()
        except Exception as e:
            import traceback
            traceback.print_exc()
            # L258：崩溃也必须变成一条 [FAIL] 行（否则扰动端只看到「零红」= 假哑弹）
            RESULTS.append((f"{g.__name__} 崩溃", False, str(e)))
            print(f"  [FAIL] {g.__name__} 崩溃（变异可能破坏了语法/加载）: {e}")
    n_ok = sum(1 for _, ok, _ in RESULTS if ok)
    n_fail = len(RESULTS) - n_ok
    # run_all 契约：输出须含 PASS=<n> 与 FAIL=<n>（无统计按 EMPTY 判失败）
    print(f"\n汇总：{n_ok} OK / {n_fail} FAIL\nPASS={n_ok} FAIL={n_fail}")
    if n_fail:
        for name, ok, d in RESULTS:
            if not ok:
                print(f"  红：{name} {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
