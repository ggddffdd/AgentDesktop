# -*- coding: utf-8 -*-
"""小臭玩AI — 最小回归测试（v4.74）

纯标准库运行，无需 pytest：
    python tests/regression.py
退出码 0=全过，1=有失败。

覆盖：
- 记忆层：钉住召回 / 关键词召回 / 冲突合并去重 / 中文搜索兜底 / 自动备份+自愈
- 上下文隔离：不同 sid 的 ContextManager 实例与落盘互不串台
- 记忆加密：启用后落盘为 .enc、明文不残留、可解密、错口令读不出、可迁移回明文
"""
import os
import sys
import json
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import memory_store as ms
import context_manager as cm

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


# ===================== 记忆层 =====================
def test_memory_layer():
    print("== 记忆层 ==")
    td = tempfile.mkdtemp(prefix="memreg_")
    ms._configure(td)
    MEM = ms.MEMORY_PATH
    CORE = ms.PINNED_PATH

    # 种子：钉住核心画像 + 记忆库
    open(CORE, "w", encoding="utf-8").write("## 核心画像\n大哥叫 xyb，昆明，电力系统。\n")
    open(MEM, "w", encoding="utf-8").write(
        "# 记忆库\n## 2026-01-01 [用户偏好] #房贷\n房贷差5万。\n")

    # T1 钉住画像召回
    r = ms.recall_memory("大哥是谁")
    check("钉住画像注入", "核心画像" in r and "xyb" in r, repr(r[:60]))

    # T2 关键词召回
    r2 = ms.recall_memory("房贷还差多少")
    check("关键词召回命中房贷", "房贷差5万" in r2, repr(r2[:60]))

    # T3 中文搜索兜底（FTS5 不切汉字，靠子串扫描）
    hits = ms.search_memory("房贷")
    check("中文搜索命中", any("房贷差5万" in h["text"] for h in hits), str(hits)[:120])

    # T4 冲突合并：同 topic 覆盖旧值，无多副本、无畸形双井号头
    ret = ms.append_memory("房贷已还清，剩0万。", type="用户偏好", topic="房贷")
    full = open(MEM, encoding="utf-8").read()
    check("冲突合并覆盖旧条目", full.count("#房贷") == 1, f"#房贷 数={full.count('#房贷')}")
    check("旧值已清除", "差5万" not in full, "仍含'差5万'")
    check("新值已写入", "已还清" in full)
    check("无畸形双井号头", "## ##" not in full, "出现 '## ##'")

    # T5 去重：相同内容再写应跳过
    ret2 = ms.append_memory("房贷已还清，剩0万。", type="用户偏好", topic="房贷")
    check("同内容去重跳过", "跳过" in ret2, ret2)

    # T6 自动备份 + 自愈（独立小节：重置为种子值再验证，避免被前面合并覆盖）
    open(MEM, "w", encoding="utf-8").write(
        "# 记忆库\n## 2026-01-01 [用户偏好] #测试\n备份自愈种子值。\n")
    ms.append_memory("临时条目，用于触发快照。", type="用户偏好", topic="临时")
    bak_dir = ms._backup_dir()
    bak_files = os.listdir(bak_dir) if os.path.isdir(bak_dir) else []
    check("写入后产生快照", any(f.startswith("memory_") for f in bak_files), str(bak_files)[:120])

    # 故意写坏 memory.md（畸形双井号头）
    open(MEM, "w", encoding="utf-8").write("## ## 2026-08-05 畸形头\n坏数据\n")
    res = ms.repair_memory()
    restored = open(MEM, encoding="utf-8").read()
    check("自愈触发", res != "healthy", res)
    check("自愈恢复到最近合法备份(种子值)", "备份自愈种子值" in restored, restored[:80])
    check("自愈后无畸形头", "## ##" not in restored)
    check("自愈未混入损坏数据", "坏数据" not in restored and "畸形" not in restored)

    # 清理临时目录
    import shutil
    shutil.rmtree(td, ignore_errors=True)


