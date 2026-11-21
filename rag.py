# -*- coding: utf-8 -*-
"""
本地 RAG 知识库：轻量 numpy 向量存储 + OpenAI 兼容 embedding API。
零重型依赖（无需 chromadb / PyTorch / sentence-transformers）。
"""
import json
import logging
import os
import subprocess  # v4.125 M-14：子进程统一无黑窗
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
import time
import urllib.request
from datetime import datetime
from pathlib import Path

log = logging.getLogger("rag")

# 单文件索引上限
MAX_INDEX_BYTES = 3_000_000    # ~3MB
MAX_INDEX_CHARS = 1_500_000    # ~1.5MB


# v4.319.0 P1-1：chunk 溯源四件套。纯函数，便于判据直测与旧索引归一。
# 一个事实一个家：entity_id 默认 source 文件名，indexed_at 写入时打 ISO8601，
# source_mtime 取源文件 mtime（拿不到留空串，不炸）。已带字段的 chunk 原样保留
# （幂等，不覆盖真实溯源）——旧版 index.json 缺字段时按默认值补齐，向后兼容。
def normalize_chunk(chunk):
    """给单个 chunk 补溯源四字段；非 dict 输入也能安全归一，不抛异常。"""
    if not isinstance(chunk, dict):
        return {"type": "doc", "entity_id": "", "indexed_at": "",
                "source_mtime": "", "text": str(chunk) if chunk else ""}
    out = dict(chunk)
    src = out.get("source") or ""
    if not out.get("type"):
        out["type"] = "doc"
    if not out.get("entity_id"):
        out["entity_id"] = os.path.basename(str(src)) if src else ""
    if not out.get("indexed_at"):
        out["indexed_at"] = datetime.now().isoformat(timespec="seconds")
    if "source_mtime" not in out:
        mtime = ""
        if src:
            try:
                mtime = int(Path(src).stat().st_mtime)
            except Exception:
                mtime = ""
        out["source_mtime"] = mtime
    return out


# ===== v4.320.0 P1-2：BM25 关键词通路 + RRF 融合（离线兜底）=====
# 设计原则：jieba 可用则用真分词，不可用/打包漏词典自动退回单字切分——
# 检索永远不因分词依赖缺失而整体失效（大哥要求"装不上退回单字兜底"）。
ENABLE_BM25_FALLBACK = True      # 关掉后仍保留 RRF 融合，只是 embedding 失败不再兜底
_BM25_K1 = 1.5
_BM25_B = 0.75
_RRF_K = 60                      # Reciprocal Rank Fusion 平滑常数

# 中文查询同义/近义变体（多措辞设计：一个概念多种说法一起检索，≤6）。
# 只做通用同义映射，不依赖模型/网络；未在表内的词原样返回（不臆造）。
_SYNONYM_MAP = {
    "电脑": ["电脑", "计算机", "pc"],
    "计算机": ["计算机", "电脑", "pc"],
    "手机": ["手机", "移动电话", "电话"],
    "ai": ["ai", "人工智能", "智能"],
    "人工智能": ["人工智能", "ai", "智能"],
    "汽车": ["汽车", "车子", "车辆"],
    "电影": ["电影", "影片", "片子"],
    "旅游": ["旅游", "旅行", "游玩"],
    "路线": ["路线", "线路", "行程"],
    "价格": ["价格", "价钱", "报价"],
}


def _tokenize_for_bm25(text):
    """分词：jieba 优先，失败退回单字/英文按词（永不抛异常）。"""
    text = (text or "").lower()
    toks = []
    try:
        import jieba
        toks = [t.strip() for t in jieba.cut(text) if t and t.strip()]
        toks = [t for t in toks if not t.isspace()]
    except Exception:
        # 单字兜底：中文逐字，英文/数字按词
        import re as _re
        toks = _re.findall(r"[a-z0-9]+|[一-鿿]", text)
    return [t for t in toks if t]


