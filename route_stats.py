# -*- coding: utf-8 -*-
"""v4.109 路由复盘统计 —— 回答「DeepSeek 到底被触发了多少次、烧了多少、是谁触发的」。

用法：
    python route_stats.py            # 全部统计
    python route_stats.py 7          # 只看最近 7 天
    python route_stats.py 7 kw       # 最近 7 天 + 按触发原因 Top 榜

输出（决定要不要动路由 / 动技能库的依据）：
  1. DeepSeek 触发率       —— 付费通道占全部对话的比例
  2. 付费 token 总量        —— 真实烧了多少
  3. 触发原因 Top 榜        —— 哪些关键词在误命中（收紧词表的依据）
  4. 手动锁定占比           —— Auto 之外被手动干预的比例
  5. 技能使用榜（v4.110）   —— 50 个技能里哪几个真在用、谁是死重

判定口径（跑完一周照这个看）：
  - 触发率 < 20% 且付费 token 可忽略 → 路由健康，方案归档，别改了
  - 触发率 > 60% 且 Top 榜大量是无关高频词（保存/运行/分析/表格）
    → 该动的是收紧 _needs_tool_intent 关键词表，不是加复杂度评分
  - 技能覆盖率 < 30% → 大部分技能是死重，把「从未使用」走 enabled_skills 禁用
    （禁用不是删除，随时能开回来），每轮系统提示能省一截
  - 某个技能全是「手动」零「自动」→ 触发词/描述写得太隐晦，模型不会主动用
"""

import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import route_log


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        return None


def _fmt(n):
    if n >= 10000:
        return "%.1f万" % (n / 10000.0)
    return str(n)


