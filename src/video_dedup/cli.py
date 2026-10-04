"""命令行接口"""

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from video_dedup import __version__
from video_dedup.config import Config
from video_dedup.extractor import VideoFingerprintExtractor
from video_dedup.deduplicator import DeduplicationEngine
from video_dedup.reporter import TextReporter, HTMLReporter, JSONReporter, ConsoleReporter
from video_dedup.handler import get_handler
from video_dedup.models import VideoFile


console = Console()


def setup_logging(verbose: bool = False, debug: bool = False) -> None:
    """设置日志级别

    Args:
        verbose: 是否输出详细日志
        debug: 是否输出调试日志
    """
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    # 配置根日志器
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    # 设置第三方库日志级别
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)


def format_size(size: int) -> str:
    """格式化文件大小

    Args:
        size: 文件大小(字节)

    Returns:
        格式化后的字符串
    """
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def format_duration(seconds: float) -> str:
    """格式化时长

    Args:
        seconds: 时长(秒)

    Returns:
        格式化后的字符串
    """
    if seconds <= 0:
        return "N/A"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


@click.group()
@click.version_option(version=__version__)
@click.option("--config", "-c", type=click.Path(exists=True), help="配置文件路径")
@click.option("--verbose", "-v", is_flag=True, help="显示详细输出")
@click.option("--debug", "-d", is_flag=True, help="显示调试信息")
@click.pass_context
def main(ctx: click.Context, config: str | None, verbose: bool, debug: bool) -> None:
    """视频去重工具 - 检测并处理重复视频文件"""
    ctx.ensure_object(dict)

    # 设置日志
    setup_logging(verbose=verbose, debug=debug)

    # 加载配置
    cfg = Config()
    if config:
        raise click.UsageError("配置文件加载尚未实现，请使用 CLI 参数；不会静默忽略配置文件")

    ctx.obj["config"] = cfg
    ctx.obj["verbose"] = verbose
    ctx.obj["debug"] = debug


def _analyze(ctx, path: str, threshold: float | None, recursive: bool = True):
    config = ctx.obj["config"]
    if threshold is not None:
        config.comparator.similarity_threshold = threshold
    console.print(f"[blue]分析目录:[/] {Path(path).resolve()}")
    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  console=console, transient=True) as progress:
        progress.add_task("只读分析：摘要及有序画面匹配...", total=None)
        report = DeduplicationEngine().analyze(Path(path), config, recursive)
    console.print(f"扫描成功 {report.total_videos} | 完成判定 {report.analyzed_videos} | "
                  f"待确认 {len(report.pending_review)} | 失败 {len(report.failures)}")
    return report


def _show_report(report, output: str | None, format: str):
    reporters = {"text": TextReporter(), "html": HTMLReporter(),
                 "json": JSONReporter(), "console": ConsoleReporter()}
    reporter = reporters[format]
    content = reporter.generate(report, Path(output) if output else None)
    if output:
        console.print(f"报告已保存至: {output}")
        # 即使保存到文件，也直接显示需要注意的失败路径。
        for failure in report.failures:
            console.print(f"失败: {failure.get('path', '')} - {failure['reason']}")
        for item in report.pending_review:
            console.print(f"待确认: {item['path']} - {item['reason']}")
    else:
        console.print(content, markup=False, highlight=False)


@main.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--recursive/--no-recursive", default=True, help="递归扫描子目录")
@click.option("--threshold", "-t", type=click.FloatRange(0, 1), default=None, help="相似度阈值 (默认: 0.85)")
@click.option("--output", "-o", type=click.Path(), help="输出报告路径")
@click.option("--format", "-f", type=click.Choice(["text", "html", "json", "console"]), default="console")
@click.pass_context
def scan(ctx, path, recursive, threshold, output, format):
    """扫描并检测重复视频，只读。"""
    result = _analyze(ctx, path, threshold, recursive)
    _show_report(result, output, format)
    if result.failures:
        raise click.exceptions.Exit(1)


