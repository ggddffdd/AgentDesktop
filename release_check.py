#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""小臭玩AI 发布前检查（release_check.py）

一条命令串起发布前的关键卡口，把「靠记忆手点一遍」变成可重复执行的门禁：

  1) 语法      —— 核心模块逐个 py_compile
  2) 回归      —— 跑 tests/run_all.py，解析通过/失败数
  3) 密钥安全  —— 源码树 + dist 全量扫描（**含二进制**，.pyc/.exe 也要扫）
  4) 版本一致  —— config.APP_VERSION / README / 已构建 exe 内版本 三方对齐
  5) 打包卫生  —— dist 顶层不得混入运行数据、_internal 不得出现真实 config.json

用法：
    python release_check.py                # 全部检查
    python release_check.py --skip-tests   # 跳过回归（改完小东西快速自查）
    python release_check.py --no-dist      # 不检查 dist（未打包时）
退出码：0 = 全过；1 = 有失败项。

为什么需要它（2026-09-27 复盘）：
  · 密钥扫描曾用「默认跳过二进制」的工具，得出过"包内零泄露"的假结论；
  · 版本号对 ≠ 改动进包，必须从 exe 内解出模块核对；
  · dist 既是产物目录、又曾长期充当运行目录，容易把用户数据一起分发出去。
