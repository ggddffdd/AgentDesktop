"""PEP 701（Python 3.12+）f-string 语法检测器 —— 纯标准库，无第三方依赖。

**为什么需要它**：本项目对外声明支持 Python 3.10+，但 3.12 引入的新 f-string
语法（同一引号嵌套、表达式内反斜杠）在 3.11 及以前是 `SyntaxError`。

**关键陷阱（实测）**：在 3.12 解释器上用 `ast.parse(src, feature_version=(3,11))`
**抓不到**这类写法 —— 3.10/3.11/3.12 全部解析通过。`feature_version` 管不到
f-string 的 tokenizer 行为，所以只能自己做词法级扫描，本模块就是那个扫描器。

用法：
    from pep701_lint import find_pep701
    for ln, why in find_pep701(open(p, encoding="utf-8-sig").read()):
        print(p, ln, why)
"""

import io
import tokenize

_QUOTES = ("'''", '"""', "'", '"')


def _quote_of(tok_str):
    """取字符串/f-string 字面量的引号形态（剥掉 r/b/f/u 前缀）。"""
    i = 0
    while i < len(tok_str) and tok_str[i] not in "\"'":
        i += 1
    rest = tok_str[i:]
    for q in _QUOTES:
        if rest.startswith(q):
            return q
    return rest[:1]


def find_pep701(src):
    """返回 [(行号, 说明)]：源码里所有「仅 3.12+ 合法」的 f-string 写法。"""
    hits = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError) as e:
        return [(getattr(e, "lineno", 0) or 0, "无法词法分析: %s" % e)]
    stack = []          # 当前 f-string 嵌套栈，存外层引号形态
    for t in toks:
        if t.type == tokenize.FSTRING_START:
            stack.append(_quote_of(t.string))
        elif t.type == tokenize.FSTRING_END:
            if stack:
                stack.pop()
        elif stack and t.type == tokenize.STRING:
            q = _quote_of(t.string)
            if q == stack[-1]:
                hits.append((t.start[0],
                             "内层字符串引号 %s 与外层 f-string 同类（Python 3.12+ 才允许）" % q))
        elif stack and t.type == tokenize.ERRORTOKEN and t.string.strip() == "\\":
            hits.append((t.start[0], "f-string 表达式内出现反斜杠（Python 3.12+ 才允许）"))
    return hits
