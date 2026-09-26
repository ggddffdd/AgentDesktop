"""小臭玩AI · UI 裸 hex 构建期护栏（被 build_safe.py 调用）。

视觉纪律见 UI_QA_CHECKLIST.md §6：所有颜色必须走 THEME[key]，禁止在 THEME 字典之外
硬编码 #RRGGBB。返回 True=通过；False=发现「基线之外的新裸 hex」。

⚠️ 现状（2026-09-21 审计）：现有 UI 源码在 THEME 之外已有 85 处 HARD + 45 处 SOFT 裸 hex
（角色色、错误红、HTML 边框色、白字等历史存量）。若直接严格 FAIL 会让每次构建都挂。
故采用「带基线的 lint」：把存量登记进 ui_hex_baseline.txt，护栏对基线内的存量静默，
只对「新引入」的裸 hex 开火：
  - 基线内（存量）：静默，不阻塞；
  - 基线外 + THEME 之外的新颜色（真正新增漂移）：HARD → False（构建失败）；
  - 基线外 + THEME 内却写死：SOFT（警告，不阻塞）。

清理存量后，重新生成基线（`python ui_hex_guard.py --write-baseline`）即可逐步收紧。

注释处理：用 tokenize 区分「字符串内的 #」（QSS / THEME 值）与「真正的行注释」，否则
朴素正则 #.*$ 会把前者当注释删掉、导致漏检。

用法：
  ui_hex_guard.ui_hex_guard(root)             # 被 build_safe 调用
  python ui_hex_guard.py --write-baseline     # 重新生成 ui_hex_baseline.txt
"""
import io
import os
import re
import sys
import tokenize

HEX = re.compile(r'#[0-9a-fA-F]{6}')
THEME_START = re.compile(r'^\s*THEME\s*=\s*\{')
THEME_END = re.compile(r'^\s*\}')
RGBA = re.compile(r'rgba\s*\(')

# 永远不扫描自身与构建脚本（非 UI 源，且含正则字面量 # 会干扰）
_SKIP_NAMES = {'ui_hex_guard.py', 'build_safe.py'}
BASELINE_NAME = 'ui_hex_baseline.txt'


def _strip_comments_keep_lines(src):
    """用 tokenize 正确剔除行注释（保留字符串内的 # 与行号）。失败则原样返回。"""
    lines = src.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                srow, scol = tok.start
                erow, ecol = tok.end
                ln = lines[srow - 1]
                if erow == srow:  # 注释不会跨行
                    lines[srow - 1] = ln[:scol] + ' ' * (ecol - scol) + ln[ecol:]
    except Exception:
        return src
    return ''.join(lines)


def _collect_approved_palette(root):
    """从 ui.py 的 THEME 字典块抽取已批准颜色（全小写，含 prism 渐变里的 hex）。"""
    approved = set()
    theme_path = os.path.join(root, 'ui.py')
    try:
        raw = open(theme_path, encoding='utf-8', errors='replace').read()
        src = _strip_comments_keep_lines(raw)
        _in = False
        for line in src.splitlines():
            if THEME_START.match(line):
                _in = True
                continue
            if _in:
                if THEME_END.match(line) and '}' in line:
                    _in = False
                    continue
                for m in HEX.finditer(line):
                    approved.add(m.group(0).lower())
    except Exception:
        pass
    return approved


