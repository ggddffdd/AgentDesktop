# -*- coding: utf-8 -*-
"""v4.314.0：memory.db 缺表自愈（2026-11-20 实测事故修复）。

事故现场（大哥桌面真实日志 22:46:21）：

    memory_store 同步SQLite失败: no such table: memories
    memory_store FTS5 搜索失败: no such table: memories_fts

根因：`~/Documents/小臭玩AI/memory.db` 变成 **0 字节空库**（无 SQLite header、
无 memories 表）。此后每次 `search_memory` 都崩，因为：

    search_memory()  →  _sync_md_to_db()  →  SELECT COUNT(*) FROM memories  →  崩

而旧代码里只有 `_ensure_db()` 会建表，`_sync_md_to_db` 自身不建表，
也没有任何路径在缺表时兜底 → **缺表即永久不自愈**，记忆搜索层静默失效。

修法（做减法）：把表存在性自愈下沉到 `_with_db` 单点，所有 DB 路径
（_sync_md_to_db / _db_append / _db_replace_by_topic / _fts）都过一遍。
查 sqlite_master 是内存操作（微秒级），不违背 _DB_READY 的省开销设计。

判据分两层：A 层跑真实事故场景（0 字节库必须自愈），B 层断言源码契约
（防止把检查改成注释、或挪到 connect 之前造成假绿）。
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import memory_store as ms

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + (("  <- " + str(detail)) if detail else ""))


def _src():
    return Path(ms.__file__).read_text(encoding="utf-8")


def _seed_md(d, n=2):
    """写一份真实形态的 memory.md（结构化头 + 正文），供 _sync_md_to_db 拆条。"""
    lines = []
    for i in range(n):
        lines.append("## 2026-11-%02d 10:%02d [用户偏好] #主题%d" % (10 + i, i, i))
        lines.append("第%d条记忆正文：用于索引重建验证。" % i)
        lines.append("")
    Path(os.path.join(d, "memory.md")).write_text("\n".join(lines), encoding="utf-8")


def _repro_0byte(d):
    """复现事故现场：memory.db 是 0 字节文件（无 header、无表）。"""
    with open(os.path.join(d, "memory.db"), "wb"):
        pass


def _count_memories(d):
    c = sqlite3.connect(os.path.join(d, "memory.db"))
    try:
        return c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    except sqlite3.OperationalError:
        return -1
    finally:
        c.close()


def _tables(d):
    c = sqlite3.connect(os.path.join(d, "memory.db"))
    try:
        return {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')")}
    finally:
        c.close()


# ============================================================
# A 层：跑真实事故场景
# ============================================================

def _a_main():
    print("\n[A] 0 字节空库 + search_memory 必须自愈（事故主路径）")
    d = tempfile.mkdtemp(prefix="memheal_314_a_")
    try:
        _seed_md(d)
        _repro_0byte(d)
        ms._configure(d)
        check("A1 现场：memory.db 为 0 字节", os.path.getsize(ms.MEMORY_DB_PATH) == 0)
        check("A1b 现场：memories 表缺失", _count_memories(d) == -1)

        try:
            hits = ms.search_memory("第0条记忆正文")
            check("A2 搜索不再抛异常（旧代码崩 no such table）", True)
        except Exception as e:
            check("A2 搜索不再抛异常（旧代码崩 no such table）", False,
                  "%s: %s" % (type(e).__name__, e))
            hits = []

        check("A3 索引已从 memory.md 重建（2 条）", _count_memories(d) == 2,
              "-> %d" % _count_memories(d))
        check("A4 memory.db 已从 0 字节长到非 0", os.path.getsize(ms.MEMORY_DB_PATH) > 0)
        check("A5 FTS 虚拟表一并重建", "memories_fts" in _tables(d), "-> %s" % _tables(d))
        check("A6 搜索命中重建后的记忆", any("第0条记忆正文" in h["text"] for h in hits),
              "-> %s" % (hits[0]["text"][:40] if hits else "无命中"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _a_sync_direct():
    print("\n[A] 事故路径 _sync_md_to_db 直调不崩（search_memory 的唯一入口）")
    d = tempfile.mkdtemp(prefix="memheal_314_b_")
    try:
        _seed_md(d, n=3)
        _repro_0byte(d)
        ms._configure(d)
        err = None
        try:
            ms._sync_md_to_db()
        except Exception as e:
            err = e
        check("A7 _sync_md_to_db 缺表时不抛异常", err is None,
              "%s: %s" % (type(err).__name__, err) if err else "")
        check("A8 同步后 memories 有 3 条", _count_memories(d) == 3,
              "-> %d" % _count_memories(d))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _a_append_path():
    print("\n[A] 写入路径 _db_append 也自愈（另一条独立 DB 路径）")
    d = tempfile.mkdtemp(prefix="memheal_314_c_")
    try:
        Path(os.path.join(d, "memory.md")).write_text("## 2026-11-10 10:00\n旧记忆\n",
                                                     encoding="utf-8")
        _repro_0byte(d)
        ms._configure(d)
        ret = ms.append_memory("自愈后新写入的记忆")
        check("A9 append_memory 在缺表库上仍成功", str(ret).startswith("已写入"),
              "-> %s" % ret[:40])
        check("A10 新记忆已进 SQLite 索引", _count_memories(d) >= 1,
              "-> %d" % _count_memories(d))
        check("A11 markdown 主源同步落盘", "自愈后新写入的记忆" in
              Path(os.path.join(d, "memory.md")).read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _a_healthy_unaffected():
    print("\n[A] 健康库不被误重建（自愈必须幂等、不丢数据）")
    d = tempfile.mkdtemp(prefix="memheal_314_d_")
    try:
        Path(os.path.join(d, "memory.md")).write_text("## 2026-11-10 10:00\n健康库记忆A\n",
                                                      encoding="utf-8")
        ms._configure(d)
        # 内容必须与 md 现有条目不同，否则会命中去重跳过、根本不落 DB
        ms.append_memory("健康库记忆B", type="用户偏好")
        first = _count_memories(d)
        n_before = os.path.getsize(ms.MEMORY_DB_PATH)
        # 第二次访问：表已存在，不得重复建表/清空
        ms.search_memory("健康库")
        ms._sync_md_to_db()
        check("A12 健康库条数不缩水", _count_memories(d) == first,
              "-> %d -> %d" % (first, _count_memories(d)))
        check("A13 健康库文件不被清空", os.path.getsize(ms.MEMORY_DB_PATH) >= n_before,
              "-> %d -> %d" % (n_before, os.path.getsize(ms.MEMORY_DB_PATH)))
        check("A14 旧记忆仍可检索", any("健康库记忆" in h["text"]
                                       for h in ms.search_memory("健康库")))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _a_encrypted():
    print("\n[A] 加密模式下 0 字节库同样自愈")
    d = tempfile.mkdtemp(prefix="memheal_314_e_")
    try:
        Path(os.path.join(d, "memory.md")).write_text(
            "## 2026-11-10 10:00\n加密模式自愈记忆\n", encoding="utf-8")
        ms._configure(d)
        ms.set_encryption("heal314_passphrase")
        _repro_0byte(d)
        try:
            hits = ms.search_memory("加密模式自愈记忆")
            check("A15 加密模式搜索不崩", True)
        except Exception as e:
            check("A15 加密模式搜索不崩", False, "%s: %s" % (type(e).__name__, e))
            hits = []
        check("A16 加密库 .enc 已生成", os.path.exists(ms.MEMORY_DB_PATH + ".enc"))
        # 直测 SQLite 索引层。不能用 search_memory 的命中数当判据——
        # search_memory 有 Python 子串兜底（FTS 崩了也能从 memory.md 扫出来），
        # 缺表被静默吞掉后仍能「命中」，判据就假绿了。
        try:
            n = ms._with_db(lambda c: c.execute(
                "SELECT COUNT(*) FROM memories").fetchone()[0])
        except Exception as e:
            n = -1
            print("       （索引查询失败：%s: %s）" % (type(e).__name__, e))
        check("A17 加密模式 SQLite 索引已重建", n >= 1, "-> %d" % n)
    finally:
        ms.set_encryption(None)
        shutil.rmtree(d, ignore_errors=True)


def _a_multi_visit():
    print("\n[A] 反复访问缺表库都稳定（幂等，不重复重建/不崩）")
    d = tempfile.mkdtemp(prefix="memheal_314_f_")
    try:
        _seed_md(d, n=1)
        _repro_0byte(d)
        ms._configure(d)
        ms._sync_md_to_db()
        first = _count_memories(d)
        ok = True
        for _ in range(5):
            try:
                ms.search_memory("主题0")
                ms._sync_md_to_db()
            except Exception:
                ok = False
                break
        check("A18 连续 5 轮访问均不崩", ok)
        check("A19 重复访问后条数稳定", _count_memories(d) == first,
              "-> %d -> %d" % (first, _count_memories(d)))
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ============================================================
# B 层：源码契约断言（防注释级假绿 / 防位置挪动）
# ============================================================

def _b_contract():
    print("\n[B] _with_db 源码契约（自愈检查必须真在 connect 之后、fn 之前）")
    s = _src()
    check("B1 _with_db 含 sqlite_master 表存在性检查",
          "sqlite_master" in s and "memories" in s)
    check("B2 自愈分支会调 _create_tables", "_create_tables(conn)" in s)

    # 位置：必须在 sqlite3.connect(MEMORY_DB_PATH) 之后、return fn(conn) 之前
    with_db = s.split("def _with_db")[1].split("def _init_db")[0]
    i_conn = with_db.find("sqlite3.connect(MEMORY_DB_PATH)")
    i_check = with_db.find("sqlite_master")
    i_fn = with_db.find("return fn(conn)")
    check("B3 自愈检查在 connect 之后", -1 < i_conn < i_check,
          "conn=%d check=%d" % (i_conn, i_check))
    check("B4 自愈检查在 fn(conn) 之前", -1 < i_check < i_fn,
          "check=%d fn=%d" % (i_check, i_fn))
    check("B5 缺表时重建（if 条件分支，非无条件建表）",
          "if not conn.execute" in with_db or "if not" in with_db)

    # 事故路径本身不得自己绕过自愈（防止有人给 _sync_md_to_db 加了局部 _ensure_db
    # 后把 _with_db 的检查删掉，造成「看起来修好了」的假绿）
    check("B6 _with_db 是唯一自愈点（_sync_md_to_db 未自行建表）",
          "_ensure_db" not in s.split("def _sync_md_to_db")[1].split("def ensure_dir")[0])


def main():
    _a_main()
    _a_sync_direct()
    _a_append_path()
    _a_healthy_unaffected()
    _a_encrypted()
    _a_multi_visit()
    _b_contract()
    print("\n汇总：PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())