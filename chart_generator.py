# -*- coding: utf-8 -*-
"""
数据可视化输出模块 v1.1
集成 matplotlib，生成柱状/折线/饼/散点图，自动配置中文字体（Windows 微软雅黑），治愈系配色。
输出 PNG 到用户目录 ~/Documents/AgentDesktop/charts/（避开重建覆盖），返回路径。

使用：
    from chart_generator import ChartGenerator
    gen = ChartGenerator()
    gen.generate("bar", {"categories": [...], "values": [...]}, title="销售", output_path="...")
"""
import os
import time
import matplotlib
matplotlib.use("Agg")  # 非交互式后端
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
from pathlib import Path


def _charts_dir():
    d = os.path.join(os.path.expanduser("~"), "Documents", "AgentDesktop", "charts")
    os.makedirs(d, exist_ok=True)
    return d


class ChartGenerator:
    def __init__(self):
        self._setup_chinese_font()
        # 配色方案（治愈系 / 荷花山水风格）
        self.palettes = {
            "healing": ['#E8D5C4', '#B8D4C8', '#A8C8E8', '#D4B8E8', '#E8C8B8'],
            "lotus": ['#F0E6EF', '#E6D5E8', '#D5C8E0', '#C8B8D8', '#B8A8D0'],
            "landscape": ['#D4E8D0', '#C8E0C4', '#B8D8B4', '#A8D0A4', '#98C894'],
            "default": ['#5B9BD5', '#ED7D31', '#A5A5A5', '#FFC000', '#70AD47'],
        }

    def _setup_chinese_font(self):
        """设置中文字体（Windows 微软雅黑优先）。"""
        candidates = [
            r'C:\Windows\Fonts\msyh.ttc',
            r'C:\Windows\Fonts\simhei.ttf',
            r'C:\Windows\Fonts\simsun.ttc',
        ]
        for fp in candidates:
            if os.path.exists(fp):
                try:
                    fm.fontManager.addfont(fp)
                    prop = fm.FontProperties(fname=fp)
                    plt.rcParams['font.sans-serif'] = [prop.get_name()]
                    plt.rcParams['axes.unicode_minus'] = False
                    return
                except Exception:
                    continue
        plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

    def generate(self, chart_type, data, title="图表", palette="default", output_path=None):
        """统一生成接口。"""
        if output_path is None:
            output_path = os.path.join(_charts_dir(), f"chart_{int(time.time())}.png")
        generators = {
            "bar": self._bar,
            "line": self._line,
            "pie": self._pie,
            "scatter": self._scatter,
        }
        g = generators.get(chart_type)
        if not g:
            return {"status": "error", "message": f"不支持的图表类型: {chart_type}"}
        try:
            return g(data, title, palette, output_path)
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _bar(self, data, title, palette, out):
        fig, ax = plt.subplots(figsize=(10, 6))
        cats = data.get("categories", [])
        vals = data.get("values", [])
        colors = self.palettes.get(palette, self.palettes["default"])
        bars = ax.bar(cats, vals, color=colors[:len(cats)], alpha=0.85, edgecolor='white')
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2., b.get_height() + max(vals) * 0.01,
                    f'{v}', ha='center', va='bottom', fontsize=10)
        ax.set_title(title, fontsize=14, pad=15)
        ax.set_ylabel('数值', fontsize=12)
        ax.grid(axis='y', alpha=0.3)
        plt.tight_layout()
        plt.savefig(out, dpi=150, bbox_inches='tight')
        plt.close()
        return {"status": "success", "path": out}

    def _line(self, data, title, palette, out):
        fig, ax = plt.subplots(figsize=(10, 6))
        cats = data.get("categories", [])
        vals = data.get("values", [])
        color = self.palettes.get(palette, self.palettes["default"])[0]
        ax.plot(cats, vals, marker='o', linewidth=2, markersize=8, color=color)
        for c, v in zip(cats, vals):
            ax.annotate(f'{v}', (c, v), textcoords="offset points", xytext=(0, 10), ha='center')
        ax.set_title(title, fontsize=14, pad=15)
        ax.set_ylabel('数值', fontsize=12)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(out, dpi=150, bbox_inches='tight')
        plt.close()
        return {"status": "success", "path": out}

    def _pie(self, data, title, palette, out):
        fig, ax = plt.subplots(figsize=(8, 8))
        labels = data.get("labels", [])
        sizes = data.get("sizes", [])
        colors = self.palettes.get(palette, self.palettes["default"])
        ax.pie(sizes, labels=labels, colors=colors[:len(labels)],
               autopct='%1.1f%%', startangle=90, textprops={'fontsize': 11})
        ax.set_title(title, fontsize=14, pad=15)
        plt.tight_layout()
        plt.savefig(out, dpi=150, bbox_inches='tight')
        plt.close()
        return {"status": "success", "path": out}

    def _scatter(self, data, title, palette, out):
        fig, ax = plt.subplots(figsize=(10, 6))
        x = data.get("x", [])
        y = data.get("y", [])
        color = self.palettes.get(palette, self.palettes["default"])[0]
        ax.scatter(x, y, c=color, alpha=0.6, s=100, edgecolors='white', linewidth=1)
        ax.set_title(title, fontsize=14, pad=15)
        ax.set_xlabel('X 轴', fontsize=12)
        ax.set_ylabel('Y 轴', fontsize=12)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(out, dpi=150, bbox_inches='tight')
        plt.close()
        return {"status": "success", "path": out}
