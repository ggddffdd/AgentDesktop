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
    # 通用兜底：给 key/token/secret 赋上较长随机串（能抓无固定前缀的，如 Serper）
    (re.compile(rb"(?i)(api_?key|apikey|secret|access_?token)[\"']?\s*[:=]\s*[\"']"
                rb"[A-Za-z0-9_\-]{20,}[\"']"), "疑似硬编码凭据赋值"),
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
SCAN_SUFFIX = (".py", ".pyc", ".json", ".jsonl", ".spec", ".md", ".txt", ".bat", ".ps1",
               ".js", ".html", ".yml", ".yaml", ".ini", ".cfg", ".env", ".exe")


def _iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIR_PARTS and not d.startswith(SKIP_NAME_PREFIX)]
        for fn in filenames:
            if fn.startswith(SKIP_NAME_PREFIX):
                continue
            if fn.endswith(SCAN_SUFFIX):
                yield Path(dirpath) / fn


def _scan_secrets(root, limit=5):
    hits = []
    for p in _iter_files(root):
        try:
            if p.stat().st_size > 200 * 1024 * 1024:
                continue
            data = p.read_bytes()
        except Exception:
            continue
        for rx, label in SECRET_PATTERNS:
            if rx.search(data):
                try:
                    rel = p.relative_to(ROOT)
                except Exception:
                    rel = p
                hits.append(f"{rel}({label})")
                break
        if len(hits) >= limit:
            break
    return hits


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


# ---------- ④ 版本一致 ----------
def _read_app_version():
    try:
        txt = (ROOT / "config.py").read_text(encoding="utf-8")
        m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', txt)
        return m.group(1) if m else None
    except Exception:
        return None


def _read_readme_version():
    try:
        txt = (ROOT / "README.md").read_text(encoding="utf-8")
        m = re.search(r"v4\.\d+\.\d+", txt)
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
            return (sorted(set(found))[-1] if found else None), ""

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

    rdv = _read_readme_version()
    if rdv:
        check("README 版本与 config 一致", rdv == appv, f"README={rdv} config={appv}")
    else:
        warn("README 版本", "README 中未找到 vX.Y.Z")

    exe = ROOT / "dist" / "小臭玩AI" / "小臭玩AI.exe"
    if not scan_dist or not exe.is_file():
        warn("已构建 exe 版本核对", "dist 内无 exe（未打包？）")
        return
    v, err = _read_exe_version(exe)
    if v is None:
        warn("已构建 exe 版本核对", err or "读取失败")
        return
    check("exe 内版本与 config 一致", v == appv, f"exe={v} config={appv}")


# ---------- ⑤ 打包卫生 ----------
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
    check_secrets(scan_dist=not args.no_dist)
    check_version(scan_dist=not args.no_dist)
    if args.no_dist:
        warn("打包卫生", "--no-dist")
    else:
        check_dist_hygiene()

    return summary()


if __name__ == "__main__":
    sys.exit(main())