"""

import argparse
import io
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ---------- 输出与计分 ----------
_RESULTS = []


def check(name, ok, detail=""):
    _RESULTS.append((name, bool(ok), detail))
    tag = "OK  " if ok else "FAIL"
    line = f"  [{tag}] {name}"
    if detail:
        line += f"  —— {detail}"
    print(line, flush=True)
    return bool(ok)


def warn(name, detail=""):
    print(f"  [SKIP] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


def summary():
    total = len(_RESULTS)
    failed = [r for r in _RESULTS if not r[1]]
    print("\n" + "=" * 60)
    if failed:
        print(f"发布检查未通过：{total - len(failed)}/{total} 项通过，{len(failed)} 项失败")
        for n, _, d in failed:
            print(f"  × {n}" + (f"  —— {d}" if d else ""))
    else:
        print(f"发布检查全部通过：{total}/{total} 项")
    print("=" * 60)
    return 1 if failed else 0


# ---------- ① 语法 ----------
CORE_MODULES = [
    "main.py", "ui.py", "config.py", "tools.py", "agent.py", "agent_node.py",
    "browser_bridge.py", "webhook_server.py", "task_status.py", "task_resume.py",
    "browser_control_tools.py", "memory_store.py", "context_manager.py", "onboarding.py",
]


def check_syntax():
    """内存编译 —— 不落任何 .pyc，避免污染源码树。

    两个坑（都踩过）：
      1) py_compile + cfile=os.devnull 在 Windows 上第二次会 FileExistsError；
      2) 必须喂 **bytes** 而不是 read_text() 的结果 —— 本项目 ui.py / config.py
         带 UTF-8 BOM，文本模式读出的首字符 ﻿ 会被判定为
         "invalid non-printable character"。喂 bytes 时 Python 自己按
         PEP 263 处理编码声明与 BOM，与真实 import 行为一致。
    """
    bad = []
    for m in CORE_MODULES:
        p = ROOT / m
        if not p.is_file():
            bad.append(f"{m}(缺失)")
            continue
        try:
            compile(p.read_bytes(), str(p), "exec")
        except Exception as e:
            bad.append(f"{m}({type(e).__name__})")
    check("核心模块语法", not bad, "、".join(bad) if bad else f"{len(CORE_MODULES)} 个模块")


# ---------- ② 回归 ----------
def check_tests():
    runner = ROOT / "tests" / "run_all.py"
    if not runner.is_file():
        return check("回归测试", False, "tests/run_all.py 不存在")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["QT_QPA_PLATFORM"] = "offscreen"
    try:
        r = subprocess.run([sys.executable, str(runner)], capture_output=True,
                           timeout=600, env=env)
    except subprocess.TimeoutExpired:
        return check("回归测试", False, "超时（>600s）")
    out = (r.stdout or b"").decode("utf-8", "replace") + (r.stderr or b"").decode("utf-8", "replace")
    # 汇总行形如「汇总：7 个套件 / PASS=202 FAIL=0」。必须用它，
    # 不能抓第一个 PASS= —— 那是单个套件的行（会少报成 19）。
    m = re.search(r"汇总[^\n]*?PASS=(\d+)\s+FAIL=(\d+)", out)
    pairs = re.findall(r"PASS=(\d+)\s+FAIL=(\d+)", out)
    if m:
        p, f = int(m.group(1)), int(m.group(2))
        return check("回归测试", r.returncode == 0 and f == 0, f"PASS={p} FAIL={f}")
    if pairs:
        p = sum(int(a) for a, _ in pairs)
        f = sum(int(b) for _, b in pairs)
        return check("回归测试", r.returncode == 0 and f == 0,
                     f"PASS={p} FAIL={f}（累加 {len(pairs)} 套件）")
    tail = out.strip().splitlines()[-1:] or [""]
    return check("回归测试", r.returncode == 0, tail[0][:80])


# ---------- ③ 密钥安全 ----------
# 教训（2026-09-27）：最初只写了 sk-/tvly-/AKIA 三条、且阈值 24 字符，
# 结果**漏掉了本机一个 23 字符的 Brave key**（BSA 开头，不用 sk- 前缀）。
# 所以规则要覆盖「无固定前缀的凭据」，并放宽长度阈值。
SECRET_PATTERNS = [
    (re.compile(rb"sk-[A-Za-z0-9]{20,}"), "OpenAI/DeepSeek 风格密钥"),
    (re.compile(rb"tvly-[A-Za-z0-9\-]{16,}"), "Tavily 密钥"),
    (re.compile(rb"\bBSA[A-Za-z0-9]{20}\b"), "Brave Search 密钥"),
    (re.compile(rb"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
    (re.compile(rb"ghp_[A-Za-z0-9]{20,}"), "GitHub Token"),
    # 通用兜底：给 key/token/secret 赋上较长随机串（能抓无固定前缀的，如 Serper）。
    # v4.188 P2-15：阈值 20→16（此前 23 字符 Brave key 那类「短 key」漏网教训的
    # 延伸——16~19 位的现代密钥同样不能放过）。
    (re.compile(rb"(?i)(api_?key|apikey|secret|access_?token)[\"']?\s*[:=]\s*[\"']"
                rb"[A-Za-z0-9_\-]{16,}[\"']"), "疑似硬编码凭据赋值（带引号）"),
    # v4.188 P2-15：无引号 shell/env 风格（api_key=xxxx、API_KEY: xxxx）——
    # .env/.yml/.cfg 常见形态，原正则只认引号字面量全漏。
    # 防误伤：要求值里至少含一个数字（真实密钥几乎必含数字；
    # 纯字母大写变量名如 MY_TOKEN_STRING 不会命中）。
    (re.compile(rb"(?i)(api_?key|apikey|secret|access_?token)[\"']?\s*[:=]\s*"
                rb"(?=[A-Za-z_\-]*\d)[A-Za-z0-9_\-]{16,}\b"),
     "疑似硬编码凭据赋值（无引号）"),
]
# tests/ 会**故意**构造「形似密钥」的字符串来验证脱敏逻辑，且不参与发布产物，
# 因此整体跳过。取舍说明：假报警会让人直接忽略这个卡口，那比放宽更危险。
# 再跳过 Qt/上游发行物目录：对「纯二进制」做随机串模式匹配必然假阳性
# （实测 PySide6 的 opengl32sw.dll / Qt6WebEngineCore.dll 里偶然出现 BSA 开头的串）。
SKIP_DIR_PARTS = {".git", "__pycache__", "node_modules", "_archive", "cdp_edge_profile",
                  "tests", "PySide6", "shiboken6", "PySide6_Addons", "PySide6_Essentials"}
SKIP_NAME_PREFIX = ("backup_", "_trash_", "_old_")
# 不扫 .dll/.pyd/.so：那是上游发行物，不是我们的代码，扫了只会假红。
# 保留 .exe —— 它含 PYZ（我们的代码），是真正要核的地方。
# v4.188 P2-15：补 .log/.db/.sqlite —— debug.log 里会回显工具输出/错误报文
# （密钥可能跟着异常栈打进去），.db/.sqlite 是记忆/会话库，同样有回显风险。
SCAN_SUFFIX = (".py", ".pyc", ".json", ".jsonl", ".spec", ".md", ".txt", ".bat", ".ps1",
               ".js", ".html", ".yml", ".yaml", ".ini", ".cfg", ".env", ".exe",
               ".log", ".db", ".sqlite")


def _iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIR_PARTS and not d.startswith(SKIP_NAME_PREFIX)]
        for fn in filenames:
            if fn.startswith(SKIP_NAME_PREFIX):
                continue
            if fn.endswith(SCAN_SUFFIX):
                yield Path(dirpath) / fn


# 合成/占位标记：判据夹具会**故意**写「形似密钥」的字符串来验证防回潮逻辑
# （典型：`_perturb_canvas_agnes.py` 的 PG7 故意把 `sk-fake…` 塞回 agnes_bridge，
#  用来证明 A8 会翻红；而 `!_perturb_*.py` 在 .gitignore 里是**明确解禁入库**的）。
# 这类字符串不是凭据，却会被「sk- + 长随机串」形态规则命中 —— 而**假报警会让人直接
# 忽略这个卡口，比放宽更危险**（本文件上方已写明这条取舍）。
# 做法：命中后**逐条**看匹配文本里有没有肉眼可辨的合成标记；真实密钥是随机串，
# 含这些词的概率可忽略（"test" 不列入 —— `sk-test-…` 可能是真凭据，不该被豁免）。
# ⚠️ 只豁免**匹配文本本身**，不是整份文件 —— 同一文件里另有真实密钥照样会被抓。
SYNTHETIC_MARKERS = ("fake", "dummy", "example", "placeholder", "redacted",
                     "sample", "notreal", "not-a-real", "not_a_real", "xxxx")


def _first_secret_hit(blob):
    """返回 (label, matched) 或 None；命中合成标记的条目跳过（逐条判定，不整文件放行）。"""
    for rx, label in SECRET_PATTERNS:
        for m in rx.finditer(blob):
            txt = m.group(0)
            low = (txt.decode("utf-8", "replace") if isinstance(txt, bytes)
                   else str(txt)).lower()
            if any(mk in low for mk in SYNTHETIC_MARKERS):
                continue
            return label, txt
    return None


def _scan_secrets(root, limit=5):
    hits = []
    for p in _iter_files(root):
        try:
            if p.stat().st_size > 200 * 1024 * 1024:
                continue
            data = p.read_bytes()
        except Exception:
            continue
        found = _first_secret_hit(data)
        if found:
            try:
                rel = p.relative_to(ROOT)
            except Exception:
                rel = p
            hits.append(f"{rel}({found[0]})")
        if len(hits) >= limit:
            break
    return hits


def _scan_pyz_modules(exe):
    """解出 exe 内嵌 PYZ 的 {模块名: [字符串/字节常量 + 标识符]}。失败返回 (None, 原因)。

    为什么要走到这一层：`_scan_secrets` 对 exe 做的是**原始字节**匹配，而 PYZ 里
    每个模块的编组字节码是 **zlib 压缩**的 —— 明文 `grep` 扫不出来。
    也就是说「扫了 exe」并不等于「扫了打进包的代码」，那是个**假阴性**。
    这里用 PyInstaller 自己的读取器解开 TOC，逐模块收集常量池/名字表再跑同一组规则。
    """
    try:
        from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader
    except Exception:
        return None, "未安装 PyInstaller 读取器"
    import marshal
    import types
    try:
        data = exe.read_bytes()
        off = data.find(b"PYZ\x00")
        if off == -1:
            return None, "exe 内找不到 PYZ 魔数"
        za = ZlibArchiveReader(str(exe), start_offset=off)
        out = {}
        for name in za.toc:
            try:
                node = za.extract(name)
            except Exception:
                continue
            code = node[1] if isinstance(node, tuple) else node
            if not isinstance(code, types.CodeType):
                try:
                    code = marshal.loads(code)
                except Exception:
                    continue
            if not isinstance(code, types.CodeType):
                continue
            acc = []

            def eat(x):
                if isinstance(x, types.CodeType):
                    walk(x)
                elif isinstance(x, (str, bytes)):
                    acc.append(x)
                elif isinstance(x, (tuple, list, frozenset, set)):
                    for y in x:
                        eat(y)

            def walk(co):
                for c in co.co_consts:
                    eat(c)
                # 常量键 dict 字面量（BUILD_CONST_KEY_MAP）的键是 tuple 常量，eat 已覆盖；
                # 标识符也收进来 —— 变量名里塞 key 极少见但零成本。
                for attr in ("co_names", "co_varnames", "co_freevars", "co_cellvars"):
                    acc.extend(getattr(co, attr, ()) or ())

            walk(code)
            out[name] = acc
        return out, ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _scan_pyz_secrets(exe, limit=5):
    """在 PYZ 常量表上跑密钥规则。返回 (hits|None, detail)。"""
    mods, why = _scan_pyz_modules(exe)
    if mods is None:
        return None, why
    hits = []
    for name, consts in sorted(mods.items()):
        # ⚠️ 只扫 **str** 常量，不扫 bytes —— 实测（v4.209.x）全量扫 bytes 会出**假阳性**：
        # `PIL.ImageFont` 里内嵌的字体二进制（bytes 常量）恰好含 ASCII 序列 `AKIA…`，
        # 被 AWS Access Key 规则命中。这正是本文件上方警告过的
        # 「对纯二进制做随机匹配必然假阳性」——而假报警会让人直接忽略门禁，比放宽更危险。
        # 取舍：源码里硬编码的凭据**几乎必然是 str 字面量**；bytes 常量在打包模块里
        # 基本都是内嵌二进制（字体/图片/证书）。实测排除 bytes 后 2298 个模块**零命中**，
        # 而唯一那一条命中就是字体。源码树另有 `_scan_secrets` 逐字节覆盖。
        strs = [c for c in consts if isinstance(c, str)]
        if not strs:
            continue
        blob = "\n".join(strs).encode("utf-8", "replace")
        found = _first_secret_hit(blob)
        if found:
            hits.append(f"{name}({found[0]})")
        if len(hits) >= limit:
            break
    return hits, f"{len(mods)} 个模块零命中（仅扫 str 常量，bytes 按上注排除）"


def check_secrets(scan_dist=True):
    targets = [("源码树", ROOT)]
    dist = ROOT / "dist" / "小臭玩AI"
    if scan_dist and dist.is_dir():
        targets.append(("dist 产物", dist))

    all_hits = []
    for label, root in targets:
        hits = _scan_secrets(root)
        all_hits += [f"{label}:{h}" for h in hits]
    check("密钥扫描（含二进制）", not all_hits,
          "、".join(all_hits) if all_hits else "、".join(f"{l}" for l, _ in targets) + " 零命中")

    # 上述对 exe 的扫描是**原始字节**匹配 —— PYZ 内模块是 zlib 压缩的，明文扫不出来。
    # 补一道「解 PYZ 查常量表」，让「扫了 exe」真的等于「扫了打进包的代码」。
    if scan_dist and dist.is_dir():
        for exe in sorted(dist.glob("*.exe"))[:2]:
            hits, detail = _scan_pyz_secrets(exe)
            if hits is None:
                warn(f"PYZ 密钥扫描跳过（{exe.name}）：{detail}")
                continue
            check(f"密钥扫描·解 PYZ 常量表（{exe.name}）", not hits,
                  "、".join(hits) if hits else detail)


# ---------- ④ 版本一致 ----------
def _read_app_version():
    try:
        txt = (ROOT / "config.py").read_text(encoding="utf-8")
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', txt)
        return m.group(1) if m else None
    except Exception:
        return None


def _read_build_date():
    try:
        txt = (ROOT / "config.py").read_text(encoding="utf-8")
        m = re.search(r'APP_BUILD_DATE\s*=\s*"([^"]+)"', txt)
        return m.group(1) if m else None
    except Exception:
        return None


def _vkey(s):
    """版本号 → 数值元组（v4.186 → (4,186)，v4.187.0 → (4,187,0)）。
    v4.188 P2-17：字典序比较的坑——"v4.99.0" > "v4.187.0"（'9'>'1'），
    exe 版本取 max 时会取错；必须按数值比较。"""
    try:
        return tuple(int(x) for x in str(s)[1:].split("."))
    except Exception:
        return ()


def _read_readme_version():
    try:
        txt = (ROOT / "README.md").read_text(encoding="utf-8")
        # v4.188 P2-17：兼容两段号（v4.186）与三段号（v4.186.0）——
        # 原正则只认三段，README 写两段号时匹配不到 → 漏检。
        m = re.search(r"v4\.\d+(?:\.\d+)?", txt)
        return m.group(0) if m else None
    except Exception:
        return None


def _read_exe_version(exe: Path):
    """从 exe 内 PYZ 里解出 config 模块的版本常量（权威，不看文件表面）。"""
    try:
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    except Exception:
        return None, "未安装 PyInstaller 读取器"
    import marshal
    import tempfile
    import types
    try:
        arc = CArchiveReader(str(exe))
        data = arc.extract("PYZ.pyz")
        tmp = Path(tempfile.gettempdir()) / "_release_check_pyz.pyz"
        tmp.write_bytes(data)
        try:
            pz = ZlibArchiveReader(str(tmp))
            if "config" not in pz.toc:
                return None, "PYZ 内无 config 模块"
            node = pz.extract("config")
            code = node[1] if isinstance(node, tuple) else node
            if not isinstance(code, types.CodeType):
                code = marshal.loads(code)

            found = []

            def walk(co):
                for c in co.co_consts:
                    if isinstance(c, str) and c.startswith("v4."):
                        found.append(c)
                    elif isinstance(c, types.CodeType):
                        walk(c)
            walk(code)
            # v4.188 P2-17：数值元组取最大（原 sorted()[-1] 字典序：
            # "v4.99.0" > "v4.187.0"，会取错旧版本）
            return (max(set(found), key=_vkey) if found else None), ""

        finally:
            try:
                tmp.unlink()
            except Exception:
                pass
    except Exception as e:
        return None, f"{type(e).__name__}"


def check_version(scan_dist=True):
    appv = _read_app_version()
    check("config.APP_VERSION 可读", bool(appv), appv or "读取失败")

    # v4.188 P3：APP_BUILD_DATE 新鲜度——写死的日期忘了更新就会带着过期日期
    # 发布（用户报障时对不上真实构建日）。发布门禁当天跑，>2 天即 fail。
    bd = _read_build_date()
    if bd:
        import datetime as _dt
        try:
            age = (_dt.date.today() - _dt.date.fromisoformat(bd)).days
            check("APP_BUILD_DATE 新鲜（≤2 天）", 0 <= age <= 2,
                  f"{bd}（{age} 天前）")
        except ValueError:
            check("APP_BUILD_DATE 格式", False, f"{bd} 非 ISO 日期")
    else:
        check("APP_BUILD_DATE 可读", False, "config.py 未找到")

    rdv = _read_readme_version()
    if rdv:
        # v4.188 P2-17：归一化比较——两段号 v4.186 ≡ 三段号 v4.186.0
        #（补 .0 后再比，README 两种写法都算一致）。
        _rd_n = _vkey(rdv) + ((0,) if len(_vkey(rdv)) == 2 else ())
        _ap_n = _vkey(appv) + ((0,) if len(_vkey(appv)) == 2 else ())
        check("README 版本与 config 一致", _rd_n == _ap_n,
              f"README={rdv} config={appv}")
    else:
        # v4.188 P2-17：README 找不到版本号由 warn 升为 fail——
        # 发布门禁的意义就是挡住「README 忘了更新版本」，warn 等于没挡。
        check("README 版本可读", False, "README 中未找到 vX.Y / vX.Y.Z")

    exe = ROOT / "dist" / "小臭玩AI" / "小臭玩AI.exe"
    if not scan_dist or not exe.is_file():
        warn("已构建 exe 版本核对", "dist 内无 exe（未打包？）")
        return
    v, err = _read_exe_version(exe)
    if v is None:
        warn("已构建 exe 版本核对", err or "读取失败")
        return
    check("exe 内版本与 config 一致", v == appv, f"exe={v} config={appv}")


# ---------- ⑤ 字号 token 守卫（v4.182.0，DESIGN.md §3.2）----------
# 三个被归档的字面值：10px→font_micro(11)、14px→font_input/title/body/icon_btn、
# 20px→font_title_xl。主目录 .py 的 QSS 里不得再出现裸字面值，
# 必须经 THEME["font_*"] 引用。webhook_server.py 是 HTML 模板（Web 区）豁免。
FONT_GUARD_BANNED = re.compile(r"font-size:\s*(10|14|20)px")
FONT_GUARD_EXEMPT_FILES = {"webhook_server.py", "theme_qss.py"}


def check_font_tokens():
    bad = []
    for p in sorted(ROOT.glob("*.py")):
        if p.name in FONT_GUARD_EXEMPT_FILES:
            continue
        try:
            txt = p.read_text(encoding="utf-8-sig", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            if FONT_GUARD_BANNED.search(line):
                bad.append(f"{p.name}:{i}")
    check("字号 token 守卫（无裸 10/14/20px）", not bad,
          "、".join(bad[:6]) + ("…" if len(bad) > 6 else "") if bad
          else f"扫描 {len(list(ROOT.glob('*.py')))} 个 .py")


# ---------- ⑥ 打包卫生 ----------
DIST_RUNTIME_DIRS = ["cdp_edge_profile", "incoming", "output", "outputs", "notes", "pages",
                     "temp", "log", "multi_platform", "orchestrate", "rag_data",
                     "director_session.json", "webhook_events.jsonl", "debug.log",
                     "sessions.json", "memory.db"]


def check_dist_hygiene():
    dist = ROOT / "dist" / "小臭玩AI"
    if not dist.is_dir():
        return warn("打包卫生", "dist 不存在，跳过")

    leaked = [d for d in DIST_RUNTIME_DIRS if (dist / d).exists()]
    check("dist 顶层无运行数据", not leaked,
          "、".join(leaked) if leaked else f"检查 {len(DIST_RUNTIME_DIRS)} 项")

    internal = dist / "_internal"
    if internal.is_dir():
        check("_internal 无真实 config.json",
              not (internal / "config.json").exists(),
              "spec 应只打 config.example.json")
        check("_internal 有 config.example.json",
              (internal / "config.example.json").exists(),
              "模板随包提供，便于用户对照填写")
    else:
        warn("_internal 检查", "_internal 不存在")

    root_cfg = ROOT / "config.json"
    if root_cfg.is_file():
        try:
            import json as _json
            d = _json.loads(root_cfg.read_text(encoding="utf-8"))
            has_key = bool(str(d.get("api_key") or "").strip())
            check("源码根 config.json 不含真实密钥", not has_key,
                  "该文件会被 spec 读取，勿放真 key" if has_key else "仅占位字段")
        except Exception as e:
            warn("源码根 config.json", f"解析失败 {type(e).__name__}")


def main():
    ap = argparse.ArgumentParser(description="小臭玩AI 发布前检查")
    ap.add_argument("--skip-tests", action="store_true", help="跳过回归测试")
    ap.add_argument("--no-dist", action="store_true", help="不检查 dist 产物")
    args = ap.parse_args()

    print("小臭玩AI 发布前检查")
    print(f"项目根：{ROOT}")
    print("-" * 60)

    check_syntax()
    if args.skip_tests:
        warn("回归测试", "--skip-tests")
    else:
        check_tests()
    check_font_tokens()
    check_secrets(scan_dist=not args.no_dist)
    check_version(scan_dist=not args.no_dist)
    if args.no_dist:
        warn("打包卫生", "--no-dist")
    else:
        check_dist_hygiene()

    return summary()


if __name__ == "__main__":
    sys.exit(main())
