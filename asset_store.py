# -*- coding: utf-8 -*-
"""资产库（v4.125 ④）：军团 / 导演台产出资产的登记 + 检索。

大哥定调（Roadmap 2026-09-08）：
  - 导演台三视图＝第一批存货，别重造；
  - **命名 + 元数据规范先行**——PM 要知道货在哪、叫什么，
    否则变第二个「技能在库里 PM 不知道」。

== 命名 + 元数据规范（每条资产） ==
  id       8 位短 id（程序引用）
  name     人读名 —— PM / 成员引用资产就说这个名字（如「都市青年三视图」）
  kind     类型：character_views（角色三视图）/ keyframe（分镜关键帧）/
           scene（场景图）/ clip（片段）/ final（成片）/ image（通用图片）/
           script（剧本脚本）/ data（数据/清单）/ other
  path     绝对路径（文件丢失时清单里标 ⚠️）
  tags     检索词列表
  project  来源项目 / 主题
  task     来源任务（一句话）
  ts       登记时间
  meta     附加信息（分辨率 / 时长 / 镜号 / 角色名…）

== 存储 ==
  ~/Documents/小臭玩AI/legion_assets.json（原子写：tmp + os.replace）
  上限 ASSET_MAX=500，超出按 ts 淘汰最旧。

== API ==
  register_asset(name, kind, path, ...)     登记（同 path 去重，只刷新）
  search_assets(query, kind=None, top=...)  关键词检索
  asset_catalog(query=None, top=15)         给 PM 的「货在哪叫什么」清单
  asset_stats()                             总数 + 分类统计
  pick_character_refs(project, character, top=2)  取角色参考图（v4.127，正面优先）
  pick_scene_ref(project, scene_tag)              取场景参考图（v4.127，单张）
  purge_missing()                           清掉文件已丢失的条目
"""
import os
import json
import time
import threading

try:
    from legion import LEGION_DIR
except Exception:  # 独立使用（测试）时兜底 —— 同样尊重 XC_LEGION_DIR 改道（v4.134.4）
    LEGION_DIR = os.path.expanduser(
        os.environ.get("XC_LEGION_DIR")
        or os.path.join("~", "Documents", "小臭玩AI"))

ASSET_PATH = os.path.join(LEGION_DIR, "legion_assets.json")
ASSET_MAX = 500
_LOCK = threading.RLock()

# kind → 人读名（清单展示用）
KIND_LABELS = {
    "character_views": "角色三视图",
    "keyframe": "分镜关键帧",
    "scene": "场景图",
    "clip": "视频片段",
    "final": "成片",
    "image": "图片",
    "script": "剧本脚本",
    "data": "数据清单",
    # 节点画布（设计稿 §9 第 2 步「接资产库」）补进的 kind：
    # 画布端口类型本就是资产 kind 的一个子集（§9 第 1 步注释），
    # 此前 asset_store 缺这三项，导致 prompt/视频 被归一化成 other 丢信息。
    "prompt": "Prompt 提示词",
    "video": "视频",
    "audio": "音频",
    "other": "其他",
}

# 合法 kind 白名单（登记时归一：未知归 other）
_VALID_KINDS = set(KIND_LABELS)


def _load():
    try:
        with open(ASSET_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("assets"), list):
            return data
    except Exception:
        pass
    return {"assets": []}


def _save(data):
    try:
        os.makedirs(os.path.dirname(ASSET_PATH), exist_ok=True)
        tmp = ASSET_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ASSET_PATH)
        return True
    except Exception:
        return False


def _norm_tags(tags):
    out = []
    for t in (tags or ()):
        t = str(t).strip()
        if t and t not in out:
            out.append(t)
    return out[:12]


def register_asset(name, kind, path, tags=(), project="", task="", meta=None):
    """登记一条资产。同 path 重复登记只刷新（name/tags/meta 以新为准）。

    返回 (ok, asset_id)。name/path 为空直接拒收——没名字的资产就是
    「技能在库里 PM 不知道」的翻版。
    """
    name = str(name or "").strip()
    path = str(path or "").strip()
    if not name or not path:
        return False, ""
    kind = kind if kind in _VALID_KINDS else "other"
    with _LOCK:
        data = _load()
        assets = data["assets"]
        ts = time.strftime("%Y-%m-%d %H:%M")
        rec = None
        for a in assets:
            if isinstance(a, dict) and (a.get("path") or "").strip() == path:
                rec = a
                break
        if rec is None:
            rec = {"id": "%08x" % (int(time.time() * 1000) & 0xFFFFFFFF), }
            assets.append(rec)
        rec.update({
            "name": name[:60],
            "kind": kind,
            "path": path,
            "tags": _norm_tags(tags),
            "project": str(project or "").strip()[:60],
            "task": str(task or "").strip()[:120],
            "ts": ts,
            "meta": meta if isinstance(meta, dict) else {},
        })
        if len(assets) > ASSET_MAX:
            assets.sort(key=lambda a: a.get("ts") or "")
            del assets[:len(assets) - ASSET_MAX]
        ok = _save(data)
        return ok, rec.get("id", "")


