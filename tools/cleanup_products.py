#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产物自动清理 —— 超过 N 天的生成物移入回收站，回收站再放 M 天后真删。

设计原则（每一条都是为了「宁可漏不可误」）：
  1. **默认 dry-run**，只有显式 `--apply` 才动文件；先看清单再执行。
  2. **只碰白名单目录**（生成物类）；**登录态/技能/知识库/配置/审计痕一律不碰**。
  3. **两段式**：先移进 `<工作区>/_trash_产物/<日期>/`（可原样取回），
     等回收站里的批次超过 `--keep-days`（默认 14 天）才真删 —— 误删有 14 天反悔期。
  4. **单次上限**：一次最多移动 3000 个文件 / 3GB，超了立刻停并报告（防脚本失控）。
  5. 只删**空目录**（`rmdir`），非空目录绝不动 —— 天然挡住"顺手删一坨"。

用法：
    python cleanup_products.py                 # 预演，只打印将做什么
    python cleanup_products.py --apply         # 真正执行
    python cleanup_products.py --apply --age-days 60 --keep-days 30
    python cleanup_products.py --report-only   # 只报告（含不建议自动清的项）
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

WS = Path(os.environ.get("XC_WORKSPACE_DIR")
          or Path.home() / "Documents" / "小臭玩AI")

# 只在这些目录里找过期文件（生成物类）
WHITELIST = ["产物", "incoming", "videos", "charts", "legion_runs",
             "legion_reports", "pages", "avatars"]

# 永不触碰（即使出现在白名单里）—— 登录态 / 技能 / 知识库 / 配置 / 审计痕
PROTECTED = {"cdp_edge_profile", "playwright_profile", "skills", "rag_data",
             "logs", "backups", "config.json", "config.json.bak", "agent_log.db",
             "legion_auth.jsonl", "sessions.json", "memory.db", "_trash_产物",
             "_pollution_backup_20260928_013916",
             # 样品/素材库：**不是临时文件，必须保留** —— 这是
             # `freestylefly/awesome-gpt-image-2` 的 clone（542 张图 / 311MB），
             # 即 `gpt-image2-style-library` 技能的**上游样品/模板库**。
             # 2026-09-28 已从 `tmp_awesome_gpt` 改名为 `sample_libs/awesome-gpt-image-2`
             # （带 tmp_ 会被误当垃圾），旧名一并留着当历史别名，防旧备份/旧脚本再引。
             "sample_libs", "awesome-gpt-image-2", "tmp_awesome_gpt"}

# 只报告、不自动动的（可能需要人判断）
REPORT_ONLY = ["backups", "debug.log.1"]

TRASH_DIR = "_trash_产物"
MAX_FILES = 3000
MAX_BYTES = 3 * 1024 ** 3


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f}{u}" if u != "B" else f"{int(n)}B"
        n /= 1024


def build_ref_index():
    """收集「被引用过的文件名」，用于放行检查。

    为什么必须做（真实风险，不是理论）：会话存档里存着附件标记（如
    `[文件: incoming/xxx.png]`）和 deliverables 路径；把这类文件移走，
    会让老会话打开附件时报「文件不存在」——**正是 v4.173.0 修过的那类 bug**。
    配置里也可能引用 avatars 等路径。

    做法：把这些文件当**文本**整体扫一遍，凡出现过的文件名/相对路径一律**保留**。
    宁可漏清（误保留），绝不错清（误移动）—— 与「宁可漏不可误」一致。
    """
    blob = []
    for name in ("sessions.json", "config.json", "agent_log.db.bak",
                 "legion_auth.jsonl"):
        p = WS / name
        if p.is_file():
            try:
                blob.append(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    for p in WS.glob("legion_*.json"):
        try:
            blob.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(blob)


def is_referenced(ref_blob, rel, basename):
    if not ref_blob:
        return False
    if basename and basename in ref_blob:
        return True
    if rel and (rel in ref_blob or rel.replace("\\", "/") in ref_blob):
        return True
    return False


def scan(age_days):
    """返回 (hits, kept_by_ref)。

    hits = [(绝对路径, 相对工作区路径, 体量, 年龄天数)]，只看白名单里的普通文件。
    kept_by_ref = 因为"被会话/配置引用"而不动的文件（附原因）。
    """
    now = time.time()
    cutoff = age_days * 86400
    ref_blob = build_ref_index()
    hits = []
    kept_by_ref = []
    for name in WHITELIST:
        root = WS / name
        if not root.is_dir() or name in PROTECTED:
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in PROTECTED for part in p.relative_to(WS).parts):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            age = now - st.st_mtime
            if age <= cutoff:
                continue
            rel = str(p.relative_to(WS))
            if is_referenced(ref_blob, rel, p.name):
                kept_by_ref.append((rel, st.st_size, age / 86400))
                continue
            hits.append((p, rel, st.st_size, age / 86400))
    hits.sort(key=lambda x: x[3], reverse=True)
    kept_by_ref.sort(key=lambda x: x[2], reverse=True)
    return hits, kept_by_ref


def move_to_trash(hits, apply_):
    """把过期文件移进回收站批次目录（保留相对结构）。"""
    batch = WS / TRASH_DIR / time.strftime("%Y-%m-%d_%H%M%S")
    moved = failed = 0
    total = 0
    for src, rel, size, age in hits:
        dst = batch / rel
        if not apply_:
            print(f"  [DRY ] {rel}   {human(size)}   闲置 {age:.0f} 天")
            moved += 1
            total += size
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved += 1
            total += size
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {rel}  {type(e).__name__}: {e}")
    # 顺手收掉白名单目录里因此变空的目录（rmdir 只能删空目录）
    if apply_:
        for name in WHITELIST:
            root = WS / name
            if not root.is_dir():
                continue
            for d in sorted(root.rglob("*"), key=lambda x: -len(str(x))):
                if d.is_dir():
                    try:
                        d.rmdir()
                    except OSError:
                        pass
    return batch, moved, failed, total


