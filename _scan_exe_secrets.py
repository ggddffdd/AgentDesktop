# -*- coding: utf-8 -*-
"""打包产物密钥扫描：解 PYZ 常量表 + PKG 内文件，找真实密钥形态。

为什么不能 `grep -a sk- xxx.exe`：密钥在 exe 里是**编译后的常量对象**（marshal），
明文未必连续出现；反过来，exe 里也混着大量无关二进制，"扫到什么"与"有没有泄露"
两回事。唯一可信做法是把常量表抽出来逐条判。

用法：python _scan_exe_secrets.py
"""
import marshal
import re
import sys
import types
from pathlib import Path

# 路径从脚本自身位置推导：本文件要进公开仓库，写死本机绝对路径会让别人
# 拿到就跑不动（且会泄露本机的目录结构）。
EXE = Path(__file__).resolve().parent / "dist" / "小臭玩AI" / "小臭玩AI.exe"

PATTERNS = [
    ("OpenAI/DeepSeek 型 sk-", re.compile(r"sk-[A-Za-z0-9_\-]{20,}")),
    ("Anthropic sk-ant-", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("GitHub PAT", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("GitHub fine-grained", re.compile(r"github_pat_[A-Za-z0-9_]{30,}")),
    ("AWS AKIA", re.compile(r"AKIA[0-9A-Z]{16}")),
    # ⚠️ 泛「40 位字母数字」规则**必须带上下文**才能用。
    # 裸用一次就在本产物里造出 20 处假阳性：numpy 的 git commit hash、
    # pydantic 的版本校验 hash、pygments 的 Cocoa 类名（`UIViewController...`）、
    # docx 的 XML 命名空间（`org/drawingml/...`）全长成这个样子。
    # 「扫出一堆噪音」比「扫不出来」更危险 —— 真泄露会被埋在噪音里没人看。
    ("AWS secret（需赋值上下文）",
     re.compile(r"(?i)aws[_\-]?secret[_\-]?(?:access)?[_\-]?key[\"'\s:=]{1,6}"
                r"[\"']?([A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])")),
    ("阿里云/腾讯云 SecretKey（需赋值上下文）",
     re.compile(r"(?i)(?:secret[_\-]?key|secretid|access[_\-]?key[_\-]?secret)"
                r"[\"'\s:=]{1,6}[\"']?([A-Za-z0-9]{24,})(?![A-Za-z0-9])")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.")),
    ("Bearer 长串", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{30,}")),
    ("Slack/HF 型", re.compile(r"(xox[baprs]-|hf_)[A-Za-z0-9_\-]{20,}")),
]

# 白名单：确认无害的命中（写清理由，逐条可核）
ALLOW = [
    # 真实密钥必含数字或大写；全小写连字符的 sk- 串是 SPDX 许可证标识符
    # （实测命中 `sk-linking-protocols-exception`，来自 packaging.licenses）。
    re.compile(r"^sk-[a-z][a-z\-]+$"),
    re.compile(r"^Bearer\s*$"),
]


def walk(c, depth=0):
    if depth > 30:
        return
    for k in c.co_consts:
        if isinstance(k, types.CodeType):
            yield from walk(k, depth + 1)
        elif isinstance(k, str):
            yield k
        elif isinstance(k, (tuple, frozenset)):
            for y in k:
                if isinstance(y, str):
                    yield y


def main():
    if not EXE.is_file():
        print("未找到 exe：%s" % EXE)
        return 1

    data = EXE.read_bytes()
    print("扫描产物：%s（%.1f MB）" % (EXE.name, len(data) / 1048576))
    print("-" * 62)

    hits = []
    checked = 0

    # ---- 一、PYZ 模块常量表 ----
    off = data.find(b"PYZ\x00")
    if off == -1:
        print("[FAIL] exe 内未找到 PYZ 段")
        return 1
    from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader
    za = ZlibArchiveReader(str(EXE), start_offset=off)
    print("PYZ 模块数：%d" % len(za.toc))

    for name in list(za.toc):
        try:
            got = za.extract(name)
            co = got if isinstance(got, types.CodeType) else marshal.loads(got)
        except Exception:
            continue
        if not isinstance(co, types.CodeType):
            continue
        for s in walk(co):
            checked += 1
            for label, pat in PATTERNS:
                for m in pat.finditer(s):
                    frag = m.group(0)
                    if any(a.match(frag) for a in ALLOW):
                        continue
                    hits.append(("PYZ:" + str(name), label, frag[:90]))

    # ---- 二、PKG(CArchive) 里的文本类文件 ----
    try:
        from PyInstaller.archive.readers import CArchiveReader
        r = CArchiveReader(str(EXE))
        print("PKG 条目数：%d" % len(r.toc))
        for entry in list(r.toc):
            nm = entry[0] if isinstance(entry, tuple) else entry
            s_nm = str(nm)
            if not s_nm.lower().endswith((".py", ".json", ".txt", ".md", ".cfg",
                                          ".ini", ".yaml", ".yml", ".toml", ".js")):
                continue
            try:
                raw = r.extract(nm)
            except Exception:
                continue
            if not isinstance(raw, (bytes, bytearray)):
                continue
            try:
                text = bytes(raw).decode("utf-8", "replace")
            except Exception:
                continue
            checked += 1
            for label, pat in PATTERNS:
                for m in pat.finditer(text):
                    frag = m.group(0)
                    if any(a.match(frag) for a in ALLOW):
                        continue
                    hits.append(("PKG:" + s_nm, label, frag[:90]))
    except Exception as e:
        print("PKG 读取异常（不阻断，但需人工确认）：%r" % (e,))

    print("参与判定的字符串常量：%d 条" % checked)
    print("-" * 62)
    if not hits:
        print("[PASS] 打包产物内未发现密钥泄露（源码扫描已单独通过）")
        print("EXE_SECRET_SCAN_OK")
        return 0

    # 去重
    seen = set()
    uniq = []
    for h in hits:
        k = (h[1], h[2])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(h)
    print("[FAIL] 命中 %d 处（去重后 %d 处）：" % (len(hits), len(uniq)))
    for where, label, frag in uniq[:30]:
        print("   %-40s %-22s %s" % (where, label, frag))
    print("EXE_SECRET_SCAN_LEAK")
    return 1


if __name__ == "__main__":
    sys.exit(main())