def _synonym_variants(query):
    """返回查询的同义变体（≤6，原查询永远在第一位）。"""
    q = (query or "").strip()
    if not q:
        return []
    out = [q]
    key = q.lower()
    for extra in _SYNONYM_MAP.get(key, []):
        if extra not in out:
            out.append(extra)
        if len(out) >= 6:
            break
    return out[:6]


def _bm25_scores(query, docs):
    """BM25 打分：query 为 token 列表，docs 为 token 列表的列表；返回分数列表。"""
    if not docs:
        return []
    tokenized = [q if isinstance(q, (list, tuple)) else _tokenize_for_bm25(q) for q in ([query] if isinstance(query, str) else query)]
    q_tokens = tokenized[0] if tokenized else []
    avgdl = max(1.0, sum(len(d) for d in docs) / len(docs))
    df = {}
    for tok in set(q_tokens):
        df[tok] = sum(1 for d in docs if tok in d)
    scores = []
    for d in docs:
        dl = max(1, len(d))
        s = 0.0
        for tok in q_tokens:
            tf = d.count(tok)
            if not tf:
                continue
            n_q = df.get(tok, 0)
            idf = _math_log(1.0 + (len(docs) - n_q + 0.5) / (n_q + 0.5))
            denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl)
            s += idf * (tf * (_BM25_K1 + 1)) / denom
        scores.append(s)
    return scores


def _math_log(x):
    """避免顶层 import math（保持 rag 零重依赖），惰性用标准库。"""
    import math
    return math.log(max(1e-12, x))


def _normalize_scores(values):
    """分数归一化到 [0,1]，全相等/全 0 时返回等分（不炸、不产生 NaN）。"""
    vals = [float(v) for v in (values or [])]
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return [1.0] * len(vals)
    return [(v - lo) / (hi - lo) for v in vals]


def _rrf_fuse(rank_lists, k=_RRF_K):
    """RRF 融合多个排名列表（元素须可哈希），按共识名次降序，稳定去重。"""
    scores = {}
    for rank in rank_lists or []:
        for pos, item in enumerate(rank):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + pos + 1)
    # 分数降序；同分按首次出现顺序（dict 保序）
    return sorted(scores, key=lambda x: -scores[x])


def plan_chunk_delta(old_chunks, new_texts, source):
    """v4.320.0 P2-1：按文本内容幂等规划增量，返回 {reused, to_embed, removed}。

    - reused   : {new_text 的下标: 旧 chunk} —— 文本逐字相同，原样复用 id+embedding
    - to_embed : [new_text 的下标] —— 新增/变更段，需重新 embed
    - removed  : [旧 chunk] —— 新切分里已消失的段，对应 chunk 清理

    只比文本 content，不比位置/ sliding-window 边界——因为固定滑窗下插入一段会让
    后续窗口全偏移，按文本 hash 幂等是唯一能"未变段 id+embedding 不动"的稳妥法。
    同文本重复出现时按出现次数一一对应复用，不重复发 id。
    """
    old_for_src = [c for c in (old_chunks or [])
                   if isinstance(c, dict) and c.get("source") == source]
    # 文本 -> 可复用旧 chunk 队列（保序）
    pool = {}
    for c in old_for_src:
        pool.setdefault(c.get("text", ""), []).append(c)
    reused = {}
    to_embed = []
    for i, t in enumerate(new_texts or []):
        bucket = pool.get(t, [])
        if bucket:
            reused[i] = bucket.pop(0)      # 一次复用一个，防重复 id
        else:
            to_embed.append(i)
    # 剩余未被复用的旧 chunk = 消失的段 → 清理
    removed = [c for bucket in pool.values() for c in bucket]
    return {"reused": reused, "to_embed": to_embed, "removed": removed}


