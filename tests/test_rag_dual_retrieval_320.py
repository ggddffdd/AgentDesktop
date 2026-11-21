# -*- coding: utf-8 -*-
"""v4.320.0 P1-2 判据：检索加 BM25 关键词通路 + RRF 融合 + 离线兜底。

工单 P1-2：新增 jieba 分词 + BM25 打分通路，与余弦通路 RRF 融合返回 top-k；
embedding API 不可达/超时(>5s)时 BM25 单独兜底不抛异常；中文查询生成同义/近义
变体(≤6)；余弦阈值保留、BM25 用分数归一化阈值。

判据两层：
  A 层 —— 纯函数行为（分词兜底 / 同义词上限 / BM25 打分 / RRF 融合 / 归一化）。
  B 层 —— 源码契约（语义锚定：search 真接了 BM25 与 RRF，且 embedding 失败走兜底）。
  C 层 —— 集成行为：stub _embed 抛异常时 search 仍能出结果（断网/拔 KEY 兜底）。
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


print("== A 层：纯函数行为 ==")
# A1 分词函数存在（jieba 可用则分词，不可用退回单字）
tok = getattr(ragmod, "_tokenize_for_bm25", None)
check("A1  _tokenize_for_bm25 存在", callable(tok))
if callable(tok):
    r = tok("昆明旅游路线推荐")
    check("A2  中文分词有结果", isinstance(r, (list, tuple)) and len(r) >= 2, repr(r))
    check("A3  分词结果无空串", all(isinstance(t, str) and t for t in r), repr(r))

# A4-A5 同义词变体（≤6，含原查询）
syn = getattr(ragmod, "_synonym_variants", None)
check("A4  _synonym_variants 存在", callable(syn))
if callable(syn):
    v = syn("电脑")
    check("A5  同义词变体 ≤6 且含原词", isinstance(v, (list, tuple)) and 1 <= len(v) <= 6 and v[0] == "电脑", repr(v))

# A6-A8 BM25 打分
bm25 = getattr(ragmod, "_bm25_scores", None)
check("A6  _bm25_scores 存在", callable(bm25))
if callable(bm25):
    docs = [["苹果", "手机", "发布"], ["香蕉", "水果"], ["苹果", "水果", "营养"]]
    try:
        scores = bm25("苹果", docs)
        ok = (len(scores) == 3 and scores[0] > scores[1] and scores[2] > scores[1])
        check("A7  BM25 相关文档排前", ok, repr(scores))
        check("A8  BM25 分数非负", all(s >= 0 for s in scores), repr(scores))
    except Exception as e:
        check("A7  BM25 相关文档排前", False, "EXC " + str(e))
        check("A8  BM25 分数非负", False, "EXC " + str(e))

# A9-A10 RRF 融合
rrf = getattr(ragmod, "_rrf_fuse", None)
check("A9  _rrf_fuse 存在", callable(rrf))
if callable(rrf):
    # 两个排名列表：余弦 [a,b,c]，BM25 [c,a,b]
    cos_rank = ["a", "b", "c"]
    kw_rank = ["c", "a", "b"]
    try:
        fused = rrf([cos_rank, kw_rank], k=60)
        check("A10 RRF 融合去重且齐全", isinstance(fused, list) and len(fused) == 3 and set(fused) == {"a", "b", "c"}, repr(fused))
        check("A11 RRF 共识项(a/c)优先于 b", fused[0] in ("a", "c") and fused[1] in ("a", "c"), repr(fused))
    except Exception as e:
        check("A10 RRF 融合去重且齐全", False, "EXC " + str(e))
        check("A11 RRF 共识项(a/c)优先于 b", False, "EXC " + str(e))

# A12 归一化阈值
norm = getattr(ragmod, "_normalize_scores", None)
check("A12 _normalize_scores 存在", callable(norm))
if callable(norm):
    try:
        ns = norm([1.0, 2.0, 3.0])
        check("A13 归一化到 [0,1]", len(ns) == 3 and all(0 <= x <= 1 for x in ns) and ns[2] >= ns[0], repr(ns))
    except Exception as e:
        check("A13 归一化到 [0,1]", False, "EXC " + str(e))


print("== B 层：源码契约（语义锚定，防 docstring 假绿） ==")
# 定位 search 函数体（锚 `def search(`，取到下一个 def 前）
import re as _re
m = _re.search(r"^    def search\([^\n]*\)(?:(?!^    def )[\s\S])*", RAG_SRC, _re.M)
SEARCH_BODY = m.group(0) if m else ""
# B1-B2 语义锚：search 真接了 BM25 通路入口与 RRF（函数名后带 `(`）
check("B1  search 内调用 BM25 通路", bool(_re.search(r"_bm25_rank\s*\(|_bm25_search\s*\(", SEARCH_BODY)))
check("B2  search 内调用 RRF 融合", bool(_re.search(r"_rrf_fuse\s*\(", SEARCH_BODY)))
# B3 embedding 失败走兜底：except 分支里返回 BM25 结果（而非裸 return []）
_exc_m = _re.search(r"except Exception as e:([\s\S]*?)cos_indices = \[\]", SEARCH_BODY)
check("B3  search 有 embedding 兜底（except 分支转 BM25）",
      bool(_exc_m) and "转 BM25" in _exc_m.group(1)
      and bool(_re.search(r"if not cos_indices", SEARCH_BODY))
      and bool(_re.search(r"_bm25_search\s*\(", SEARCH_BODY)))
# B4 同义词变体被 search 调用
check("B4  search 内调用同义词变体", bool(_re.search(r"_synonym_variants\s*\(", SEARCH_BODY)))
# B5 配置开关存在（允许关闭 BM25 兜底，默认开）
check("B5  有 BM25 兜底开关常量", bool(_re.search(r"RAG_BM25|BM25_FALLBACK|ENABLE_BM25", RAG_SRC)))
# B6 同义词变体上限硬截断为 6（语义锚定，防上限被放宽而数据恰好不越界的假绿）
_m6 = _re.search(r"def _synonym_variants\([^\n]*\)(?:(?!^def )[\s\S])*", RAG_SRC, _re.M)
check("B6  同义词变体上限截断为 6", bool(_m6) and bool(_re.search(r"return out\[:6\]", _m6.group(0))))


print("== C 层：集成行为（断网兜底） ==")
if ragmod is not None:
    try:
        store = ragmod.RAGStore(os.path.join(HERE, "_tmp_rag_dual_320"))
        store.available = True
        # 造几条带 embedding 的 chunk（用简单向量，避免真 API）
        import math
        store._chunks = [
            {"id": "c0", "source": "s1.md", "text": "苹果手机新品发布", "embedding": [1.0, 0.0, 0.0]},
            {"id": "c1", "source": "s2.md", "text": "香蕉水果营养", "embedding": [0.0, 1.0, 0.0]},
            {"id": "c2", "source": "s3.md", "text": "苹果水果市场", "embedding": [0.9, 0.1, 0.0]},
        ]
        # stub _embed 抛异常模拟断网/拔 KEY
        orig_embed = store._embed
        store._embed = lambda texts: (_ for _ in ()).throw(RuntimeError("offline"))
        try:
            res = store.search("苹果", top_k=3)
        except Exception as e:
            res = []
            print("  search raised:", e)
        finally:
            store._embed = orig_embed
        check("C1  断网时 search 不抛异常且有结果", isinstance(res, list) and len(res) >= 1, repr(res))
        if res:
            check("C2  离线兜底命中最相关文档", "苹果" in res[0][1], repr(res[0]))
            check("C3  返回三元组 (source,text,distance)",
                  all(isinstance(x, tuple) and len(x) == 3 for x in res), repr(res[:1]))
    except Exception as e:
        check("C1  断网时 search 不抛异常且有结果", False, "EXC " + str(e))
else:
    check("C1  断网时 search 不抛异常且有结果", False, "rag module import failed")

print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
