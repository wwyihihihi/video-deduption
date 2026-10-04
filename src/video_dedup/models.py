"""数据模型定义"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
import uuid


@dataclass
class VideoFile:
    """视频文件信息"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    path: Path = field(default_factory=lambda: Path("."))
    size: int = 0
    duration: float = 0.0
    width: int = 0
    height: int = 0
    format: str = ""
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    checksum: str = ""
    checksum_algorithm: str = ""
    file_signature: tuple[int, int, int, int] | None = None

    @property
    def resolution(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def resolution_str(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class VideoFingerprint:
    """视频指纹"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    video_id: str = ""
    frame_hashes: list[str] = field(default_factory=list)
    duration_hash: str = ""
    color_histogram: list[int] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    feature_version: int = 0  # 0 denotes legacy, unordered fingerprints
    hash_method: str = ""
    hash_bits: int = 0
    sample_positions: list[dict] = field(default_factory=list)
    frame_positions: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return (self.feature_version == 2 and bool(self.frame_hashes)
                and bool(self.sample_positions) and not self.errors
                and all(p["status"] == "ok" for p in self.sample_positions))

    @property
    def hash_count(self) -> int:
        return len(self.frame_hashes)


@dataclass
class DuplicateGroup:
    """重复视频组"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    video_ids: list[str] = field(default_factory=list)
    similarity: float = 0.0
    representative_id: Optional[str] = None
    reason: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    match_type: str = "content"

    @property
    def count(self) -> int:
        return len(self.video_ids)


@dataclass
class ScanResult:
    """扫描结果"""
    total_files: int = 0
    video_files: int = 0
    skipped_files: int = 0
    errors: list[str] = field(default_factory=list)
    videos: list[VideoFile] = field(default_factory=list)


@dataclass
class ComparisonResult:
    """比对结果"""
    video1_id: str
    video2_id: str
    similarity: float
    details: dict = field(default_factory=dict)


@dataclass
class DeduplicationReport:
    """去重报告"""
    total_videos: int = 0
    duplicate_groups: int = 0
    total_duplicates: int = 0
    space_savings: int = 0  # 可节省空间(字节)
    groups: list[DuplicateGroup] = field(default_factory=list)
    video_map: dict[str, VideoFile] = field(default_factory=dict)  # 视频ID到VideoFile的映射
    generated_at: datetime = field(default_factory=datetime.now)
    analyzed_videos: int = 0
    pending_review: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    fingerprint_details: dict[str, dict] = field(default_factory=dict)
    file_statuses: dict[str, str] = field(default_factory=dict)
    operation: dict = field(default_factory=dict)