def _find_ui_files(root):
    ui_files = []
    for _root, _dirs, _files in os.walk(root):
        if any(seg in _root for seg in ('.git', 'dist', '__pycache__')):
            continue
        for fn in _files:
            # 排除以 _ 开头的临时/开发脚本（如 _smoke_* / _verify_* / _audit_*）
            if not fn.endswith('.py') or fn.startswith('_') or fn in _SKIP_NAMES:
                continue
            fp = os.path.join(_root, fn)
            try:
                txt = open(fp, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            if ('setStyleSheet' in txt) or re.search(r'(panel|ui|widget|style|_view)\.py', fn, re.I) or 'THEME' in txt:
                ui_files.append(fp)
    return ui_files


def _load_baseline(root):
    """返回已知存量裸 hex 的 key 集合（'relfile:#hex' 小写）；文件缺失返回 None。"""
    bp = os.path.join(root, BASELINE_NAME)
    if not os.path.isfile(bp):
        return None
    keys = set()
    try:
        for line in open(bp, encoding='utf-8', errors='replace').read().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                keys.add(line.lower())
    except Exception:
        return None
    return keys


def _scan(root):
    """返回 (approved, ui_files, hits)。hits = list of (rel, line_no, hex_lower)。"""
    approved = _collect_approved_palette(root)
    ui_files = _find_ui_files(root)
    hits = []
    for fp in sorted(set(ui_files)):
        try:
            raw = open(fp, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        src = _strip_comments_keep_lines(raw)
        in_theme = False
        for i, line in enumerate(src.splitlines(), 1):
            if THEME_START.match(line):
                in_theme = True
            if in_theme:
                if THEME_END.match(line) and '}' in line:
                    in_theme = False
                continue
            if RGBA.search(line):
                continue
            for m in HEX.finditer(line):
                rel = os.path.relpath(fp, root)
                hits.append((rel, i, m.group(0).lower()))
    return approved, ui_files, hits


def ui_hex_guard(root):
    """扫描 root 下 UI 源文件，返回是否通过（True=无「基线外」HARD 违规）。"""
    approved, ui_files, hits = _scan(root)
    baseline = _load_baseline(root)
    hard_new, soft_new, baselined = [], [], []
    for rel, ln, h in hits:
        key = '%s:%s' % (rel, h)
        if baseline is not None and key in baseline:
            baselined.append((rel, ln, h))
            continue
        if h in approved:
            soft_new.append((rel, ln, h))
        else:
            hard_new.append((rel, ln, h))

    if baseline is None:
        # 没有基线时绝不阻塞构建（否则 85 处存量会全军覆没），改为全量警告并提示生成基线
        print('[build_safe] ⚠️ ui_hex_baseline.txt 缺失，护栏降级为「仅警告」模式（不阻塞构建）。'
              '请运行 `python ui_hex_guard.py --write-baseline` 生成基线。')

    if soft_new:
        print('[build_safe] ⚠️ UI 裸 hex 护栏（SOFT，不阻塞）：%d 处 THEME 内颜色却写死（建议改 THEME[key]）：'
              % len(soft_new))
        for rel, ln, h in soft_new[:50]:
            print('[build_safe]   %s:%d  %s' % (rel, ln, h))
    if hard_new:
        print('[build_safe] ⛔ UI 裸 hex 护栏（HARD，构建失败）：发现 %d 处基线之外的新颜色，必须改用 THEME[key]：'
              % len(hard_new))
        for rel, ln, h in hard_new[:50]:
            print('[build_safe]   %s:%d  %s' % (rel, ln, h))
        return False
    print('[build_safe] ✅ UI 裸 hex 护栏通过（扫描 %d 个 UI 源文件，新增违规 0 / 存量 %d 处已基线豁免）'
          % (len(ui_files), len(baselined)))
    return True


def _write_baseline(root):
    _, _, hits = _scan(root)
    bp = os.path.join(root, BASELINE_NAME)
    lines = ['# 已知存量裸 hex 基线（file:#hex，小写）。护栏对基线内静默、只拦新增。',
             '# 清理一部分存量后，运行 `python ui_hex_guard.py --write-baseline` 重新生成。', '']
    seen = set()
    for rel, ln, h in sorted(hits, key=lambda x: (x[0], x[2])):
        key = '%s:%s' % (rel, h)
        if key in seen:
            continue
        seen.add(key)
        lines.append(key)
    with open(bp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('[ui_hex_guard] 已写入基线 %s（%d 个 file:#hex 去重项，含全部存量裸 hex）' % (bp, len(seen)))
    return len(seen)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    here = os.path.dirname(os.path.abspath(__file__))
    if '--write-baseline' in sys.argv:
        _write_baseline(here)
    else:
        ok = ui_hex_guard(here)
        print('RESULT =', ok)
