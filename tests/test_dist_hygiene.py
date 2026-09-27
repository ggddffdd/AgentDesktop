"""v4.175.0 回归：dist 顶层运行数据目录的清扫与清单。

背景：从 dist 直接启动 exe 时 APP_DIR = dist/小臭玩AI，个别运行期组件会在**程序目录**
里建出 log/ 这类目录（2026-09-28 实测出现过一次，空目录，冷启动 80 秒不复现）。
它会被发布门禁的「dist 顶层无运行数据」判红，但门禁只报个目录名、看不出原因。

取舍：**不放宽门禁**（它判得对，放宽就会真漏数据），改在**打包流程**里清扫。
底线：只扫**空的**，有内容的一律保留（dist 里混着用户的文档/技能，删不得）。

本套件不 import build_safe（它模块级会打补丁、有副作用），改用 AST 取出函数单独执行。
"""
import ast
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_p = _f = 0


def check(label, got, exp=True, extra=""):
    global _p, _f
    ok = (got == exp)
    _p += ok
    _f += (not ok)
    print(f"  {'✓' if ok else '✗'} {label:<56} got={got!s:<6} exp={exp}  {extra}")


BUILD_SRC = open(os.path.join(ROOT, "build_safe.py"), encoding="utf-8-sig").read()
RC_SRC = open(os.path.join(ROOT, "release_check.py"), encoding="utf-8-sig").read()


def _load_sweep(src=None):
    """从源码里取出 _sweep_empty_runtime_dirs 单独执行（避免 import 副作用）。"""
    src = src or BUILD_SRC
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_sweep_empty_runtime_dirs")
    rts = next(n for n in tree.body
               if isinstance(n, ast.Assign)
               and getattr(n.targets[0], "id", "") == "RUNTIME_DIRS")
    # 注入 shutil：负面验证会造一个 rmtree 版本；缺了它只会抛 NameError 被 except 吞掉，
    # 结果"看起来很安全"，其实什么都没验到。
    import shutil
    ns = {"os": os, "shutil": shutil, "print": lambda *a, **k: None}
    exec(compile(ast.Module(body=[rts, fn], type_ignores=[]), "<build_safe>", "exec"), ns)
    return ns["_sweep_empty_runtime_dirs"], tuple(ns["RUNTIME_DIRS"])


def part_a_lists_same_source():
    print("\n-- A) 漂移守卫：两份清单（打包清扫 / 发布门禁）必须一致 --")
    _, rts = _load_sweep()
    rc_dirs = None
    for n in ast.walk(ast.parse(RC_SRC)):
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "DIST_RUNTIME_DIRS":
            rc_dirs = ast.literal_eval(n.value)
            break
    check("A1 能解析出 release_check 的清单", bool(rc_dirs), True)
    # 门禁清单里含文件（debug.log 等）；本处只比对**目录项**（无扩展名的）
    rc_dir_only = sorted(x for x in (rc_dirs or []) if "." not in x)
    check("A2 目录项与 build_safe 完全一致（改一处忘另一处 = 漏检）",
          sorted(rts), rc_dir_only)
    check("A3 清单非空（解析不到会让守卫变瞎）", len(rts) > 5, True)


