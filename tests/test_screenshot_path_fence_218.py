# -*- coding: utf-8 -*-
"""v4.218.0 判据：screenshot.save_path 路径围栏（审查报告 P1-2）。

根因：screenshot 标 READ，但 _resolve_save_path 直接尊重任意 save_path，
可写任意绝对路径 / 建目录 / 静默覆盖文件。
修复：显式 save_path 必须落在允许根（PRODUCTS_DIR 树 / app_dir 树）内，
越界回落默认；已存在文件自动加时间戳后缀，禁止静默覆盖。

契约：
  ① 越界绝对路径被拒、回落默认产物目录
  ② 已存在文件不被静默覆盖（返回带时间戳后缀的新路径）
  ③ 落允许根内的路径原样返回

零副作用：用临时目录做允许根，只在该临时目录内活动，不触碰真实文件系统。
用法：python tests/test_screenshot_path_fence_218.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print(f"  [PASS] {name}")
    else:
        _f += 1
        print(f"  [FAIL] {name}  {detail}")


import system_control_tools as sct  # noqa: E402
import config as _cfg  # noqa: E402

PRODUCTS_DIR_PREV = getattr(_cfg, "PRODUCTS_DIR", None)


def test_fence():
    allowed = tempfile.mkdtemp(prefix="shotfence_")
    _cfg.PRODUCTS_DIR = allowed
    app_dir = allowed
    existing = os.path.join(allowed, "shot.png")
    try:
        # ① 越界绝对路径
        out = sct._resolve_save_path(r"C:/Windows/System32/evil.png", app_dir=app_dir)
        norm_out = os.path.normcase(os.path.abspath(out))
        norm_allowed = os.path.normcase(os.path.abspath(allowed))
        check("越界绝对路径被拒（回落默认产物目录）",
              not norm_out.startswith(os.path.normcase(r"C:/Windows")),
              "out=%r" % out)
        check("回落路径仍在允许根内",
              norm_out == norm_allowed or norm_out.startswith(norm_allowed + os.sep),
              "out=%r allowed=%r" % (out, allowed))

        # ② 已存在文件 → 不覆盖
        open(existing, "w").close()
        out2 = sct._resolve_save_path(existing, app_dir=app_dir)
        check("已存在文件不被静默覆盖",
              os.path.normcase(os.path.abspath(out2)) != os.path.normcase(os.path.abspath(existing)),
              "out2=%r existing=%r" % (out2, existing))
        try:
            if os.path.exists(out2):
                os.remove(out2)
        except OSError:
            pass

        # ③ 落允许根内（不存在）原样返回（用 normcase 消除盘符大小写差异）
        inside = os.path.join(allowed, "sub", "a.png")
        out3 = sct._resolve_save_path(inside, app_dir=app_dir)
        check("落允许根内的路径原样返回",
              os.path.normcase(os.path.abspath(out3)) == os.path.normcase(os.path.abspath(inside)),
              "out3=%r inside=%r" % (out3, inside))
    finally:
        if PRODUCTS_DIR_PREV is None:
            try:
                del _cfg.PRODUCTS_DIR
            except Exception:
                pass
        else:
            _cfg.PRODUCTS_DIR = PRODUCTS_DIR_PREV
        for fp in (existing,):
            try:
                os.remove(fp)
            except OSError:
                pass
        try:
            os.rmdir(allowed)
        except OSError:
            pass


if __name__ == "__main__":
    test_fence()
    print(f"\nPASS={_p} FAIL={_f}")
    sys.exit(1 if _f else 0)
