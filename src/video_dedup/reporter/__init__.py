"""报告生成模块"""

import json
import html as html_lib
import math
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from video_dedup.models import DeduplicationReport, DuplicateGroup, VideoFile

if TYPE_CHECKING:
    pass


class BaseReporter(ABC):
    """报告生成器基类"""

    @abstractmethod
    def generate(self, report: DeduplicationReport, output_path: Path | None = None) -> str:
        """生成报告

        Args:
            report: 去重报告对象
            output_path: 输出文件路径，如果为None则只返回内容

        Returns:
            生成的报告内容
        """
        pass

    def _diagnostics_text(self, report: DeduplicationReport) -> str:
        lines = [f"完成判定: {report.analyzed_videos} | 待确认: {len(report.pending_review)} | "
                 f"失败: {len(report.failures)}"]
        for item in report.pending_review:
            lines.append(f"待确认: {item['path']} - {item['reason']}")
            for sample in item["sample_positions"]:
                if sample["status"] != "ok":
                    lines.append(f"  失败采样: {sample}")
            for candidate in item["candidates"]:
                lines.append(f"  候选: {candidate['path']} | 可读部分相似度 "
                             f"{candidate['similarity']:.2%} | 覆盖率 {candidate['coverage']:.2%}")
        for failure in report.failures:
            lines.append(f"失败: {failure.get('path', '')} - {failure['reason']}")
        if report.operation:
            stats = report.operation
            lines.append(f"计划处理: {stats['planned']} | 模拟操作: {stats['simulated']} | "
                         f"实际成功: {stats['succeeded']} | 失败: {stats['failed']} | 跳过: {stats['skipped']}")
        return "\n".join(lines)

    def _format_size(self, size: int) -> str:
        """格式化文件大小

        Args:
            size: 文件大小(字节)

        Returns:
            格式化后的字符串，如 "1.25 GB"
        """
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"

    def _format_duration(self, seconds: float) -> str:
        """格式化时长

        Args:
            seconds: 时长(秒)

        Returns:
            格式化后的字符串，如 "1:30:45"
        """
        if not math.isfinite(seconds) or seconds <= 0:
            return "N/A"
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def _get_video_by_id(self, report: DeduplicationReport, video_id: str) -> VideoFile | None:
        """根据ID获取视频对象

        Args:
            report: 去重报告
            video_id: 视频ID

        Returns:
            VideoFile对象，如果不存在返回None
        """
        return report.video_map.get(video_id)


class TextReporter(BaseReporter):
    """文本报告生成器"""

    def generate(self, report: DeduplicationReport, output_path: Path | None = None) -> str:
        """生成纯文本报告"""
        lines = [
            "=" * 70,
            "视频去重报告",
            "=" * 70,
            f"生成时间: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"扫描视频总数: {report.total_videos}",
            f"重复组数量: {report.duplicate_groups}",
            f"重复文件数量: {report.total_duplicates}",
            f"可节省空间: {self._format_size(report.space_savings)}",
            "",
        ]

        for i, group in enumerate(report.groups, 1):
            lines.extend(self._format_group(report, group, i))
            lines.append("")

        lines.append(self._diagnostics_text(report))
        content = "\n".join(lines)

        if output_path:
            output_path.write_text(content, encoding="utf-8")

        return content

    def _format_group(self, report: DeduplicationReport, group: DuplicateGroup, index: int) -> list[str]:
        """格式化单个重复组

        Args:
            report: 去重报告
            group: 重复组
            index: 组序号

        Returns:
            格式化后的行列表
        """
        lines = [
            f"{'─' * 70}",
            f"重复组 {index}",
            f"{'─' * 70}",
            f"相似度: {group.similarity:.2%}",
            f"原因: {group.reason}",
            f"文件数量: {group.count}",
            "",
        ]

        # 获取代表文件
        representative = self._get_video_by_id(report, group.representative_id) if group.representative_id else None

        for video_id in group.video_ids:
            video = self._get_video_by_id(report, video_id)
            if video:
                is_representative = video_id == group.representative_id
                lines.extend(self._format_video(video, is_representative))

        return lines

    def _format_video(self, video: VideoFile, is_representative: bool = False) -> list[str]:
        """格式化单个视频信息

        Args:
            video: 视频对象
            is_representative: 是否为保留文件

        Returns:
            格式化后的行列表
        """
        marker = "★ [保留]" if is_representative else "  [待处理]"
        lines = [
            f"{marker} {video.path}",
            f"     大小: {self._format_size(video.size)}",
            f"     分辨率: {video.resolution_str}",
            f"     时长: {self._format_duration(video.duration)}",
            f"     格式: {video.format or 'N/A'}",
            "",
        ]
        return lines


