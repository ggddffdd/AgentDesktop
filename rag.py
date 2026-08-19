# -*- coding: utf-8 -*-
"""
本地 RAG 知识库：轻量 numpy 向量存储 + OpenAI 兼容 embedding API。
零重型依赖（无需 chromadb / PyTorch / sentence-transformers）。
"""
import json
import logging
import os
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("rag")

# 单文件索引上限
MAX_INDEX_BYTES = 3_000_000    # ~3MB
MAX_INDEX_CHARS = 1_500_000    # ~1.5MB


class RAGStore:
    """本地向量知识库"""

    def __init__(self, store_dir, cfg=None):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.store_dir / "index.json"
        self._chunks: list[dict] = []   # [{id, source, text, embedding}]
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
                self._chunks = data.get("chunks", [])
                self._chunk_counter = data.get("next_id", len(self._chunks))
            except Exception:
                self._chunks = []
                self._chunk_counter = 0
        else:
            self._chunks = []
            self._chunk_counter = 0

    def _save_index(self):
        try:
            with open(self._index_path, "w", encoding="utf-8") as f:
                json.dump({"chunks": self._chunks, "next_id": self._chunk_counter}, f, ensure_ascii=False)
        except Exception as e:
            log.warning("保存 RAG 索引失败: %s", e)

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

    def index_file(self, file_path):
        """索引单个文件。"""
        try:
            p = Path(file_path)
            try:
                if p.stat().st_size > MAX_INDEX_BYTES:
                    return f"文件过大已跳过(>{MAX_INDEX_BYTES // 1_000_000}MB): {p.name}"
            except OSError:
                pass

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
                                       capture_output=True, timeout=120)
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
                                           "-frames:v", "1", fr], capture_output=True, timeout=60)
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

            # 清除该文件的旧索引
            self._chunks = [c for c in self._chunks if c["source"] != str(file_path)]

            fname = p.name
            ok = 0
            BATCH = 64
            for i in range(0, len(chunks), BATCH):
                batch = chunks[i:i + BATCH]
                try:
                    embeddings = self._embed(batch)
                except Exception as e:
                    log.warning("embedding API 批量请求失败: %s", e)
                    # 逐条重试
                    embeddings = []
                    for t in batch:
                        try:
                            embeddings.extend(self._embed([t]))
                        except Exception:
                            embeddings.append(None)

                for j, (chunk, emb) in enumerate(zip(batch, embeddings)):
                    if emb is None:
                        continue
                    self._chunks.append({
                        "id": f"{fname}_chunk_{self._chunk_counter}",
                        "source": str(file_path),
                        "text": chunk,
                        "embedding": emb,
                    })
                    self._chunk_counter += 1
                    ok += 1

            self._save_index()
            return f"已索引: {fname} ({ok} chunks)"
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
            out = subprocess.run([voice.FFMPEG, "-i", path],
                                 capture_output=True, text=True, timeout=30).stderr
            m = _re.search(r"Duration:\s*(\d+):(\d+):(\d+)", out)
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        except Exception:
            pass
        return 0

    # ---------------------- 检索 ----------------------

    def search(self, query, top_k=5):
        """检索最相关的 chunk，返回 [(source, text, distance), ...]"""
        if not self.available:
            self.init()
        if not self.available or not self._chunks:
            return []

        try:
            import numpy as np
        except ImportError:
            log.error("numpy 未安装")
            return []

        try:
            q_embs = self._embed([query])
            q_vec = np.array(q_embs[0], dtype=np.float32)
        except Exception as e:
            log.warning("查询 embedding 失败: %s", e)
            return []

        # 计算余弦相似度
        vectors = np.array([c["embedding"] for c in self._chunks], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return []
        q_vec_normed = q_vec / q_norm
        norms[norms == 0] = 1
        vecs_normed = vectors / norms
        similarities = np.dot(vecs_normed, q_vec_normed)

        top_indices = np.argsort(similarities)[-top_k:][::-1]

        results = []
        for idx in top_indices:
            sim = float(similarities[idx])
            if sim < 0.3:  # 相似度阈值
                continue
            c = self._chunks[idx]
            results.append((c["source"], c["text"], 1.0 - sim))
        return results

    def list_indexed(self):
        """列出已索引的文件"""
        sources = set(c["source"] for c in self._chunks)
        return sorted(sources)

    # chromadb 兼容接口空实现
    def clear_system_cache(self):
        pass