def main():
    days = 0
    show_kw = False
    for a in sys.argv[1:]:
        if a.isdigit():
            days = int(a)
        elif a == "kw":
            show_kw = True

    recs = route_log.read_recent(limit=200000)
    if days > 0:
        cut = datetime.now() - timedelta(days=days)
        recs = [r for r in recs if (_parse_ts(r.get("ts")) or cut) >= cut]

    routes = [r for r in recs if r.get("event") == "route"]
    usages = [r for r in recs if r.get("event") == "usage"]

    if not routes:
        print("暂无路由记录。（日志文件：%s）" % route_log._log_path())
        return

    total = len(routes)
    paid = [r for r in routes if r.get("tier") == "paid"]
    free = [r for r in routes if r.get("tier") == "free"]
    locked = [r for r in routes if r.get("lock")]

    print("=" * 56)
    print("路由复盘%s" % ("（最近 %d 天）" % days if days else "（全部）"))
    print("=" * 56)
    print("路由决策次数：%d" % total)
    print("  付费档(DeepSeek)：%d  (%.1f%%)" % (len(paid), 100.0 * len(paid) / total))
    print("  免费档(Agnes)  ：%d  (%.1f%%)" % (len(free), 100.0 * len(free) / total))
    print("  其他           ：%d" % (total - len(paid) - len(free)))
    print("  手动锁定       ：%d  (%.1f%%)" % (len(locked), 100.0 * len(locked) / total))

    # ---- token 按档位聚合 ----
    tok_by_tier = defaultdict(lambda: {"pt": 0, "ct": 0, "n": 0})
    for u in usages:
        t = u.get("tier") or "other"
        try:
            tok_by_tier[t]["pt"] += int(u.get("prompt_tokens") or 0)
            tok_by_tier[t]["ct"] += int(u.get("completion_tokens") or 0)
        except Exception:
            pass
        tok_by_tier[t]["n"] += 1

    print("\n-- token 消耗 --")
    if not usages:
        print("  （无 usage 记录：多数通道末个 chunk 才回 usage，或该通道不统计）")
    for t in ("paid", "free", "other"):
        d = tok_by_tier.get(t)
        if not d or not d["n"]:
            continue
        tot = d["pt"] + d["ct"]
        name = {"paid": "付费 DeepSeek", "free": "免费 Agnes", "other": "其他"}[t]
        print("  %-14s %d 次  输入 %s + 输出 %s = 合计 %s tok（均 %d/次）"
              % (name, d["n"], _fmt(d["pt"]), _fmt(d["ct"]), _fmt(tot),
                 tot // max(d["n"], 1)))

    # ---- 触发原因 Top ----
    reasons = Counter()
    for r in routes:
        rs = str(r.get("reason") or "")
        # 关键词命中格式 kw:xxx，单独归一统计
        if rs.startswith("kw:"):
            reasons["[关键词] " + rs[3:]] += 1
        elif rs.startswith("len:"):
            reasons["[超长度] " + rs] += 1
        else:
            reasons[rs or "(空)"] += 1

    print("\n-- 触发原因 Top --")
    for k, v in reasons.most_common(15):
        print("  %-28s %4d 次  (%.1f%%)" % (k, v, 100.0 * v / total))

    if show_kw:
        kw = Counter()
        for r in routes:
            rs = str(r.get("reason") or "")
            if rs.startswith("kw:"):
                kw[rs[3:]] += 1
        print("\n-- 关键词命中榜（收紧词表的直接依据）--")
        if not kw:
            print("  （无关键词命中）")
        for k, v in kw.most_common(30):
            print("  %-12s %4d 次" % (k, v))

    # ---- 结论建议 ----
    rate = 100.0 * len(paid) / total
    print("\n-- 判定 --")
    if rate < 20:
        print("  付费触发率 %.1f%% < 20%% → 路由健康，不必改造，路由方案可归档。" % rate)
    elif rate > 60:
        print("  付费触发率 %.1f%% > 60%% → 疑似过度升舱。先跑 `python route_stats.py %d kw`"
              % (rate, days or 7))
        print("  看关键词榜：若大量是「保存/运行/分析/表格」等无关高频词，")
        print("  该动的是收紧 _needs_tool_intent 词表，而不是加复杂度评分。")
    else:
        print("  付费触发率 %.1f%%，处于中间地带。继续观察，或看关键词榜有无明显误命中。" % rate)

    # ============ 技能使用榜（v4.110）============
    print("\n" + "=" * 56)
    print("技能使用榜%s" % ("（最近 %d 天）" % days if days else "（全部）"))
    print("=" * 56)

    skills = [r for r in recs if r.get("event") == "skill"]
    if not skills:
        print("无技能使用记录。埋点从 v4.110 起生效 —— 正常用几天后这一节才有数据。")
        return

    try:
        from skill_loader import normalize_skill_name, get_available_skills
    except Exception:
        def normalize_skill_name(s):
            return str(s or "").strip().lower()
        get_available_skills = None

    _stat = defaultdict(lambda: {"n": 0, "auto": 0, "manual": 0})
    _fails = Counter()
    for s in skills:
        nm = normalize_skill_name(s.get("name"))
        if not nm:
            continue
        if s.get("ok") is False:
            _fails[nm] += 1          # 模型想用但没找到 → 单独统计，不算命中
            continue
        d = _stat[nm]
        d["n"] += 1
        src = s.get("source") or ""
        if src in ("auto", "manual"):
            d[src] += 1

    _hit = sum(d["n"] for d in _stat.values())
    print("技能加载 %d 次：命中 %d / 落空 %d"
          % (len(skills), _hit, sum(_fails.values())))

    print("\n-- Top 排行 --")
    print("  %-26s %5s %6s %6s" % ("技能", "次数", "自动", "手动"))
    for k, v in sorted(_stat.items(), key=lambda x: -x[1]["n"])[:20]:
        print("  %-26s %5d %6d %6d" % (k[:26], v["n"], v["auto"], v["manual"]))

    # ---- 覆盖率 / 死重候选 ----
    all_names = set()
    if get_available_skills:
        try:
            from config import get_skill_scan_dirs
            for d in get_skill_scan_dirs():
                for sk in get_available_skills(d):
                    n = normalize_skill_name(sk.get("name", ""))
                    if n:
                        all_names.add(n)
        except Exception:
            all_names = set()

    used = set()
    if all_names:
        used = set(_stat.keys()) & all_names
        dead = sorted(all_names - set(_stat.keys()))
        print("\n-- 覆盖率 --")
        print("  用过 %d / 共 %d 个技能（%.0f%%）"
              % (len(used), len(all_names), 100.0 * len(used) / len(all_names)))
        if dead:
            print("\n-- 从未使用（死重候选 %d 个）--" % len(dead))
            for n in dead:
                print("  " + n)

    if _fails:
        print("\n-- 模型想用但没找到的名字（幻觉 / 清单对不上）--")
        for k, v in _fails.most_common(10):
            print("  %-30s %d 次" % (k, v))

    # ---- 技能判定 ----
    print("\n-- 技能判定 --")
    if all_names:
        rate_used = 100.0 * len(used) / len(all_names)
        if rate_used < 30:
            print("  覆盖率 %.0f%% < 30%% → 大部分技能是死重。" % rate_used)
            print("  建议把「从未使用」清单走 enabled_skills 禁用（不是删除，随时能开回来），")
            print("  每轮固定注入的技能清单能省下一截，也少干扰模型选择。")
        else:
            print("  覆盖率 %.0f%%，技能库整体在用，不必清理。" % rate_used)
    manual_only = [k for k, v in _stat.items() if v["n"] >= 2 and v["auto"] == 0]
    if manual_only:
        print("  以下 %d 个技能全是手动点选、模型从未自动命中 → 触发词/描述写得太隐晦："
              % len(manual_only))
        print("    " + "、".join(manual_only[:12]))


if __name__ == "__main__":
    main()