def search_assets(query, kind=None, top=10):
    """关键词检索：命中 name/tags/project/task/meta 值。返回按命中度排序的列表。"""
    q = str(query or "").strip().lower()
    if not q:
        return []
    with _LOCK:
        assets = [a for a in _load()["assets"] if isinstance(a, dict)]
    if kind:
        assets = [a for a in assets if a.get("kind") == kind]
    scored = []
    for a in assets:
        name = (a.get("name") or "").lower()
        blob = " ".join([
            name,
            " ".join(str(t) for t in (a.get("tags") or [])),
            (a.get("project") or "").lower(),
            (a.get("task") or "").lower(),
            " ".join(str(v) for v in (a.get("meta") or {}).values()),
        ])
        sc = 0
        if q in name:
            sc += 5                       # 名字直接命中权重最高
        if q in blob:
            sc += 2
        for kw in [k for k in q.split() if len(k) >= 2]:
            if kw in blob:
                sc += 1
        if sc:
            scored.append((sc, a))
    scored.sort(key=lambda x: -x[0])
    return [a for _sc, a in scored[:top]]


# ---------------------------------------------------------------- v4.127 取图
# 资产库从「存档复用」升级为「直接喂生成链路」：
#   导演台逐镜装配参考图时，按角色名 / 场景 tag 从这里取已登记的图。
# 铁律：**找不到就返回空，绝不抛异常**——取图失败只降级为少一张参考图，
#        绝不能把整镜生成打断（大哥实测跑一集中断一次 = 前面全白烧）。

# 三视图角度优先级：正面 > 侧面 > 背面（锁脸最有效的是正面）
_VIEW_ORDER = (("正面", 0), ("front", 0), ("侧面", 1), ("side", 1),
               ("背面", 2), ("back", 2))


def _blob(a):
    """一条资产的「可被检索文本」：name + tags + project + task + meta 值。"""
    return " ".join([
        str(a.get("name") or ""),
        " ".join(str(t) for t in (a.get("tags") or [])),
        str(a.get("project") or ""),
        str(a.get("task") or ""),
        " ".join(str(v) for v in (a.get("meta") or {}).values()),
    ]).lower()


def _live_assets(kind):
    """取某一 kind 且**文件确实还在**的资产（清单不撒谎：丢了的直接不算候选）。"""
    try:
        with _LOCK:
            assets = [a for a in _load()["assets"] if isinstance(a, dict)]
    except Exception:
        return []
    if kind:
        assets = [a for a in assets if a.get("kind") == kind]
    out = []
    seen = set()
    for a in assets:
        p = (a.get("path") or "").strip()
        if not p or p in seen:
            continue
        if not os.path.isfile(p):
            continue
        seen.add(p)
        out.append(a)
    return out


def _match_assets(kind, query=None, project=None):
    """按 (query, project) 过滤候选。返回 (候选列表, 是否命中 query)。

    query 空 = 不过滤；query 非空但一条都没命中 → 返回 ([], False)，
    调用方据此返回空结果（宁可不给图，也不给错的图）。
    project 是**硬条件**（v4.127 修正）：给了项目名就只在该项目的资产里取。
    —— 早期版本收窄不到就退回全库，等于把别的项目角色图塞进本次生成，
    违反「禁止跨项目自动注入」，会让画面人物串台。宁可少一张图，不串台。
    （双向子串匹配：主题名与登记项目名互为子串即算同项目。）
    """
    pool = _live_assets(kind)
    q = str(query or "").strip()
    if q:
        # 复用现有 search_assets 的命中逻辑（name/tags/project/task/meta），
        # 再与「文件还在」的池子求交集 —— 保证取出来的图一定能用。
        try:
            hits = {(a.get("path") or "").strip()
                    for a in search_assets(q, kind=kind, top=50)}
        except Exception:
            hits = set()
        hit = [a for a in pool if (a.get("path") or "").strip() in hits]
        if not hit:
            return [], False
        pool = hit
    pj = str(project or "").strip().lower()
    if pj:
        inj = []
        for a in pool:
            ap = str(a.get("project") or "").lower()
            if pj in ap or (ap and ap in pj):
                inj.append(a)
        if not inj:
            return [], True      # 该项目名下没有这种资产 → 不给图（不串台）
        pool = inj
    return pool, True


