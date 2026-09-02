# -*- coding: utf-8 -*-
"""小臭玩AI — 跨对话长期记忆库（用户画像 / 偏好 / 约定 / 项目状态）

记忆文件放在用户文档目录（~/Documents/小臭玩AI/memory.md），
刻意避开程序目录（dist/小臭玩AI/），因为重建会清空整个程序目录，
放在文档目录可保证记忆在多次重打包间持久保留。

v2（2026-07-23）改进：
- load_recent(limit_chars) 返回末尾 N 字符（新记忆优先注入，修旧版取文件开头
  导致新记忆被截断丢弃的 bug）
- append 前去重（避免同类事实反复堆积）
- append 后滚动淘汰（保留最近 MAX_ENTRIES 条，防止无限臃肿；
  不搞"30天定时清理"，因为时间过期会误删永久画像，滚动淘汰更安全）
- 关键路径 except 补 log，便于排查
- v4.59 SQLite + FTS5 全文搜索层（与 markdown 文件并联，搜索走 DB，写入双写）

"""
import os
import re
import shutil
import logging
import sqlite3
import threading
import functools
from datetime import datetime

log = logging.getLogger(__name__)

# 记忆目录刻意放在用户文档下，不受程序重建影响
MEMORY_DIR = os.path.expanduser("~/Documents/小臭玩AI")
MEMORY_PATH = os.path.join(MEMORY_DIR, "memory.md")
MEMORY_DB_PATH = os.path.join(MEMORY_DIR, "memory.db")  # v4.59 SQLite FTS5 搜索库
# v4.73：钉住核心画像（永远注入，不滚出、不依赖召回命中）——解决"早期重要记忆被滚动淘汰冲掉"
PINNED_PATH = os.path.join(MEMORY_DIR, "memory_core.md")

# ---- v4.74：路径可配置（测试隔离用，生产绝不调用） + 自动备份/自愈 ----
MAX_BACKUPS = 20  # 轮转保留快照份数（每份含 memory/core/db 三文件）

def _configure(base_dir):
    """测试/隔离用：把全部记忆路径重定向到 base_dir。生产环境不调用，默认走文档目录。

    这是我上一轮『误污染真实记忆文件』事故的根因修复：路径常量此前在 import 时固化，
    测试只改 MEMORY_DIR 无效，写入落到了真实文件。改为可重配置后，测试先 _configure
    到临时目录即可彻底隔离，绝不碰真实数据。
    """
    global MEMORY_DIR, MEMORY_PATH, MEMORY_DB_PATH, PINNED_PATH, _DB_READY
    MEMORY_DIR = os.path.abspath(base_dir)
    MEMORY_PATH = os.path.join(MEMORY_DIR, "memory.md")
    MEMORY_DB_PATH = os.path.join(MEMORY_DIR, "memory.db")
    PINNED_PATH = os.path.join(MEMORY_DIR, "memory_core.md")
    _DB_READY = False


# ---- v4.75：可选加密存储（过掉"敢交重要资料"最后一道明文坎） ----
# 默认关闭（_cipher=None），行为与旧版完全一致，回归测试不受影响。
# 启用后：markdown 记忆与 SQLite 索引在磁盘上以 Fernet 加密（.enc）存放，
# 明文文件不落地；口令经 PBKDF2 派生密钥，salt 存于 MEMORY_DIR/memory.salt。
_cipher = None
_SALT_PATH = None  # 延迟按 MEMORY_DIR 计算

# v4.108 M-21：跨线程互斥锁——Agent worker 线程与 UI 线程并发 append/trim/_with_db，
# 无锁时 tmp 文件互踩、加密 DB 竞态丢数据。RLock 可重入，允许同线程嵌套调用。
_LOCK = threading.RLock()


def _sync(fn):
    """把写函数包进全局互斥锁。"""
    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return _wrapper


def _salt_path():
    global _SALT_PATH
    if _SALT_PATH is None:
        _SALT_PATH = os.path.join(MEMORY_DIR, "memory.salt")
    return _SALT_PATH