# ===================== 上下文隔离 =====================
def test_context_isolation():
    print("== 上下文隔离 ==")
    tmp = tempfile.mkdtemp(prefix="ctxreg_")
    # 隔离：重定向 _user_data_dir 到临时目录，并清空实例缓存
    cm._user_data_dir = lambda: Path(tmp)
    cm._MGRS.clear()

    a = cm.get_context_manager("A")
    b = cm.get_context_manager("B")
    check("A/B 是不同实例", a is not b)

    a.add_message("user", "我喜欢吃苹果，记一下")
    b.add_message("user", "电力线路故障告警分析")

    check("A 收到 A 的消息", a.messages and a.messages[0]["content"] == "我喜欢吃苹果，记一下")
    check("B 收到 B 的消息", b.messages and b.messages[0]["content"] == "电力线路故障告警分析")

    # 落盘隔离：key_info_{sid}.json 各自独立，A 文件不含 B 内容
    a_path = Path(tmp) / "key_info_A.json"
    b_path = Path(tmp) / "key_info_B.json"
    check("A 落盘文件存在", a_path.exists())
    check("B 落盘文件存在", b_path.exists())
    if a_path.exists():
        check("A 文件不含 B 的对话内容(不串台)", "电力线路故障告警分析" not in a_path.read_text(encoding="utf-8"))
    # 缓存一致性 + 隔离：同 sid 返回同一实例，不同 sid 互不干扰
    check("同 sid 返回同一实例(缓存一致)", cm.get_context_manager("A") is a)
    check("B 与 A 缓存隔离", cm.get_context_manager("B") is b)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


# ===================== 记忆加密 =====================
def test_encryption():
    print("== 记忆加密 ==")
    import shutil
    td = tempfile.mkdtemp(prefix="memenc_")
    ms._configure(td)
    ms.set_encryption("secret-pass")
    check("加密已启用", ms.encryption_enabled())

    # 写入钉住 + 记忆（走加密）
    ms.append_pinned("核心画像：大哥 xyb，昆明。")
    ms.append_memory("房贷已还清。", type="用户偏好", topic="房贷")

    MEM = ms.MEMORY_PATH
    CORE = ms.PINNED_PATH
    DB = ms.MEMORY_DB_PATH
    # 磁盘上应为 .enc，明文不应残留
    check("memory.md.enc 存在", os.path.exists(MEM + ".enc"))
    check("memory_core.md.enc 存在", os.path.exists(CORE + ".enc"))
    check("memory.db.enc 存在", os.path.exists(DB + ".enc"))
    check("明文 memory.md 不落地", not os.path.exists(MEM))
    check("明文 memory_core.md 不落地", not os.path.exists(CORE))
    check("明文 memory.db 不落地", not os.path.exists(DB))

    # 读取解密正常
    check("钉住画像可解密读出", "xyb" in ms.load_pinned())
    check("记忆可解密读出", "房贷已还清" in ms.load_memory())
    check("召回可解密", "房贷已还清" in ms.recall_memory("房贷"))
    hits = ms.search_memory("房贷")
    check("加密下中文搜索可用", any("房贷已还清" in h["text"] for h in hits))

    # 错口令读不出（解密失败返回空，不崩）
    ms.set_encryption("wrong-pass")
    check("错口令读不到钉住(返回空)", ms.load_pinned() == "")
    check("错口令读不到记忆(返回空)", ms.load_memory() == "")
    # 还原正确口令仍可解密
    ms.set_encryption("secret-pass")
    check("还原口令后可再解密", "房贷已还清" in ms.load_memory())

    # decrypt_existing 迁移回明文
    ms.decrypt_existing()
    check("关闭加密后明文落地", os.path.exists(MEM) and not os.path.exists(MEM + ".enc"))
    check("关闭后内容完整", "房贷已还清" in ms.load_memory())

    ms.set_encryption(None)  # 复位，避免污染其他测试
    shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    try:
        test_memory_layer()
        test_context_isolation()
        test_encryption()
    except Exception:
        FAIL += 1
        traceback.print_exc()
    print(f"\n结果：PASS={PASS}  FAIL={FAIL}")
    sys.exit(1 if FAIL else 0)