def purge_trash(keep_days, apply_):
    """真删回收站里超过 keep_days 的批次目录。"""
    tdir = WS / TRASH_DIR
    if not tdir.is_dir():
        return 0, 0, []
    now = time.time()
    purged_files = 0
    purged_bytes = 0
    names = []
    for batch in sorted(tdir.iterdir()):
        if not batch.is_dir():
            continue
        try:
            age = (now - batch.stat().st_mtime) / 86400
        except OSError:
            continue
        if age <= keep_days:
            continue
        n = b = 0
        for p in batch.rglob("*"):
            if p.is_file():
                try:
                    b += p.stat().st_size
                    n += 1
                except OSError:
                    pass
        names.append(f"{batch.name}（{n} 个文件 / {human(b)}，滞留 {age:.0f} 天）")
        if apply_:
            shutil.rmtree(batch, ignore_errors=True)
        purged_files += n
        purged_bytes += b
    return purged_files, purged_bytes, names


def report_only_items():
    """列出"不建议自动清、需要人看一眼"的项。"""
    out = []
    for name in REPORT_ONLY:
        p = WS / name
        if not p.exists():
            continue
        if p.is_dir():
            n = b = 0
            for f in p.rglob("*"):
                if f.is_file():
                    try:
                        b += f.stat().st_size
                        n += 1
                    except OSError:
                        pass
            try:
                age = (time.time() - p.stat().st_mtime) / 86400
            except OSError:
                age = -1
            out.append(f"  · {name}/  {n} 个文件 / {human(b)}（最后改动 {age:.0f} 天前）")
        else:
            try:
                out.append(f"  · {name}  {human(p.stat().st_size)}"
                           f"（{(time.time()-p.stat().st_mtime)/86400:.0f} 天前）")
            except OSError:
                pass
    return out


def main():
    ap = argparse.ArgumentParser(description="产物自动清理（默认 dry-run）")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只预演）")
    ap.add_argument("--age-days", type=int, default=30, help="超过多少天算过期（默认 30）")
    ap.add_argument("--keep-days", type=int, default=14,
                    help="回收站里再放多少天才真删（默认 14，反悔期）")
    ap.add_argument("--report-only", action="store_true", help="只出报告，不预演移动")
    a = ap.parse_args()

    if not WS.is_dir():
        print(f"⛔ 工作区不存在：{WS}")
        return 1

    mode = "执行" if a.apply else "预演(DRY-RUN)"
    print(f"=== 产物清理 · {mode} ===")
    print(f"工作区 : {WS}")
    print(f"阈值   : 闲置 > {a.age_days} 天 → 移入回收站；回收站滞留 > {a.keep_days} 天 → 真删")
    print(f"白名单 : {'、'.join(WHITELIST)}")
    print(f"不碰   : 登录态/技能/知识库/配置/审计痕（{len(PROTECTED)} 项）\n")

    hits, kept_ref = scan(a.age_days)
    tot = sum(h[2] for h in hits)
    print(f"[1] 命中过期文件：{len(hits)} 个 / {human(tot)}")
    if kept_ref:
        kt = sum(x[1] for x in kept_ref)
        print(f"    其中 {len(kept_ref)} 个 / {human(kt)} **因被会话或配置引用而保留**"
              f"（移走会让老会话报「文件不存在」）：")
        for rel, size, age in kept_ref[:10]:
            print(f"       [KEEP] {rel}  {human(size)}  闲置 {age:.0f} 天")
        if len(kept_ref) > 10:
            print(f"       … 另 {len(kept_ref)-10} 个")
    if hits and not a.report_only:
        if len(hits) > MAX_FILES or tot > MAX_BYTES:
            print(f"  ⛔ 超过单次上限（{MAX_FILES} 个 / {human(MAX_BYTES)}）—— 本次只列前 20 个，"
                  f"不移动。请人工分批处理。")
            for _p, rel, size, age in hits[:20]:
                print(f"       {rel}  {human(size)}  {age:.0f} 天")
        else:
            batch, moved, failed, total = move_to_trash(hits, a.apply)
            print(f"  → {'已移入' if a.apply else '将移入'} {TRASH_DIR}/"
                  f"{batch.name}：{moved} 个 / {human(total)}"
                  + (f"，失败 {failed}" if failed else ""))
    elif not hits:
        print("  （没有过期文件，无需处理）")

    pf, pb, names = purge_trash(a.keep_days, a.apply)
    print(f"\n[2] 回收站清出：{pf} 个文件 / {human(pb)}"
          + ("" if a.apply else "（预演）"))
    for n in names:
        print(f"       - {n}")

    print("\n[3] 以下项**只报告、不自动清**（可能还要用，请你判断）：")
    items = report_only_items()
    print("\n".join(items) if items else "  （无）")

    tdir = WS / TRASH_DIR
    if tdir.is_dir():
        n = b = 0
        for f in tdir.rglob("*"):
            if f.is_file():
                try:
                    b += f.stat().st_size
                    n += 1
                except OSError:
                    pass
        print(f"\n回收站现状：{TRASH_DIR}/  {n} 个文件 / {human(b)}"
              f"（{a.keep_days} 天后自动清）")

    if not a.apply:
        print("\n提示：以上为预演。确认无误后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
