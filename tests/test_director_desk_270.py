# -*- coding: utf-8 -*-
"""导演台后续四项：界面编辑进提示词、开拍前少做默认选择、尾帧留座、方法论自检。

无网络。模型调用用桩。界面续跑用假控件，不弹真实窗口。
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import video_pipeline as vp  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS]", name)
    else:
        FAIL += 1
        print("  [FAIL]", name, ("| " + str(extra)) if extra else "")


LONG_BRIEF = (
    "我想拍一个讲时间管理的口播，语气轻松，不要提产品名，多讲方法少讲鸡汤，"
    "结尾留一个问题给观众，让他们自己想明天先做哪一件小事。"
)
GOOD_EN = (
    "a young woman in a washed denim jacket, slowly raising a steaming ceramic mug "
    "toward her lips, warm morning light from a window on her left, soft shallow "
    "depth of field, medium shot, slow push-in, eye-level"
)


def _touch(folder, name):
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(b"x" * 200)
    return path


def _pipe(folder):
    p = vp.VideoPipeline({}, folder)
    p.smart = True
    p.duration = 5
    p.n = 4
    p.portrait_mode = False
    p.ref_image_path = None
    p.style_prompt = ""
    p.spec = {}
    p.with_dialogue = False
    p.relay = True
    p.multiref = True
    p.character_lock = ""
    p.clue_lock = ""
    return p


def main():
    print("=== 1) 运镜栏进画面，不再生成后丢掉 ===")
    check("长说明超过 60 字", len(LONG_BRIEF) >= 60, len(LONG_BRIEF))
    back = vp._enrich_shots_camera([{"en": GOOD_EN, "cam": ""}], False)
    check("已写进英文的运镜回填到栏里",
          "medium shot" in (back[0].get("cam") or "")
          and "eye-level" in (back[0].get("cam") or ""),
          back[0].get("cam"))
    check("回填时不把 cam 从分镜里删掉", "cam" in back[0])
    bare = vp._enrich_shots_camera(
        [{"en": "a young woman in a red coat slowly waving", "zh": "她挥手"}], False)
    check("没写镜头时运镜栏有字", bool((bare[0].get("cam") or "").strip()), bare[0])
    folded = vp.apply_camera_to_en(
        "a young woman in a red coat slowly waving, wide shot, static camera, eye-level",
        "close-up, slow push-in, eye-level")
    check("改过的运镜替换旧镜头词",
          "close-up" in folded and "wide shot" not in folded.lower(), folded)
    check("新运镜只出现一次", folded.lower().count("slow push-in") == 1, folded)

    folder = tempfile.mkdtemp(prefix="desk270_")
    p = _pipe(folder)
    p.set_shots([{
        "en": "a young woman in a red coat slowly waving, wide shot, static camera, eye-level",
        "cam": "close-up, slow push-in, eye-level",
        "zh": "她挥手", "scene": 1, "line": "",
    }])
    clip = p._build_clip_prompt(0)
    kf = p._build_keyframe_prompt(0)
    check("视频提示词带上运镜栏", "close-up" in clip and "wide shot" not in clip.lower(), clip)
    check("关键帧提示词带上运镜栏", "close-up" in kf, kf)
    check("关键帧不再拼 cinematic", "cinematic" not in kf.lower(), kf)
    check("视频提示词带上提交规则", "SHOT RULES" in clip and "cinematic" not in clip.lower(), clip)

    print("\n=== 2) 开拍前不替用户做不可逆选择 ===")
    brief = vp.voiceover_start_plan(LONG_BRIEF)
    check("长说明不当原稿",
          brief.get("passthrough") is None and brief.get("needs_confirm") is False, brief)
    ask = vp.voiceover_start_plan("原稿：大家好，今天讲三件事。")
    check("原稿前缀要先问", ask.get("needs_confirm") is True, ask)
    verb = vp.voiceover_start_plan("原稿：大家好，今天讲三件事。", choice="verbatim")
    check("确认后才按原文念",
          (verb.get("passthrough") or "").startswith("大家好")
          and verb.get("needs_confirm") is False, verb)
    write = vp.voiceover_start_plan("原稿：大家好，今天讲三件事。", choice="write")
    check("选写稿时不直通",
          write.get("passthrough") is None and "大家好" in (write.get("topic") or ""), write)
    nar = vp.build_setting_block(audio="旁白解说（后期配音）")
    check("旁白写明不会生成配音", "不会生成配音" in nar, nar)
    check("写实风格不再带 cinematic", "cinematic" not in vp.STYLE_PROMPTS["realistic"])

    cap = {}

    def fake_chat(cfg, messages, temperature=0.7, **k):
        cap["m"] = messages
        return "[]"

    orig = vp._agnes_chat
    vp._agnes_chat = fake_chat
    try:
        p.smart = True
        p._gen_story("小猫看月亮", 4)
        u = cap["m"][1]["content"]
        check("智能剧本看见用户镜数", "用户指定了 4 镜" in u and "2~6" in u, u[:180])
        check("智能剧本不再写通常 6~18", "通常 6~18" not in u)
        check("剧本带 8 镜范文", "阿禾" in u and "关键道具" in u)
        p.smart = False
        p._gen_story("小猫看月亮", 4)
        u = cap["m"][1]["content"]
        check("关闭智能后剧本必须正好 N 镜", "必须写成正好 4 个镜头" in u, u[:200])
        check("关闭智能后不再写可酌情", "可酌情" not in u)
        p.smart = True
        p._gen_shots("剧本正文。" * 8, 4)
        u = cap["m"][1]["content"]
        check("智能分镜看见用户镜数", "用户指定了 4 镜" in u and "2~6" in u)
        check("分镜带一组衔接范例", "远景建立" in u and "同场承接" in u)
        p.smart = False
        p.duration = 5
        p._gen_shots("剧本正文。" * 8, 4)
        u = cap["m"][1]["content"]
        check("关闭智能后必须正好 N 个分镜", "必须拆成正好 4 个" in u, u[:220])
        check("关闭智能后不再按纪录片加镜", "可酌情增加到" not in u)
    finally:
        vp._agnes_chat = orig

    lo, hi = vp.shot_count_bounds(4, True)
    check("智能镜数窗口是 ±2", (lo, hi) == (2, 6), (lo, hi))
    check("手动镜数窗口就是 N", vp.shot_count_bounds(4, False) == (4, 4))

    print("\n=== 3) 同一场给尾帧留座 ===")
    d = tempfile.mkdtemp(prefix="refs270_")
    files = {n: _touch(d, n + ".png") for n in ("kf", "front", "side", "scene", "clue", "tail")}
    p.shots = [
        {"scene": 1, "zh": "杯子", "en": "mug", "line": ""},
        {"scene": 1, "zh": "杯子", "en": "mug", "line": ""},
    ]
    p.characters = [{"name": "阿禾", "views": [files["front"], files["side"]]}]
    p.keyframes = [None, files["kf"]]
    p.scene_images = {1: files["scene"], 2: files["scene"]}
    p.clues = [{"name": "杯子", "image": files["clue"]}]
    refs = p._assemble_ref_images(1, prev_tail=files["tail"])
    roles = [r[1] for r in refs]
    check("同场参考图不超过 5 张", len(refs) <= vp.AGNES_MAX_REF_IMAGES, roles)
    check("同场尾帧没有被道具挤掉", "tail" in roles, roles)
    p.shots[1]["scene"] = 2
    refs2 = p._assemble_ref_images(1, prev_tail=files["tail"])
    roles2 = [r[1] for r in refs2]
    check("换场且名额已满时不硬塞尾帧", "tail" not in roles2, roles2)
    note = p.ref_gap_note(1, [("a", "keyframe")])
    p.shots[1]["scene"] = 1
    note_same = p.ref_gap_note(1, [("a", "keyframe")])
    check("同场缺尾帧会写进说明", "尾帧" in note_same, note_same)
    check("换场不把尾帧算成缺口", "尾帧" not in note, note)

    print("\n=== 4) 剧本 / 提示词自检只改写一次 ===")
    check("合格范例自检通过", vp.audit_visual_prompt(GOOD_EN) == [],
          vp.audit_visual_prompt(GOOD_EN))
    check("剪辑词会被标出", "cut" in vp.audit_visual_prompt(
        "a young woman in a red coat walks then runs"))
    check("两个动作会被标出", "multi_action" in vp.audit_visual_prompt(
        "a young woman in a red coat slowly raising a mug and sipping tea"))
    check("没有外观会被标出", "no_appearance" in vp.audit_visual_prompt(
        "beautiful sunset over the sea, slow push-in"))
    check("屏上文字会被标出", "onscreen_text" in vp.audit_visual_prompt(
        "a young woman in a red coat, subtitle on screen, slowly waving"))
    story_ok = (
        "清晨，阿禾推开木窗，看见院子。\n"
        "她在灶边烧水，陶杯冒着热气。\n"
        "她端着杯子走到门口，还是出了门。\n"
        "关键道具/陈设：冒热气的陶杯。"
    )
    check("有道具收口的剧本通过自检", vp.audit_story(story_ok) == [], vp.audit_story(story_ok))
    check("过短剧本过不了自检", "too_short" in vp.audit_story("太短"))

    calls = {"n": 0}

    def rewrite_chat(cfg, messages, temperature=0.3, **k):
        calls["n"] += 1
        return ("a young woman in a red coat slowly waving, warm side light, "
                "close-up, eye-level")

    vp._agnes_chat = rewrite_chat
    try:
        p.portrait_mode = False
        weak = [{"en": "then a beautiful sunset", "cam": "", "zh": "", "scene": 1}]
        once = p.polish_shot_prompts(weak)
        check("弱提示词会改写一次",
              calls["n"] == 1 and "young woman" in once[0]["en"], once[0]["en"])
        p.polish_shot_prompts(once)
        check("同一句不重复改写", calls["n"] == 1, calls["n"])
    finally:
        vp._agnes_chat = orig

    print("\n=== 5) 续跑记住未采用的稿和智能分镜开关 ===")
    _panel_checks()

    print("\n结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


class _Text:
    def __init__(self, value=""):
        self.value = value

    def text(self):
        return self.value

    def setPlainText(self, value):
        self.value = value

    def toPlainText(self):
        return self.value


class _Num:
    def __init__(self, value=0):
        self.value_ = value

    def value(self):
        return self.value_

    def setValue(self, value):
        self.value_ = value


class _Check:
    def __init__(self, value=True):
        self.value = value

    def isChecked(self):
        return self.value

    def setChecked(self, value):
        self.value = bool(value)


class _Combo:
    def currentData(self):
        return "768x1152"

    def findData(self, _v):
        return -1

    def setCurrentIndex(self, _i):
        return None


class _Label:
    def setStyleSheet(self, _s):
        return None

    def setText(self, _s):
        return None

    def setPixmap(self, _p):
        return None


class _Stack:
    def setCurrentIndex(self, i):
        self.i = i


def _panel_checks():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import director_panel as dp  # noqa: E402
    except Exception as e:
        check("导演台面板可导入", False, e)
        return

    started = []
    orig_offer = dp._offer_understand_fallback
    orig_card = dp._show_understand_card

    def _ask(*_a, **_k):
        started.append("ask")

    def _card(*_a, **_k):
        started.append("card")

    dp._offer_understand_fallback = _ask
    dp._show_understand_card = _card
    try:
        ok = dp._finish_understand(None, None, "主题", lambda: started.append("go"), "realistic")
        check("理解失败不直接开写", ok is False and started == ["ask"], started)
        started.clear()
        ok = dp._finish_understand(
            None, {"genre": "小猫", "narrative": "一天"}, "主题",
            lambda: started.append("go"), "realistic")
        check("理解成功才出确认卡", ok is True and started == ["card"], started)
    finally:
        dp._offer_understand_fallback = orig_offer
        dp._show_understand_card = orig_card

    session = os.path.join(tempfile.mkdtemp(prefix="sess270_"), "director_session.json")
    project = tempfile.mkdtemp(prefix="proj270_")
    orig_path = dp._session_path
    dp._session_path = lambda: session
    built = {}
    orig_rows = dp._build_shot_rows
    orig_chars = dp._build_character_cards
    orig_clues = dp._build_clue_cards
    dp._build_shot_rows = lambda app, shots: built.setdefault("shots", shots)
    dp._build_character_cards = lambda *a, **k: None
    dp._build_clue_cards = lambda *a, **k: None
    try:
        pipe = _pipe(project)
        pipe.project_dir = project
        pipe.story = "已采用的旧剧本"
        pipe.shots = [{"en": "old", "zh": "旧", "cam": "", "scene": 1, "line": "", "dur": 5}]
        pipe.smart = False
        pipe.qc_status = {0: "pass"}
        pipe.n = 4
        pipe.duration = 5
        pipe.topic = "猫"
        app = _session_app(pipe)
        dp._save_session(app)
        with open(session, encoding="utf-8") as f:
            data = json.load(f)
        check("续跑记下智能分镜是关的", data["params"].get("smart") is False, data["params"].get("smart"))
        check("续跑记下未采用的剧本",
              (data.get("draft") or {}).get("story") == "屏幕上改过、还没采用",
              data.get("draft"))
        check("续跑记下未采用的运镜",
              ((data.get("draft") or {}).get("shots") or [{}])[0].get("cam") == "close-up, slow push-in",
              data.get("draft"))
        check("续跑记下质检状态",
              (data.get("pipeline") or {}).get("qc_status", {}).get("0") == "pass")

        app2 = _session_app(pipe)
        app2.director_smart = _Check(True)
        app2.director_story_edit = _Text("不该留下")
        loaded = dp._load_session(app2)
        check("续跑能读回来", loaded is True, loaded)
        check("续跑恢复智能分镜开关", app2.director_smart.isChecked() is False)
        check("续跑编辑框是未采用稿",
              app2.director_story_edit.toPlainText() == "屏幕上改过、还没采用",
              app2.director_story_edit.toPlainText())
        check("续跑分镜表是未采用稿",
              (built.get("shots") or [{}])[0].get("zh") == "新字幕", built.get("shots"))
        check("续跑流水线记住智能分镜关", app2.director_pipeline.smart is False)
        check("续跑恢复质检状态",
              app2.director_pipeline.qc_status.get(0) == "pass",
              getattr(app2.director_pipeline, "qc_status", None))
    finally:
        dp._session_path = orig_path
        dp._build_shot_rows = orig_rows
        dp._build_character_cards = orig_chars
        dp._build_clue_cards = orig_clues


def _session_app(pipe):
    app = type("App", (), {})()
    app.cfg = {}
    app.director_pipeline = pipe
    app.director_step = 3
    app.director_final_path = None
    app.director_spec = {}
    app.director_topic = _Text("猫")
    app.director_n = _Num(4)
    app.director_duration = _Num(5)
    app.director_resolution = _Combo()
    app.director_style = _Combo()
    app.director_dialogue = _Check(False)
    app.director_portrait = _Check(False)
    app.director_relay = _Check(True)
    app.director_subtitle = _Check(True)
    app.director_vision_review = _Check(False)
    app.director_multiref = _Check(True)
    app.director_smart = _Check(False)
    app.director_ref_image = None
    app.director_story_edit = _Text("屏幕上改过、还没采用")
    app.director_shot_rows = [{
        "scene": _Num(1),
        "zh": _Text("新字幕"),
        "en": _Text("a young woman in a red coat slowly waving"),
        "cam": _Text("close-up, slow push-in"),
        "dur": _Num(5),
        "line": None,
    }]
    app.director_inputs = []
    app.director_params_box = type("B", (), {"setEnabled": lambda self, v: None})()
    app.director_go = type("B", (), {"setEnabled": lambda self, v: None})()
    app.director_stop = type("B", (), {"setEnabled": lambda self, v: None})()
    app.director_ref_preview = _Label()
    app.director_ref_label = _Label()
    app.director_step_labels = [_Label() for _ in range(7)]
    app.director_stack = _Stack()
    app.director_log = []
    app.director_status = _Label()
    app.director_chat = None
    return app


if __name__ == "__main__":
    sys.exit(main())
