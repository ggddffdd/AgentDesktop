# v4.179.1：video-agent/core 的落点归口回归。
# 从仓库根目录以 `python tests/xxx.py` 运行时会补 sys.path（见下）。
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

"""离线单测：`video-agent/core/config.py` 的工作目录归口。

背景（2026-09-29 定位）：
    打包时 spec 把 `../video-agent/core` 装成 **`_internal/core/`** 包；
    而 `core/config.py` 的 `APP_DIR = dirname(dirname(__file__))` ——
      · 源码运行 = `video-agent/`（正常）
      · **打包后 = `_internal/`（分发目录）** ← 问题所在
    于是 `load_config()`（文件不存在就写默认）会把 config.json 写进**分发目录**，
    连带 `agnes_outputs/`（产物）也落在那儿。三个后果：
      ① 发布门禁判红（release_check 第 5 项）；
      ② 装到只读目录（Program Files）时**直接抛异常**；
      ③ 重打包时整个 `_internal` 被替换，写进去的东西白丢。

修复：`_app_dir()` 在 **frozen 时改道用户数据目录**（认 `XC_USER_DATA_DIR`），
      源码运行完全不变。

本套件全程沙箱化：不碰真实用户目录，也不依赖网络。
"""
import ast
import importlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_PASS = []
_FAIL = []


def check(name, cond, extra=""):
    if cond:
        _PASS.append(name)
        print(f"✅ {name} {extra}")
    else:
        _FAIL.append(name)
        print(f"❌ {name} {extra}")


# ---- 真实用户目录哨兵（测试绝不该碰它）--------------------------------------
_REAL_APPDIR = os.path.join(os.path.expanduser("~"), "Documents", "小臭玩AI",
                            "video-agent")


def _snap(p):
    try:
        return sorted(os.listdir(p))
    except OSError:
        return None      # 不存在


_REAL_BEFORE = _snap(_REAL_APPDIR)

try:
    import core_agnes                      # 它负责把 video-agent 挂进 sys.path
    import core.config as core_config
except Exception as e:
    # v4.239.1（CI 红 → 修）：core 包来自仓库外的 video-agent（本机相邻目录），
    # CI 干净 clone 上不存在。属环境依赖，缺失 → SKIP（与 frozen_smoke 同口径），
    # 不冒充通过、也不假红。
    print(f"  [SKIP] video-agent/core 不在（{e}）——外部依赖缺失，跳过不冒充通过")
    sys.exit(0)


def _app_dir_fn():
    tree = ast.parse(open(core_config.__file__, encoding="utf-8-sig").read())
    return next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "_app_dir")


def main():
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(core_config.__file__)))

    print("=" * 60)
    print("A) 源码运行：落点保持 video-agent/（历史行为不变）")
    print("=" * 60)
    check("A1 APP_DIR == video-agent 目录",
          os.path.abspath(core_config.APP_DIR) == src_dir, core_config.APP_DIR)
    check("A2 不在 _internal 下（源码运行本就不该在）",
          "_internal" not in core_config.APP_DIR.replace("\\", "/"))
    check("A3 CONFIG_PATH 仍在 video-agent 下",
          os.path.abspath(core_config.CONFIG_PATH)
          == os.path.join(src_dir, "config.json"), core_config.CONFIG_PATH)
    check("A4 MOV_DIR 仍在 video-agent 下",
          os.path.abspath(core_config.MOV_DIR).startswith(src_dir), core_config.MOV_DIR)

    print()
    print("=" * 60)
    print("B) ★frozen（打包后）：必须改道用户数据目录，不许落 _internal")
    print("=" * 60)
    tmp = tempfile.mkdtemp(prefix="xc_core_home_sbx_")
    old_env = os.environ.get("XC_USER_DATA_DIR")
    os.environ["XC_USER_DATA_DIR"] = tmp
    sys.frozen = True          # 模拟 PyInstaller 运行期
    try:
        importlib.reload(core_config)
        check("B1 APP_DIR 落在用户数据目录下",
              os.path.abspath(core_config.APP_DIR).startswith(os.path.abspath(tmp)),
              core_config.APP_DIR)
        check("B2 ★APP_DIR 不再是 _internal（分发目录）",
              "_internal" not in core_config.APP_DIR.replace("\\", "/"),
              core_config.APP_DIR)
        check("B3 CONFIG_PATH 跟着改道（config.json 不再写进分发目录）",
              os.path.abspath(core_config.CONFIG_PATH)
              == os.path.join(tmp, "video-agent", "config.json"),
              core_config.CONFIG_PATH)
        check("B4 MOV_DIR / OUT_DIR 也改道（产物不再写进分发目录）",
              os.path.abspath(core_config.MOV_DIR).startswith(os.path.abspath(tmp)),
              core_config.MOV_DIR)
        check("B5 目录已自动创建", os.path.isdir(core_config.APP_DIR))
    finally:
        del sys.frozen
        if old_env is None:
            os.environ.pop("XC_USER_DATA_DIR", None)
        else:
            os.environ["XC_USER_DATA_DIR"] = old_env
        importlib.reload(core_config)

    print()
    print("=" * 60)
    print("C) 契约：frozen 分支不得依赖 __file__（否则打包后又落回 _internal）")
    print("=" * 60)
    fn = _app_dir_fn()
    frozen_if = None
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and "frozen" in ast.unparse(node.test):
            frozen_if = node
            break
    check("C1 找到 frozen 分支", frozen_if is not None)
    uses_file = frozen_if is not None and any(
        isinstance(x, ast.Name) and x.id == "__file__" for x in ast.walk(frozen_if))
    check("C2 ★frozen 分支不引用 __file__（防打包后落回 _internal）", not uses_file)
    fsrc = ast.unparse(frozen_if) if frozen_if else ""
    check("C3 frozen 分支认 XC_USER_DATA_DIR（测试/沙箱可改道）",
          "XC_USER_DATA_DIR" in fsrc)
    check("C4 仍保留源码分支（非 frozen 时用 __file__ 推导）",
          any(isinstance(x, ast.Name) and x.id == "__file__" for x in ast.walk(fn)))

    print()
    print("=" * 60)
    print("D) 安全哨兵：测试没有碰真实用户目录")
    print("=" * 60)
    check("D1 真实 video-agent 目录未被创建/改动",
          _snap(_REAL_APPDIR) == _REAL_BEFORE)

    print()
    print("=" * 60)
    print(f"汇总：PASS={len(_PASS)} FAIL={len(_FAIL)}")
    if _FAIL:
        print("失败项：", _FAIL)
    else:
        print("ALL_VIDEO_CORE_HOME_OK")
    return 0 if not _FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
