"""文件处理模块"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import logging
import uuid

from video_dedup.models import VideoFile
from video_dedup.config import HandlerConfig


logger = logging.getLogger(__name__)


@dataclass
class HandlerResult:
    """处理结果统计"""
    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    total_size: int = 0  # 处理的文件总大小(字节)
    messages: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        """总处理数量"""
        return self.success_count + self.failure_count + self.skipped_count

    def summary(self) -> str:
        """生成处理结果摘要"""
        lines = [
            f"处理完成:",
            f"  成功: {self.success_count}",
            f"  失败: {self.failure_count}",
            f"  跳过: {self.skipped_count}",
            f"  处理文件总大小: {self._format_size(self.total_size)}",
        ]
        return "\n".join(lines)

    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"


class BaseHandler(ABC):
    """文件处理器基类"""

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.result = HandlerResult()

    def _record_success(self, video: VideoFile, msg: str):
        """记录成功处理"""
        self.result.success_count += 1
        self.result.total_size += video.size
        self.result.messages.append(msg)
        logger.info(msg)

    def _record_failure(self, video: VideoFile, msg: str):
        """记录失败处理"""
        self.result.failure_count += 1
        self.result.messages.append(msg)
        self.result.failures.append({"stage": "handle", "path": str(video.path), "reason": msg})
        logger.error(msg)

    def _record_skip(self, video: VideoFile, msg: str):
        """记录跳过处理"""
        self.result.skipped_count += 1
        self.result.messages.append(msg)
        logger.info(msg)

    def _validate_pair(self, to_keep: VideoFile, video: VideoFile) -> bool:
        from video_dedup.scanner import file_signature
        try:
            if not to_keep.path.is_file() or not video.path.is_file():
                raise OSError("文件或保留文件不存在")
            if to_keep.path.resolve() == video.path.resolve():
                raise OSError("待处理路径与保留路径相同")
            for item in (to_keep, video):
                if item.file_signature is not None and file_signature(item.path) != item.file_signature:
                    raise OSError(f"分析后文件已变化: {item.path}")
            return True
        except OSError as error:
            self._record_failure(video, str(error))
            return False

    def get_result(self) -> HandlerResult:
        """获取处理结果"""
        return self.result

    def reset_result(self):
        """重置处理结果"""
        self.result = HandlerResult()

    @abstractmethod
    def handle(self, to_keep: VideoFile, to_remove: list[VideoFile]) -> HandlerResult:
        """处理重复文件，返回处理结果"""
        pass


class ReportHandler(BaseHandler):
    """仅报告，不执行操作"""

    def __init__(self):
        super().__init__(dry_run=True)

    def handle(self, to_keep: VideoFile, to_remove: list[VideoFile]) -> HandlerResult:
        """报告将要执行的操作"""
        self.reset_result()

        if not to_remove:
            msg = "没有需要处理的重复文件"
            self.result.messages.append(msg)
            logger.info(msg)
            return self.result

        # 报告保留文件
        keep_msg = f"将保留: {to_keep.path} (大小: {self._format_size(to_keep.size)})"
        self.result.messages.append(keep_msg)
        logger.info(keep_msg)

        # 报告将要删除的文件
        for video in to_remove:
            if not self._validate_pair(to_keep, video):
                continue
            msg = f"待处理: {video.path} (大小: {self._format_size(video.size)})"
            self.result.skipped_count += 1
            self.result.total_size += video.size
            self.result.messages.append(msg)
            logger.info(msg)

        return self.result

    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"


class MoveHandler(BaseHandler):
    """移动重复文件"""

    def __init__(self, target_dir: Path, dry_run: bool = True):
        super().__init__(dry_run)
        self.target_dir = Path(target_dir) if target_dir else Path("./duplicates")

    def handle(self, to_keep: VideoFile, to_remove: list[VideoFile]) -> HandlerResult:
        """移动重复文件到目标目录"""
        self.reset_result()

        if not to_remove:
            msg = "没有需要移动的重复文件"
            self.result.messages.append(msg)
            logger.info(msg)
            return self.result

        # 创建目标目录
        if not self.dry_run:
            try:
                self.target_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"目标目录: {self.target_dir}")
            except Exception as e:
                msg = f"创建目标目录失败: {self.target_dir}, 错误: {e}"
                self.result.messages.append(msg)
                logger.error(msg)
                for video in to_remove:
                    self._record_failure(video, msg)
                return self.result

        for video in to_remove:
            if not self._validate_pair(to_keep, video):
                continue
            target = self._get_unique_target(video)

            if self.dry_run:
                msg = f"[模拟] 移动: {video.path} -> {target}"
                self._record_skip(video, msg)
            else:
                try:
                    # 确保目标目录存在
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(video.path), str(target))
                    msg = f"已移动: {video.path} -> {target}"
                    self._record_success(video, msg)
                except Exception as e:
                    msg = f"移动失败: {video.path} -> {target}, 错误: {e}"
                    self._record_failure(video, msg)

        return self.result

    def _get_unique_target(self, video: VideoFile) -> Path:
        """获取唯一的目标路径，处理文件名冲突"""
        target = self.target_dir / video.path.name

        if not target.exists():
            return target

        # 添加序号避免冲突
        i = 1
        while target.exists():
            target = self.target_dir / f"{video.path.stem}_{i}{video.path.suffix}"
            i += 1

        return target


class DeleteHandler(BaseHandler):
    """删除重复文件"""

    def __init__(self, dry_run: bool = True, create_backup: bool = False, backup_dir: Path | None = None):
        super().__init__(dry_run)
        self.create_backup = create_backup
        self.backup_dir = Path(backup_dir) if backup_dir else Path("./backup")

    def handle(self, to_keep: VideoFile, to_remove: list[VideoFile]) -> HandlerResult:
        """删除重复文件"""
        self.reset_result()

        if not to_remove:
            msg = "没有需要删除的重复文件"
            self.result.messages.append(msg)
            logger.info(msg)
            return self.result

        # 创建备份目录
        if self.create_backup and not self.dry_run:
            try:
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"备份目录: {self.backup_dir}")
            except Exception as e:
                msg = f"创建备份目录失败: {self.backup_dir}, 错误: {e}"
                self.result.messages.append(msg)
                logger.error(msg)
                for video in to_remove:
                    self._record_failure(video, msg)
                return self.result

        for video in to_remove:
            if not self._validate_pair(to_keep, video):
                continue
            if self.dry_run:
                backup_info = " (并备份)" if self.create_backup else ""
                msg = f"[模拟] 删除: {video.path}{backup_info}"
                self._record_skip(video, msg)
            else:
                try:
                    # 备份文件
                    if self.create_backup:
                        backup_path = self._backup_file(video)
                        logger.info(f"已备份: {video.path} -> {backup_path}")

                    # 备份可能耗时，删除前再次检查源文件和保留文件。
                    if not self._validate_pair(to_keep, video):
                        continue
                    video.path.unlink()
                    msg = f"已删除: {video.path}"
                    if self.create_backup:
                        msg += f" (备份于: {self.backup_dir})"
                    self._record_success(video, msg)
                except Exception as e:
                    msg = f"删除失败: {video.path}, 错误: {e}"
                    self._record_failure(video, msg)

        return self.result

    def _backup_file(self, video: VideoFile) -> Path:
        """备份文件"""
        backup_path = self._get_unique_backup_path(video)
        shutil.copy2(str(video.path), str(backup_path))
        return backup_path

    def _get_unique_backup_path(self, video: VideoFile) -> Path:
        """获取唯一的备份路径"""
        backup_path = self.backup_dir / video.path.name

        if not backup_path.exists():
            return backup_path

        # 添加序号避免冲突
        i = 1
        while backup_path.exists():
            backup_path = self.backup_dir / f"{video.path.stem}_{i}{video.path.suffix}"
            i += 1

        return backup_path


class LinkHandler(BaseHandler):
    """创建软链接替换重复文件"""

    def __init__(self, dry_run: bool = True):
        super().__init__(dry_run)

    def handle(self, to_keep: VideoFile, to_remove: list[VideoFile]) -> HandlerResult:
        """删除重复文件后创建软链接"""
        self.reset_result()

        if not to_remove:
            msg = "没有需要创建链接的重复文件"
            self.result.messages.append(msg)
            logger.info(msg)
            return self.result

        for video in to_remove:
            if not self._validate_pair(to_keep, video):
                continue
            if self.dry_run:
                msg = f"[模拟] 创建链接: {video.path} -> {to_keep.path}"
                self._record_skip(video, msg)
            else:
                try:
                    # 先成功创建临时链接，再原子替换；创建失败时保留原文件。
                    temporary = video.path.with_name(f".{video.path.name}.{uuid.uuid4().hex}.link")
                    try:
                        temporary.symlink_to(to_keep.path.resolve())
                        if not self._validate_pair(to_keep, video):
                            continue
                        temporary.replace(video.path)
                    finally:
                        if temporary.is_symlink():
                            temporary.unlink()
                    msg = f"已创建链接: {video.path} -> {to_keep.path}"
                    self._record_success(video, msg)
                except Exception as e:
                    msg = f"创建链接失败: {video.path} -> {to_keep.path}, 错误: {e}"
                    self._record_failure(video, msg)

        return self.result


def get_handler(config: HandlerConfig) -> BaseHandler:
    """根据配置获取处理器"""
    action = config.action.lower()

    if action == "report":
        return ReportHandler()
    elif action == "move":
        target_dir = config.target_dir or Path("./duplicates")
        return MoveHandler(target_dir, config.dry_run)
    elif action == "delete":
        return DeleteHandler(config.dry_run, config.create_backup)
    elif action == "link":
        return LinkHandler(config.dry_run)
    else:
        raise ValueError(f"未知的操作类型: {config.action}。支持的类型: report, move, delete, link")


__all__ = [
    "BaseHandler",
    "ReportHandler",
    "MoveHandler",
    "DeleteHandler",
    "LinkHandler",
    "HandlerResult",
    "get_handler",
]
