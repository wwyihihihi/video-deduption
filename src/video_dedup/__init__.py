"""
Video Deduplication - 视频去重工具

用于检测和处理重复视频文件的命令行工具。
"""

__version__ = "0.1.0"

from video_dedup.models import VideoFile, VideoFingerprint, DuplicateGroup

__all__ = [
    "VideoFile",
    "VideoFingerprint",
    "DuplicateGroup",
    "__version__",
]
