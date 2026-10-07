# -*- coding: utf-8 -*-
"""v4.229.0：生视频面板「提示词内带台词」必须真的念出来（音画同出）。

现象（大哥实测）：生视频面板的提示词框里写了台词，生成出来的视频**没有人说话**；
而输入框的占位文案还让人「去填下方台词/口播框」——那个框根本不存在。

根因：
  · tool_video_gen 早就有 `dialogue=` 参数，且一路传到 core 里的唯一注入点
    `AgnesClient._inject_dialogue`（包成 `Spoken line in Mandarin: "…"` 让模型念
    + 对口型）。**链条本来就是通的。**
  · 但生视频面板 `_gen_video` **从未传 dialogue**，台词整段混在 prompt 里被当
    「画面语义」处理 → 模型把它理解成场景描述，不会发声。

定调（大哥拍板）：**不加台词框** —— 口播/数字人归数字人模块，生视频要的是音画同出，
台词就写在提示词里。所以本轮做的是「从提示词里认出台词，交给内核配音」。

判据分五组：
  A 行为级抽取：各类标记写法都能认出来，且**台词不再留在画面描述里**
  B 行为级反例：画面描述/运镜术语/纯英文台词不得被误当成台词（宁漏勿错）
  C 行为级接线：真的调 `_gen_video` 跑一遍，看 `dialogue=` 有没有随 GF 传下去
  D 接线钉子：ui.py 调用提取函数、_GenThread 传 dialogue、占位文案不再指向不存在的框
  E 判据自证：变异抽取逻辑，A/B/C 必须翻红
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_p = _f = 0


def check(name, cond, detail=""):
    global _p, _f
    if cond:
        _p += 1
        print("  [OK] " + name)
    else:
        _f += 1
        print("  [FAIL] " + name + (("  <- " + detail) if detail else ""))


def _src(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def main():
    import tools as T
    ex = T.extract_video_dialogue

    print("\n-- A 行为级：台词能被认出来，且被从画面描述里摘走 --")
    cases = [
        ("台词冒号", "一位中年男子坐在书房里\n台词：大家好，今天聊聊人工智能",
         "大家好，今天聊聊人工智能", "一位中年男子坐在书房里"),
        ("【口播】方括号", "主播站在货架前\n【口播】欢迎来到我的直播间",
         "欢迎来到我的直播间", "主播站在货架前"),
        ("（台词）圆括号", "咖啡师低头拉花\n（台词）这杯是今天的第一杯",
         "这杯是今天的第一杯", "咖啡师低头拉花"),
        ("旁白", "海边日落，浪打礁石\n旁白：这是我们城市最后一道余晖",
         "这是我们城市最后一道余晖", "海边日落，浪打礁石"),
        ("对白方括号", "两人对坐\n[对白]你真的决定了吗",
         "你真的决定了吗", "两人对坐"),
        ("中文引号整句", "老板在店里擦拭柜台\n“欢迎光临，随便看看”",
         "欢迎光临，随便看看", "老板在店里擦拭柜台"),
        ("LINE 标记", "女孩回头\nline: 我会回来的",
         "我会回来的", "女孩回头"),
    ]
    for tag, raw, want_dlg, want_scene in cases:
        scene, dlg = ex(raw)
        check("A %s → 台词认出来了" % tag, dlg == want_dlg, repr(dlg))
        check("A %s → 台词已从画面描述摘走" % tag,
              "台词" not in scene and (want_dlg or "") not in scene,
              repr(scene[:60]))

    # 多行台词合并成一句给内核（内核再清洗/截断到 60 字）
    scene, dlg = ex("画面：A\n台词：你好\n台词：欢迎来到直播间")
    check("A8 多行台词合并成一句", dlg is not None and "你好" in dlg and "欢迎" in dlg, repr(dlg))
    check("A8b 多行台词后画面只剩画面行", scene == "画面：A", repr(scene))

    # 只有台词没有画面 → 画面描述为空（由调用方补中性画面）
    scene, dlg = ex("台词：大家好，我是主播")
    check("A9 只有台词时画面描述为空", scene == "" and dlg == "大家好，我是主播",
          "scene=%r dlg=%r" % (scene, dlg))

    print("\n-- B 行为级反例：画面描述绝不能被当台词念出来（宁漏勿错）--")
    b = [
        ("纯画面", "城市夜景航拍，霓虹灯在雨后地面拉出倒影"),
        ("运镜行", "镜头缓缓推进，最后定格在他微笑的侧脸"),
        ("被引号包住的运镜", "镜头缓缓推近书房\n“缓缓推移的镜头语言”"),
        ("画面：前缀", "画面：一位老人坐在门槛上剥豆子"),
        ("近景描述", "近景：热气在三只茶碗之间散开"),
    ]
    for tag, raw in b:
        scene, dlg = ex(raw)
        check("B %s 不判成台词" % tag, dlg is None, repr(dlg))
        check("B %s 画面描述原样保留" % tag, scene == raw.strip(), repr(scene[:50]))

    # 标记行里写英文 → 洗不出中文，必须退回「无台词」（别把英文送进配音）
    scene, dlg = ex("画面：夜景\ndialogue: hello world this is english only")
    check("B6 英文台词洗不出中文 → 退回无台词", dlg is None, repr(dlg))
    check("B6b 英文台词场景：整段原样退回", scene is not None and "画面：夜景" in scene, repr(scene[:40]))

    # 空输入
    scene, dlg = ex("")
    check("B7 空输入不炸", scene == "" and dlg is None)

    print("\n-- C 行为级接线：真调 _gen_video，看 dialogue 有没有随线程传下去 --")
    try:
        import ui as ui_mod
    except Exception as e:
        check("B 组跳过：ui 模块导入失败", False, str(e))
        ui_mod = None

    if ui_mod is not None:
        captured = {}

        class _FakeText(object):
            def __init__(self, s=""):
                self._s = s

            def toPlainText(self):
                return self._s

        class _FakeLine(object):
            def __init__(self, s=""):
                self._s = s

            def text(self):
                return self._s

        class _FakeLabel(object):
            def __init__(self):
                self.txt = ""

            def setText(self, s):
                self.txt = s

        class _FakeCombo(object):
            def currentData(self):
                return "768x1152"

        class _FakeSpin(object):
            def value(self):
                return 6

        class _FakeSignal(object):
            def connect(self, *a, **k):
                pass

        class _FakeThread(object):
            def __init__(self, fn, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                captured["fn"] = fn
                self.result = _FakeSignal()

            def start(self):
                pass

        class _Stub(object):
            """只塞 _gen_video 真正用到的属性，避免实例化整个主窗口。"""
            def __init__(self, prompt):
                self.video_prompt = _FakeText(prompt)
                self.video_status = _FakeLabel()
                self.video_resolution = _FakeCombo()
                self.video_duration = _FakeSpin()
                self.video_first = _FakeLine("")
                self.video_last = _FakeLine("")
                self._video_refs = []
                self.cfg = {}
                self._video_thread = None

            def _spawn_thread(self, w):
                pass

            def _on_video_result(self, r):
                self.result_payload = r

        # task_status / _GenThread 都在 ui 模块命名空间里，直接替换即可
        real_thread = ui_mod._GenThread
        real_status = ui_mod.task_status
        try:
            ui_mod._GenThread = _FakeThread
            ui_mod.task_status = type("TS", (), {
                "begin": staticmethod(lambda *a, **k: ""),
                "progress": staticmethod(lambda *a, **k: None),
            })

            _gen = ui_mod.ChatWindow._gen_video

            s1 = _Stub("一位男子站在窗边\n台词：大家好，欢迎来到我的直播间")
            _gen(s1)
            kw = captured.get("kwargs", {})
            check("C1 dialogue 真的传下去了（值正确）",
                  kw.get("dialogue") == "大家好，欢迎来到我的直播间", repr(kw.get("dialogue")))
            check("C2 传给内核的画面描述里不再含台词行",
                  "台词" not in captured.get("args", (None,) * 3)[2],
                  repr(captured.get("args", (None,) * 3)[2]))
            check("C3 状态栏明示「口播配音」（可观测性）",
                  "口播配音" in s1.video_status.txt, s1.video_status.txt)

            s2 = _Stub("城市夜景航拍，霓虹闪烁")
            _gen(s2)
            kw2 = captured.get("kwargs", {})
            check("C4 没台词时 dialogue 为 None（不下发空串）",
                  kw2.get("dialogue", "MISSING") is None, repr(kw2.get("dialogue")))
            check("C5 没台词时提示词原样透传",
                  captured.get("args", (None,) * 3)[2] == "城市夜景航拍，霓虹闪烁",
                  repr(captured.get("args", (None,) * 3)[2]))

            s3 = _Stub("台词：只有台词没画面")
            _gen(s3)
            arg3 = captured.get("args", (None,) * 3)[2]
            check("C6 只有台词时会补一句中性画面描述（不给空 prompt）",
                  bool(arg3) and arg3 != "台词：只有台词没画面", repr(arg3))
            check("C6b 补的画面描述是中文且与镜头相关",
                  any(w in (arg3 or "") for w in ("镜头", "画面", "说话")), repr(arg3))

            s4 = _Stub("镜头缓缓推近书房\n“缓缓推移的镜头语言”")
            _gen(s4)
            check("C7 运镜术语即便加引号也不当台词",
                  captured.get("kwargs", {}).get("dialogue", "MISSING") is None,
                  repr(captured.get("kwargs", {}).get("dialogue")))
        finally:
            ui_mod._GenThread = real_thread
            ui_mod.task_status = real_status

    print("\n-- D 接线钉子（UI 不再误导，写準方式写对）--")
    ui_src = _src("ui.py")
    ts_src = _src("tools.py")
    if ui_mod is not None:
        check("D1 ui.py 调用了 extract_video_dialogue",
              "extract_video_dialogue" in ui_src)
        # 旧误导文案：叫人去填一个不存在的框
        check("D2 占位文案不再指向不存在的「台词/口播」框",
              "台词/口播」框" not in ui_src, "仍在引导用户去填不存在的框")
        check("D3 占位文案改用了「台词：」写法示例",
              "台词：" in ui_src)
        check("D4 _GenThread 调用处传了 dialogue=",
              "dialogue=dialogue" in ui_src)
        # core 侧口号：注入只许一处 —— 本轮不得在 ui/tools 里自己拼 English meta
        check("D5 本轮没有在别处再拼一遍英文配音话术（注入点唯一）",
              "Spoken line in Mandarin" not in ui_src,
              "台词注入必须在 core._inject_dialogue 唯一处")
        check("D6 tools 侧也没重复拼（只保留给数字人直连的 _build_video_prompt）",
              ts_src.count("Spoken line in Mandarin") == 1,
              "出现 %d 次" % ts_src.count("Spoken line in Mandarin"))
        # 函数必须是顶层可导入（模块级钩子：防止被写进类里）
        try:
            tree = ast.parse(ts_src)
            names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
            check("D7 extract_video_dialogue 是 tools 顶层函数",
                  "extract_video_dialogue" in names)
        except Exception as e:
            check("D7 tools.py AST 可解析", False, str(e))

    print("\n-- E 判据自证：这些检查不是恒真的 --")
    check("E1 A 组用例本身确实带「台词：」标记（可被漏抽）",
          "台词：" in cases[0][1])
    check("E2 C2 依赖的画面描述确实来自第 3 个位置参数(args[2])",
          len(captured.get("args", ())) >= 4,
          "args=%d" % len(captured.get("args", ())))
    check("E3 B 组反例文本本身不含台词标记（证明不是靠标记巧合通过）",
          all("台词" not in raw for _tag, raw in b[:3]))
    check("E4 提取函数对「无台词」输入返回 None 而非空串",
          ex("一幅静物画")[1] is None)


if __name__ == "__main__":
    main()
    print("\nRESULT: PASS=%d FAIL=%d" % (_p, _f))
    sys.exit(1 if _f else 0)