class HTMLReporter(BaseReporter):
    """HTML 报告生成器"""

    def generate(self, report: DeduplicationReport, output_path: Path | None = None) -> str:
        """生成 HTML 格式报告"""
        html_parts = [
            self._generate_html_header(),
            self._generate_summary_section(report),
            self._generate_groups_section(report),
            "<section><h2>判定状态与异常</h2><pre>" + html_lib.escape(self._diagnostics_text(report)) + "</pre></section>",
            self._generate_html_footer(),
        ]

        html = "\n".join(html_parts)

        if output_path:
            output_path.write_text(html, encoding="utf-8")

        return html

    def _generate_html_header(self) -> str:
        """生成 HTML 头部"""
        return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>视频去重报告</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
        }

        .header {
            text-align: center;
            color: white;
            padding: 30px 0;
        }

        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }

        .header .subtitle {
            font-size: 1.1em;
            opacity: 0.9;
        }

        .summary {
            background: white;
            border-radius: 15px;
            padding: 25px;
            margin-bottom: 30px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.15);
        }

        .summary h2 {
            color: #333;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #667eea;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
        }

        .stat-card {
            background: linear-gradient(135deg, #f5f7fa 0%, #e4e8eb 100%);
            border-radius: 10px;
            padding: 20px;
            text-align: center;
        }

        .stat-card.highlight {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }

        .stat-value {
            font-size: 2em;
            font-weight: bold;
            margin-bottom: 5px;
        }

        .stat-label {
            font-size: 0.9em;
            opacity: 0.8;
        }

        .group-card {
            background: white;
            border-radius: 15px;
            margin-bottom: 20px;
            overflow: hidden;
            box-shadow: 0 5px 20px rgba(0,0,0,0.1);
        }

        .group-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .group-title {
            font-size: 1.2em;
            font-weight: 600;
        }

        .group-meta {
            display: flex;
            gap: 15px;
            font-size: 0.9em;
            opacity: 0.9;
        }

        .video-list {
            padding: 15px;
        }

        .video-item {
            display: grid;
            grid-template-columns: auto 1fr auto;
            gap: 15px;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 10px;
            align-items: center;
        }

        .video-item.representative {
            background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%);
            border-left: 4px solid #28a745;
        }

        .video-item.duplicate {
            background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%);
            border-left: 4px solid #ffc107;
        }

        .video-badge {
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }

        .badge-keep {
            background: #28a745;
            color: white;
        }

        .badge-delete {
            background: #dc3545;
            color: white;
        }

        .video-info {
            display: flex;
            flex-direction: column;
            gap: 5px;
        }

        .video-path {
            font-weight: 500;
            color: #333;
            word-break: break-all;
        }

        .video-details {
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            font-size: 0.9em;
            color: #666;
        }

        .video-detail-item {
            display: flex;
            align-items: center;
            gap: 5px;
        }

        .video-size {
            font-size: 0.95em;
            font-weight: 600;
            text-align: right;
            min-width: 100px;
        }

        .footer {
            text-align: center;
            color: white;
            padding: 20px;
            opacity: 0.8;
            font-size: 0.9em;
        }

        .icon {
            display: inline-block;
            width: 16px;
            text-align: center;
        }

        @media (max-width: 768px) {
            .header h1 {
                font-size: 1.8em;
            }

            .stats-grid {
                grid-template-columns: repeat(2, 1fr);
            }

            .video-item {
                grid-template-columns: 1fr;
            }

            .video-size {
                text-align: left;
            }
        }
    </style>
