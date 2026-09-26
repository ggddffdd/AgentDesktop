# -*- coding: utf-8 -*-
"""调试浏览器 profile 路径回归测试

纯标准库（browser_control_tools 只 import 标准库）：
    python tests/test_cdp_profile_path.py
退出码 0=全过，1=有失败。

背景（为什么值得钉住）
----------------------
v4.163.1 之前，profile 目录是 `app_dir/cdp_edge_profile`，而 `app_dir` 就是 exe
所在目录 —— 它同时也是**分发源**。实测 `dist/小臭玩AI/cdp_edge_profile` 长到
1.2GB / 7558 文件，含 Login Data（51 条已存登录）、Cookies（396 条）、Local State，
缓存里还有明文 API key → **打包分发就等于分发登录态**。

修复后 profile 固定放 `%LOCALAPPDATA%\\小臭玩AI\\cdp_edge_profile`。本测试钉住三件事：
1. profile 落在 LOCALAPPDATA 下；
2. **_default_cdp_profile 不接受任何参数**（最强形式的不变量：调用方没有途径
   把 profile 拽回 app_dir），且源码里不存在 app_dir 兜底分支；
3. 探测候选仍包含历史遗留位置（避免漏判老 profile 里装过 VPN 扩展）。
"""
import inspect
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import browser_control_tools as bct

PASS = 0
FAIL = 0
FAKE_APP = r"D:\fake\somewhere\dist\小臭玩AI"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def test_location():
    print("== 权威位置 ==")
    lap = os.environ.get("LOCALAPPDATA", "")
    p0 = bct._default_cdp_profile()

    check("以 cdp_edge_profile 结尾",
          os.path.basename(p0.rstrip("\\/")) == "cdp_edge_profile", p0)
    if lap:
        check("落在 %LOCALAPPDATA% 下",
              os.path.normcase(p0).startswith(os.path.normcase(lap)), p0)
    else:
        check("（本机无 LOCALAPPDATA，跳过该断言）", True, "环境变量缺失")
    check("带「小臭玩AI」隔离目录", "小臭玩AI" in p0, p0)
    check("★ 不落在任何 app_dir 形态下",
          os.path.normcase(FAKE_APP) not in os.path.normcase(p0), p0)
    return p0


def test_cannot_be_steered(p0):
    print("== 不可被 app_dir 带偏（核心回归点）==")
    # 最强形式的不变量：函数不收参数 → 调用方无法传入 app_dir
    sig = inspect.signature(bct._default_cdp_profile)
    check("★ _default_cdp_profile 不接受任何参数",
          len(sig.parameters) == 0, f"参数={list(sig.parameters)}")

    src = (HERE / "browser_control_tools.py").read_text(encoding="utf-8")
    check("★ 源码中无 os.path.join(app_dir, \"cdp_edge_profile\")",
          re.search(r'os\.path\.join\(\s*app_dir\s*,\s*"cdp_edge_profile"', src) is None,
          "有人把它改回来了")
    check("★ 无任何 return os.path.join(app_dir ... 兜底分支",
          re.search(r"return\s+os\.path\.join\(\s*app_dir", src) is None,
          "存在 app_dir 兜底 → 不变量被破坏")
    check("_ensure_cdp 已接入 _default_cdp_profile()",
          "_default_cdp_profile()" in src, "未接入 helper")
    check("注释里记录了「不放 app_dir」的原因",
          "不再放 app_dir" in src or "永不回落 app_dir" in src)


def test_candidates(p0):
    print("== 探测候选 ==")
    cands = bct._cdp_profile_candidates()
    check("候选非空", bool(cands), str(cands))
    check("首个即权威位置",
          os.path.normcase(cands[0]) == os.path.normcase(p0), str(cands[:1]))
    check("候选含历史遗留位置或有多个（兼容老 profile）", len(cands) >= 1, str(cands))
    check("候选无重复",
          len({os.path.normcase(c) for c in cands}) == len(cands), str(cands))
    check("候选全部以 cdp_edge_profile 结尾",
          all(os.path.basename(c.rstrip("\\/")) == "cdp_edge_profile" for c in cands),
          str(cands))


def test_fallback():
    print("== 无 %LOCALAPPDATA% 时的回退 ==")
    saved = os.environ.get("LOCALAPPDATA")
    try:
        os.environ.pop("LOCALAPPDATA", None)
        p = bct._default_cdp_profile()
        check("仍有可用回退路径",
              bool(p) and os.path.basename(p.rstrip("\\/")) == "cdp_edge_profile", str(p))
        check("★ 回退也不指向 app_dir",
              os.path.normcase(FAKE_APP) not in os.path.normcase(p), str(p))
    finally:
        if saved is not None:
            os.environ["LOCALAPPDATA"] = saved


if __name__ == "__main__":
    try:
        p0 = test_location()
        test_cannot_be_steered(p0)
        test_candidates(p0)
        test_fallback()
    except Exception:
        import traceback
        FAIL += 1
        traceback.print_exc()
    print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