def _view_rank(a):
    """角度优先级：正面 0 < 侧面 1 < 背面 2 < 未知 9。"""
    b = _blob(a)
    for kw, rank in _VIEW_ORDER:
        if kw in b:
            return rank
    return 9


def _sort_views(cands):
    """先按角度优先级升序，同角度按登记时间倒序（新的优先）。稳定排序两趟实现。"""
    out = list(cands)
    out.sort(key=lambda a: str(a.get("ts") or ""), reverse=True)
    out.sort(key=lambda a: _view_rank(a))
    return out


def pick_character_refs(project=None, character=None, top=2):
    """取角色参考图路径列表（最多 top 张，正面优先，其次侧面）。

    project   : 项目/主题名（收窄用，命中不到不硬过滤）
    character : 角色名（命中 name/tags/meta），**给了但一条没中 → 返回 []**
    top       : 最多几张（默认 2 = 正面 + 侧面）

    返回存在的绝对路径列表；任何异常都吞掉并返回 []。
    """
    try:
        pool, hit = _match_assets("character_views", character, project)
        if character and not hit:
            return []
        out = []
        for a in _sort_views(pool):
            p = (a.get("path") or "").strip()
            if p and p not in out:
                out.append(p)
            if len(out) >= max(1, int(top or 1)):
                break
        return out
    except Exception:
        return []


def pick_scene_ref(project=None, scene_tag=None):
    """取场景参考图路径（单张，最新的那张）。

    project   : 项目/主题名（收窄用）
    scene_tag : 场景名 / tag（如「老屋院子」），给了但没命中 → 返回 None

    返回绝对路径字符串；找不到或异常一律返回 None。
    """
    try:
        pool, hit = _match_assets("scene", scene_tag, project)
        if scene_tag and not hit:
            return None
        if not pool:
            return None
        pool = sorted(pool, key=lambda a: str(a.get("ts") or ""), reverse=True)
        p = (pool[0].get("path") or "").strip()
        return p if p and os.path.isfile(p) else None
    except Exception:
        return None


def asset_stats():
    """返回 (总数, {kind: 数量})。"""
    with _LOCK:
        assets = [a for a in _load()["assets"] if isinstance(a, dict)]
    by = {}
    for a in assets:
        k = a.get("kind") or "other"
        by[k] = by.get(k, 0) + 1
    return len(assets), by


def purge_missing():
    """清掉文件已丢失的条目（清单不撒谎）。返回清除数。"""
    with _LOCK:
        data = _load()
        before = len(data["assets"])
        data["assets"] = [a for a in data["assets"]
                          if isinstance(a, dict)
                          and os.path.exists((a.get("path") or ""))]
        n = before - len(data["assets"])
        if n:
            _save(data)
        return n


def asset_catalog(query=None, top=15):
    """给 PM 的「货在哪叫什么」清单（注入 PM 上下文 / 工具输出共用）。

    query 给了按关键词排优先；没给按登记时间倒序。
    文件丢失的条目标 ⚠️ 提示（不自动删， purge_missing 才删）。
    """
    with _LOCK:
        assets = [a for a in _load()["assets"] if isinstance(a, dict)]
    if not assets:
        return ""
    if query:
        ranked = search_assets(query, top=top)
        if not ranked:   # 没命中也把最近的给 PM 看（货少时宁多勿缺）
            ranked = sorted(assets, key=lambda a: a.get("ts") or "", reverse=True)[:top]
    else:
        ranked = sorted(assets, key=lambda a: a.get("ts") or "", reverse=True)[:top]
    n_all, by_kind = asset_stats()
    lines = [f"\n## 资产库（共 {n_all} 件存货；"
             + "、".join(f"{KIND_LABELS.get(k, k)}{v}" for k, v in sorted(by_kind.items()))
             + "）"]
    lines.append("已产出的图/视频/剧本可复用——**别重造，先查库**（工具 legion_find_asset）：")
    for a in ranked:
        k = KIND_LABELS.get(a.get("kind"), a.get("kind") or "?")
        missing = "" if os.path.exists((a.get("path") or "")) else " ⚠️文件丢失"
        tags = ("#" + " #".join(a.get("tags") or [])) if a.get("tags") else ""
        extra = ""
        meta = a.get("meta") or {}
        if meta.get("resolution"):
            extra = f" · {meta['resolution']}"
        if meta.get("dur"):
            extra += f" · {meta['dur']}s"
        lines.append(f"- 【{k}】{a.get('name')}（{a.get('ts')}）{tags}{extra}{missing}\n"
                     f"  路径：{a.get('path')}")
    return "\n".join(lines)
