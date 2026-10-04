# -*- coding: utf-8 -*-
"""画布 · 统一资产校验 + 导入加固 + 语法兼容 判据套件（纯逻辑，无 Qt 窗口）。

覆盖 2026-10-04 复审轮的四类改动：
  A 组  Python 3.12-only f-string（PEP 701）：检测器自证 + 生产源码零残留
  B 组  统一资产有效性入口 `assess_asset` 的四态判定，以及「自定义执行器返回
        非法路径 → 登记但标 invalid + 节点诚实 failed」
  C 组  工程文件导入校验加严（status / pos / local_edits / 边重复 /
        单入端口多连 / placeholder / version）
  D 组  占位文件改名（.node-placeholder + meta.content_type）
  E 组  多入端口下执行器收到的确实是 list

环境约定：
  * 默认 import canvas_graph / canvas_export / executors；若设 `CG_PATH` 则从该
    路径加载 canvas_graph（供扰动脚本用，与 test_canvas_model 的写法一致）。
  * 若设 `AV_CHECK=<name>`，只按该判据的成败返回 exit code（供扰动脚本断言翻转）。
"""

import os
import sys
import json
import subprocess
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib.util                                      # noqa: E402

# ---- 扰动注入点：CG_PATH / CX_PATH / EX_PATH 指向变异副本 ----
# canvas_graph 必须**先占位 sys.modules**，再 import canvas_export：
# 否则 canvas_export 里的 `import canvas_graph as cg` 拿到的还是真文件（假绿）。
_cg_path = os.environ.get("CG_PATH")
if _cg_path and os.path.exists(_cg_path):
    _s = importlib.util.spec_from_file_location("canvas_graph", _cg_path)
    _m = importlib.util.module_from_spec(_s)
    sys.modules["canvas_graph"] = _m
    _s.loader.exec_module(_m)

_cx_path = os.environ.get("CX_PATH")
if _cx_path and os.path.exists(_cx_path):
    _s2 = importlib.util.spec_from_file_location("canvas_export_under_test", _cx_path)
    cx = importlib.util.module_from_spec(_s2)
    _s2.loader.exec_module(cx)
else:
    import canvas_export as cx

import canvas_graph as cg                                  # noqa: E402

_ex_path = os.environ.get("EX_PATH")
if _ex_path and os.path.exists(_ex_path):
    _s3 = importlib.util.spec_from_file_location("executors_under_test", _ex_path)
    _exmod = importlib.util.module_from_spec(_s3)
    _s3.loader.exec_module(_exmod)
else:
    import executors as _exmod

_pl_path = os.environ.get("PL_PATH")
if _pl_path and os.path.exists(_pl_path):
    _s4 = importlib.util.spec_from_file_location("pep701_lint_under_test", _pl_path)
    _m4 = importlib.util.module_from_spec(_s4)
    _s4.loader.exec_module(_m4)
    find_pep701 = _m4.find_pep701
else:
    from pep701_lint import find_pep701                    # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond)))
    ok = "OK " if cond else "FAIL"
    _d = f" :: {detail}" if (detail and not cond) else ""
    print(f"  [{ok}] {name}{_d}")