def set_encryption(passphrase):
    """设置/清除加密口令。passphrase 为空或 None → 关闭加密（_cipher=None）。

    密钥 = PBKDF2(passphrase, salt)，salt 首次启用时随机生成并落盘（salt 非 secret）。
    """
    global _cipher
    if not passphrase:
        _cipher = None
        return
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        import base64
        sp = _salt_path()
        if os.path.exists(sp):
            with open(sp, "rb") as f:
                salt = f.read()
        else:
            salt = os.urandom(16)
            with open(sp, "wb") as f:
                f.write(salt)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                         salt=salt, iterations=200_000)
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        _cipher = Fernet(key)
    except Exception as e:
        log.warning("设置加密失败: %s", e)
        _cipher = None


def encryption_enabled():
    return _cipher is not None


def _physical(path):
    """返回该逻辑路径在磁盘上的实际文件（加密模式为 .enc）。"""
    if _cipher is not None:
        return path + ".enc"
    return path


def _read_text_file(path):
    """读文本文件：加密模式自动解密，否则明文（兼容旧明文文件）。"""
    if _cipher is not None:
        ep = path + ".enc"
        if os.path.exists(ep):
            try:
                return _cipher.decrypt(open(ep, "rb").read()).decode("utf-8")
            except Exception as e:
                log.warning("记忆文件解密失败: %s", e)
                return ""
        if os.path.exists(path):  # 加密模式但尚无 .enc（首次迁移场景）
            try:
                return open(path, "r", encoding="utf-8").read()
            except Exception:
                return ""
        return ""
    if os.path.exists(path):
        try:
            return open(path, "r", encoding="utf-8").read()
        except Exception:
            return ""
    return ""


def _write_text_file(path, text):
    """原子写文本文件；加密模式写 .enc 并清除明文残留。"""
    if _cipher is not None:
        ep = path + ".enc"
        data = _cipher.encrypt(text.encode("utf-8"))
        tmp = ep + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, ep)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    else:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)


def encrypt_existing(passphrase):
    """启用加密：把当前明文记忆转为加密（先读明文→设 cipher→写加密）。"""
    mem = _read_text_file(MEMORY_PATH)
    pinned = _read_text_file(PINNED_PATH)
    set_encryption(passphrase)
    if mem:
        _write_text_file(MEMORY_PATH, mem)
    if pinned:
        _write_text_file(PINNED_PATH, pinned)
    if os.path.exists(MEMORY_DB_PATH):
        try:
            _with_db(lambda c: None)
        except Exception:
            pass
    for p in (MEMORY_PATH, PINNED_PATH, MEMORY_DB_PATH):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


def decrypt_existing():
    """关闭加密：把加密记忆转回明文。"""
    global _cipher
    mem = _read_text_file(MEMORY_PATH)
    pinned = _read_text_file(PINNED_PATH)
    _cipher = None
    if mem:
        _write_text_file(MEMORY_PATH, mem)
    if pinned:
        _write_text_file(PINNED_PATH, pinned)
    if os.path.exists(MEMORY_DB_PATH + ".enc"):
        try:
            _with_db(lambda c: None)
        except Exception:
            pass
    for p in (MEMORY_PATH + ".enc", PINNED_PATH + ".enc", MEMORY_DB_PATH + ".enc"):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


def _backup_dir():
    return os.path.join(MEMORY_DIR, "backups")