</head>
<body>
    <div class="container">"""

    def _generate_summary_section(self, report: DeduplicationReport) -> str:
        """生成汇总统计区域"""
        return f"""
        <div class="header">
            <h1>视频去重报告</h1>
            <p class="subtitle">生成时间: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>

        <div class="summary">
            <h2>统计概览</h2>
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-value">{report.total_videos}</div>
                    <div class="stat-label">扫描视频总数</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{report.duplicate_groups}</div>
                    <div class="stat-label">重复组数量</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value">{report.total_duplicates}</div>
                    <div class="stat-label">重复文件数量</div>
                </div>
                <div class="stat-card highlight">
                    <div class="stat-value">{self._format_size(report.space_savings)}</div>
                    <div class="stat-label">可节省空间</div>
                </div>
            </div>
        </div>"""

    def _generate_groups_section(self, report: DeduplicationReport) -> str:
        """生成分组卡片区域"""
        groups_html = ['<div class="groups">']

        for i, group in enumerate(report.groups, 1):
            groups_html.append(self._generate_group_card(report, group, i))

        groups_html.append("</div>")
        return "\n".join(groups_html)

    def _generate_group_card(self, report: DeduplicationReport, group: DuplicateGroup, index: int) -> str:
        """生成单个分组卡片"""
        # 计算组内总大小
        total_size = 0
        for video_id in group.video_ids:
            video = self._get_video_by_id(report, video_id)
            if video:
                total_size += video.size

        video_items = []
        for video_id in group.video_ids:
            video = self._get_video_by_id(report, video_id)
            if video:
                is_representative = video_id == group.representative_id
                video_items.append(self._generate_video_item(video, is_representative))

        return f"""
        <div class="group-card">
            <div class="group-header">
                <span class="group-title">重复组 #{index}</span>
                <div class="group-meta">
                    <span>相似度: {group.similarity:.1%}</span>
                    <span>文件: {group.count} 个</span>
                    <span>总计: {self._format_size(total_size)}</span>
                </div>
            </div>
            <div class="video-list">
                {"".join(video_items)}
            </div>
        </div>"""

    def _generate_video_item(self, video: VideoFile, is_representative: bool) -> str:
        """生成单个视频项"""
        item_class = "representative" if is_representative else "duplicate"
        badge_class = "badge-keep" if is_representative else "badge-delete"
        badge_text = "保留" if is_representative else "删除"

        return f"""
                <div class="video-item {item_class}">
                    <span class="video-badge {badge_class}">{badge_text}</span>
                    <div class="video-info">
                        <div class="video-path">{video.path}</div>
                        <div class="video-details">
                            <span class="video-detail-item">
                                <span class="icon">📐</span>
                                {video.resolution_str}
                            </span>
                            <span class="video-detail-item">
                                <span class="icon">⏱️</span>
                                {self._format_duration(video.duration)}
                            </span>
                            <span class="video-detail-item">
                                <span class="icon">📁</span>
                                {video.format or 'N/A'}
                            </span>
                        </div>
                    </div>
                    <div class="video-size">{self._format_size(video.size)}</div>
                </div>"""

    def _generate_html_footer(self) -> str:
        """生成 HTML 底部"""
        return """
        <div class="footer">
            <p>视频去重工具 - 智能识别重复视频</p>
        </div>
    </div>