def _mkasset(root, name, data=b"x"):
    p = os.path.join(root, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def main():
    tmp = tempfile.mkdtemp(prefix="test_canvas_assetvalid_")
    root = os.path.join(tmp, "assets")
    os.makedirs(root, exist_ok=True)

    # ================= A 组：PEP 701 检测器自证 =================
    print("\n[A] PEP 701（Python 3.12-only f-string）")
    # 自证优先：检测器若抓不到，下面那条「源码零残留」就是空判据（永远绿）
    _same = "x = f'a {d['k']}'\n"
    check("A1 检测器抓到「同引号嵌套」（外单引号嵌内单引号）",
          len(find_pep701(_same)) == 1, str(find_pep701(_same)))
    _other = 'x = f"a {d[\'k\']}"\n'
    check("A2 检测器不误报「异引号嵌套」（外双引号嵌内单引号）",
          find_pep701(_other) == [])
    _mix = 'y = f"b {THEME["x"]}"\n'
    check("A3 检测器抓到「外双引号嵌内双引号」",
          len(find_pep701(_mix)) == 1)
    _bs = 'z = f"{chr(92)}n".join(a)\n'
    check("A4 合法转义不误报（检测器不靠「有反斜杠就报」这种粗判）",
          find_pep701(_bs) == [], str(find_pep701(_bs)))

    # 扫描根可用 PEP701_ROOT 替换 —— 扰动脚本据此造一份「带违规写法的假源码」，
    # 不必去改真源文件（改真文件既危险又会污染其它套件的读取）。
    _scan_root = os.environ.get("PEP701_ROOT") or _ROOT
    _prod = {}
    for fn in sorted(os.listdir(_scan_root)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        with open(os.path.join(_scan_root, fn), "rb") as f:
            hits = find_pep701(f.read().decode("utf-8-sig"))
        if hits:
            _prod[fn] = hits
    check("A5 生产源码零 PEP 701（README 声明 3.10+，3.11 及以前会 SyntaxError）",
          not _prod, str(_prod))

    # ---- A6~A8：外部解释器兼容体检 ----
    # 与 A1~A5 的分工：A5 用自研词法检测器扫**已知形态**（同类引号嵌套 / 表达式内
    # 反斜杠），这里用真 3.10 / 3.11 解释器 `compile()` 全库兜底 —— 未知写法只有真
    # 解释器认得出来。两者互补，都不能省（`ast.parse(feature_version=...)` 抓不到
    # PEP 701，已实测，所以别拿它当替代）。
    #
    # 解释器来自临时下载的官方 embeddable 包（不在仓库里、别的机器上也没有），
    # 故**缺失即 SKIP、不算失败**；存在则必须零语法错。
    _CHECK_SRC = (
        "import sys, pathlib\n"
        "root = pathlib.Path(sys.argv[1])\n"
        "names = pathlib.Path(sys.argv[2]).read_text(encoding='utf-8').split('\\n')\n"
        "bad = []\n"
        "for n in names:\n"
        "    n = n.strip()\n"
        "    if not n:\n"
        "        continue\n"
        "    p = root / n\n"
        "    try:\n"
        "        compile(p.read_bytes(), str(p), 'exec')\n"
        "    except SyntaxError as e:\n"
        "        bad.append('%s:%s %s' % (n, e.lineno, e.msg))\n"
        "    except OSError as e:\n"
        "        bad.append('%s: 读不到 (%s)' % (n, e))\n"
        "print('BAD=%d' % len(bad))\n"
        "for b in bad[:10]:\n"
        "    print('  ' + b)\n"
    )

    # 扫描范围＝**随仓库交付的 .py**（git 跟踪），不是「目录里所有 .py」。
    # 顶层那些 `_cur_*` / `_bak_*` / `_ui_199_keep.py` 是上一轮的**未跟踪备份副本**
    # （`git ls-files` 为空、打包 spec 也不收编），里面残留旧写法是预期内的 ——
    # 2026-10-04 已核实并报告过。把它们算进来只会造一条永远红的判据。
    def _tracked_py():
        # ★ 2026-10-04 补盲区：只认 `git ls-files` 会让**本轮新写的文件**整轮隐身 ——
        # 发布列车的顺序是「打包 → 全量回归 → commit」，回归跑在 `git add` 之前，
        # 新文件当时还是未跟踪状态。v4.211.3 的 tests/test_system_control_b.py
        # 就是这样带着 3.10/3.11 语法错漏过 A6（下一轮才被暴露）。
        # 扩展为：已跟踪 ∪ 未跟踪新增（仍排除 `_` 开头的未跟踪副本：
        # _cur_*/_bak_* 等旧写法备份不是交付物，算进来只会造一条永远红的判据）。
        out = []
        try:
            _r = subprocess.run(["git", "ls-files", "*.py"], cwd=_ROOT,
                                capture_output=True, text=True)
            if _r.returncode == 0 and _r.stdout.strip():
                out = [p for p in _r.stdout.split() if p.endswith(".py")]
        except Exception:  # noqa: BLE001 - 无 git 时退回前缀约定
            pass
        try:
            _r2 = subprocess.run(["git", "ls-files", "--others", "--exclude-standard",
                                  "*.py"], cwd=_ROOT, capture_output=True, text=True)
            if _r2.returncode == 0:
                for _p in _r2.stdout.split():
                    if _p.endswith(".py") and not os.path.basename(_p).startswith("_"):
                        out.append(_p)
        except Exception:  # noqa: BLE001
            pass
        if out:
            return out
        return sorted(n for n in os.listdir(_ROOT)
                      if n.endswith(".py") and not n.startswith("_"))

    _py_list = _tracked_py()
    # 注入点（供扰动脚本自证「改坏必红」，与 A5 的 PEP701_ROOT 同一思路）：
    #   EXT_CHECK_ROOT → 换扫描根（造一份含违规写法的假源码树）
    #   EXT_CHECK_LIST → 换清单文件（只扫那份假源码）
    _ext_root = os.environ.get("EXT_CHECK_ROOT") or _ROOT
    _env_list = os.environ.get("EXT_CHECK_LIST")
    if _env_list and os.path.exists(_env_list):
        with open(_env_list, encoding="utf-8") as _f:
            _py_list = [x.strip() for x in _f if x.strip()]
    _list_file = os.path.join(tmp, "pyfiles_for_ext_check.txt")
    with open(_list_file, "w", encoding="utf-8") as _f:
        _f.write("\n".join(_py_list))

    _ext_py = [
        ("3.10", os.environ.get("PY310_EXE")
         or os.path.join(tempfile.gettempdir(), "py310", "python.exe")),
        ("3.11", os.environ.get("PY311_EXE")
         or os.path.join(tempfile.gettempdir(), "py311", "python.exe")),
    ]
    for _ver, _exe in _ext_py:
        if not os.path.exists(_exe):
            print("  [SKIP] 外部 Python %s 不在（%s）—— 缺失即跳过，不算失败"
                  % (_ver, _exe))
            continue
        _r = subprocess.run([_exe, "-c", _CHECK_SRC, _ext_root, _list_file],
                            capture_output=True, text=True)
        _out = (_r.stdout or "") + (_r.stderr or "")
        check("A6 %s 全库 compile() 零语法错（真解释器兜底，%d 个交付文件）"
              % (_ver, len(_py_list)),
              _r.returncode == 0 and "BAD=0" in (_r.stdout or ""),
              _out.strip()[:200])

    # A8：**真导入** canvas_panel（本机解释器）。语法错、以及导入期的名称错误
    # （`from x import nope`、模块顶层 AttributeError）只有真导入才暴露 ——
    # 纯静态扫描看不见这些。
    _r_panel = subprocess.run(
        [sys.executable, "-c", "import canvas_panel; print('PANEL_OK')"],
        cwd=_ROOT, capture_output=True, text=True,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    _plines = (_r_panel.stderr or _r_panel.stdout or "").strip().splitlines()
    check("A8 本机 Python %s 真导入 canvas_panel（offscreen 冒烟）"
          % ".".join(str(v) for v in sys.version_info[:2]),
          _r_panel.returncode == 0 and "PANEL_OK" in (_r_panel.stdout or ""),
          (_plines[-1] if _plines else "")[:200])

    # ================= B 组：统一资产有效性四态 =================
    print("\n[B] 统一资产有效性入口 assess_asset")
    _real = _mkasset(root, "ok.png", b"\x89PNG")
    _missing = os.path.join(root, "nope.png")
    _empty = _mkasset(root, "empty.png", b"")
    _outside = _mkasset(tmp, "outside.png")

    check("B1 真文件 + 在 asset_root 内 → real",
          cg.assess_asset(cg.AssetRef(kind="image", name="a", path=_real),
                          root)[0] == cg.ASSET_REAL)
    check("B2 文件不存在 → invalid",
          cg.assess_asset(cg.AssetRef(kind="image", name="a", path=_missing),
                          root)[0] == cg.ASSET_INVALID)
    check("B3 空文件 → invalid",
          cg.assess_asset(cg.AssetRef(kind="image", name="a", path=_empty),
                          root)[0] == cg.ASSET_INVALID)
    check("B4 越出 asset_root → invalid",
          cg.assess_asset(cg.AssetRef(kind="image", name="a", path=_outside),
                          root)[0] == cg.ASSET_INVALID)
    check("B5 placeholder=True → placeholder（即便文件不存在也不判 invalid，"
          "否则纯 stub 的图会集体 failed）",
          cg.assess_asset(cg.AssetRef(kind="video", name="p", path=_missing,
                                      placeholder=True), root)[0]
          == cg.ASSET_PLACEHOLDER)
    check("B6 stale=True → stale",
          cg.assess_asset(cg.AssetRef(kind="image", name="s", path=_real,
                                      stale=True), root)[0] == cg.ASSET_STALE)
    check("B7 路径为空 → invalid",
          cg.assess_asset(cg.AssetRef(kind="image", name="a", path=""),
                          root)[0] == cg.ASSET_INVALID)
    check("B8 reason 说明具体原因（不能只给一个档位）",
          bool(cg.assess_asset(cg.AssetRef(kind="image", name="a",
                                           path=_missing), root)[1]))

    # --- 端到端：自定义执行器（绕过内置执行器的 validate_output）---
    def _custom(provider, node_id="bad"):
        g = cg.CanvasGraph()
        g.asset_root = root
        n = cg.CanvasNode(node_id, "gen_image", inputs={},
                          outputs={"image": cg.Port("image", "image")})

        def _e(st):
            n.out_assets = {"image": cg.AssetRef(kind="image", name="产物",
                                                 path=provider())}
            return {"ok": True}
        n.executor = _e
        g.add_node(n)
        return g, n

    g1, n1 = _custom(lambda: _missing)
    g1.run({})
    r1 = g1.asset_registrations()
    check("B9 自定义执行器返回**不存在的路径** → 节点 failed（不冒充完成）",
          n1.status == "failed", "status=%s" % n1.status)
    check("B10 该坏资产仍被登记（留证据）+ validity=invalid + 原因可见",
          bool(r1) and r1[0]["validity"] == cg.ASSET_INVALID
          and bool(r1[0]["invalid_reason"]), str(r1))
    g2, n2 = _custom(lambda: _outside)
    g2.run({})
    check("B11 自定义执行器返回**越出 asset_root** 的路径 → 同样 failed 且标 invalid",
          n2.status == "failed"
          and g2.asset_registrations()[0]["validity"] == cg.ASSET_INVALID,
          "status=%s" % n2.status)
    g3, n3 = _custom(lambda: "")
    g3.run({})
    check("B12 路径为空 → 节点仍 completed（只是没给引用，不算假完成）",
          n3.status == "completed"
          and g3.asset_registrations()[0]["registered"] is False,
          "status=%s regs=%s" % (n3.status, g3.asset_registrations()))
    g4, n4 = _custom(lambda: _real)
    g4.run({})
    check("B13 真产物不受影响 → completed 且 validity=real（校验没误伤）",
          n4.status == "completed"
          and g4.asset_registrations()[0]["validity"] == cg.ASSET_REAL,
          "status=%s regs=%s" % (n4.status, g4.asset_registrations()))

    ga = cg.build_sample_graph()
    ga.run({})
    au = ga.audit_assets()
    check("B14 audit_assets 把 stub 全图计进 placeholder 档、invalid 为 0",
          au["counts"].get(cg.ASSET_PLACEHOLDER, 0) > 0
          and au["counts"].get(cg.ASSET_INVALID, 0) == 0, str(au["counts"]))

    g5 = cg.CanvasGraph()
    g5.asset_root = root
    _p5 = _mkasset(root, "s5.png")
    n5 = cg.CanvasNode("s5", "gen_image", outputs={"image": cg.Port("image", "image")})

    def _e5(st):
        n5.out_assets = {"image": cg.AssetRef(kind="image", name="s", path=_p5)}
        return {"ok": True}
    n5.executor = _e5
    g5.add_node(n5)
    g5.run({})
    _round1 = n5.out_assets["image"]
    # 「重跑该节点」= 直接走 _wrap（等价于任务图重跑它一次）。
    # 不能靠再调一次 g5.run({})：任务跑完就是终态，再 run 不会重跑任何任务，
    # 那样测出来的 stale 是错报（第一轮成果被误标成历史产物），
    # 恰恰不是这个机制该有的行为。
    g5._wrap(n5)({})
    check("B15 重跑时上一轮产出被标 stale（历史产物不当本轮成果）",
          _round1.stale is True and _round1 is not n5.out_assets["image"],
          "round1.stale=%s" % _round1.stale)

    # ================= C 组：导入校验加严 =================
    print("\n[C] 工程文件导入校验（复审第 4 条）")

    def _base():
        g = cg.build_sample_graph()
        return cx.export_project_json(g, os.path.join(tmp, "proj.json"))

    def _imp(data, tag):
        """导入并返回错误串（成功返回 None）。"""
        pp = os.path.join(tmp, "proj_%s.json" % tag)
        with open(pp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        try:
            cx.import_project_json(pp)
            return None
        except cx.CanvasImportError as e:
            return str(e)

    def _first_nid(d):
        return sorted(d["graph"]["nodes"])[0]

    check("C1 导出→导入闭环仍成立（加严校验不误伤合法文件）",
          _imp(_base(), "ok") is None)

    d = _base()
    _nid = _first_nid(d)
    d["graph"]["nodes"][_nid]["status"] = "done"
    _err = _imp(d, "status")
    check("C2 status 非法值 → CanvasImportError（报出节点名与可选值）",
          _err is not None and _nid in _err and "status" in _err, str(_err))

    d = _base()
    _nid = _first_nid(d)
    d["graph"]["nodes"][_nid]["status"] = "incomplete"
    check("C3 status 取合法值 → 照常导入（白名单不是只认 pending）",
          _imp(d, "status_ok") is None)

    d = _base()
    _nid = _first_nid(d)
    d["graph"]["nodes"][_nid]["pos"] = ["10", 20]
    _err = _imp(d, "pos")
    check("C4 pos 里是字符串 → CanvasImportError（否则布局层静默错位）",
          _err is not None and "pos" in _err, str(_err))

    d = _base()
    _nid = _first_nid(d)
    d["graph"]["nodes"][_nid].setdefault("config", {})["local_edits"] = {"mode": "transform"}
    _err = _imp(d, "le")
    check("C5 config.local_edits 不是数组 → CanvasImportError",
          _err is not None and "local_edits" in _err, str(_err))

    d = _base()
    _nid = _first_nid(d)
    d["graph"]["nodes"][_nid].setdefault("config", {})["local_edits"] = [{"mode": "blur"}]
    _err = _imp(d, "le_mode")
    check("C6 local_edits 条目 mode 非法 → CanvasImportError（指出第几条）",
          _err is not None and "mode" in _err and "[0]" in _err, str(_err))

    d = _base()
    _nid = _first_nid(d)
    d["graph"]["nodes"][_nid].setdefault("config", {})["local_edits"] = [
        {"mode": "transform",
         "region": {"type": "rect", "x": 0, "y": 0, "w": 1, "h": 1},
         "params": {"op": "brightness"}}]
    check("C7 合法 local_edits 结构 → 照常导入（结构校验不误伤）",
          _imp(d, "le_ok") is None)

    d = _base()
    if d["graph"].get("data_edges"):
        d["graph"]["data_edges"].append(dict(d["graph"]["data_edges"][0]))
        _err = _imp(d, "dup")
        check("C8 数据边完全重复 → CanvasImportError",
              _err is not None and "重复" in _err, str(_err))
    else:
        check("C8 数据边完全重复 → CanvasImportError", False,
              "样例图没有 data_edges，无法构造该场景")

    def _two_to_one(multi):
        return {
            "version": 2,
            "graph": {
                "nodes": {
                    "s1": {"type": "source_prompt",
                           "outputs": {"out": {"type": "prompt", "multi": False}}},
                    "s2": {"type": "source_prompt",
                           "outputs": {"out": {"type": "prompt", "multi": False}}},
                    "g": {"type": "gen_image",
                          "inputs": {"prompt": {"type": "prompt", "multi": multi}},
                          "outputs": {"out": {"type": "image", "multi": False}}},
                },
                "data_edges": [{"from": "s1.out", "to": "g.prompt"},
                               {"from": "s2.out", "to": "g.prompt"}],
                "order_edges": [],
            },
        }

    _err = _imp(_two_to_one(False), "single")
    # 断言里必须点名**第一条边**的下标（data_edges[0]）：运行期的 connect_data 也会
    # 拒掉第二条边，但它的报错只知道「当前这条」；只有导入预检才会说「你和前面第 0 条
    # 冲突」。不这么写，这条判据就分不清「预检抓到」与「运行时兜到」——
    # 扰动 M11 会打出哑弹（去掉预检后判据照样绿）。
    check("C9 单入端口被两条边连 → CanvasImportError（点名两条边的下标）",
          _err is not None and "单入端口" in _err and "data_edges[0]" in _err,
          str(_err))
    check("C10 同一场景改成 multi=True → 放行（多入端口本就该收多条边）",
          _imp(_two_to_one(True), "multi") is None)

    d = _base()
    _nid = _first_nid(d)
    d["graph"]["nodes"][_nid]["placeholder"] = "false"
    _err = _imp(d, "ph")
    check("C11 placeholder 是字符串 → CanvasImportError"
          "（bool('false') 会是 True，静默反转语义）",
          _err is not None and "placeholder" in _err, str(_err))

    d = _base()
    d["version"] = 99
    _err = _imp(d, "ver")
    check("C12 version=99 → CanvasImportError（不当旧版本静默读取后丢字段）",
          _err is not None and "version" in _err, str(_err))

    d = _base()
    d["version"] = "2"
    _err = _imp(d, "ver_str")
    check("C13 version 不是整数 → CanvasImportError",
          _err is not None and "version" in _err, str(_err))

    d = _base()
    d["version"] = 1
    check("C14 version=1 → 照常导入（v1 向后兼容）", _imp(d, "v1") is None)
    d = _base()
    d.pop("version", None)
    check("C15 缺 version 字段 → 按 v1 处理，照常导入",
          _imp(d, "nover") is None)

    # ================= D 组：占位文件 =================
    print("\n[D] 占位文件命名与内容类型标记")
    passthrough_executor = _exmod.passthrough_executor
    stage_path = _exmod.stage_path
    PLACEHOLDER_EXT = _exmod.PLACEHOLDER_EXT
    PLACEHOLDER_CONTENT_TYPE = _exmod.PLACEHOLDER_CONTENT_TYPE
    _pdir = os.path.join(tmp, "pass")
    _pn = cg.CanvasNode("px", "gen_video",
                        outputs={"out": cg.Port("out", "video")})
    passthrough_executor(_pn, _pdir)({})
    _pref = _pn.out_assets["out"]
    check("D1 占位文件不再借真实媒体后缀（用 .node-placeholder）",
          _pref.path.endswith(PLACEHOLDER_EXT), _pref.path)
    check("D2 meta 带 content_type=placeholder（扩展名给人看，元数据给程序看）",
          (_pref.meta or {}).get("content_type") == PLACEHOLDER_CONTENT_TYPE,
          str(_pref.meta))
    check("D3 占位物 assess 判为 placeholder 档（既非 real 也非 invalid）",
          cg.assess_asset(_pref, _pdir)[0] == cg.ASSET_PLACEHOLDER)
    check("D4 真实产物仍用 .mp4/.png（改名没误伤 stage_path 默认行为）",
          stage_path(_pdir, "video", "n", "out").endswith(".mp4")
          and stage_path(_pdir, "image", "n", "out").endswith(".png"))

    # ================= E 组：多入端口收到 list =================
    print("\n[E] multi=True 端口：执行器收到的确实是列表")
    ge = cg.CanvasGraph()
    ge.asset_root = root
    _got = {}

    def _rec(nid):
        def _e(st):
            _got[nid] = dict(ge.nodes[nid].in_assets)
            ge.nodes[nid].out_assets = {
                "o": cg.AssetRef(kind="image" if nid == "m" else "prompt",
                                 name=nid, path=_real)}
            return {"ok": True}
        return _e

    _na = cg.CanvasNode("a", "source_prompt", outputs={"o": cg.Port("o", "prompt")})
    _nb = cg.CanvasNode("b", "source_prompt", outputs={"o": cg.Port("o", "prompt")})
    _nm = cg.CanvasNode("m", "gen_image",
                        inputs={"in": cg.Port("in", "prompt", multi=True)},
                        outputs={"o": cg.Port("o", "image")})
    _na.executor = _rec("a")
    _nb.executor = _rec("b")
    _nm.executor = _rec("m")
    for _n in (_na, _nb, _nm):
        ge.add_node(_n)
    ge.connect_data("a", "o", "m", "in")
    ge.connect_data("b", "o", "m", "in")
    ge.run({})
    _v = _got.get("m", {}).get("in")
    check("E1 multi=True 端口收到 list（不是单个 AssetRef）",
          isinstance(_v, list) and len(_v) == 2, repr(_v))
    check("E2 列表元素是上游 AssetRef",
          isinstance(_v, list) and all(isinstance(x, cg.AssetRef) for x in _v),
          repr(_v))
    check("E3 三节点全 completed（多入端口连线与调度都正常）",
          all(ge.nodes[k].status == "completed" for k in ("a", "b", "m")),
          str({k: ge.nodes[k].status for k in ("a", "b", "m")}))


if __name__ == "__main__":
    main()
    target = os.environ.get("AV_CHECK")
    if target:
        for name, ok in _results:
            if name.startswith(target):
                sys.exit(0 if ok else 1)
        sys.exit(2)  # 目标判据未找到
    failed = [n for n, ok in _results if not ok]
    total = len(_results)
    print("\n" + "=" * 56)
    # run_all 统计约定：这一行必须是纯数字形态，否则整条被判 EMPTY、对门禁隐形
    print(f"PASS={total - len(failed)} FAIL={len(failed)}")
    print(f"  资产有效性判据套件：{total - len(failed)}/{total} 通过"
          + ("  ALL GREEN" if not failed else f"  FAIL={failed}"))
    print("=" * 56)
    sys.exit(1 if failed else 0)
