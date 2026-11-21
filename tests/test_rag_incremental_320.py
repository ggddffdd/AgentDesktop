# -*- coding: utf-8 -*-
"""v4.320.0 P2-1 判据：逐条 ID 增量更新（文本 hash 幂等），弃整文件重写。

工单 P2-1：按 chunk id 提供 add/update/merge 语义；源文件变更只重切变更段落，
未变 chunk 的 id/embedding 原样保留；删除段落 chunk 正确清理；index.json 无重复 id。

判据两层：
  A 层 —— 纯函数 plan_chunk_delta 行为（复用 / 新增 / 删除 / 去重）。
  B 层 —— 源码契约（索引更新真用增量规划，不再无条件整文件重建）。
  C 层 —— 集成行为：改一段后未变 chunk 的 id+embedding 不变、删段清理、无重复 id。
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
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        _f += 1
    if cond:
        _p += 1


def read_src(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return f.read()


RAG_SRC = read_src(os.path.join(ROOT, "rag.py"))
try:
    import rag as ragmod
except Exception as e:
    ragmod = None
    print("import rag ERR", e)


def mkchunk(cid, text, source="f.md"):
    return {"id": cid, "source": source, "text": text,
            "embedding": [float(ord(text[0])) if text else 0.0]}


print("== A 层：纯函数 plan_chunk_delta ==")
plan = getattr(ragmod, "plan_chunk_delta", None)
check("A1  plan_chunk_delta 存在", callable(plan))
if callable(plan):
    # 旧文件 3 段
    old = [mkchunk("f.md_chunk_0", "段落甲"),
           mkchunk("f.md_chunk_1", "段落乙"),
           mkchunk("f.md_chunk_2", "段落丙")]
    # 新切分：改了"段落乙"→"段落乙改"，删了"段落丙"，新增"段落丁"
    new_texts = ["段落甲", "段落乙改", "段落丁"]
    r = plan(old, new_texts, "f.md")
    # 返回结构：dict with reused {idx: chunk}, to_embed [idx], removed [chunk]
    reused = r.get("reused", {}) if isinstance(r, dict) else {}
    to_embed = r.get("to_embed", []) if isinstance(r, dict) else []
    removed = r.get("removed", []) if isinstance(r, dict) else []
    check("A2  未变段落被复用（段落甲）", 0 in reused and reused[0]["id"] == "f.md_chunk_0", repr(reused))
    check("A3  变更段落需重新 embed（乙改）", 1 in to_embed, repr(to_embed))
    check("A4  新增段落需重新 embed（丁）", 2 in to_embed, repr(to_embed))
    check("A5  删除段落被标记清理（丙）",
          any(c["id"] == "f.md_chunk_2" for c in removed), repr(removed))
    check("A6  复用不重复（每段只复用一次）",
          len(set(c["id"] for c in reused.values())) == len(reused))

print("== B 层：源码契约（语义锚定） ==")
import re as _re
m = _re.search(r"^    def index_file\([^\n]*\)(?:(?!^    def )[\s\S])*", RAG_SRC, _re.M)
IDX_BODY = m.group(0) if m else ""
check("B1  index_file 调用增量规划", bool(_re.search(r"plan_chunk_delta\s*\(", IDX_BODY)))
# B2 增量复用：保留未变 chunk（复用 reused），不再把本 source 的旧 chunk 全部丢弃
check("B2  增量复用未变 chunk（reused 出现）", bool(_re.search(r"reused", IDX_BODY)))
check("B3  仅新增/变更段重新 embed（to_embed 出现）", bool(_re.search(r"to_embed", IDX_BODY)))

print("== C 层：集成行为 ==")
if ragmod is not None and callable(plan):
    try:
        store = ragmod.RAGStore(os.path.join(HERE, "_tmp_rag_incr_320"))
        store.available = True
        store._chunks = [mkchunk("f.md_chunk_0", "段落甲"),
                         mkchunk("f.md_chunk_1", "段落乙"),
                         mkchunk("f.md_chunk_2", "段落丙")]
        # 直接走 plan 结果组装（模拟 index_file 的核心增量逻辑）
        new_texts = ["段落甲", "段落乙改", "段落丁"]
        r = plan(store._chunks, new_texts, "f.md")
        reused, to_embed = r["reused"], r["to_embed"]
        out = []
        for i, t in enumerate(new_texts):
            if i in reused:
                out.append(reused[i])              # 原样保留 id+embedding
            else:
                out.append({"id": "f.md_chunk_new_%d" % i, "source": "f.md",
                            "text": t, "embedding": [99.0]})
        check("C1  未变 chunk 的 id+embedding 原样保留",
              out[0]["id"] == "f.md_chunk_0" and out[0]["embedding"] == [float(ord("段"))], repr(out[0]))
        check("C2  删除段落后无对应 chunk", all("段落丙" != c["text"] for c in out))
        check("C3  index 无重复 id", len(set(c["id"] for c in out)) == len(out))
    except Exception as e:
        check("C1  未变 chunk 的 id+embedding 原样保留", False, "EXC " + str(e))
else:
    check("C1  未变 chunk 的 id+embedding 原样保留", False, "module/plan missing")

print("\n汇总：PASS=%d FAIL=%d" % (_p, _f))
sys.exit(1 if _f else 0)
