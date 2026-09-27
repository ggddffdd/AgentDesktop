# v4.175.0：从仓库根目录搬入 tests/ —— 统一入口以 `python tests/xxx.py` 运行，
# 此时 sys.path[0] 是 tests/，必须显式把仓库根加回来，否则 import ui 会失败。
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

# -*- coding: utf-8 -*-
"""热修15 (v4.84) 离线单测：自进化双轨（轨迹自动提炼 + 技能审核队列）。

覆盖：
- risk.classify("create_skill") == WRITE_LOCAL（解除外部白名单拦截）
- skill_review 全路径：submit/list/approve/reject/count + 元数据解析 + name 清洗 + 缺字段报错
- tools.tool_create_skill 路由到待审核目录（不经正式 skills/）
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fail = []


def ok(cond, msg):
    if cond:
        print("PASS:", msg)
    else:
        print("FAIL:", msg)
        fail.append(msg)


# ---------- 1. risk.classify create_skill = WRITE_LOCAL ----------
import risk
ok(risk.classify("create_skill") == risk.RiskClass.WRITE_LOCAL,
   "risk.classify('create_skill') == WRITE_LOCAL")
ok(risk.tier_of("create_skill") == "semi",
   "tier_of('create_skill') == 'semi' (半自主，可运行不经外部白名单拦截)")


# ---------- 2. skill_review 全路径 ----------
import skill_review

tmp = tempfile.mkdtemp()
cfg = {
    "skills_pending_dir": os.path.join(tmp, "pending"),
    "skills_dir": os.path.join(tmp, "skills"),
}

# 缺字段
ok("失败" in skill_review.submit_skill(cfg, "", "desc", ""),
   "submit_skill 缺 name/prompt 报错")
ok(skill_review.count_pending(cfg) == 0, "提交失败时不落盘")

# 正常提交
msg = skill_review.submit_skill(cfg, "weekly-report", "周报生成",
                                "1. 收集数据\n2. 生成图表", "📊", "效率办公")
ok("审核队列" in msg, "submit_skill 正常提交到审核队列")
ok(skill_review.count_pending(cfg) == 1, "count_pending == 1")

pending = skill_review.list_pending(cfg)
ok(len(pending) == 1, "list_pending 返回 1 条")
p = pending[0]
ok(p["name"] == "weekly-report", "解析到 name")
ok(p["category"] == "效率办公", "解析到 category")
ok(p["emoji"] == "📊", "解析到 emoji")
ok(p["description"].startswith("周报生成"), "解析到 description")
ok("weekly-report" in p["path"] and os.path.isdir(p["path"]), "path 为待审核子目录")

# 待审核目录不应被正式 skills 目录包含（skill_loader 不扫 pending）
ok(not os.path.exists(cfg["skills_dir"]), "提交后正式 skills 目录仍为空（未直接生效）")

# 通过 -> 移入正式目录
ok("已通过" in skill_review.approve_skill(cfg, "weekly-report"), "approve_skill 成功")
ok(skill_review.count_pending(cfg) == 0, "approve 后 count == 0")
ok(os.path.isfile(os.path.join(cfg["skills_dir"], "weekly-report", "SKILL.md")),
   "approve 后正式目录出现 SKILL.md")
ok(not os.path.exists(os.path.join(cfg["skills_pending_dir"], "weekly-report")),
   "approve 后 pending 源目录已移走")

# 拒绝 -> 删除
skill_review.submit_skill(cfg, "tmp-skill", "x", "y", "⚡", "自测")
ok(skill_review.count_pending(cfg) == 1, "再次提交 count == 1")
ok("已拒绝" in skill_review.reject_skill(cfg, "tmp-skill"), "reject_skill 成功")
ok(skill_review.count_pending(cfg) == 0, "reject 后 count == 0")

# name 清洗：去掉路径分隔符
skill_review.submit_skill(cfg, "a/b\\c", "desc", "prompt", "⚡", "自测")
names = [s["name"] for s in skill_review.list_pending(cfg)]
ok("a_b_c" in names, "name 含路径分隔符被清洗为下划线")
for s in skill_review.list_pending(cfg):
    skill_review.reject_skill(cfg, s["name"])


# ---------- 3. tools.tool_create_skill 路由到 pending ----------
try:
    import tools
    r = tools.tool_create_skill(cfg, tmp, {"name": "from-tools", "description": "d",
                                          "prompt": "p", "emoji": "🔧", "category": "自测"})
    ok("审核队列" in r, "tool_create_skill 落审核队列")
    ok(skill_review.count_pending(cfg) == 1, "tool_create_skill 后 pending == 1")
    ok(not os.path.exists(os.path.join(cfg["skills_dir"], "from-tools")),
       "tool_create_skill 不写正式 skills/")
except Exception as e:
    print("SKIP tools import (heavy deps):", e)


shutil.rmtree(tmp, ignore_errors=True)

if fail:
    print("\n=== %d FAIL ===" % len(fail))
    sys.exit(1)
else:
    print("\n=== ALL_HOTFIX15_OK ===")