@main.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--action", "-a", type=click.Choice(["report", "move", "delete", "link"]), default="report")
@click.option("--target", type=click.Path(), help="移动目标目录")
@click.option("--dry-run/--no-dry-run", default=True, help="模拟运行，默认开启")
@click.option("--threshold", "-t", type=click.FloatRange(0, 1), default=None)
@click.option("--output", "-o", type=click.Path())
@click.option("--format", "-f", type=click.Choice(["text", "html", "json"]), default="text")
@click.pass_context
def dedupe(ctx, path, action, target, dry_run, threshold, output, format):
    """处理已确认重复项；实际操作需指定 action 和 --no-dry-run。"""
    config = ctx.obj["config"]
    config.handler.action = action
    config.handler.dry_run = dry_run
    if target:
        config.handler.target_dir = Path(target).resolve()
    console.print(f"操作: {action} | 模拟运行: {dry_run}")
    result = _analyze(ctx, path, threshold)
    handler = get_handler(config.handler)
    from video_dedup.scanner import file_signature
    stats = {"action": action, "dry_run": dry_run,
             "planned": result.total_duplicates, "simulated": 0,
             "succeeded": 0, "failed": 0, "skipped": 0, "messages": []}
    for group in result.groups:
        keep = result.video_map[group.representative_id]
        remove = [result.video_map[vid] for vid in group.video_ids if vid != keep.id]
        valid = []
        for video in remove:
            try:
                stable = (keep.path.is_file() and file_signature(keep.path) == keep.file_signature
                          and file_signature(video.path) == video.file_signature)
            except OSError:
                stable = False
            if not stable:
                message = f"文件或保留文件在分析后变化或无法读取: {video.path}"
                stats["failed"] += 1
                stats["messages"].append(message)
                result.failures.append({"stage": "handle", "path": str(video.path), "reason": message})
            else:
                valid.append(video)
        handled = handler.handle(keep, valid)
        stats["succeeded"] += handled.success_count
        stats["failed"] += handled.failure_count
        if dry_run and action != "report":
            stats["simulated"] += handled.skipped_count
        else:
            stats["skipped"] += handled.skipped_count
        stats["messages"].extend(handled.messages)
        result.failures.extend(handled.failures)
    result.operation = stats
    console.print(f"计划处理 {stats['planned']} | 模拟操作 {stats['simulated']} | "
                  f"实际成功 {stats['succeeded']} | 失败 {stats['failed']} | 跳过 {stats['skipped']}")
    for message in stats["messages"]:
        console.print(message, markup=False, highlight=False)
    _show_report(result, output, format)
    if result.failures or stats["failed"]:
        raise click.exceptions.Exit(1)


@main.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--format", "-f", type=click.Choice(["text", "html", "json", "console"]), default="console")
@click.option("--output", "-o", type=click.Path())
@click.option("--threshold", "-t", type=click.FloatRange(0, 1), default=None)
@click.pass_context
def report(ctx, path, format, output, threshold):
    """重新扫描并生成报告；不读取未实现的缓存。"""
    result = _analyze(ctx, path, threshold)
    _show_report(result, output, format)
    if result.failures:
        raise click.exceptions.Exit(1)


