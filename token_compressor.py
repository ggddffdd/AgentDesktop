# -*- coding: utf-8 -*-
"""小臭玩AI — Token 压缩器 v4.60

参照 OpenHuman TokenJuice 设计：工具结果进模型前自动清理压缩。
三层规则：
  1. HTML 标签剥离
  2. 重复行去重
  3. 智能截断（在段落边界，保留开头+结尾关键信息）

不丢失关键数据，仅裁剪无效 Token。
"""

import re


def compress(text: str, max_chars: int = 4000) -> str:
    """压缩文本，目标 max_chars 字符以内。

    - HTML 标签/脚本/CSS 全部移除
    - 连续空行合并
    - 重复行去重（保留首次出现）
    - 超出 max_chars 时：保留开头 2/3 + 结尾 1/3，中间插省略标记
    """
    if not text or len(text) <= max_chars:
        return text

    # 1. 去 HTML
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.I)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-z]+;', ' ', text)  # &nbsp; &amp; 等

    # 2. 合并空白
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 3. 去重（连续出现的相同行）
    lines = text.split('\n')
    seen = set()
    deduped = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            deduped.append('')
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        deduped.append(line)
    text = '\n'.join(deduped)

    # 4. 如果还是超长 → 智能截断
    if len(text) <= max_chars:
        return text

    # 在段落边界截断：保留前 2/3 + 后 1/3
    head_size = int(max_chars * 0.65)
    tail_size = max_chars - head_size - 100  # 100 给省略标记

    head = text[:head_size]
    tail = text[-tail_size:]

    # 对齐到换行
    if '\n' in head[head_size//2:]:
        head = head.rsplit('\n', 1)[0]
    if '\n' in tail[:tail_size//2]:
        tail = tail.split('\n', 1)[-1]

    sep = "\n\n…[中间内容已压缩，完整结果可通过 read_file 查看]…\n\n"
    result = head + sep + tail

    # 如果还不够短（极端情况），硬截断
    if len(result) > max_chars:
        result = result[:max_chars - 80] + "\n…[已截断]"

    return result


def compress_html(html: str, max_chars: int = 4000) -> str:
    """专门处理 HTML 网页内容的压缩：提取正文文本，去导航/广告。"""
    # 去掉 nav/footer/script/style/iframe/header
    for tag in ('nav', 'footer', 'script', 'style', 'iframe', 'header', 'aside'):
        html = re.sub(f'<{tag}[^>]*>.*?</{tag}>', '', html, flags=re.DOTALL | re.I)
    # 提取可见文本
    text = re.sub(r'<[^>]+>', '\n', html)
    text = re.sub(r'&[a-z]+;', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return compress(text, max_chars)
