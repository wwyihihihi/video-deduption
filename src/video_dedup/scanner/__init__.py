"""视频扫描模块"""

import hashlib
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Iterator

from video_dedup.config import ScannerConfig
from video_dedup.models import VideoFile, ScanResult

logger = logging.getLogger(__name__)


def file_signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino


class BaseScanner(ABC):
    """扫描器基类"""

    def __init__(self, config: ScannerConfig | None = None):
        self.config = config or ScannerConfig()

    @abstractmethod
    def scan(self, path: Path, recursive: bool = True) -> ScanResult:
        """扫描指定路径"""
        pass

    @abstractmethod
    def is_video_file(self, path: Path) -> bool:
        """判断是否为视频文件"""
        pass


class VideoScanner(BaseScanner):
    """视频扫描器实现"""

    def __init__(self, config: ScannerConfig | None = None):
        super().__init__(config)
        self._import_opencv()

    def _import_opencv(self) -> None:
        """导入 OpenCV 库"""
        try:
            import cv2
            self._cv2 = cv2
        except ImportError:
            logger.warning("OpenCV 未安装，视频元数据提取功能将不可用")
            self._cv2 = None

    def scan(self, path: Path, recursive: bool = True) -> ScanResult:
        """扫描目录获取视频文件列表

        Args:
            path: 要扫描的目录路径
            recursive: 是否递归扫描子目录

        Returns:
            ScanResult 包含所有找到的视频文件信息
        """
        result = ScanResult()
        path = Path(path).resolve()

        if not path.exists():
            result.errors.append(f"路径不存在: {path}")
            logger.error(f"扫描路径不存在: {path}")
            return result

        if path.is_file():
            # 单个文件
            result.total_files = 1
            if self.is_video_file(path):
                video_info = self._get_video_info(path)
                if video_info:
                    result.videos.append(video_info)
                    result.video_files = 1
                else:
                    result.skipped_files = 1
                    result.errors.append(f"无法读取视频信息: {path}")
            else:
                result.skipped_files = 1
            return result

        # 目录扫描
        logger.info(f"开始扫描目录: {path}")
        try:
            for file_path in self._walk_directory(path, recursive):
                result.total_files += 1

                if self._should_skip(file_path):
                    result.skipped_files += 1
                    continue

                if self.is_video_file(file_path):
                    video_info = self._get_video_info(file_path)
                    if video_info:
                        result.videos.append(video_info)
                        result.video_files += 1
                    else:
                        result.skipped_files += 1
                        result.errors.append(f"无法读取视频信息: {file_path}")
                else:
                    result.skipped_files += 1
        except PermissionError as e:
            result.errors.append(f"权限不足: {e}")
            logger.error(f"扫描目录时权限不足: {e}")
        except Exception as e:
            result.errors.append(f"扫描错误: {e}")
            logger.exception(f"扫描目录时发生错误: {e}")

        logger.info(
            f"扫描完成: 共 {result.total_files} 个文件, "
            f"{result.video_files} 个视频文件, "
            f"{result.skipped_files} 个跳过, "
            f"{len(result.errors)} 个错误"
        )
        return result

    def _walk_directory(self, path: Path, recursive: bool) -> Iterator[Path]:
        """遍历目录

        Args:
            path: 目录路径
            recursive: 是否递归遍历

        Yields:
            文件路径
        """
        if recursive:
            for root, dirs, files in os.walk(
                path,
                followlinks=self.config.follow_symlinks
            ):
                # 过滤排除的目录
                dirs[:] = [
                    d for d in dirs
                    if not self._should_skip_dir(Path(root) / d)
                ]
                for file in files:
                    yield Path(root) / file
        else:
            for item in path.iterdir():
                if item.is_file():
                    yield item

    def is_video_file(self, path: Path) -> bool:
        """判断文件是否为支持的视频格式

        Args:
            path: 文件路径

        Returns:
            是否为视频文件
        """
        if not path.is_file():
            return False

        # 检查扩展名
        if path.suffix.lower() not in self.config.supported_formats:
            return False

        # 检查文件大小
        try:
            file_size = path.stat().st_size
            if file_size < self.config.min_file_size:
                logger.debug(f"文件过小，跳过: {path}")
                return False
            if self.config.max_file_size and file_size > self.config.max_file_size:
                logger.debug(f"文件过大，跳过: {path}")
                return False
        except OSError as e:
            logger.warning(f"无法获取文件大小: {path}, 错误: {e}")
            return False

        return True

    def _should_skip(self, path: Path) -> bool:
        """检查是否应该跳过该文件路径

        Args:
            path: 文件路径

        Returns:
            是否应该跳过
        """
        if path.is_symlink() and not self.config.follow_symlinks:
            return True
        path_str = str(path)
        for pattern in self.config.exclude_patterns:
            if pattern in path_str:
                logger.debug(f"匹配排除模式 '{pattern}'，跳过: {path}")
                return True
        return False

    def _should_skip_dir(self, dir_path: Path) -> bool:
        """检查是否应该跳过该目录

        Args:
            dir_path: 目录路径

        Returns:
            是否应该跳过
        """
        dir_name = dir_path.name
        # 检查目录名是否在排除列表中
        if dir_name in self.config.exclude_patterns:
            logger.debug(f"排除目录: {dir_path}")
            return True

        # 检查路径中是否包含排除模式
        path_str = str(dir_path)
        for pattern in self.config.exclude_patterns:
            if pattern in path_str:
                logger.debug(f"目录路径匹配排除模式 '{pattern}'，跳过: {dir_path}")
                return True

        return False

    def _get_video_info(self, path: Path) -> VideoFile | None:
        """获取视频文件信息

        使用 OpenCV 获取视频元数据，使用标准库获取文件属性。

        Args:
            path: 视频文件路径

        Returns:
            VideoFile 对象，失败返回 None
        """
        try:
            path = Path(path).resolve()
            stat = path.stat()

            # 基础文件信息
            video_file = VideoFile(
                path=path,
                size=stat.st_size,
                created_at=self._get_creation_time(stat),
                modified_at=datetime.fromtimestamp(stat.st_mtime),
                format=path.suffix.lower().lstrip('.')
            )
            video_file.file_signature = (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino)

            # 使用 OpenCV 获取视频元数据
            if self._cv2 is not None:
                self._extract_video_metadata(path, video_file)
            else:
                logger.warning(f"OpenCV 不可用，无法提取视频元数据: {path}")

            return video_file

        except Exception as e:
            logger.error(f"获取视频信息失败: {path}, 错误: {e}")
            return None

    def _get_creation_time(self, stat: os.stat_result) -> datetime:
        """获取文件创建时间

        Windows 上有 st_ctime（创建时间），
        Unix 上 st_ctime 是元数据更改时间，st_birthtime 才是创建时间（如果可用）。

        Args:
            stat: os.stat 结果

        Returns:
            创建时间
        """
        # Windows: st_ctime 是创建时间
        # Unix: st_ctime 是元数据更改时间
        try:
            # 尝试获取真正的创建时间
            if hasattr(stat, 'st_birthtime'):
                return datetime.fromtimestamp(stat.st_birthtime)
            else:
                # 回退到 ctime
                return datetime.fromtimestamp(stat.st_ctime)
        except (AttributeError, OSError):
            return datetime.fromtimestamp(stat.st_mtime)

    def _extract_video_metadata(self, path: Path, video_file: VideoFile) -> None:
        """使用 OpenCV 提取视频元数据

        Args:
            path: 视频文件路径
            video_file: VideoFile 对象，将被原地修改
        """
        cap = None
        try:
            cap = self._cv2.VideoCapture(str(path))

            if not cap.isOpened():
                logger.warning(f"无法打开视频文件: {path}")
                return

            # 获取帧数
            frame_count = int(cap.get(self._cv2.CAP_PROP_FRAME_COUNT))
            # 获取帧率
            fps = cap.get(self._cv2.CAP_PROP_FPS)
            # 获取宽度
            width = int(cap.get(self._cv2.CAP_PROP_FRAME_WIDTH))
            # 获取高度
            height = int(cap.get(self._cv2.CAP_PROP_FRAME_HEIGHT))

            # 计算时长（秒）
            if fps > 0 and frame_count > 0:
                duration = frame_count / fps
            else:
                duration = 0.0
                logger.warning(f"无法计算视频时长: {path} (fps={fps}, frames={frame_count})")

            video_file.width = width
            video_file.height = height
            video_file.duration = duration

            logger.debug(
                f"视频元数据: {path.name}, "
                f"分辨率={width}x{height}, "
                f"时长={duration:.2f}s, "
                f"帧率={fps:.2f}fps"
            )

        except Exception as e:
            logger.error(f"提取视频元数据失败: {path}, 错误: {e}")
        finally:
            if cap is not None:
                cap.release()

    def calculate_checksum(self, path: Path, chunk_size: int = 1024 * 1024,
                           algorithm: str = "md5") -> str:
        """分块计算完整文件摘要，默认 MD5 兼容旧调用，可选择 SHA-256

        Args:
            path: 文件路径
            chunk_size: 读取块大小

        Returns:
            摘要十六进制字符串，失败或文件变化返回空字符串
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        digest = hashlib.new(algorithm)
        try:
            before = file_signature(path)
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(chunk_size), b''):
                    digest.update(chunk)
            if before != file_signature(path):
                raise OSError("文件在计算摘要期间发生变化")
            return digest.hexdigest()
        except Exception as e:
            logger.error(f"计算校验和失败: {path}, 错误: {e}")
            return ""

    def scan_with_checksum(
        self,
        path: Path,
        recursive: bool = True,
        calculate_checksum: bool = False
    ) -> ScanResult:
        """扫描目录并可选计算校验和

        Args:
            path: 要扫描的目录路径
            recursive: 是否递归扫描子目录
            calculate_checksum: 是否计算 MD5 校验和

        Returns:
            ScanResult 包含所有找到的视频文件信息
        """
        result = self.scan(path, recursive)

        if calculate_checksum:
            logger.info("开始计算视频文件校验和...")
            for video in result.videos:
                video.checksum = self.calculate_checksum(video.path)
                video.checksum_algorithm = "md5" if video.checksum else ""
            logger.info("校验和计算完成")

        return result


__all__ = ["BaseScanner", "VideoScanner", "ScannerConfig"]