def _snapshot_memory(tag=""):
    """关键写入前轮转快照 memory.md / memory_core.md / memory.db（保留最近 MAX_BACKUPS 份）。

    这是『敢交重要资料』的底线保障：单文件损坏/误写也不至于记忆全失，可一键回滚。
    """
    try:
        bd = _backup_dir()
        os.makedirs(bd, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        suf = f"_{tag}" if tag else ""
        for src, nm in ((MEMORY_PATH, "memory"), (PINNED_PATH, "core"),
                        (MEMORY_DB_PATH, "db")):
            phys = _physical(src)
            if os.path.exists(phys):
                ext = os.path.splitext(phys)[1]
                shutil.copy2(phys, os.path.join(bd, f"{nm}_{ts}{suf}{ext}"))
        files = sorted(os.listdir(bd))
        while len(files) > MAX_BACKUPS * 3:
            os.remove(os.path.join(bd, files[0]))
            files = files[1:]
    except Exception as e:
        log.warning("记忆快照失败（不影响主流程）: %s", e)


def _latest_backups():
    """返回 {'memory','core','db': 最新一份备份路径 或 None}。"""
    bd = _backup_dir()
    if not os.path.isdir(bd):
        return {"memory": None, "core": None, "db": None}
    mem = core = db = None
    for f in sorted(os.listdir(bd), reverse=True):
        if mem and core and db:
            break
        if f.startswith("memory_") and mem is None:
            mem = os.path.join(bd, f)
        elif f.startswith("core_") and core is None:
            core = os.path.join(bd, f)
        elif f.startswith("db_") and db is None:
            db = os.path.join(bd, f)
    return {"memory": mem, "core": core, "db": db}


def repair_memory():
    """自检 + 自愈：memory.md 缺失/含畸形双井号头则从最新备份恢复；memory.db 损坏由搜索层自动重建。

    返回动作说明字符串（供日志/诊断）。幂等，可在启动与每次加载前安全调用。
    """
    ensure_dir()
    try:
        need_repair = False
        phys = _physical(MEMORY_PATH)
        if not os.path.exists(phys):
            need_repair = True
        else:
            txt = _read_text_file(MEMORY_PATH)
            if re.search(r"## ## \d", txt):  # 畸形双井号头（旧 bug 残留）
                need_repair = True
        if need_repair:
            bk = _latest_backups().get("memory")
            if bk and os.path.exists(bk):
                shutil.copy2(bk, phys)
                for key, dst in (("core", PINNED_PATH), ("db", MEMORY_DB_PATH)):
                    cbk = _latest_backups().get(key)
                    if cbk and not os.path.exists(_physical(dst)):
                        shutil.copy2(cbk, _physical(dst))
                log.warning("记忆文件已自愈恢复: %s", bk)
                return "已从备份恢复 memory.md"
    except Exception as e:
        log.warning("记忆自愈失败（将尝试从空状态重建）: %s", e)
    return "healthy"


# 滚动淘汰阈值：超过则删最老的，保留最近这么多条
MAX_ENTRIES = 200

# v4.73：DB 初始化一次性标记（避免每次 append 都连库建表）
_DB_READY = False


def _create_tables(conn):
    """v4.59：在已打开的 conn 上初始化 SQLite + FTS5 全文搜索表结构。"""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    # FTS5 虚拟表用于全文搜索
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content, content='memories', content_rowid='id'
        )
    """)
    # 触发器：保持 FTS 与主表同步
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.id, old.content);
        END
    """)
    conn.commit()


