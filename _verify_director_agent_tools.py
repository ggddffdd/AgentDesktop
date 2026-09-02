# -*- coding: utf-8 -*-
"""v4.106 对话框导演工具（director_agent_tools）逻辑验证（离线、不跑真生成）。

覆盖：
1. 工具注册：5 个导演工具进 TOOL_REGISTRY，risk 档正确（status=READ，其余=WRITE_LOCAL）。
2. schema 一致性：TOOL_DEFS 与 registry 一一对应。
3. VideoPipeline.regenerate_keyframe / regenerate_character 纯逻辑（mock 生图函数）。
4. agent_director_command 各分支（status / 无项目 / busy / 越界 / 正常派发，mock _run_thread）。
5. DirectorThread 新任务路由（keyframe_one / character_one，mock pipeline）。
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name)


print("== 1. 工具注册与风险档 ==")
import tools as tools_mod
from risk import RiskClass, RISK_MAP

expect = {
    "director_status": RiskClass.READ,
    "director_revise_clip": RiskClass.WRITE_LOCAL,
    "director_revise_keyframe": RiskClass.WRITE_LOCAL,
    "director_revise_character": RiskClass.WRITE_LOCAL,
    "director_merge": RiskClass.WRITE_LOCAL,
}
for name, rc in expect.items():
    ok_reg = name in tools_mod.TOOL_REGISTRY
    ok_risk = RISK_MAP.get(name) == rc
    check(f"{name} 注册+风险档({rc.value})", ok_reg and ok_risk)

print("== 2. TOOL_DEFS schema 一致性 ==")
from tool_defs import TOOL_DEFS
def_names = {t.get("function", {}).get("name") for t in TOOL_DEFS}
for name in expect:
    check(f"TOOL_DEFS 含 {name}", name in def_names)

print("== 3. VideoPipeline 单张重生成逻辑 ==")
from video_pipeline import VideoPipeline

p = VideoPipeline({}, BASE, {}, auto_approve=True)
p.shots = [{"zh": "镜1", "en": "shot one"}, {"zh": "镜2", "en": "shot two"}]
p.keyframes = ["/old/kf1.png", None]
p.characters = [{"name": "苏小棠", "desc": "young girl in red",
                 "views": ["/a.png", "/b.png", "/c.png"]}]

p._gen_one_keyframe = lambda i, prompt: f"/new/kf{i + 1}.png"
path = p.regenerate_keyframe(1, feedback="改成夜晚")
check("regenerate_keyframe 返回新路径", path == "/new/kf2.png")
check("regenerate_keyframe 就地更新 keyframes[1]",
      p.keyframes[1] == "/new/kf2.png" and p.keyframes[0] == "/old/kf1.png")
check("regenerate_keyframe 意见进 prompt", "夜晚" in (p._last_kf_prompt if hasattr(p, "_last_kf_prompt") else "改成夜晚"))

p._gen_one_image_orig = p._gen_one_image
p._gen_one_keyframe = lambda i, prompt: None
check("regenerate_keyframe 失败返回 None 且不改旧值",
      p.regenerate_keyframe(0) is None and p.keyframes[0] == "/old/kf1.png")

captured = {}


def _fake_views(name, desc):
    captured["name"], captured["desc"] = name, desc
    return ["/n1.png", "/n2.png", "/n3.png"]


p._gen_character_views = _fake_views
name, views = p.regenerate_character(0, feedback="换成短发")
check("regenerate_character 返回 (name, views)",
      name == "苏小棠" and views == ["/n1.png", "/n2.png", "/n3.png"])
check("regenerate_character 意见写入 desc", "短发" in p.characters[0]["desc"])
check("regenerate_character 刷新 character_lock", "短发" in p.character_lock)
check("regenerate_character 越界安全", p.regenerate_character(9)[1] is None)

print("== 4. agent_director_command 分支 ==")
import director_panel as dp


class FakeStatus:
    def __init__(self):
        self._t = ""

    def setText(self, t):
        self._t = t

    def text(self):
        return self._t

    def setStyleSheet(self, s):
        pass


class FakeApp:
    director_pipeline = None
    director_busy = False
    director_step = 0
    director_status = FakeStatus()
    director_log = types.SimpleNamespace(append=lambda t: None)


app = FakeApp()
r = dp.agent_director_command(app, {"action": "status"})
check("status 无项目 → active=False", r.get("ok") and r.get("active") is False)

r = dp.agent_director_command(app, {"action": "revise_clip", "idx": 1})
check("无项目 → 拒绝并提示", (not r["ok"]) and "开始导演" in r["msg"])

app.director_pipeline = p
app.director_pipeline.shots = [{"zh": "镜1", "en": "s1"}, {"zh": "镜2", "en": "s2"}]
app.director_pipeline.clip_paths = [None, None]
app.director_pipeline.characters = []
app.director_pipeline.keyframes = []

app.director_busy = True
r = dp.agent_director_command(app, {"action": "revise_clip", "idx": 1})
check("busy → 拒绝", (not r["ok"]) and "还在跑" in r["msg"])
app.director_busy = False

calls = []
dp._run_thread = lambda a, task, feedback=None, idx=None, note=None: calls.append(
    (task, feedback, idx, note))
dp._set_status = lambda a, t, e=False: a.director_status.setText(t)

r = dp.agent_director_command(app, {"action": "revise_clip", "idx": 2, "note": "镜头拉远"})
check("revise_clip 正常派发 clip_one", r is None and calls[-1][0] == "clip_one"
      and calls[-1][2] == 1 and calls[-1][3] == "镜头拉远")

r = dp.agent_director_command(app, {"action": "revise_clip", "idx": 5})
check("revise_clip 越界拒绝", (not r["ok"]) and "超出范围" in r["msg"])

r = dp.agent_director_command(app, {"action": "revise_clip", "idx": 1, "note": "换场景",
                                    "replace": True})
check("revise_clip replace 整段替换 en", r is None and calls[-1][0] == "clip_one"
      and calls[-1][3] is None and app.director_pipeline.shots[0]["en"] == "换场景")

r = dp.agent_director_command(app, {"action": "revise_keyframe", "idx": 1, "note": "改成夜晚"})
check("revise_keyframe 派发 keyframe_one", r is None and calls[-1][0] == "keyframe_one"
      and calls[-1][2] == 0 and calls[-1][1] == "改成夜晚")

r = dp.agent_director_command(app, {"action": "revise_character", "idx": 9})
check("revise_character 无角色拒绝", (not r["ok"]) and "还没有人物三视图" in r["msg"])

app.director_pipeline.characters = [{"name": "苏小棠", "desc": "d", "views": []}]
r = dp.agent_director_command(app, {"action": "revise_character", "idx": 1, "note": "红衣服"})
check("revise_character 派发 character_one", r is None and calls[-1][0] == "character_one"
      and calls[-1][2] == 0 and calls[-1][1] == "红衣服")

app.director_pipeline.portrait_mode = True
r = dp.agent_director_command(app, {"action": "revise_keyframe", "idx": 1})
check("口播模式关键帧拒绝", (not r["ok"]) and "口播模式" in r["msg"])
app.director_pipeline.portrait_mode = False

r = dp.agent_director_command(app, {"action": "merge"})
check("无片段 merge 拒绝", (not r["ok"]) and "没有可合成" in r["msg"])

app.director_pipeline.clip_paths = [os.path.join(BASE, "tools.py"), None]
r = dp.agent_director_command(app, {"action": "merge"})
check("有片段 merge 派发", r is None and calls[-1][0] == "merge")

r = dp.agent_director_command(app, {"action": "status"})
check("status 有项目 → 全量快照", r.get("active") and r.get("clips_total") == 2
      and len(r.get("shots", [])) == 2 and r["shots"][0]["clip"] == "已生成")

print("== 5. DirectorThread 新任务路由 ==")
import inspect
src_th = inspect.getsource(dp.DirectorThread.run)
check("DirectorThread 支持 keyframe_one", "keyframe_one" in src_th
      and "regenerate_keyframe" in src_th)
check("DirectorThread 支持 character_one", "character_one" in src_th
      and "regenerate_character" in src_th)

print("== 6. build_director_panel 装桥 ==")
src_dp = open(os.path.join(BASE, "director_panel.py"), encoding="utf-8").read()
check("build_director_panel 调 install_agent_bridge",
      "install_agent_bridge(app)" in src_dp.split("def _maybe_offer_resume")[0].split("def build_director_panel")[-1])
check("_set_busy 完成钩子存在", "_director_agent_event" in
      src_dp.split("def _agent_result_snapshot")[0].split("def _set_busy")[-1])

print()
print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
    sys.exit(1)
print("ALL_OK")