def part_b_sweep_behavior():
    print("\n-- B) 清扫行为：只清空的，非空必须保留 --")
    sweep, rts = _load_sweep()
    tmp = tempfile.mkdtemp(prefix="xc_dist_")
    # 空的 log → 应被清
    empty = os.path.join(tmp, "log")
    os.makedirs(empty, exist_ok=True)
    # 非空 incoming → 必须保留
    full = os.path.join(tmp, "incoming")
    os.makedirs(full, exist_ok=True)
    with open(os.path.join(full, "a.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    # 没出现过的目录 → 不该报错
    swept, kept = sweep(tmp)
    check("B1 空目录被清扫", os.path.isdir(empty), False)
    check("B2 非空目录被保留", os.path.isdir(full), True)
    check("B3 保留下来的数据没被删", os.path.isfile(os.path.join(full, "a.png")), True)
    check("B4 returned swept 含 log", "log" in swept, True)
    check("B5 returned kept 含 incoming", "incoming" in kept, True)
    check("B6 目录不存在时不报错（temp 不在清单外也不炸）",
          os.path.isdir(os.path.join(tmp, "temp")), False)
    check("B7 清单里含 log（事故那个）", "log" in rts, True)


def part_c_source_contract():
    print("\n-- C) 源码契约：安全底线写在明处 --")
    check("C1 清扫前先判空（非空保留）", "if any(os.scandir(p)):" in BUILD_SRC)
    check("C2 非空时显式喊出来（不是静默跳过）", "非空，保留不动" in BUILD_SRC)
    check("C3 用 os.rmdir 而不是 rmtree（空目录才删得掉）",
          "os.rmdir(p)" in BUILD_SRC and "rmtree" not in BUILD_SRC.split(
              "def _sweep_empty_runtime_dirs")[1].split("def ")[0])
    check("C4 打包流程里确实调用了清扫", "_sweep_empty_runtime_dirs(live_dist)" in BUILD_SRC)
    check("C5 门禁那条检查没有被放宽（仍是 check 而非 warn）",
          'check("dist 顶层无运行数据"' in RC_SRC)


def part_d_negative():
    print("\n-- D) 负面验证：拆掉「非空保留」这道底线 → 真实数据会被删 --")
    # 造一个没有空目录判断的版本
    broken = BUILD_SRC.replace(
        """            if any(os.scandir(p)):
                kept.append(name)
                print('[build_safe] ⚠️ dist 顶层 %s/ 非空，保留不动（可能含真实数据）' % name)
                continue
""", "")
    check("D1 锚点命中（底线可拆）", broken != BUILD_SRC)
    if broken != BUILD_SRC:
        sweep_bad, _ = _load_sweep(broken)
        tmp = tempfile.mkdtemp(prefix="xc_dist_bad_")
        full = os.path.join(tmp, "incoming")
        os.makedirs(full, exist_ok=True)
        with open(os.path.join(full, "用户附件.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        sweep_bad(tmp)
        # 拆掉空目录判断后，os.rmdir 依然删不掉非空目录 —— 这是**第二道保险**：
        # 两道都在，任何一道单独失效都不足以误删用户数据。
        check("D2 拆掉空目录判断后，os.rmdir 仍删不掉非空目录（第二道保险）",
              os.path.isdir(full), True)
        check("D2b 里面的文件也还在", os.path.isfile(os.path.join(full, "用户附件.png")), True)
        # 反过来证明为什么必须用 rmdir：换成 rmtree 的天真版会把真实数据删掉。
        # ⚠️ 必须在**已拆掉空目录判断**的版本上再换 —— 否则仍会被空目录判断跳过，
        # 看起来"很安全"，其实什么都没验到（这正是 D4 第一版假绿的原因）。
        naive = broken.replace("os.rmdir(p)", "shutil.rmtree(p)")
        check("D3 锚点命中（可换成 rmtree）", naive != BUILD_SRC)
        sweep_naive, _ = _load_sweep(naive)
        tmp3 = tempfile.mkdtemp(prefix="xc_dist_naive_")
        full3 = os.path.join(tmp3, "incoming")
        os.makedirs(full3, exist_ok=True)
        with open(os.path.join(full3, "用户附件.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        sweep_naive(tmp3)
        check("D4 换成 rmtree 后非空目录被整棵删掉（证明必须用 rmdir）",
              os.path.isdir(full3), False)
        # 对照：正常版本必须保住
        sweep_ok, _ = _load_sweep()
        tmp2 = tempfile.mkdtemp(prefix="xc_dist_ok_")
        full2 = os.path.join(tmp2, "incoming")
        os.makedirs(full2, exist_ok=True)
        with open(os.path.join(full2, "用户附件.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        sweep_ok(tmp2)
        check("D3 正常版本保住了同一份数据", os.path.isfile(
            os.path.join(full2, "用户附件.png")), True)


def main():
    part_a_lists_same_source()
    part_b_sweep_behavior()
    part_c_source_contract()
    part_d_negative()
    print(f"\n汇总：PASS={_p} FAIL={_f}")
    return 0 if _f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