@main.command()
@click.pass_context
def config_cmd(ctx: click.Context) -> None:
    """显示当前配置信息"""
    cfg: Config = ctx.obj["config"]

    console.print(Panel(
        Text("当前配置", style="bold magenta"),
        expand=False,
    ))

    # Scanner 配置
    scanner_table = Table(title="Scanner 配置", show_header=False, box=None)
    scanner_table.add_column("配置项", style="cyan")
    scanner_table.add_column("值", style="green")

    scanner_table.add_row("支持格式", ", ".join(cfg.scanner.supported_formats))
    scanner_table.add_row("最小文件大小", f"{cfg.scanner.min_file_size / 1024 / 1024:.1f} MB")
    if cfg.scanner.max_file_size:
        scanner_table.add_row("最大文件大小", f"{cfg.scanner.max_file_size / 1024 / 1024:.1f} MB")
    scanner_table.add_row("跟随符号链接", str(cfg.scanner.follow_symlinks))
    scanner_table.add_row("排除模式", ", ".join(cfg.scanner.exclude_patterns))

    console.print(scanner_table)
    console.print()

    # Extractor 配置
    extractor_table = Table(title="Extractor 配置", show_header=False, box=None)
    extractor_table.add_column("配置项", style="cyan")
    extractor_table.add_column("值", style="green")

    extractor_table.add_row("关键帧数量", str(cfg.extractor.num_frames))
    extractor_table.add_row("哈希大小", str(cfg.extractor.hash_size))
    extractor_table.add_row("哈希方法", cfg.extractor.hash_method)
    extractor_table.add_row("场景检测", str(cfg.extractor.scene_detection))
    extractor_table.add_row("场景阈值", str(cfg.extractor.scene_threshold))

    console.print(extractor_table)
    console.print()

    # Comparator 配置
    comparator_table = Table(title="Comparator 配置", show_header=False, box=None)
    comparator_table.add_column("配置项", style="cyan")
    comparator_table.add_column("值", style="green")

    comparator_table.add_row("相似度阈值", f"{cfg.comparator.similarity_threshold:.0%}")
    comparator_table.add_row("时长容差", f"{cfg.comparator.duration_tolerance:.0%}")
    comparator_table.add_row("使用 LSH", "未实现（本次分析未使用）")
    comparator_table.add_row("LSH 表数量", str(cfg.comparator.lsh_tables))

    console.print(comparator_table)
    console.print()

    # Handler 配置
    handler_table = Table(title="Handler 配置", show_header=False, box=None)
    handler_table.add_column("配置项", style="cyan")
    handler_table.add_column("值", style="green")

    handler_table.add_row("操作类型", cfg.handler.action)
    handler_table.add_row("模拟运行", str(cfg.handler.dry_run))
    handler_table.add_row("创建备份", str(cfg.handler.create_backup))
    if cfg.handler.target_dir:
        handler_table.add_row("目标目录", str(cfg.handler.target_dir))

    console.print(handler_table)
    console.print()

    # 运行时配置
    runtime_table = Table(title="运行时配置", show_header=False, box=None)
    runtime_table.add_column("配置项", style="cyan")
    runtime_table.add_column("值", style="green")

    runtime_table.add_row("最大工作线程", str(cfg.max_workers))
    runtime_table.add_row("缓存目录", str(cfg.cache_dir))
    runtime_table.add_row("数据库路径", str(cfg.db_path))
    runtime_table.add_row("日志级别", cfg.log_level)

    console.print(runtime_table)


# 为 config 命令添加别名
main.add_command(config_cmd, name="config")


@main.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--num-frames", "-n", type=int, default=32, help="提取的关键帧数量")
@click.pass_context
def extract(ctx: click.Context, path: str, num_frames: int) -> None:
    """提取单个视频的特征信息 (调试用)"""
    config: Config = ctx.obj["config"]
    config.extractor.num_frames = num_frames

    video_path = Path(path).resolve()
    console.print(f"[bold blue]提取视频特征:[/] {video_path}")

    # 创建 VideoFile 对象
    video = VideoFile(path=video_path)

    # 获取文件信息
    try:
        stat = video_path.stat()
        video.size = stat.st_size
    except Exception as e:
        console.print(f"[red]无法读取文件信息: {e}[/]")
        return

    # 提取指纹
    extractor = VideoFingerprintExtractor(config.extractor)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("提取特征...", total=None)
        fingerprint = extractor.extract(video)

    if fingerprint:
        console.print()
        console.print(Panel(
            Text("提取结果", style="bold magenta"),
            expand=False,
        ))

        result_table = Table(show_header=False, box=None)
        result_table.add_column("配置项", style="cyan")
        result_table.add_column("值", style="green")

        result_table.add_row("视频ID", fingerprint.video_id)
        result_table.add_row("指纹ID", fingerprint.id)
        result_table.add_row("帧哈希数量", str(fingerprint.hash_count))
        result_table.add_row("创建时间", fingerprint.created_at.strftime("%Y-%m-%d %H:%M:%S"))

        console.print(result_table)

        # 显示哈希值
        console.print()
        console.print("[bold]帧哈希值:[/]")
        for i, h in enumerate(fingerprint.frame_hashes):
            console.print(f"  帧 {i+1}: {h}")
    else:
        console.print("[red]特征提取失败[/]")


@main.command()
def version() -> None:
    """显示版本信息"""
    console.print(f"[bold]视频去重工具[/] v{__version__}")
    console.print()
    console.print("功能模块:")
    console.print("  - Scanner: 视频文件扫描")
    console.print("  - Extractor: 特征指纹提取")
    console.print("  - Comparator: 相似度计算")
    console.print("  - Deduplicator: 去重分析")
    console.print("  - Reporter: 报告生成")
    console.print("  - Handler: 文件处理")


if __name__ == "__main__":
    main()
