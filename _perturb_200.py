# -*- coding: utf-8 -*-
"""扰动验证：证明 test_tokens_200 的判据真的会红（不是摆设）

每条规则：注入一个**真实会犯的错** → 期望**指定的**判据变红 → 恢复源码。
若某个扰动一条都没打红，说明那条判据是死代码（L160：全绿不等于有效）。
"""
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
TEST = os.path.join("tests", "test_tokens_200.py")


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def _compile(old):
    """把待替换字样编译成兼容 CRLF 的正则。

    不能图省事写 `re.escape(old).replace(r"\\n", r"\\r?\\n")` —— re.escape("\\n")
    返回的是「反斜杠 + 真换行符」，不是两个字符 \\n，replace 根本找不到目标，
    于是所有多行扰动都静默失效（看起来像"判据不敏感"，其实是注入没生效）。
    """
    return re.compile(r"\r?\n".join(re.escape(ln) for ln in old.split("\n")))


def read(p):
    return open(p, encoding="utf-8", newline="").read()


def write(p, s):
    open(p, "w", encoding="utf-8", newline="").write(s)


def run():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    p = subprocess.run([PY, TEST], cwd=ROOT, env=env,
                       capture_output=True, timeout=180)
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    if p.returncode not in (0, 1) or not out.strip():
        # 探针自己崩了 —— 这跟「判据不敏感」是两回事，必须分开报告
        print(f"      ‼️ 探针异常退出 rc={p.returncode}")
        print("      " + (err.strip().splitlines() or [""])[-1][:300])
    return [r.strip() for r in re.findall(r"\[FAIL\] (.+?)(?:  —|\s*$)", out, re.M)], out


def perturb(desc, edits, expect_match):
    """注入一个真实会犯的错，看对应判据会不会红，然后恢复源码。"""
    saved = {}
    missed = []
    for fp, old, new in edits:
        path = os.path.join(ROOT, fp)
        saved[fp] = read(path)
        pat = _compile(old)
        s2, n = pat.subn(new.replace("\\", "\\\\"), saved[fp])
        if n == 0:
            missed.append((fp, old[:44]))
        write(path, s2)
    red, _ = run()
    hit = [r for r in red if expect_match(r)]
    ok = bool(hit)
    print(f"{'✅' if ok else '❌'} {desc}")
    print(f"      实际变红 {len(red)} 条: {[r.split()[0] for r in red][:6]}")
    if missed:
        print(f"      ⚠️ 扰动未命中源码: {missed}")
    for fp, content in saved.items():
        write(os.path.join(ROOT, fp), content)
    return ok