</body>
</html>"""


class JSONReporter(BaseReporter):
    """JSON 报告生成器"""

    def generate(self, report: DeduplicationReport, output_path: Path | None = None) -> str:
        """生成 JSON 格式报告"""
        data = {
            "pending_review": report.pending_review,
            "failures": report.failures,
            "fingerprint_details": report.fingerprint_details,
            "file_statuses": report.file_statuses,
            "operation": report.operation,
            "generated_at": report.generated_at.isoformat(),
            "summary": {
                "total_videos": report.total_videos,
                "analyzed_videos": report.analyzed_videos,
                "pending_review_count": len(report.pending_review),
                "failure_count": len(report.failures),
                "duplicate_groups": report.duplicate_groups,
                "total_duplicates": report.total_duplicates,
                "space_savings": report.space_savings,
                "space_savings_formatted": self._format_size(report.space_savings),
            },
            "groups": [
                self._format_group(report, group, i)
                for i, group in enumerate(report.groups, 1)
            ],
        }

        content = json.dumps(data, indent=2, ensure_ascii=False)

        if output_path:
            output_path.write_text(content, encoding="utf-8")

        return content

    def _format_group(self, report: DeduplicationReport, group: DuplicateGroup, index: int) -> dict:
        """格式化单个重复组

        Args:
            report: 去重报告
            group: 重复组
            index: 组序号

        Returns:
            格式化后的字典
        """
        videos = []
        for video_id in group.video_ids:
            video = self._get_video_by_id(report, video_id)
            if video:
                videos.append(self._format_video(video, video_id == group.representative_id))

        return {
            "id": group.id,
            "index": index,
            "similarity": group.similarity,
            "reason": group.reason,
            "match_type": group.match_type,
            "file_count": group.count,
            "representative_id": group.representative_id,
            "videos": videos,
        }

    def _format_video(self, video: VideoFile, is_representative: bool) -> dict:
        """格式化单个视频信息

        Args:
            video: 视频对象
            is_representative: 是否为保留文件

        Returns:
            格式化后的字典
        """
        return {
            "id": video.id,
            "path": str(video.path),
            "size": video.size,
            "size_formatted": self._format_size(video.size),
            "duration": video.duration,
            "duration_formatted": self._format_duration(video.duration),
            "resolution": {
                "width": video.width,
                "height": video.height,
                "string": video.resolution_str,
            },
            "format": video.format,
            "is_representative": is_representative,
            "checksum": video.checksum,
            "checksum_algorithm": video.checksum_algorithm,
            "created_at": video.created_at.isoformat() if video.created_at else None,
            "modified_at": video.modified_at.isoformat() if video.modified_at else None,
        }


class ConsoleReporter(BaseReporter):
    """控制台报告生成器 (使用 rich 库美化输出)"""

    def __init__(self, use_color: bool = True):
        """初始化控制台报告生成器

        Args:
            use_color: 是否使用彩色输出
        """
        self.use_color = use_color
        self._console = None

    def _get_console(self):
        """延迟加载 rich Console"""
        if self._console is None:
            try:
                from rich.console import Console
                self._console = Console(force_terminal=self.use_color)
            except ImportError:
                # rich 未安装，使用简单输出
                self._console = None
        return self._console

    def generate(self, report: DeduplicationReport, output_path: Path | None = None) -> str:
        """生成控制台报告"""
        console = self._get_console()

        if console:
            return self._generate_rich_report(report, console, output_path)
        else:
            return self._generate_simple_report(report, output_path)

    def _generate_rich_report(self, report: DeduplicationReport, console, output_path: Path | None) -> str:
        """使用 rich 库生成美观的控制台报告"""
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        from io import StringIO

        # 捕获输出
        string_io = StringIO()
        from rich.console import Console as RichConsole
        capture_console = RichConsole(file=string_io, force_terminal=True, width=120)

        # 标题
        title = Text("视频去重报告", style="bold magenta")
        capture_console.print(Panel(title, expand=False))

        # 统计摘要
        summary_table = Table(show_header=False, box=None, padding=(0, 2))
        summary_table.add_column("Label", style="cyan")
        summary_table.add_column("Value", style="green")

        summary_table.add_row("生成时间", report.generated_at.strftime("%Y-%m-%d %H:%M:%S"))
        summary_table.add_row("扫描视频总数", str(report.total_videos))
        summary_table.add_row("重复组数量", str(report.duplicate_groups))
        summary_table.add_row("重复文件数量", str(report.total_duplicates))
        summary_table.add_row("可节省空间", self._format_size(report.space_savings))

        capture_console.print(Panel(summary_table, title="统计概览", border_style="blue"))

        # 每个重复组的详细信息
        for i, group in enumerate(report.groups, 1):
            group_table = Table(title=f"重复组 #{i}", show_lines=True, expand=True)
            group_table.add_column("状态", style="bold", width=8)
            group_table.add_column("文件路径", style="white")
            group_table.add_column("大小", style="yellow", justify="right", width=12)
            group_table.add_column("分辨率", style="cyan", width=12)
            group_table.add_column("时长", style="magenta", width=10)

            for video_id in group.video_ids:
                video = self._get_video_by_id(report, video_id)
                if video:
                    is_rep = video_id == group.representative_id
                    status = Text("保留", style="bold green") if is_rep else Text("待处理", style="bold red")
                    group_table.add_row(
                        status,
                        str(video.path),
                        self._format_size(video.size),
                        video.resolution_str,
                        self._format_duration(video.duration),
                    )

            capture_console.print(group_table)
            capture_console.print(f"  相似度: [bold]{group.similarity:.1%}[/] | 原因: {group.reason}")
            capture_console.print()

        capture_console.print(self._diagnostics_text(report), markup=False, highlight=False)
        content = string_io.getvalue()

        if output_path:
            # 写入纯文本版本（去除ANSI颜色码）
            import re
            clean_content = re.sub(r'\x1b\[[0-9;]*m', '', content)
            output_path.write_text(clean_content, encoding="utf-8")

        return content

    def _generate_simple_report(self, report: DeduplicationReport, output_path: Path | None) -> str:
        """生成简单文本报告（rich库未安装时的回退方案）"""
        lines = [
            "=" * 60,
            "视频去重报告",
            "=" * 60,
            f"生成时间: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"扫描视频总数: {report.total_videos}",
            f"重复组数量: {report.duplicate_groups}",
            f"重复文件数量: {report.total_duplicates}",
            f"可节省空间: {self._format_size(report.space_savings)}",
            "",
        ]

        for i, group in enumerate(report.groups, 1):
            lines.append(f"--- 重复组 #{i} (相似度: {group.similarity:.1%}) ---")
            lines.append(f"原因: {group.reason}")

            for video_id in group.video_ids:
                video = self._get_video_by_id(report, video_id)
                if video:
                    is_rep = video_id == group.representative_id
                    status = "[保留]" if is_rep else "[待处理]"
                    lines.append(
                        f"  {status} {video.path} | "
                        f"{self._format_size(video.size)} | "
                        f"{video.resolution_str} | "
                        f"{self._format_duration(video.duration)}"
                    )
            lines.append("")

        lines.append(self._diagnostics_text(report))
        content = "\n".join(lines)

        if output_path:
            output_path.write_text(content, encoding="utf-8")

        return content

    def print(self, report: DeduplicationReport) -> None:
        """直接打印报告到控制台

        Args:
            report: 去重报告
        """
        console = self._get_console()

        if console:
            self._print_rich_report(report, console)
            console.print(self._diagnostics_text(report), markup=False, highlight=False)
        else:
            print(self._generate_simple_report(report, None))

    def _print_rich_report(self, report: DeduplicationReport, console) -> None:
        """使用 rich 库打印美观的控制台报告"""
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text

        # 标题
        title = Text("视频去重报告", style="bold magenta")
        console.print(Panel(title, expand=False))

        # 统计摘要
        summary_table = Table(show_header=False, box=None, padding=(0, 2))
        summary_table.add_column("Label", style="cyan")
        summary_table.add_column("Value", style="green")

        summary_table.add_row("生成时间", report.generated_at.strftime("%Y-%m-%d %H:%M:%S"))
        summary_table.add_row("扫描视频总数", str(report.total_videos))
        summary_table.add_row("重复组数量", str(report.duplicate_groups))
        summary_table.add_row("重复文件数量", str(report.total_duplicates))
        summary_table.add_row("可节省空间", self._format_size(report.space_savings))

        console.print(Panel(summary_table, title="统计概览", border_style="blue"))

        # 每个重复组
        for i, group in enumerate(report.groups, 1):
            console.print()
            console.rule(f"[bold blue]重复组 #{i}[/]")

            info_text = Text()
            info_text.append("相似度: ", style="dim")
            info_text.append(f"{group.similarity:.1%}", style="bold yellow")
            info_text.append(" | 原因: ", style="dim")
            info_text.append(group.reason, style="white")
            console.print(info_text)

            group_table = Table(show_header=True, box=None, padding=(0, 1), expand=True)
            group_table.add_column("状态", width=8)
            group_table.add_column("文件路径")
            group_table.add_column("大小", justify="right", width=12)
            group_table.add_column("分辨率", width=12)
            group_table.add_column("时长", width=10)

            for video_id in group.video_ids:
                video = self._get_video_by_id(report, video_id)
                if video:
                    is_rep = video_id == group.representative_id
                    if is_rep:
                        status = Text("保留", style="bold green")
                    else:
                        status = Text("待处理", style="bold red")

                    group_table.add_row(
                        status,
                        str(video.path),
                        self._format_size(video.size),
                        video.resolution_str,
                        self._format_duration(video.duration),
                    )

            console.print(group_table)


__all__ = [
    "BaseReporter",
    "TextReporter",
    "HTMLReporter",
    "JSONReporter",
    "ConsoleReporter",
]
