"""配置管理"""

from pathlib import Path
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field


class ScannerConfig(BaseModel):
    """扫描器配置"""
    supported_formats: list[str] = Field(
        default=[".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg"]
    )
    min_file_size: int = Field(default=1024 * 1024)  # 最小1MB
    max_file_size: Optional[int] = None  # 无上限
    follow_symlinks: bool = False
    exclude_patterns: list[str] = Field(default=[".git", ".cache", "__pycache__"])


class ExtractorConfig(BaseModel):
    """特征提取配置"""
    num_frames: int = Field(default=32, ge=1, le=100)
    hash_size: int = Field(default=8, ge=4, le=16)
    hash_method: Literal["phash", "dhash", "ahash"] = Field(default="phash")  # phash, dhash, ahash
    scene_detection: bool = False
    scene_threshold: float = Field(default=30.0)


class ComparatorConfig(BaseModel):
    """比对配置"""
    similarity_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    duration_tolerance: float = Field(default=0.1, ge=0.0, le=1.0)
    min_matching_frames: float = Field(default=0.9, ge=0.0, le=1.0)
    use_lsh: bool = True  # 使用 LSH 加速
    lsh_tables: int = Field(default=10)


class HandlerConfig(BaseModel):
    """文件处理配置"""
    action: str = Field(default="report")  # report, move, delete, link
    target_dir: Optional[Path] = None
    create_backup: bool = False
    dry_run: bool = True  # 默认模拟运行


class Config(BaseModel):
    """全局配置"""
    scanner: ScannerConfig = Field(default_factory=ScannerConfig)
    extractor: ExtractorConfig = Field(default_factory=ExtractorConfig)
    comparator: ComparatorConfig = Field(default_factory=ComparatorConfig)
    handler: HandlerConfig = Field(default_factory=HandlerConfig)

    # 运行时配置
    max_workers: int = Field(default=4, ge=1, le=32)
    cache_dir: Path = Field(default_factory=lambda: Path.home() / ".video_dedup" / "cache")
    db_path: Path = Field(default_factory=lambda: Path.home() / ".video_dedup" / "videos.db")
    log_level: str = Field(default="INFO")

    model_config = ConfigDict(arbitrary_types_allowed=True)
