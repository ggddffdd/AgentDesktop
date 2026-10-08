# -*- coding: utf-8 -*-
"""v4.236.0 判据：运行数据不得落 APP_DIR（= exe 目录 = dist = 分发源）。

事故：v4.164.0 已把「用户数据类」运行时目录统一归口到 WORKSPACE_DIR，
但 ui_widgets 的粘贴图片落点 `Path(APP_DIR) / "temp" / "images"` 是当时漏改的
残留。实测后果：dist/小臭玩AI/temp/images/ 下攒着一张 195KB 粘贴截图
（paste_20261006_125019_370661.png），而 **dist 正是分发源** —— 打包卫生门禁
常年报红，且这类残留天然带着用户的真实数据。

只改那一处不够：同类写法随时会在别的地方长回来（v4.164.0 就是漏了一处）。
所以本判据三道：

  A) 行为：粘贴图片的真实落点 ensure_workspace("temp","images") 必须在 APP_DIR 之外；
  B) 静态：活跃源码里不得再出现「APP_DIR + 运行数据目录名」的组合（防别处再犯）；
  C) 反向：喂一段真往 APP_DIR 写 temp 的样本，B 必须判红（证明判据非恒真）；
     再喂一段改用 ensure_workspace 的样本，必须不判红（防误伤）。

运行数据目录清单以 release_check.DIST_RUNTIME_DIRS 为唯一真源，不复制一份，
避免两处清单日后漂移。

注意：静态扫描必须先剔除注释 —— config.py 里「默认 {APP_DIR}/rag_data」这类说明
会命中清单里的 rag_data，但那只是历史注释，不是写盘代码（P11：剔除注释只做一次，
按列挖空不整行删，否则行号会漂）。
"""
import io
import os
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import APP_DIR, ensure_workspace  # noqa: E402
from release_check import DIST_RUNTIME_DIRS  # noqa: E402

_N_PASS = 0
_N_FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global _N_PASS, _N_FAIL
    if cond:
        _N_PASS += 1
        print(f"  [PASS] {name}")
    else:
        _N_FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name}  {detail}")


def _strip_comments(src: str) -> str:
    """按 tokenize 的 COMMENT 位置把注释挖成空格：列位不变，行号不漂。"""
    lines = src.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                continue
            sl, sc = tok.start
            el, ec = tok.end
            if 1 <= sl <= len(lines):
                ln = lines[sl - 1]
                lines[sl - 1] = ln[:sc] + " " * max(0, ec - sc) + ln[ec:]
    except Exception:
        pass
    return "".join(lines)


# 历史备份 / 快照 / 归档目录不参与扫描（它们是被冻结的旧代码，改它们没有意义）
_SKIP_DIRS = {"__pycache__", ".git", "dist", "build", "node_modules",
              "_dev_history", "tests"}
_SKIP_DIR_PREFIX = ("backup_", "_bak", "_rw_dry", "_cur", "_ui_")
# release_check / build_safe 只是**定义**这份清单本身，不写盘
_SKIP_FILES = {"release_check.py", "build_safe.py"}


def _active_files():
    for dirpath, dirnames, filenames in os.walk(str(ROOT)):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS
                       and not d.startswith(_SKIP_DIR_PREFIX)]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            if fn.startswith("_"):      # 根目录 _*.py 是开发期诊断/核验脚本，不入包
                continue
            if fn in _SKIP_FILES:
                continue
            yield os.path.join(dirpath, fn)


def _scan_one(path, src):
    """扫描单份源码，返回 [(文件, 行号, 行内容, 命中的目录名)]。"""
    hits = []
    for i, line in enumerate(_strip_comments(src).splitlines(), 1):
        if "APP_DIR" not in line:
            continue
        for name in DIST_RUNTIME_DIRS:
            if name in line:
                hits.append((path, i, line.strip(), name))
                break
    return hits


def scan_app_dir_runtime():
    hits = []
    for path in _active_files():
        try:
            src = Path(path).read_text(encoding="utf-8-sig", errors="ignore")
        except Exception:
            continue
        hits.extend(_scan_one(path, src))
    return hits


def main():
    print("v4.236.0 运行数据落点判据（不得落 APP_DIR = dist = 分发源）")

    # ---------- A) 行为 ----------
    print("\n-- A) 行为：粘贴图片落点在工作区，不在 APP_DIR 内 --")
    land = ensure_workspace("temp", "images")
    land_abs = os.path.abspath(land)
    app_abs = os.path.abspath(APP_DIR)
    in_app = land_abs.startswith(app_abs + os.sep) or land_abs == app_abs
    check("★ 粘贴图片落点不在 APP_DIR 内",
          not in_app, f"落点={land_abs} APP_DIR={app_abs}")
    check("★ 粘贴图片落点仍是 temp/images（改名会导致别处取不到图）",
          land_abs.replace("\\", "/").endswith("/temp/images"),
          f"落点={land_abs}")
    check("★ 落点目录已真实创建（不是只拼了个字符串）",
          os.path.isdir(land_abs), f"落点={land_abs}")
    check("★ 工作区落点与 APP_DIR 不是同一个根（归口规则成立）",
          land_abs != app_abs and not app_abs.startswith(land_abs + os.sep),
          f"落点={land_abs} APP_DIR={app_abs}")

    # ---------- B) 静态：别处不得再犯 ----------
    print("\n-- B) 静态：活跃源码不得再有「APP_DIR + 运行数据目录」组合 --")
    hits = scan_app_dir_runtime()
    check("★ 活跃源码无 APP_DIR 下写运行数据的写法",
          not hits,
          "；".join(f"{Path(p).name}:{ln} [{n}]" for p, ln, _s, n in hits[:6]))
    ui_w = ROOT / "ui_widgets.py"
    ui_src = ui_w.read_text(encoding="utf-8-sig", errors="ignore")
    check("★ ui_widgets.py 代码里已不含 APP_DIR（注释除外）",
          "APP_DIR" not in _strip_comments(ui_src),
          "残留 → 粘贴图片仍可能落回 dist")
    check("★ ui_widgets.py 真的改用 ensure_workspace（不是只删了引用）",
          "ensure_workspace" in ui_src, "没接上归口 helper")

    # ---------- C) 反向用例：证明 B 非恒真 ----------
    print("\n-- C) 反向：判据必须能抓出真违规，且不误伤合规写法 --")
    bad = 'tmp_dir = Path(APP_DIR) / "temp" / "images"\n'
    bad_hits = _scan_one("<bad>", bad)
    check("★ 反向：真往 APP_DIR 写 temp 的样本必须被判红（判据非空谓词）",
          len(bad_hits) == 1, f"命中={len(bad_hits)} → 判据恒真，等于没钉")
    good = 'tmp_dir = Path(ensure_workspace("temp", "images"))\n'
    check("★ 反向对照：改用 ensure_workspace 的样本不得被判红（不误伤）",
          len(_scan_one("<good>", good)) == 0, "误伤 → 判据过宽")
    note = '# 默认 {APP_DIR}/rag_data 只是注释，不该被判红\n'
    check("★ 反向对照：注释里提到 APP_DIR 不判红（注释已剔除）",
          len(_scan_one("<note>", note)) == 0, "注释没剔干净 → 会误报")

    print("\n" + "=" * 62)
    print(f"汇总：PASS={_N_PASS} FAIL={_N_FAIL}")
    if FAILED:
        for f in FAILED:
            print(f"  × {f}")
    return 1 if _N_FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
