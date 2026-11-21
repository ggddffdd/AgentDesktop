# -*- coding: utf-8 -*-
"""v4.319.0 P1-1 判据：RAG chunk 溯源四件套 + 旧索引向后兼容。

工单 P1-1：每条 chunk 增加 type / entity_id / indexed_at / source_mtime；
旧 index.json 缺字段加载时按默认值补齐，不报错；list_indexed 行为不变。

判据两层：
  A 层 —— 纯函数行为（normalize_chunk 补字段 / 不覆盖已有 / 默认值合法）。
  B 层 —— 源码契约（语义锚定：写入处确实带四字段，加载处确实归一）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_p = 0
_f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        print("  [PASS] %s" % name)
        _p += 1
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        _f += 1


def read_src(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


RAG_SRC = read_src(os.path.join(ROOT, "rag.py"))
try:
    import rag as ragmod
except Exception as e:
    ragmod = None
    print("import rag ERR", e)

PROVENANCE_KEYS = ("type", "entity_id", "indexed_at", "source_mtime")

print("=== A 层：normalize_chunk 行为 ===")

norm = getattr(ragmod, "normalize_chunk", None) if ragmod else None
if norm is not None:
    # 旧 chunk（缺四字段）→ 补齐，默认值合法
    old = {"id": "a_chunk_0", "source": "/tmp/a.md", "text": "内容", "embedding": [0.1]}
    out = norm(old)
    check("A1  旧 chunk 补齐四字段", all(k in out for k in PROVENANCE_KEYS),
          "out=%r" % out)
    check("A2  type 默认 doc", out.get("type") == "doc", "type=%r" % out.get("type"))
    check("A3  entity_id 默认 source 文件名",
          out.get("entity_id") == "a.md", "entity_id=%r" % out.get("entity_id"))
    check("A4  indexed_at 为合法 ISO8601 字符串",
          isinstance(out.get("indexed_at"), str) and "T" in out.get("indexed_at"),
          "indexed_at=%r" % out.get("indexed_at"))
    check("A5  source_mtime 为数值或空串（缺源文件不炸）",
          isinstance(out.get("source_mtime"), (int, float, str)),
          "source_mtime=%r" % out.get("source_mtime"))
    # 不破坏原有字段
    check("A6  原有字段不变（id/source/text/embedding）",
          out["id"] == old["id"] and out["source"] == old["source"]
          and out["text"] == old["text"] and out["embedding"] == old["embedding"])
    # 已有四字段 → 原样保留（幂等，不覆盖真实溯源）
    full = dict(old)
    full.update({"type": "session", "entity_id": "custom", "indexed_at": "2026-01-01T00:00:00",
                 "source_mtime": 12345})
    out2 = norm(full)
    check("A7  已有四字段原样保留（幂等）",
          all(out2.get(k) == full[k] for k in PROVENANCE_KEYS), "out=%r" % out2)
    # 非 dict / 异常输入不炸
    check("A8  非 dict 输入不炸且返回可用结构",
          isinstance(norm(None), dict) and isinstance(norm("x"), dict))
else:
    for n in ["A1 补齐四字段", "A2 type默认", "A3 entity_id默认", "A4 indexed_at",
              "A5 source_mtime", "A6 原字段不变", "A7 幂等", "A8 非dict不炸"]:
        check(n, False, "rag.normalize_chunk 不存在")

# 端到端：用临时目录构造旧版 index.json，加载后每块都有四字段
if ragmod is not None:
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        legacy = {"chunks": [{"id": "x_chunk_0", "source": "/tmp/x.md", "text": "旧数据",
                              "embedding": [0.2, 0.3]}], "next_id": 1, "files": {}}
        with open(os.path.join(td, "index.json"), "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        store = ragmod.RAGStore(td)
        store._load_index()
        check("A9  旧版 index.json 加载不炸且 chunk 数不变", len(store._chunks) == 1)
        if store._chunks:
            check("A10 加载后 chunk 自动带四字段",
                  all(k in store._chunks[0] for k in PROVENANCE_KEYS),
                  "chunk=%r" % store._chunks[0])
        check("A11 list_indexed 行为不变（仍返回 source 列表）",
              store.list_indexed() == ["/tmp/x.md"], "got=%r" % store.list_indexed())
else:
    for n in ["A9 旧索引加载", "A10 加载补字段", "A11 list_indexed不变"]:
        check(n, False, "rag 未导入")

print("=== B 层：源码契约（语义锚定）===")

# 写入处必须带四字段（锚 new_chunks.append 构造块内，防别处同名字段假绿）
import re as _re
_m = _re.search(r"new_chunks\.append\(\{(?:(?!\}\))[\s\S])*?\}\)", RAG_SRC)
_blk = _m.group(0) if _m else ""
check("B0  定位到 new_chunks.append 构造块", bool(_blk), "block=%r" % _blk[:80])
check("B1  写入 chunk 带 type 字段", '"type":' in _blk)
check("B2  写入 chunk 带 entity_id 字段且非死空串",
      '"entity_id":' in _blk and '"entity_id": fname' in _blk,
      "blk=%r" % _blk[:200])
check("B3  写入 chunk 带 indexed_at 字段", '"indexed_at":' in _blk)
check("B4  写入 chunk 带 source_mtime 字段", '"source_mtime":' in _blk)
# 加载处必须调用归一（真实调用行，非文档）
check("B5  _load_index 调用 normalize_chunk",
      "normalize_chunk" in RAG_SRC and RAG_SRC.count("normalize_chunk") >= 2,
      "count=%d" % RAG_SRC.count("normalize_chunk"))

print("汇总：PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
