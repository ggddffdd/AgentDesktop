"""小臭玩AI · UI 裸 hex 构建期护栏（被 build_safe.py 调用）。

视觉纪律见 UI_QA_CHECKLIST.md §6：所有颜色必须走 THEME[key]，禁止在 THEME 字典之外
硬编码 #RRGGBB。返回 True=通过；False=发现「基线之外的新裸 hex」。

⚠️ 现状（2026-09-21 审计）：现有 UI 源码在 THEME 之外已有 85 处 HARD + 45 处 SOFT 裸 hex
（角色色、错误红、HTML 边框色、白字等历史存量）。若直接严格 FAIL 会让每次构建都挂。
故采用「带基线的 lint」：把存量登记进 ui_hex_baseline.txt，护栏对基线内的存量静默，
只对「新引入」的裸 hex 开火：
  - 基线内（存量）：静默，不阻塞；
  - 基线外 + THEME 之外的新颜色（真正新增漂移）：HARD → False（构建失败）；
  - 基线外 + THEME 内却写死：SOFT（警告，不阻塞）；
  - THEME 兜底副本（见 `_theme_fallback_spans`）：静默 —— 那是 `except` 分支里
    「THEME 取不到时顶上」的字面量，**必须**写死（改成 THEME[key] 会成自引用、
    兜底失效）。豁免数会打进结果行，不做隐形放宽。

清理存量后，重新生成基线（`python ui_hex_guard.py --write-baseline`）即可逐步收紧。

注释处理：用 tokenize 区分「字符串内的 #」（QSS / THEME 值）与「真正的行注释」，否则
朴素正则 #.*$ 会把前者当注释删掉、导致漏检。

用法：
  ui_hex_guard.ui_hex_guard(root)             # 被 build_safe 调用
  python ui_hex_guard.py --write-baseline     # 重新生成 ui_hex_baseline.txt
"""
import ast
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


def _theme_fallback_spans(src):
    """返回「THEME 兜底副本」占用的行号集合（1-based）。

    模式：`try:` 体里有 `return THEME`，且某个 `except` 分支里 `return {…字面量…}`。
    那份字面量是「THEME 取不到时顶上」的备份（离线/测试场景），**必须**写死 ——
    若改成 `THEME[key]` 就成了自引用，正是它要兜的场景失效。
    护栏对本集合内的行静默（豁免数会打进结果行，可见、不隐形）。

    解析失败一律返回空集（护栏是旁路，绝不因解析问题改变扫描结果）。
    """
    spans = set()
    try:
        tree = ast.parse(src)
    except Exception:
        return spans
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        has_theme = False
        for st in node.body:
            for sub in ast.walk(st):
                if (isinstance(sub, ast.Return) and isinstance(sub.value, ast.Name)
                        and sub.value.id == 'THEME'):
                    has_theme = True
        if not has_theme:
            continue
        for h in node.handlers:
            for st in h.body:
                if isinstance(st, ast.Return) and isinstance(st.value, ast.Dict):
                    lo = st.lineno
                    hi = getattr(st, 'end_lineno', lo) or lo
                    spans.update(range(lo, hi + 1))
    return spans


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
    # v4.197.0：tests/ 与 backup_*/ 不进扫描范围。
    # 理由：**测试是「检查」颜色的地方，不是「使用」颜色的地方**。
    # 可读性守卫（tests/test_readability_197.py）必须在代码里写死旧值 #9aa0a6
    # 才能证明"它不达标"——把它算成违规，等于禁止写这条守卫。
    # 同理 backup_*/ 是历史快照，改它没有意义，也不该阻塞构建。
    _EXCLUDE_DIRS = ('tests', 'backup', '__pycache__', '.git', 'dist')

    def _excluded(dirpath):
        rel = os.path.relpath(dirpath, root).replace('\\', '/')
        return any(seg in rel.split('/') for seg in _EXCLUDE_DIRS)

    ui_files = []
    for _root, _dirs, _files in os.walk(root):
        if _excluded(_root):
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
            probe = _strip_comments_keep_lines(txt)
            # 判定必须用「剥注释后的源码」：注释里出现 THEME / setStyleSheet 不该改变
            # 扫描集合。2026-10-03 之前用原文判定 —— 在导出层模块里写一句含
            # THEME 字样的说明注释，就把它拉进了扫描范围（扫描边界由注释决定，太脆）。
            # 改为剥注释后，导出层模块靠真实的 `from ui import THEME` 入选。
            if ('setStyleSheet' in probe) or re.search(r'(panel|ui|widget|style|_view)\.py', fn, re.I) or 'THEME' in probe:
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
    """返回 (approved, ui_files, hits, fallback_hits)。

    hits = list of (rel, line_no, hex_lower)；
    fallback_hits = 被「THEME 兜底」豁免掉的命中数（只报数，供结果行显示）。
    """
    approved = _collect_approved_palette(root)
    ui_files = _find_ui_files(root)
    hits = []
    fallback_hits = 0
    for fp in sorted(set(ui_files)):
        try:
            raw = open(fp, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        src = _strip_comments_keep_lines(raw)
        fallback = _theme_fallback_spans(src)
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
            if i in fallback:
                fallback_hits += len(HEX.findall(line))
                continue
            for m in HEX.finditer(line):
                rel = os.path.relpath(fp, root)
                hits.append((rel, i, m.group(0).lower()))
    return approved, ui_files, hits, fallback_hits


def ui_hex_guard(root):
    """扫描 root 下 UI 源文件，返回是否通过（True=无「基线外」HARD 违规）。"""
    approved, ui_files, hits, fallback_hits = _scan(root)
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
    print('[build_safe] ✅ UI 裸 hex 护栏通过（扫描 %d 个 UI 源文件，新增违规 0 / 存量 %d 处已基线豁免 / %d 处 THEME 兜底豁免）'
          % (len(ui_files), len(baselined), fallback_hits))
    return True


def _write_baseline(root):
    _, _, hits, _ = _scan(root)
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