@_sync  # v4.108 M-21：DB 解密→执行→加密整段临界区加锁
def _with_db(fn):
    """解密 db→执行 fn(conn)→关闭并重新加密 db（加密模式）。fn 内需 commit。

    v4.75：让 SQLite 索引也走加密存储——加密模式下磁盘上是 memory.db.enc，
    每次操作解密到工作文件、用完即重新加密并清除明文残留（含 -wal/-shm）。
    """
    if _cipher is not None:
        ep = MEMORY_DB_PATH + ".enc"
        if os.path.exists(ep) and not os.path.exists(MEMORY_DB_PATH):
            try:
                open(MEMORY_DB_PATH, "wb").write(_cipher.decrypt(open(ep, "rb").read()))
            except Exception as e:
                log.warning("记忆DB解密失败: %s", e)
    try:
        conn = sqlite3.connect(MEMORY_DB_PATH)
        return fn(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass
        if _cipher is not None and os.path.exists(MEMORY_DB_PATH):
            try:
                ep = MEMORY_DB_PATH + ".enc"
                data = _cipher.encrypt(open(MEMORY_DB_PATH, "rb").read())
                tmp = ep + ".tmp"
                open(tmp, "wb").write(data)
                os.replace(tmp, ep)
                for ext in ("", "-wal", "-shm"):
                    p = MEMORY_DB_PATH + ext
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
            except Exception as e:
                log.warning("记忆DB加密失败: %s", e)


def _init_db():
    """v4.59：初始化 SQLite + FTS5 全文搜索（兼容旧调用，内部走 _with_db）。"""
    ensure_dir()
    _with_db(_create_tables)


def _sync_md_to_db():
    """v4.59：首次启动时将 memory.md 现有内容同步到 SQLite（幂等）。"""
    def _f(conn):
        db_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if db_count > 0:
            return
        txt = _read_text_file(MEMORY_PATH)
        if not txt:
            return
        # 按 ## 拆分条目
        entries = [e.strip() for e in txt.split("\n## ") if e.strip()]
        for e in entries:
            lines = e.split("\n", 1)
            ts = lines[0].strip() if lines else datetime.now().strftime("%Y-%m-%d %H:%M")
            content = lines[1].strip() if len(lines) > 1 else lines[0].strip()
            conn.execute("INSERT INTO memories (content, created_at) VALUES (?, ?)", (content, ts))
        conn.commit()
        log.info("记忆同步到 SQLite：%d 条", len(entries))
    try:
        _with_db(_f)
    except Exception as e:
        log.warning("同步SQLite失败: %s", e)


def ensure_dir():
    try:
        os.makedirs(MEMORY_DIR, exist_ok=True)
    except Exception as e:
        log.warning("创建记忆目录失败: %s", e)


def load_memory():
    """读取全部长期记忆（Markdown 文本），无则返回空串。"""
    ensure_dir()
    return _read_text_file(MEMORY_PATH).strip()


def load_recent(limit_chars=4000):
    """返回最近的记忆（末尾 limit_chars 字符），新记忆优先注入。

    解决旧版 load_memory()+[:4000] 取文件开头导致新记忆被截断丢弃的问题：
    记忆是追加式（新在末尾），取开头等于总是注入最老的、丢最新的。
    尽量按条目边界（## 标题行）切分，避免截断单条记忆。
    """
    txt = load_memory()
    if not txt or len(txt) <= limit_chars:
        return txt
    tail = txt[-limit_chars:]
    # 截到第一个条目标题行，避免半条记忆
    idx = tail.find("\n## ")
    if 0 < idx < len(tail) // 2:
        tail = tail[idx + 1:]
    return tail.strip()


def _is_duplicate(fact, existing_txt):
    """简单去重：fact 完全包含在已有文本中，或前 20 字符已出现，视为重复。"""
    fl = (fact or "").lower().strip()
    if not fl:
        return False
    low = existing_txt.lower()
    if fl in low:
        return True
    if len(fl) > 15 and fl[:20] in low:
        return True
    return False


def _trim_oldest(max_entries):
    """超过 max_entries 条时，删最老的，保留最近 max_entries 条。"""
    if not os.path.exists(_physical(MEMORY_PATH)):
        return
    try:
        txt = _read_text_file(MEMORY_PATH)
    except Exception as e:
        log.warning("淘汰读取失败: %s", e)
        return
    if "\n## " not in txt:
        return
    parts = txt.split("\n## ")
    if len(parts) <= max_entries:
        return
    # parts[0] 可能是文件头（空或非条目内容），保留；其余取最后 max_entries 条
    head = parts[0]
    keep = parts[-max_entries:]
    if head and not head.startswith("## "):
        new_txt = head.rstrip() + "\n\n## " + "\n## ".join(keep)
    else:
        new_txt = "## " + "\n## ".join(keep)
    try:
        _write_text_file(MEMORY_PATH, new_txt + "\n")
        log.info("记忆滚动淘汰：%d 条 -> %d 条", len(parts), max_entries)
    except Exception as e:
        log.warning("淘汰写入失败: %s", e)


@_sync  # v4.108 M-21：跨线程写加锁
def clear_memory():
    """清空全部长期记忆，返回是否成功。清空前先快照（可回滚）。"""
    ensure_dir()
    _snapshot_memory("clear")
    try:
        phys = _physical(MEMORY_PATH)
        if os.path.exists(phys):
            os.remove(phys)
        return True
    except Exception as e:
        log.warning("清空记忆失败: %s", e)
        return False


def memory_stats():
    """返回 (条目数, 字节数)。"""
    ensure_dir()
    txt = _read_text_file(MEMORY_PATH)
    if not txt:
        return (0, 0)
    entries = txt.count("\n## ")
    return (entries, len(txt.encode("utf-8")))


# ============ v4.73 钉住核心画像 + 语义（关键词）召回 ============

def load_pinned():
    """读取钉住的核心画像（memory_core.md），永远注入系统提示。无则返回空串。"""
    ensure_dir()
    return _read_text_file(PINNED_PATH).strip()


@_sync  # v4.108 M-21：跨线程写加锁
def append_pinned(fact, type=None, tags=None):
    """追加一条钉住核心画像（写入 memory_core.md，去重，永远注入）。"""
    fact = (fact or "").strip()
    if not fact:
        return "核心画像内容为空，未写入"
    ensure_dir()
    _snapshot_memory("pinned")
    existing = load_pinned()
    if _is_duplicate(fact, existing):
        return "核心画像已存在，跳过"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"## {ts}"
    if type:
        header += f" [{type}]"
    if tags:
        header += " " + " ".join("#" + t.lstrip("#") for t in tags)
    block = f"\n{header}\n{fact}\n"
    try:
        _write_text_file(PINNED_PATH, _read_text_file(PINNED_PATH) + block)
    except Exception as e:
        log.warning("写入核心画像失败: %s", e)
        return f"写入核心画像失败：{e}"
    return f"已钉住核心画像：{fact[:60] + '…' if len(fact) > 60 else fact}"


def _split_entries(txt):
    """把 memory.md 拆成 [(timestamp, content), ...]，去掉空段与过长文件头说明。"""
    if not txt:
        return []
    parts = txt.split("\n## ")
    entries = []
    for i, p in enumerate(parts):
        p = p.strip()
        if not p:
            continue
        if i == 0 and not p.startswith("##"):
            # 文件头（非条目），若过长是说明文字则跳过
            if len(p) > 200:
                continue
            entries.append(("", p))
            continue
        lines = p.split("\n", 1)
        ts = lines[0].strip()
        content = lines[1].strip() if len(lines) > 1 else ts
        entries.append((ts, content))
    return entries


# 中文常见停用词（召回时过滤，避免"的/了/是"等单字把无关条目拉进来）
_STOPWORDS = set(
    "的 了 是 在 我 你 他 她 它 这 那 不 都 就 也 啊 吗 呢 吧 和 与 及 "
    "个 们 把 被 让 给 从 到 对 于 而 则 或 若 如 要 会 能 可 该 没 有 "
    "为 以 之 其 此 等 并 但 却 越 更 最 上 下 中 内 外 前 后 里 边 用 去 来 着 过".split()
)


def _tokenize(text):
    """中文按字、英文/数字按词切分，小写，并过滤停用词。"""
    text = (text or "").lower()
    toks = re.findall(r"[a-z0-9]+|[一-鿿]", text)
    return [t for t in toks if t not in _STOPWORDS]


def recall_memory(query, limit=8, max_chars=4000):
    """v4.73：语义（关键词）召回长期记忆，替代旧版尾部平铺注入。

    返回：[钉住核心画像] + [相关记忆 top-k]。永远可用（纯 Python，不依赖 FTS5/网络）。
    - 钉住核心画像：来自 memory_core.md，无条件注入（核心身份/约定永不丢失）。
    - 相关记忆：用 query 与每条记忆做关键词重叠打分，取 top-k。
    - query 为空或无命中：仅返回钉住核心画像。
    """
    pinned = load_pinned().strip()
    full = load_memory()
    # 无查询：回退到尾部优先（兼容首次/空上下文），核心画像仍优先
    if not query or not query.strip():
        tail = load_recent(max_chars) if full else ""
        if pinned and tail:
            return "【核心画像】\n" + pinned + "\n\n" + tail
        return ("【核心画像】\n" + pinned) if pinned else tail
    if not full and not pinned:
        return ""
    entries = _split_entries(full)
    q_set = set(_tokenize(query))
    if q_set:
        scored = []
        for ts, content in entries:
            e_set = set(_tokenize(content))
            if not e_set:
                continue
            overlap = q_set & e_set
            if not overlap:
                continue
            # 得分：重叠词数 / sqrt(条目长度)，避免长条目虚高
            score = len(overlap) / max(1.0, len(e_set) ** 0.5)
            scored.append((score, content))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [c for _, c in scored[:limit]]
    else:
        top = []
    parts = []
    if pinned:
        parts.append("【核心画像】\n" + pinned)
    if top:
        parts.append("【相关记忆】\n" + "\n\n".join(top))
    out = "\n\n".join(parts)
    if len(out) > max_chars:
        out = out[:max_chars]
    return out


@_sync  # v4.108 M-21：跨线程写加锁
def _write_memory(text):
    """原子写回整份 memory.md（加密感知）。"""
    _write_text_file(MEMORY_PATH, text)


def _replace_by_topic(topic, fact, type=None, tags=None):
    """在 memory.md 中找到同 topic 的旧条目并替换，返回新全文；找不到返回 None。

    v4.73 修：替换后的新条目头部持久化 `#topic` 标签，否则后续同主题写入会因
    关键字丢失而退化为"追加"，导致新旧并存。匹配时也认 `#topic` 标签。
    """
    if not os.path.exists(_physical(MEMORY_PATH)):
        return None
    try:
        txt = _read_text_file(MEMORY_PATH)
    except Exception:
        return None
    parts = txt.split("\n## ")
    replaced = False
    new_parts = []
    topic_l = topic.lower()
    topic_tag = f"#{topic_l}"
    for i, p in enumerate(parts):
        if i == 0:
            new_parts.append(p)
            continue
        # 命中：带 #topic 标签，或正文前段含 topic 关键词
        if topic_tag in p.lower() or topic_l in p[:140].lower():
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            # 注意：non-title 段经 "\n## ".join 会自动补 "## "，这里头部不加 "## "，
            # 否则重建后会变成 "## ## 时间"（双井号）畸形头。
            header = f"{ts}"
            if type:
                header += f" [{type}]"
            header += f" #{topic}"
            if tags:
                header += " " + " ".join("#" + t.lstrip("#") for t in tags)
            new_parts.append(f"{header}\n{fact}")
            replaced = True
        else:
            new_parts.append(p)
    if not replaced:
        return None
    return "\n## ".join(new_parts)


def _db_replace_by_topic(topic, fact):
    """同步替换 FTS5 中同主题旧条目（删含 topic 的行 + 插入新行）。v4.75 走 _with_db。"""
    def _f(conn):
        conn.execute("DELETE FROM memories WHERE lower(content) LIKE ?",
                     ("%" + topic.lower() + "%",))
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn.execute("INSERT INTO memories (content, created_at) VALUES (?, ?)", (fact, ts))
        conn.commit()
    try:
        if not _ensure_db():
            return
        _with_db(_f)
    except Exception:
        pass


@_sync  # v4.108 M-21：跨线程写加锁
def append_memory(fact, type=None, topic=None, tags=None, pinned=False):
    """v4.73：追加/更新一条长期记忆。

    - pinned=True：写入 memory_core.md（钉住核心画像，永远注入）。
    - topic 给定：若已有同主题旧条目则替换（冲突合并，防新旧并存），否则追加。
    - 结构化头：## 时间 [类型] #标签 事实
    - 去重：与已有条目重复则跳过；超 MAX_ENTRIES 滚动淘汰。
    """
    fact = (fact or "").strip()
    if not fact:
        return "记忆内容为空，未写入"
    if pinned:
        return append_pinned(fact, type=type, tags=tags)
    ensure_dir()
    _snapshot_memory("append")
    existing = load_memory()
    if _is_duplicate(fact, existing):
        return "记忆已存在，跳过"
    # 冲突合并：同 topic 旧条目替换
    if topic:
        new_txt = _replace_by_topic(topic, fact, type=type, tags=tags)
        if new_txt is not None:
            try:
                _write_memory(new_txt)
            except Exception as e:
                log.warning("记忆覆盖写回失败: %s", e)
                return f"记忆覆盖失败：{e}"
            _db_replace_by_topic(topic, fact)
            preview = fact if len(fact) <= 60 else fact[:60] + "…"
            return f"已更新长期记忆（覆盖同主题旧条目）：{preview}"
    # 普通追加
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"## {ts}"
    if type:
        header += f" [{type}]"
    if topic:
        header += f" #{topic}"
    if tags:
        header += " " + " ".join("#" + t.lstrip("#") for t in tags)
    block = f"\n{header}\n{fact}\n"
    try:
        _write_text_file(MEMORY_PATH, _read_text_file(MEMORY_PATH) + block)
        _db_append(fact, ts)
    except Exception as e:
        log.warning("写入记忆失败: %s", e)
        return f"写入记忆失败：{e}"
    _trim_oldest(MAX_ENTRIES)
    preview = fact if len(fact) <= 60 else fact[:60] + "…"
    return f"已写入长期记忆：{preview}"


# ============ v4.59 SQLite FTS5 搜索层 ============

def _ensure_db():
    """v4.73：惰性初始化 SQLite FTS5（只建一次）。失败则降级（搜索层不可用，但主流程不受影响）。"""
    global _DB_READY
    if _DB_READY:
        return True
    try:
        _with_db(_create_tables)
        _DB_READY = True
        return True
    except Exception as e:
        log.warning("记忆 DB 初始化失败，搜索层降级: %s", e)
        return False


def _db_append(fact, ts):
    """追加一条到 SQLite FTS5 索引（不影响主 markdown 流程）。v4.75 修：先确保 DB 已建表，走 _with_db。"""
    def _f(conn):
        conn.execute("INSERT INTO memories (content, created_at) VALUES (?, ?)", (fact, ts))
        conn.commit()
    try:
        if not _ensure_db():
            return
        _with_db(_f)
    except Exception:
        pass  # 搜索层失败不影响主流程


def search_memory(query, limit=5):
    """v4.59/4.73：全文搜索长期记忆，返回匹配条目列表。

    双路检索：
    1) FTS5 MATCH：适合英文/数字短语（拉丁分词准），保留原 snippet 逻辑；
    2) Python 兜底子串扫描：中文必需——FTS5 默认 unicode61 分词器不按汉字切分，
       '房贷' 匹配不到 '房贷已还清' 会恒返 0。兜底走子串扫描保证中文能召回。
    两者合并去重后返回（FTS5 命中优先）。
    """
    _sync_md_to_db()
    results = []
    seen = set()

    def _snip(content, q):
        idx = content.lower().find(q.lower())
        if idx >= 0:
            start = max(0, idx - 40)
            end = min(len(content), idx + len(q) + 40)
            sn = content[start:end]
            if start > 0:
                sn = "…" + sn
            if end < len(content):
                sn += "…"
            return sn
        return content[:120]

    # 1) FTS5（拉丁/数字短语）
    def _fts(conn):
        safe_q = '"' + query.replace('"', '""') + '"'
        return conn.execute(
            "SELECT m.content, m.created_at FROM memories_fts "
            "JOIN memories m ON m.id = memories_fts.rowid "
            "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
            (safe_q, limit)
        ).fetchall()
    try:
        rows = _with_db(_fts) or []
        for content, ts in rows:
            if content in seen:
                continue
            seen.add(content)
            results.append({"text": content, "snippet": _snip(content, query), "time": ts})
    except Exception as e:
        log.warning("FTS5 搜索失败: %s", e)

    # 2) 中文/兜底子串扫描（FTS5 为空或查询含汉字时启用）
    has_cjk = any('一' <= ch <= '鿿' for ch in (query or ""))
    if (not results or has_cjk) and query and query.strip():
        q = query.lower().strip()
        for ts, content in _split_entries(load_memory()):
            if not content:
                continue
            if q in content.lower():
                if content in seen:
                    continue
                seen.add(content)
                results.append({"text": content, "snippet": _snip(content, query), "time": ts})

    return results[:limit]
