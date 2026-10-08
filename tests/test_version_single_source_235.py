# -*- coding: utf-8 -*-
"""v4.235.0：版本单一真源（模块级 VERSION 不得漂移）。

背景：config.APP_VERSION 是版本号唯一真源，但另有 8 个模块各自持有
`VERSION = "..."` 常量。release_check 的 ④ 号门禁只核 config / README / exe
三方，管不到模块级常量 → 长期漂移无人发现：
    agent_task_mixin.py / intent.py / task_state.py 停在 v4.225.0（落后 9 版）
    intent_guard.py 停在 v4.168.0（落后 66 版）

修法（judge-first，先红后绿）：
  ① 把全部模块级 VERSION 对齐到真源；
  ② release_check 新增纯函数 `_module_version_drift(appv)` 并在 check_version 内
     接线，让下次 bump 忘改模块时被门禁拦下（规则交给基础设施执行，不靠记忆）。

判据：
  V1 config.APP_VERSION 可读（真源存在）
  V2 行为级：调用 _module_version_drift(APP_VERSION) 返回空（修复前有 4 处 → 红）
  V3 反向用例（防空谓词）：喂假版本号必须报出漂移 —— 证明函数不是恒返回空
  V4 接线：check_version 内确实调用了 _module_version_drift
  V5 守卫正则锚定行首 VERSION，不把 config.APP_VERSION 误当模块级常量

为什么 V3 必须存在（P9/P19 教训）：只断言「返回空」是恒真的 —— 一个永远
return [] 的函数也能让 V2 绿。必须有反向用例钉住「扫得到」。
"""
import os
import re
import sys

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


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def main():
    print("-- v4.235.0：版本单一真源（模块级 VERSION 漂移守卫） --")

    # ---- V1 真源可读 ----
    appv = None
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', _read(os.path.join(ROOT, "config.py")))
    appv = m.group(1) if m else None
    check("V1 config.APP_VERSION 可读（版本唯一真源）", bool(appv), "got=%r" % appv)

    # ---- V2 / V3 行为级 ----
    fn = None
    try:
        from release_check import _module_version_drift
        fn = _module_version_drift
    except Exception as e:
        check("V2 可导入 release_check._module_version_drift", False, repr(e))

    if fn is not None and appv:
        drift = fn(appv)
        check("V2 行为级：模块级 VERSION 全部等于 APP_VERSION（无漂移）",
              not drift, "漂移=%s" % (drift[:6],))

        # V3 反向用例：喂一个不可能相同的假版本号，必须报出漂移
        rev = fn("v0.0.1-fake")
        check("V3 反向用例：喂假版本号必须报出漂移（防函数恒返回空）",
              len(rev) >= 8, "got=%d 处（期望 >=8）" % len(rev))
    else:
        check("V2 行为级：模块级 VERSION 全部等于 APP_VERSION（无漂移）",
              False, "前置失败（函数不可导入或真源不可读）")
        check("V3 反向用例：喂假版本号必须报出漂移（防函数恒返回空）",
              False, "前置失败")

    # ---- V4 / V5 接线与正则 ----
    # ⚠️ V4 必须用 AST 判定「check_version 函数体内真有调用」，不能用
    # 全文字符串匹配：字符串「模块级 VERSION」在 _module_version_drift 的
    # docstring 里同样存在（P6 类坑：拿 docstring 当证据 → 判据恒真，
    # 删掉接线后依然绿，扰动直接哑弹）。AST 的 Call 节点天然排除 docstring。
    rc = _read(os.path.join(ROOT, "release_check.py"))
    called = False
    try:
        import ast
        tree = ast.parse(rc)
        fn_cv = next((n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "check_version"),
                     None)
        if fn_cv is not None:
            for n in ast.walk(fn_cv):
                if isinstance(n, ast.Call):
                    f = n.func
                    if isinstance(f, ast.Name) and f.id == "_module_version_drift":
                        called = True
    except SyntaxError as e:
        check("V4 release_check.py 可解析（AST）", False, repr(e))
    check("V4 接线：check_version 内真调用 _module_version_drift（AST）", called)
    check("V5 守卫正则锚定行首 VERSION（不误吃 APP_VERSION）",
          "^VERSION" in rc)

    print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