class RAGStore:
    """本地向量知识库"""

    def __init__(self, store_dir, cfg=None):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.store_dir / "index.json"
        self._chunks: list[dict] = []   # [{id, source, text, embedding}]
        self._chunk_counter = 0          # chunk id 计数器（原依赖 _load_index 初始化，补进 __init__ 更健壮）
        self._files: dict = {}          # source -> {"mtime_ns": int, "size": int}（增量索引元数据）
        self._src_set: set = set()      # 已索引 source 集合（迁移兼容旧索引，无 files 元数据）
        self.collection = self           # 兼容旧接口引用
        self.client = self
        self.model = None
        self.openai_ef = None
        self.available = False
        self._cfg = cfg or {}
        self._api_key = ""
        self._api_base = ""
        self._api_model = ""

    def init(self, cfg=None):
        """初始化 embedding API。

        本地模型不再尝试（依赖 HuggingFace / PyTorch 太重），
        直接走 OpenAI 兼容 API（如 SiliconFlow BGE-M3）。
        """
        if cfg is None:
            cfg = self._cfg

        embedding_api_key = cfg.get("embedding_api_key", "")
        embedding_base_url = cfg.get("embedding_base_url", "https://api.siliconflow.cn/v1")
        embedding_model = cfg.get("embedding_model", "BAAI/bge-large-zh-v1.5")

        if not embedding_api_key:
            profiles = cfg.get("model_profiles", {})
            for name, prof in profiles.items():
                if "silicon" in name.lower() or "硅基" in name or "siliconflow" in name.lower():
                    embedding_api_key = prof.get("api_key", "")
                    if embedding_api_key:
                        log.info("RAG embedding 自动使用 %s 的 API key", name)
                        break

        if not embedding_api_key:
            log.warning("RAG embedding 不可用：无 API key")
            self.available = False
            return

        self._api_key = embedding_api_key
        self._api_base = embedding_base_url.rstrip("/")
        self._api_model = embedding_model

        # 加载已有索引
        self._load_index()
        self.available = True
        log.info("RAG embedding API 就绪: %s @ %s (%d chunks)", embedding_model, embedding_base_url, len(self._chunks))

    # ---------------------- 持久化 ----------------------

    def _load_index(self):
        if self._index_path.exists():
            try:
                with open(self._index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # v4.319.0 P1-1：旧索引缺溯源四字段 → 加载时补齐（幂等，不覆盖已有）。
                self._chunks = [normalize_chunk(c) for c in data.get("chunks", [])]
                self._chunk_counter = data.get("next_id", len(self._chunks))
                self._files = data.get("files", {})
            except Exception:
                self._chunks = []
                self._chunk_counter = 0
                self._files = {}
        else:
            self._chunks = []
            self._chunk_counter = 0
            self._files = {}
        self._src_set = {c.get("source") for c in self._chunks if c.get("source")}

    def _save_index(self):
        try:
            # v4.125 M-18：原子写——直接 open("w") 写一半崩溃会留截断 JSON，
            # 下次加载静默回空索引（config.py 的原子写没同步过来）。
            import os as _os
            # v4.134.2 修复：_index_path 是 Path（第 28 行 store_dir / "index.json"），
            # Path + str 会抛 TypeError（unsupported operand type(s) for +），
            # 被下面 except 吞成 warning → 索引**从未真正落盘**：每次重启都丢，
            # 重跑一遍 embedding（白花钱+白等）。必须 str() 后再拼。
            _tmp = str(self._index_path) + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump({"chunks": self._chunks, "next_id": self._chunk_counter,
                           "files": self._files}, f, ensure_ascii=False)
            _os.replace(_tmp, self._index_path)
            return True
        except Exception as e:
            log.warning("保存 RAG 索引失败: %s", e)
            return False

    # ---------------------- embedding API ----------------------

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """调用 OpenAI 兼容 embedding API，返回向量列表。"""
        payload = json.dumps({
            "model": self._api_model,
            "input": texts,
            "encoding_format": "float",
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._api_base}/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                embeddings = sorted(result["data"], key=lambda d: d["index"])
                return [d["embedding"] for d in embeddings]
            except Exception as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                else:
                    raise e

    # ---------------------- 索引 ----------------------

    def _chunk_text(self, text, chunk_size=500, overlap=50, max_chunks=5000):
        chunks = []
        if not text:
            return chunks
        chunk_size = max(50, int(chunk_size))
        overlap = max(0, min(int(overlap), chunk_size - 1))
        start = 0
        n = len(text)
        while start < n and len(chunks) < max_chunks:
            end = min(start + chunk_size, n)
            chunks.append(text[start:end])
            if end >= n:
                break
            start = end - overlap
        return chunks

    def index_file(self, file_path, force=False):
        """索引单个文件。

        force=False 时做增量判断：mtime 与 size 均未变则跳过（返回 None），
        避免每次启动对未修改文件重复调用 embedding API（省 token）。
        """
        try:
            p = Path(file_path)
            key = str(file_path)
            try:
                st = p.stat()
            except OSError:
                return f"无法读取文件状态: {p.name}"

            if st.st_size > MAX_INDEX_BYTES:
                return f"文件过大已跳过(>{MAX_INDEX_BYTES // 1_000_000}MB): {p.name}"

            # ---- 增量索引（v4.122.1）：未变化则跳过，不重烧 embedding token ----
            if not force:
                meta = self._files.get(key)
                if meta is not None:
                    if meta.get("mtime_ns") == st.st_mtime_ns and meta.get("size") == st.st_size:
                        return None
                elif key in self._src_set:
                    # 迁移兼容：旧索引无 files 元数据但已有 chunk —— 补记 mtime，不重 embedding
                    self._files[key] = {"mtime_ns": st.st_mtime_ns, "size": st.st_size}
                    self._save_index()
                    return None

            ext = p.suffix.lower()
            text = ""

            if ext in ('.txt', '.md', '.py', '.json', '.yaml', '.yml', '.csv', '.log'):
                try:
                    text = p.read_text(encoding='utf-8')
                except Exception:
                    try:
                        text = p.read_text(encoding='gbk')
                    except Exception:
                        return f"无法读取文件: {file_path}"
            elif ext == '.pdf':
                try:
                    import pdfplumber
                    with pdfplumber.open(file_path) as pdf:
                        text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
                except ImportError:
                    try:
                        from PyPDF2 import PdfReader
                        reader = PdfReader(file_path)
                        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
                    except ImportError:
                        return "需要安装 pdfplumber: pip install pdfplumber"
                    except Exception as e:
                        return f"PDF 解析失败 {file_path}: {e}"
                except Exception as e:
                    return f"PDF 解析失败 {file_path}: {e}"
            elif ext in ('.docx', '.doc'):
                try:
                    from docx import Document
                    doc = Document(file_path)
                    text = '\n'.join(par.text for par in doc.paragraphs)
                except ImportError:
                    return "需要安装 python-docx: pip install python-docx"
                except Exception as e:
                    return f"DOCX 解析失败 {file_path}: {e}"
            elif ext in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'):
                desc = self._vision_describe(file_path)
                if not desc:
                    return (f"图片理解需要硅基流动 key（在 config 的 model_profiles「硅基流动」中配置 api_key），"
                            f"或视觉模型不可用：{p.name}")
                text = f"[图片内容识别] {desc}"
            elif ext in ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'):
                try:
                    import voice
                    sf = self._sf_conf()
                    if not sf.get("api_key"):
                        return f"音频转写需要硅基流动 key：{p.name}"
                    a = voice.transcribe(file_path, sf) or ""
                    if not a.strip():
                        return f"音频转写失败或无语音：{p.name}"
                    text = "[音频转写] " + a
                except Exception as e:
                    return f"音频处理失败 {file_path}: {e}"
            elif ext in ('.mp4', '.mov', '.avi', '.mkv', '.webm'):
                try:
                    import voice, tempfile, subprocess
                    sf = self._sf_conf()
                    if not sf.get("api_key"):
                        return f"视频处理需要硅基流动 key（音轨转写/画面理解）：{p.name}"
                    parts = []
                    tmp_wav = tempfile.mktemp(suffix=".wav")
                    try:
                        subprocess.run([voice.FFMPEG, "-y", "-i", file_path, "-vn", tmp_wav],
                                       capture_output=True, timeout=120,
                                       creationflags=_NO_WINDOW)
                        if os.path.exists(tmp_wav):
                            a = voice.transcribe(tmp_wav, sf)
                            if a:
                                parts.append("[视频音轨转写] " + a)
                    finally:
                        if os.path.exists(tmp_wav):
                            os.remove(tmp_wav)
                    dur = self._video_duration(file_path)
                    for sec in ([1, max(1, dur // 2)] if dur > 3 else [0]):
                        fr = tempfile.mktemp(suffix=".jpg")
                        try:
                            subprocess.run([voice.FFMPEG, "-y", "-ss", str(sec), "-i", file_path,
                                           "-frames:v", "1", fr], capture_output=True, timeout=60,
                                           creationflags=_NO_WINDOW)
                            if os.path.exists(fr):
                                d = self._vision_describe(fr)
                                if d:
                                    parts.append("[视频画面] " + d)
                        finally:
                            if os.path.exists(fr):
                                os.remove(fr)
                    if not parts:
                        return f"视频处理未产生内容：{p.name}"
                    text = "\n".join(parts)
                except Exception as e:
                    return f"视频处理失败 {file_path}: {e}"
            else:
                return f"不支持的文件类型: {ext}"

            if not text or not text.strip():
                return f"文件为空: {file_path}"
            if len(text) > MAX_INDEX_CHARS:
                return f"文本过长已跳过: {p.name}"

            chunks = self._chunk_text(text)

            if not self.available:
                self.init()
            if not self.available:
                return f"RAG 不可用: {file_path}"

            # v4.289.1：先在局部构建完整新索引；任何 chunk 失败都保留旧数据。
            # v4.320.0 P2-1：按文本 hash 幂等增量——未变段原样复用 id+embedding，
            # 只给真正新增/变更段重新 embed，消失段清理。杜绝"改一段全库重烧"。
            fname = p.name
            delta = plan_chunk_delta(self._chunks, chunks, str(file_path))
            reused = delta["reused"]          # {下标: 旧 chunk}
            to_embed = delta["to_embed"]      # [下标]

            new_by_index = {}
            next_counter = self._chunk_counter
            BATCH = 64
            for start in range(0, len(to_embed), BATCH):
                batch_idx = to_embed[start:start + BATCH]
                batch_texts = [chunks[i] for i in batch_idx]
                try:
                    embeddings = self._embed(batch_texts)
                except Exception as e:
                    log.warning("embedding API 批量请求失败: %s", e)
                    embeddings = []
                    for t in batch_texts:
                        try:
                            embeddings.extend(self._embed([t]))
                        except Exception:
                            embeddings.append(None)
                if len(embeddings) != len(batch_texts):
                    return f"索引失败，旧索引已保留: {fname}（embedding 数量不完整）"
                for i, emb in zip(batch_idx, embeddings):
                    if emb is None:
                        return f"索引失败，旧索引已保留: {fname}（embedding 失败）"
                    new_by_index[i] = emb

            # 按新切分顺序组装：未变段复用旧对象（id+embedding 原样），变更段新建
            ordered = []
            for i, chunk_text in enumerate(chunks):
                if i in reused:
                    ordered.append(reused[i])
                else:
                    ordered.append({
                        "id": f"{fname}_chunk_{next_counter}",
                        "source": str(file_path),
                        "text": chunk_text,
                        "embedding": new_by_index.get(i),
                        # v4.319.0 P1-1：溯源四件套（一个事实一个家）
                        "type": "doc",
                        "entity_id": fname,
                        "indexed_at": datetime.now().isoformat(timespec="seconds"),
                        "source_mtime": st.st_mtime,
                    })
                    next_counter += 1

            old_state = (self._chunks, self._chunk_counter,
                         self._files, self._src_set)
            # 保留其它 source 的 chunk；本 source 用增量结果整体替换
            other_chunks = [c for c in self._chunks if c.get("source") != str(file_path)]
            self._chunks = other_chunks + ordered
            self._chunk_counter = next_counter
            self._files = dict(self._files)
            self._files[key] = {"mtime_ns": st.st_mtime_ns, "size": st.st_size}
            self._src_set = set(self._src_set)
            self._src_set.add(key)
            if not self._save_index():
                (self._chunks, self._chunk_counter,
                 self._files, self._src_set) = old_state
                return f"索引保存失败，旧索引已保留: {fname}"
            return f"已索引: {fname} ({len(ordered)} chunks)"
        except MemoryError:
            return f"内存不足，已跳过: {Path(file_path).name}"
        except Exception as e:
            log.warning("RAG 索引异常: %s: %s", file_path, e)
            return f"索引异常已跳过: {Path(file_path).name}"

    # ---------------------- 多模态辅助 ----------------------

    def _sf_conf(self):
        """构造硅基流动 ASR/VLM 配置：优先 config['siliconflow']，否则从 model_profiles 找硅基流动。"""
        cfg = self._cfg or {}
        sf = cfg.get("siliconflow") or {}
        key = sf.get("api_key", "")
        base = sf.get("base_url", "https://api.siliconflow.cn/v1")
        model = sf.get("asr_model", "FunAudioLLM/SenseVoiceSmall")
        if not key:
            for name, prof in cfg.get("model_profiles", {}).items():
                if "silicon" in name.lower() or "硅基" in name:
                    k = prof.get("api_key", "")
                    if k:
                        key = k
                        base = prof.get("base_url", base)
                        break
        return {"api_key": key, "base_url": base, "asr_model": model}

    def _vision_describe(self, image_path):
        """用硅基流动 VLM 把图片转成文字描述；无 key/失败返回空串。"""
        cfg = self._cfg or {}
        key = ""
        base = "https://api.siliconflow.cn/v1"
        for name, prof in cfg.get("model_profiles", {}).items():
            if "silicon" in name.lower() or "硅基" in name:
                key = prof.get("api_key", "")
                base = prof.get("base_url", base)
                if key:
                    break
        if not key:
            sf = cfg.get("siliconflow") or {}
            key = sf.get("api_key", "")
            base = sf.get("base_url", base)
        if not key:
            return ""
        model = cfg.get("vision_model", "OpenGVLab/InternVL2-8B")
        try:
            import base64
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}", "detail": "low"}},
                    {"type": "text", "text": "请详细描述这张图片的内容、文字、图表、场景，用于知识库检索。"}
                ]}],
                "max_tokens": 800,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{base.rstrip('/')}/chat/completions",
                data=payload,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                resp = json.loads(r.read().decode("utf-8", "ignore"))
            return (resp.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
        except Exception as e:
            log.warning("VLM 图片描述失败 %s: %s", image_path, e)
            return ""

    def _video_duration(self, path):
        """返回视频秒数（解析失败返回 0）。"""
        try:
            import subprocess, re as _re, voice
            # v4.125 M-19：text=True 在 GBK 控制台下遇到非 GBK 输出会
            # UnicodeDecodeError 被吞返 0 → 长视频只抽第 0 秒一帧。
            # 统一 bytes + utf-8 replace（与 core/agnes.py 的 ffmpeg 冻结环境约定一致）。
            out = subprocess.run([voice.FFMPEG, "-i", path],
                                 capture_output=True, timeout=30,
                                 creationflags=_NO_WINDOW).stderr
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            m = _re.search(r"Duration:\s*(\d+):(\d+):(\d+)", out)
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        except Exception:
            pass
        return 0

    # ---------------------- 检索 ----------------------

    def search(self, query, top_k=5):
        """检索最相关的 chunk，返回 [(source, text, distance), ...]

        v4.320.0 P1-2：余弦 + BM25 双通路 RRF 融合。embedding 失败/超时
        时 BM25 单独兜底（离线可检索），不再裸 `return []`。
        """
        if not self.available:
            self.init()
        if not self.available or not self._chunks:
            return []

        try:
            import numpy as np
        except ImportError:
            log.error("numpy 未安装")
            # numpy 都没有也走 BM25 兜底
            return self._bm25_search(query, top_k)

        # ---- 余弦通路（embedding 失败则跳过，交给 BM25 兜底）----
        cos_indices = []
        cos_sim = {}
        try:
            # 同义/近义变体一起送 embedding，取各变体余弦最大值（多措辞召回）
            variants = _synonym_variants(query)
            q_embs = self._embed(variants)
            q_mat = np.array(q_embs, dtype=np.float32)
            vectors = np.array([c["embedding"] for c in self._chunks], dtype=np.float32)
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            q_norms = np.linalg.norm(q_mat, axis=1, keepdims=True)
            q_norms[q_norms == 0] = 1
            norms[norms == 0] = 1
            vecs_normed = vectors / norms
            q_normed = q_mat / q_norms
            sims = np.dot(vecs_normed, q_normed.T)          # (n_chunks, n_variants)
            best = sims.max(axis=1)                          # 每 chunk 取最佳变体分
            for i in np.argsort(best)[::-1]:
                if float(best[i]) >= 0.3:                    # 余弦阈值（保留原逻辑）
                    cos_indices.append(int(i))
                    cos_sim[int(i)] = float(best[i])
        except Exception as e:
            log.warning("查询 embedding 失败，转 BM25 兜底: %s", e)
            cos_indices = []

        # ---- BM25 通路（jieba/单字分词，多措辞变体并集召回）----
        kw_rank = self._bm25_rank(query)

        if not cos_indices:
            # embedding 不可用：BM25 单独兜底
            if ENABLE_BM25_FALLBACK or not kw_rank:
                return [self._result_tuple(i, 0.5) for i in kw_rank[:top_k]]
            return []

        # ---- RRF 融合两条排名 ----
        fused = _rrf_fuse([cos_indices, kw_rank], k=_RRF_K)
        out = []
        for i in fused:
            if len(out) >= top_k:
                break
            # distance 沿用余弦 1-sim；BM25-only 项给中性值（保持三元组契约）
            dist = (1.0 - cos_sim[i]) if i in cos_sim else 0.5
            out.append(self._result_tuple(i, dist))
        return out

    def _bm25_rank(self, query):
        """BM25 通路：多措辞变体对全库打分，返回 chunk 索引排名。"""
        docs = [_tokenize_for_bm25(c.get("text", "")) for c in self._chunks]
        variants = _synonym_variants(query)
        best = None
        for v in variants:
            s = _bm25_scores(v, docs)
            if best is None:
                best = list(s)
            else:
                best = [max(a, b) for a, b in zip(best, s)]
        if not best:
            return []
        norm = _normalize_scores(best)
        # 分数归一化阈值：低于 0.15 的视为不相关（BM25 通路自己的阈值）
        ranked = sorted((i for i, x in enumerate(norm) if x >= 0.15),
                        key=lambda i: -norm[i])
        return ranked

    def _bm25_search(self, query, top_k):
        """无 numpy 时的纯 BM25 兜底。"""
        return [self._result_tuple(i, 0.5) for i in self._bm25_rank(query)[:top_k]]

    def _result_tuple(self, idx, distance):
        c = self._chunks[idx]
        return (c["source"], c["text"], float(distance))

    def list_indexed(self):
        """列出已索引的文件"""
        sources = set(c["source"] for c in self._chunks)
        return sorted(sources)

    # chromadb 兼容接口空实现
    def clear_system_cache(self):
        pass
