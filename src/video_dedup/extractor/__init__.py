"""特征提取模块

提供视频关键帧提取、哈希计算和指纹提取功能。
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from video_dedup.config import ExtractorConfig
from video_dedup.models import VideoFile, VideoFingerprint

logger = logging.getLogger(__name__)


class BaseExtractor(ABC):
    """特征提取器基类"""

    def __init__(self, config: Optional[ExtractorConfig] = None):
        self.config = config or ExtractorConfig()

    @abstractmethod
    def extract(self, video: VideoFile) -> Optional[VideoFingerprint]:
        """提取视频特征指纹"""
        pass


class FrameExtractor:
    """关键帧提取器

    提供均匀采样法和场景变化检测法两种关键帧提取方式。
    """

    def __init__(self, max_frames: int = 1000):
        """初始化帧提取器

        Args:
            max_frames: 最大提取帧数限制，防止内存溢出
        """
        self.max_frames = max_frames

    def sample(self, video_path: Path, num_frames: int,
               scene_threshold: float | None = None) -> list[tuple[dict, np.ndarray | None]]:
        """保留失败采样点和实际定位信息，成功帧按时间排序。"""
        cap = cv2.VideoCapture(str(video_path))
        try:
            if not cap.isOpened():
                return [({"target_frame": None, "actual_frame": None,
                          "status": "open_failed"}, None)]
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if total <= 0 or not np.isfinite(fps) or fps <= 0:
                return [({"target_frame": None, "actual_frame": None,
                          "status": "invalid_metadata"}, None)]
            count = min(num_frames, total, self.max_frames)
            # 包含首尾，避免遗漏不同的片头片尾。
            targets = set(int(v) for v in np.linspace(0, total - 1, count))
            if scene_threshold is not None:
                # 用有界的粗采样寻找场景变化，避免全量解码和保存原尺寸帧。
                probes = sorted(set(int(v) for v in np.linspace(0, total - 1,
                                    min(total, count * 4))))
                changes = []
                previous = None
                for position in probes:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, position)
                    ok, frame = cap.read()
                    actual = cap.get(cv2.CAP_PROP_POS_FRAMES) - 1
                    if not ok or abs(actual - position) > 1:
                        continue
                    gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90))
                    if previous is not None:
                        score = float(cv2.absdiff(gray, previous).mean())
                        if score > scene_threshold:
                            changes.append((score, position))
                    previous = gray
                # 保留均匀覆盖的一半位置，以场景点补足，最终不超过配置数量。
                if changes:
                    anchors = set(int(v) for v in np.linspace(0, total - 1,
                                                             max(2, count // 2)))
                    targets = anchors
                    for _, position in sorted(changes, reverse=True):
                        if len(targets) >= count:
                            break
                        targets.add(position)
                    for position in sorted(set(int(v) for v in np.linspace(0, total - 1, count))):
                        if len(targets) >= count:
                            break
                        targets.add(position)
                    targets = set(sorted(targets)[:count])
            samples = []
            for target in sorted(targets):
                positioned = cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                ok, frame = cap.read()
                actual = cap.get(cv2.CAP_PROP_POS_FRAMES) - 1 if ok else None
                status = "ok" if ok and positioned and abs(actual - target) <= 1 else (
                    "position_mismatch" if ok else "read_failed")
                record = {"target_frame": target, "actual_frame": actual,
                          "target_seconds": target / fps,
                          "actual_seconds": actual / fps if actual is not None else None,
                          "normalized_position": target / max(1, total - 1), "status": status}
                # 及时缩小尺寸，内存不随视频分辨率成倍增长。
                small = cv2.resize(frame, (128, 128)) if status == "ok" else None
                samples.append((record, small))
            return samples
        finally:
            cap.release()

    def extract_frames(self, video_path: Path, num_frames: int) -> list[np.ndarray]:
        return [frame for _, frame in self.sample(video_path, num_frames) if frame is not None]

    def extract_scene_frames(self, video_path: Path, threshold: float) -> list[np.ndarray]:
        return [frame for _, frame in self.sample(video_path, 32, threshold) if frame is not None]


class HashCalculator:
    """哈希计算器

    实现多种图像感知哈希算法。
    """

    def __init__(self, hash_size: int = 8):
        """初始化哈希计算器

        Args:
            hash_size: 哈希大小，生成 hash_size * hash_size 位的哈希
        """
        self.hash_size = hash_size

    def _bits_to_hex(self, bits: np.ndarray) -> str:
        """将位数组转换为十六进制字符串

        Args:
            bits: 布尔值或0/1数组

        Returns:
            按配置位数编码的十六进制字符串
        """
        bits = bits.astype(np.uint8).flatten()
        value = 0
        for bit in bits:
            value = (value << 1) | int(bit)
        return format(value, f"0{(len(bits) + 3) // 4}x")

    def calculate_phash(self, frame: np.ndarray) -> str:
        """计算感知哈希 (pHash)

        基于DCT(离散余弦变换)的感知哈希，对图像缩放、压缩、轻微修改具有鲁棒性。

        Args:
            frame: BGR格式的帧图像

        Returns:
            hash_size ** 2 位哈希的十六进制字符串
        """
        # 1. 缩放并转灰度
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        # 缩放到 hash_size 的四倍尺寸进行 DCT
        img = cv2.resize(gray, (self.hash_size * 4, self.hash_size * 4))

        # 2. DCT 变换
        dct = cv2.dct(img.astype(np.float32))

        # 3. 取左上角低频部分
        dct_low = dct[: self.hash_size, : self.hash_size]

        # 4. 计算中位数 (仅排除 DC 分量)
        avg = np.median(dct_low.flatten()[1:])

        # 5. 生成哈希
        hash_bits = (dct_low > avg).flatten()

        return self._bits_to_hex(hash_bits)

    def calculate_dhash(self, frame: np.ndarray) -> str:
        """计算差异哈希 (dHash)

        基于相邻像素差异的哈希，计算速度快。

        Args:
            frame: BGR格式的帧图像

        Returns:
            hash_size ** 2 位哈希的十六进制字符串
        """
        # 1. 缩放并转灰度
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        # 缩放到 (hash_size+1) x hash_size
        img = cv2.resize(gray, (self.hash_size + 1, self.hash_size))

        # 2. 计算水平相邻像素差异
        diff = img[:, 1:] > img[:, :-1]

        # 3. 生成哈希
        hash_bits = diff.flatten()

        return self._bits_to_hex(hash_bits)

    def calculate_ahash(self, frame: np.ndarray) -> str:
        """计算平均哈希 (aHash)

        基于像素平均值的简单哈希，计算速度最快但鲁棒性较差。

        Args:
            frame: BGR格式的帧图像

        Returns:
            hash_size ** 2 位哈希的十六进制字符串
        """
        # 1. 缩放并转灰度
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        # 缩放到 hash_size x hash_size
        img = cv2.resize(gray, (self.hash_size, self.hash_size))

        # 2. 计算均值
        avg = img.mean()

        # 3. 生成哈希
        hash_bits = (img > avg).flatten()

        return self._bits_to_hex(hash_bits)


class VideoFingerprintExtractor(BaseExtractor):
    """视频指纹提取器

    继承BaseExtractor，实现完整的视频指纹提取流程。
    """

    def __init__(self, config: Optional[ExtractorConfig] = None):
        super().__init__(config)
        self.frame_extractor = FrameExtractor()
        self.hash_calculator = HashCalculator(hash_size=self.config.hash_size)

    def _calculate_color_histogram(
        self, frame: np.ndarray, bins: int = 64
    ) -> list[int]:
        """计算颜色直方图特征

        Args:
            frame: BGR格式的帧图像
            bins: 直方图bin数量

        Returns:
            直方图特征列表
        """
        if len(frame.shape) == 2:
            # 灰度图
            hist = cv2.calcHist([frame], [0], None, [bins], [0, 256])
            return hist.flatten().astype(int).tolist()
        else:
            # BGR图像，计算各通道直方图并拼接
            hist_features = []
            for channel in range(3):
                hist = cv2.calcHist([frame], [channel], None, [bins], [0, 256])
                hist_features.extend(hist.flatten().astype(int).tolist())
            return hist_features

    def _get_hash_method(self) -> str:
        """获取配置的哈希方法"""
        return self.config.hash_method.lower()

    def extract(self, video: VideoFile) -> Optional[VideoFingerprint]:
        """提取视频完整指纹

        流程:
        1. 根据配置选择帧提取方法
        2. 计算每帧的感知哈希
        3. 可选: 计算颜色直方图特征
        4. 组合为 VideoFingerprint 对象

        Args:
            video: 视频文件信息对象

        Returns:
            VideoFingerprint 对象，失败返回 None
        """
        logger.info(f"开始提取视频指纹: {video.path.name}")

        samples = self.frame_extractor.sample(
            video.path, self.config.num_frames,
            self.config.scene_threshold if self.config.scene_detection else None,
        )
        frames = [frame for _, frame in samples if frame is not None]
        if not frames:
            logger.warning(f"未能提取任何关键帧: {video.path.name}")
            return VideoFingerprint(video_id=video.id, feature_version=2,
                                    hash_method=self.config.hash_method,
                                    hash_bits=self.config.hash_size ** 2,
                                    sample_positions=[record for record, _ in samples],
                                    errors=["未能提取任何有效采样帧"])

        logger.debug(f"成功提取 {len(frames)} 个关键帧")

        # 2. 计算每帧的哈希
        frame_hashes = []
        hash_method = self._get_hash_method()

        for i, frame in enumerate(frames):
            if hash_method == "phash":
                hash_value = self.hash_calculator.calculate_phash(frame)
            elif hash_method == "dhash":
                hash_value = self.hash_calculator.calculate_dhash(frame)
            elif hash_method == "ahash":
                hash_value = self.hash_calculator.calculate_ahash(frame)
            else:
                # 默认使用 phash
                hash_value = self.hash_calculator.calculate_phash(frame)

            frame_hashes.append(hash_value)

            if (i + 1) % 5 == 0 or i == len(frames) - 1:
                logger.debug(f"已计算 {i + 1}/{len(frames)} 帧哈希")

        logger.info(f"哈希计算完成: {len(frame_hashes)} 个帧哈希")

        # 3. 计算颜色直方图特征 (取中间帧)
        color_histogram = []
        if frames:
            mid_frame_idx = len(frames) // 2
            color_histogram = self._calculate_color_histogram(frames[mid_frame_idx])
            logger.debug(f"颜色直方图特征: {len(color_histogram)} 维")

        # 4. 生成时长哈希 (基于视频时长的简单特征)
        duration_hash = format(int(video.duration * 1000) % (2**64), "016x") if np.isfinite(video.duration) else ""

        # 5. 创建指纹对象
        fingerprint = VideoFingerprint(
            video_id=video.id,
            feature_version=2,
            hash_method=hash_method,
            hash_bits=self.config.hash_size ** 2,
            sample_positions=[record for record, _ in samples],
            frame_positions=[record["normalized_position"] for record, frame in samples
                             if frame is not None],
            errors=[f"采样失败: {record}" for record, frame in samples if frame is None],
            frame_hashes=frame_hashes,
            duration_hash=duration_hash,
            color_histogram=color_histogram,
        )

        logger.info(
            f"指纹提取完成: video_id={video.id}, 帧哈希数={len(frame_hashes)}"
        )

        return fingerprint

    def extract_with_all_hashes(
        self, video: VideoFile
    ) -> dict[str, Optional[VideoFingerprint]]:
        """提取包含所有哈希类型的指纹 (用于对比分析)

        Args:
            video: 视频文件信息对象

        Returns:
            各哈希类型的指纹字典
        """
        result = {}
        original_method = self.config.hash_method

        try:
            for method in ["phash", "dhash", "ahash"]:
                self.config.hash_method = method
                result[method] = self.extract(video)
        finally:
            self.config.hash_method = original_method
        return result


__all__ = [
    "BaseExtractor",
    "VideoFingerprintExtractor",
    "FrameExtractor",
    "HashCalculator",
]