def main():
    # 2026-10-03 实测事故：本脚本被超时强杀（SIGTERM）时，Python 默认处理器直接终止进程
    # —— 不抛异常、不走下面的 try/finally，于是 automation_panel.py 里被注入的
    # `scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")`
    # 留在磁盘上，成了「看起来像业务代码坏了」的变异残留（还差点进构建）。
    # 本仓早有专用护栏 _perturb_guard（快照 + SIGTERM/SIGINT/atexit 三重还原 + 残留预检），
    # 画布那批扰动脚本都走它，这里补上 —— 注释约定见该模块 docstring。
    # arm(None) 会快照 ROOT 顶层 .py 与 tests/*.py 并在被强杀时还原；
    # ⑥ 那个夹具（tests/fixtures/*.json）不在其快照范围，由脚本自身的内存快照兜着。
    import _perturb_guard
    _perturb_guard.arm()

    base_ui = md5(os.path.join(ROOT, "ui.py"))
    base_tq = md5(os.path.join(ROOT, "theme_qss.py"))
    results = []

    # ① 有人手滑写回裸标签 → C1（收口干净度）必须抓到
    results.append(perturb(
        "① 新增一处裸写标签（回归到收口前）",
        # v4.216.0：_trust 控件随对话框族搬到 ui_widgets.py（锚点跟着搬；
        # C1/C3 判据扫 SCAN_TARGETS 全表，ui_widgets 里的裸写照样被抓）
        [("ui_widgets.py", '        self._trust.setStyleSheet(label_second())\n',
          '        self._trust.setStyleSheet(label_second())\n'
          '        self._dummy.setStyleSheet(f"color:{THEME[\'dim\']};font-size:12px;")  # 扰动\n')],
        lambda r: "C1" in r or "C3" in r))

    # ② theme_qss 里擅自改字号 → B1 属性等价必须炸
    results.append(perturb(
        "② label_second 字号被改成 11px",
        [("theme_qss.py", 'return f"color:{THEME[color_key]};font-size:{F[\'second\']};"',
          'return f"color:{THEME[color_key]};font-size:11px;"  # 扰动')],
        lambda r: "B1" in r or "D[" in r))

    # ③ 改 UI 侧间距真源 → A2 红，同时 A3 必须仍绿（派生关系成立）
    #    v4.216.0：THEME 唯一真源迁 theme_tokens.py（ui.py 顶部只留 re-export）
    results.append(perturb(
        "③ THEME['space_lg'] 改成 18（派生关系应仍成立）",
        [("theme_tokens.py", '"space_lg": 16,', '"space_lg": 18,  # 扰动')],
        lambda r: "A2" in r))

    # ④ theme_qss 退化成第二份手写字典 → A5 必须抓到
    results.append(perturb(
        "④ theme_qss 里又写死一份间距数字",
        [("theme_qss.py", '_SPACE_KEYS = ("xs", "sm", "md", "lg", "xl", "xxl")',
          '_SPACE_KEYS = ("xs", "sm", "md", "lg", "xl", "xxl")\n_LEAK = {"lg": 16}  # 扰动')],
        lambda r: "A5" in r or "E2" in r))

    # ⑤ gap() 丢了 px → A4/A6 红
    results.append(perturb(
        "⑤ gap() 返回值丢掉 px 单位",
        [("theme_qss.py", 'return f"{S[key]}px"', 'return str(S[key])  # 扰动')],
        lambda r: "A4" in r or "A6" in r))

    # ⑥ 冻结夹具被改写 → B1 必须发现（夹具是新文件，没有 HEAD 版本，靠内存快照恢复）
    #    注：本 case 的变异体是 JSON 夹具，**加不了 `# 扰动` 标记**（JSON 没有注释语法），
    #    所以它是全套 13 例里唯一只靠 arm() 的 atexit/信号还原兜底、预检标记扫不到的一条。
    fx = os.path.join(ROOT, "tests", "fixtures", "labels_200_before.json")
    fx_old = read(fx)
    # 夹具是 JSON：属性名带引号在文件里被转义成 \"font-size\"，直接用裸引号找不到
    tampered, n = re.subn(r'\\"font-size\\": \\"12px\\"', r'\\"font-size\\": \\"12.5px\\"',
                          fx_old, count=1)
    if n == 0:
        print("      ⚠️ 夹具篡改未命中，判据敏感度无法证实")
    write(fx, tampered)
    red, _ = run()
    ok6 = any("B1" in r for r in red)
    print(f"{'✅' if ok6 else '❌'} ⑥ 冻结夹具被篡改 → 期望 B1 红")
    print(f"      实际变红: {[r.split()[0] for r in red][:6]}")
    write(fx, fx_old)
    results.append(ok6)

    # ⑦ 有人把已收口的滚动区写回裸写 → F1/F2 必须抓到（v4.201.0 新增）
    results.append(perturb(
        "⑦ 一处 scroll_transparent() 被改回裸写",
        [("automation_panel.py", '    scroll.setStyleSheet(scroll_transparent())\n',
          '    scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")  # 扰动\n')],
        lambda r: "F1" in r or "F2" in r))

    # ⑧ token 本身的输出被改（丢属性）→ F3 红；同时 B1 也应炸（视觉走样）
    results.append(perturb(
        "⑧ scroll_transparent() 输出丢了 border:none",
        [("theme_qss.py",
          'return "QScrollArea{border:none;background:transparent;}"',
          'return "QScrollArea{background:transparent;}"  # 扰动')],
        lambda r: "F3" in r or "B1" in r))

    # ⑨ 本地 helper 判据失明回归：把 _chk_style 改成不可静态求值的形态 → F4 红
    results.append(perturb(
        "⑨ digital_twin 的 _chk_style 改成不可静态求值",
        [("digital_twin_panel.py", "def _chk_style():\n",
          "def _chk_style(_x=None):  # 扰动\n")],
        lambda r: "F4" in r or "B1" in r))

    # ⑩ v4.202.0：C3 改成「非标残留 == 0」后，新增一处 12px+text 裸写必须红
    results.append(perturb(
        "⑩ 新增一处 12px+text 裸写（C3 应红）",
        [("director_panel.py", '    replace_chk.setStyleSheet(label_second("text"))\n',
          '    replace_chk.setStyleSheet(label_second("text"))\n'
          '    _x = QLabel()\n'
          '    _x.setStyleSheet(f"font-size:12px;color:{THEME[\'text\']};")  # 扰动\n')],
        lambda r: "C3" in r or "B1" in r))

    # ⑪ v4.203.0：加粗变体收口后，写回一处 12px/600/text 裸写 → G1 必须红
    results.append(perturb(
        "⑪ 新增一处加粗裸写（G1 应红）",
        [("director_panel.py", '    replace_chk.setStyleSheet(label_second("text"))\n',
          '    replace_chk.setStyleSheet(label_second("text"))\n'
          '    _y = QLabel()\n'
          '    _y.setStyleSheet(f"font-size:12px;font-weight:600;color:{THEME[\'text\']};")  # 扰动\n')],
        lambda r: "G1" in r or "B1" in r))

    # ⑫ weight 不走 W 字典改成写死 → G4 必须红（防"看起来能跑"的假 token）
    results.append(perturb(
        "⑫ weight 写死数字不走 W（G4 应红）",
        [("theme_qss.py", '    return f"font-weight:{W[weight]};"',
          '    return "font-weight:600;"  # 扰动')],
        lambda r: "G4" in r or "B1" in r))

    # ⑬ 未经批准把 500 归一成 600 → G5 必须红（决策 B 的锁）
    results.append(perturb(
        "⑬ 把 500 归一成 600（G5 应红）",
        [("theme_qss.py", '    "medium": 500,', '    "medium": 600,  # 扰动')],
        lambda r: "G5" in r or "G6" in r or "B1" in r))

    print(f"\n=== 扰动结果：{sum(bool(r) for r in results)}/{len(results)} 命中 ===")
    print(f"源码完整性: ui.py {'OK' if md5(os.path.join(ROOT, 'ui.py')) == base_ui else '**被改坏**'}"
          f" / theme_qss.py {'OK' if md5(os.path.join(ROOT, 'theme_qss.py')) == base_tq else '**被改坏**'}")
    # 统一输出契约（2026-10-03）：run_all --with-perturb 用 PASS=/FAIL= 汇总。
    _hit = sum(1 for r in results if r)
    print("PERTURB PASS=%d FAIL=%d" % (_hit, len(results) - _hit))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    sys.exit(main())
