"""相似度比对模块

提供视频指纹比对的核心功能:
- HashComparator: 基于哈希的比较器
- DTWComparator: 动态时间规整比较器
- SimilarityMatrix: 相似度矩阵计算与存储
"""

import logging
from abc import ABC, abstractmethod
from typing import Iterator, Generator
import numpy as np

from video_dedup.models import VideoFingerprint, ComparisonResult
from video_dedup.config import ComparatorConfig

logger = logging.getLogger(__name__)


class BaseComparator(ABC):
    """比对器基类"""

    def __init__(self, config: ComparatorConfig | None = None):
        self.config = config or ComparatorConfig()

    @abstractmethod
    def compare(self, fp1: VideoFingerprint, fp2: VideoFingerprint) -> float:
        """比较两个指纹的相似度

        Args:
            fp1: 第一个视频指纹
            fp2: 第二个视频指纹

        Returns:
            相似度值 (0.0 - 1.0)
        """
        pass

    def compare_with_details(
        self, fp1: VideoFingerprint, fp2: VideoFingerprint
    ) -> ComparisonResult:
        """比较两个指纹并返回详细结果

        Args:
            fp1: 第一个视频指纹
            fp2: 第二个视频指纹

        Returns:
            包含详细信息的比对结果
        """
        similarity = self.compare(fp1, fp2)
        return ComparisonResult(
            video1_id=fp1.video_id,
            video2_id=fp2.video_id,
            similarity=similarity,
            details={
                "fp1_hash_count": fp1.hash_count,
                "fp2_hash_count": fp2.hash_count,
            },
        )


def hamming_distance(hash1: str, hash2: str) -> int:
    """计算两个哈希字符串的汉明距离

    Args:
        hash1: 第一个哈希字符串
        hash2: 第二个哈希字符串

    Returns:
        汉明距离 (不同二进制位的数量)

    Raises:
        ValueError: 当哈希长度不一致时
    """
    if len(hash1) != len(hash2):
        raise ValueError(
            f"Hash lengths must be equal: {len(hash1)} != {len(hash2)}"
        )
    return (int(hash1, 16) ^ int(hash2, 16)).bit_count()


def hash_similarity(hash1: str, hash2: str, hash_bits: int | None = None) -> float:
    """计算两个哈希的相似度

    Args:
        hash1: 第一个哈希字符串
        hash2: 第二个哈希字符串

    Returns:
        相似度值 (0.0 - 1.0), 1.0 表示完全相同
    """
    if not hash1 or not hash2:
        return 0.0
    dist = hamming_distance(hash1, hash2)
    width = hash_bits if hash_bits is not None else len(hash1) * 4
    if width <= 0 or max(int(hash1, 16), int(hash2, 16)) >= 2 ** width:
        raise ValueError("哈希值超出声明位数")
    return 1.0 - dist / width


class HashComparator(BaseComparator):
    """哈希比较器

    使用帧哈希进行视频相似度比较,支持多种比对策略。
    """

    def __init__(
        self,
        config: ComparatorConfig | None = None,
        strategy: str = "average",
    ):
        """初始化哈希比较器

        Args:
            config: 比对配置
            strategy: 比对策略
                - "average": 平均相似度
                - "max": 最大相似度 (最佳匹配)
                - "min": 最小相似度
        """
        super().__init__(config)
        self.strategy = strategy
        logger.debug(f"HashComparator initialized with strategy: {strategy}")

    def compare(self, fp1: VideoFingerprint, fp2: VideoFingerprint) -> float:
        """比较两个指纹的相似度

        Args:
            fp1: 第一个视频指纹
            fp2: 第二个视频指纹

        Returns:
            相似度值 (0.0 - 1.0)
        """
        if not fp1.frame_hashes or not fp2.frame_hashes:
            logger.warning(
                f"Empty frame hashes: fp1 has {len(fp1.frame_hashes)}, "
                f"fp2 has {len(fp2.frame_hashes)}"
            )
            return 0.0

        if self.strategy == "average":
            return DTWComparator(self.config).compare(fp1, fp2)
        similarities = self._compute_pairwise_similarities(fp1.frame_hashes, fp2.frame_hashes, fp1.hash_bits or None)
        if self.strategy == "max":
            return float(np.max(similarities))
        if self.strategy == "min":
            return float(np.min(similarities))
        raise ValueError(f"Unknown strategy: {self.strategy}")

    def _compute_pairwise_similarities(
        self, hashes1: list[str], hashes2: list[str], hash_bits: int | None = None
    ) -> np.ndarray:
        """计算两组哈希之间的两两相似度

        Args:
            hashes1: 第一组哈希
            hashes2: 第二组哈希

        Returns:
            相似度矩阵 (len(hashes1) x len(hashes2))
        """
        n, m = len(hashes1), len(hashes2)
        similarities = np.zeros((n, m), dtype=np.float64)

        for i, h1 in enumerate(hashes1):
            for j, h2 in enumerate(hashes2):
                similarities[i, j] = hash_similarity(h1, h2, hash_bits)

        return similarities

    def compare_best_match(
        self, fp1: VideoFingerprint, fp2: VideoFingerprint
    ) -> float:
        """使用最佳匹配策略比较

        每个帧找最佳匹配,然后取平均

        Args:
            fp1: 第一个视频指纹
            fp2: 第二个视频指纹

        Returns:
            相似度值 (0.0 - 1.0)
        """
        if not fp1.frame_hashes or not fp2.frame_hashes:
            return 0.0

        similarities = self._compute_pairwise_similarities(
            fp1.frame_hashes, fp2.frame_hashes, fp1.hash_bits or None
        )

        # 双向最佳匹配
        max_sim_1 = np.max(similarities, axis=1)  # fp1 每帧在 fp2 中的最佳匹配
        max_sim_2 = np.max(similarities, axis=0)  # fp2 每帧在 fp1 中的最佳匹配

        return float((np.mean(max_sim_1) + np.mean(max_sim_2)) / 2)


class DTWComparator(BaseComparator):
    """按时间顺序对齐，按实际路径长度和哈希位数归一化。"""

    def __init__(self, config: ComparatorConfig | None = None, normalize: bool = True):
        super().__init__(config)
        if not normalize:
            logger.warning("normalize=False 已弃用，DTW 始终按实际路径长度归一化")

    def _alignment(self, fp1: VideoFingerprint, fp2: VideoFingerprint):
        if not fp1.frame_hashes or not fp2.frame_hashes:
            return [], []
        if fp1.feature_version != 2 or fp2.feature_version != 2:
            raise ValueError("旧格式指纹必须重新提取")
        if (fp1.hash_bits != fp2.hash_bits or fp1.hash_bits <= 0
                or fp1.hash_method != fp2.hash_method):
            raise ValueError("指纹哈希方法或位数不一致")
        n, m = len(fp1.frame_hashes), len(fp2.frame_hashes)
        if len(fp1.frame_positions) != n or len(fp2.frame_positions) != m:
            raise ValueError("指纹缺少帧位置")
        dp = np.full((n + 1, m + 1), np.inf)
        dp[0, 0] = 0
        previous = {}
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                # 防止不同时间的相似画面通过过度扭曲路径形成重复判定。
                if abs(fp1.frame_positions[i - 1] - fp2.frame_positions[j - 1]) > 0.1:
                    continue
                cost = hamming_distance(fp1.frame_hashes[i - 1], fp2.frame_hashes[j - 1])
                predecessor = min([(dp[i-1, j-1], i-1, j-1),
                                   (dp[i-1, j], i-1, j), (dp[i, j-1], i, j-1)],
                                  key=lambda item: item[0])
                if np.isfinite(predecessor[0]):
                    dp[i, j] = predecessor[0] + cost
                    previous[i, j] = predecessor[1:]
        if not np.isfinite(dp[n, m]):
            return [], []
        path = []
        i, j = n, m
        while i and j:
            path.append((i - 1, j - 1))
            i, j = previous[i, j]
        path.reverse()
        distances = [hamming_distance(fp1.frame_hashes[i], fp2.frame_hashes[j])
                     for i, j in path]
        return path, distances

    def compare(self, fp1: VideoFingerprint, fp2: VideoFingerprint) -> float:
        _, distances = self._alignment(fp1, fp2)
        return max(0.0, 1 - sum(distances) / (len(distances) * fp1.hash_bits)) if distances else 0.0

    def compute_alignment_path(self, fp1: VideoFingerprint, fp2: VideoFingerprint):
        return self._alignment(fp1, fp2)[0]

    def compare_with_details(self, fp1: VideoFingerprint, fp2: VideoFingerprint) -> ComparisonResult:
        path, distances = self._alignment(fp1, fp2)
        scores = [1 - distance / fp1.hash_bits for distance in distances]
        matched = {(i, j) for (i, j), score in zip(path, scores)
                   if score >= self.config.similarity_threshold}
        # 分别约束两个序列，避免重复使用一帧虚增匹配覆盖率。
        ratio = min(len({i for i, _ in matched}) / max(1, len(fp1.frame_hashes)),
                    len({j for _, j in matched}) / max(1, len(fp2.frame_hashes)))
        return ComparisonResult(fp1.video_id, fp2.video_id,
                                sum(scores) / len(scores) if scores else 0.0,
                                {"matching_ratio": ratio, "alignment_length": len(path)})

    def compare_partial(self, fp1: VideoFingerprint, fp2: VideoFingerprint) -> ComparisonResult:
        """损坏文件仅按邻近时间点提供证据，不用于自动操作。"""
        scores = []
        for position, value in zip(fp1.frame_positions, fp1.frame_hashes):
            candidates = [other for t, other in zip(fp2.frame_positions, fp2.frame_hashes)
                          if abs(position - t) <= 0.02]
            if candidates:
                scores.append(max(1 - hamming_distance(value, other) / fp1.hash_bits
                                  for other in candidates))
        return ComparisonResult(fp1.video_id, fp2.video_id,
                                sum(scores) / len(scores) if scores else 0.0,
                                {"compared_frames": len(scores),
                                 "coverage": len(scores) / max(1, len(fp1.sample_positions))})


class SimilarityMatrix:
    """相似度矩阵

    批量计算并存储视频指纹之间的相似度。
    使用对称矩阵优化存储空间。
    """

    def __init__(self, fingerprints: list[VideoFingerprint]):
        """初始化相似度矩阵

        Args:
            fingerprints: 视频指纹列表
        """
        self.fingerprints = fingerprints
        self._id_to_index: dict[str, int] = {
            fp.id: i for i, fp in enumerate(fingerprints)
        }
        self._video_id_to_index: dict[str, int] = {
            fp.video_id: i for i, fp in enumerate(fingerprints)
        }
        # 使用一维数组存储上三角矩阵 (对称矩阵优化)
        # 对于 n 个元素,需要 n*(n-1)/2 个空间
        self._matrix: np.ndarray | None = None
        self._computed = False

        logger.debug(
            f"SimilarityMatrix initialized with {len(fingerprints)} fingerprints"
        )

    @property
    def size(self) -> int:
        """矩阵大小 (指纹数量)"""
        return len(self.fingerprints)

    @property
    def is_computed(self) -> bool:
        """是否已完成计算"""
        return self._computed

    def _get_flat_index(self, i: int, j: int) -> int:
        """获取对称矩阵在一维数组中的索引

        只存储上三角部分 (i < j)

        Args:
            i: 行索引 (较小值)
            j: 列索引 (较大值)

        Returns:
            一维数组索引
        """
        if i > j:
            i, j = j, i
        # 上三角矩阵的线性索引公式
        return i * (2 * self.size - i - 1) // 2 + (j - i - 1)

    def compute(
        self,
        comparator: BaseComparator,
        show_progress: bool = False,
    ) -> None:
        """计算所有指纹对的相似度

        Args:
            comparator: 比较器实例
            show_progress: 是否显示进度
        """
        n = self.size
        if n < 2:
            logger.warning("Less than 2 fingerprints, nothing to compare")
            self._computed = True
            return

        # 初始化存储
        total_pairs = n * (n - 1) // 2
        self._matrix = np.zeros(total_pairs, dtype=np.float64)

        logger.info(
            f"Computing similarity matrix for {n} fingerprints "
            f"({total_pairs} pairs)"
        )

        # 计算所有指纹对
        pair_count = 0
        for i in range(n):
            for j in range(i + 1, n):
                similarity = comparator.compare(
                    self.fingerprints[i], self.fingerprints[j]
                )
                self._matrix[self._get_flat_index(i, j)] = similarity
                pair_count += 1

                if show_progress and pair_count % 100 == 0:
                    progress = pair_count / total_pairs * 100
                    logger.info(f"Progress: {progress:.1f}% ({pair_count}/{total_pairs})")

        self._computed = True
        logger.info(f"Similarity matrix computation completed")

    def compute_generator(
        self,
        comparator: BaseComparator,
    ) -> Generator[tuple[str, str, float], None, None]:
        """使用生成器计算相似度 (减少内存占用)

        Args:
            comparator: 比较器实例

        Yields:
            (video_id1, video_id2, similarity) 元组
        """
        n = self.size
        total_pairs = n * (n - 1) // 2

        # 仍然需要存储矩阵以支持后续查询
        self._matrix = np.zeros(total_pairs, dtype=np.float64)

        logger.info(
            f"Computing similarity matrix for {n} fingerprints "
            f"({total_pairs} pairs) using generator"
        )

        pair_count = 0
        for i in range(n):
            for j in range(i + 1, n):
                similarity = comparator.compare(
                    self.fingerprints[i], self.fingerprints[j]
                )
                self._matrix[self._get_flat_index(i, j)] = similarity
                pair_count += 1

                yield (
                    self.fingerprints[i].video_id,
                    self.fingerprints[j].video_id,
                    similarity,
                )

        self._computed = True
        logger.info(f"Similarity matrix computation completed")

    def get(self, id1: str, id2: str) -> float:
        """获取两个视频的相似度

        Args:
            id1: 第一个视频的 ID (fingerprint.id 或 fingerprint.video_id)
            id2: 第二个视频的 ID

        Returns:
            相似度值,如果未找到返回 0.0
        """
        if not self._computed or self._matrix is None:
            logger.warning("Similarity matrix not computed yet")
            return 0.0

        # 尝试通过 fingerprint ID 或 video_id 查找索引
        i = self._id_to_index.get(id1, self._video_id_to_index.get(id1))
        j = self._id_to_index.get(id2, self._video_id_to_index.get(id2))

        if i is None or j is None:
            logger.warning(f"Fingerprint not found: {id1} or {id2}")
            return 0.0

        if i == j:
            return 1.0  # 相同视频相似度为 1

        return self._matrix[self._get_flat_index(i, j)]

    def get_by_indices(self, i: int, j: int) -> float:
        """通过索引获取相似度

        Args:
            i: 第一个指纹的索引
            j: 第二个指纹的索引

        Returns:
            相似度值
        """
        if not self._computed or self._matrix is None:
            logger.warning("Similarity matrix not computed yet")
            return 0.0

        if i < 0 or i >= self.size or j < 0 or j >= self.size:
            raise IndexError(f"Index out of range: ({i}, {j})")

        if i == j:
            return 1.0

        return self._matrix[self._get_flat_index(i, j)]

    def get_similar_pairs(
        self, threshold: float
    ) -> Iterator[tuple[str, str, float]]:
        """获取所有相似度超过阈值的视频对

        Args:
            threshold: 相似度阈值 (0.0 - 1.0)

        Yields:
            (video_id1, video_id2, similarity) 元组
        """
        if not self._computed or self._matrix is None:
            logger.warning("Similarity matrix not computed yet")
            return

        n = self.size
        for i in range(n):
            for j in range(i + 1, n):
                similarity = self._matrix[self._get_flat_index(i, j)]
                if similarity >= threshold:
                    yield (
                        self.fingerprints[i].video_id,
                        self.fingerprints[j].video_id,
                        similarity,
                    )

    def get_neighbors(self, video_id: str, threshold: float) -> list[tuple[str, float]]:
        """获取与指定视频相似度超过阈值的所有邻居

        Args:
            video_id: 视频ID
            threshold: 相似度阈值

        Returns:
            [(neighbor_video_id, similarity), ...] 列表
        """
        if not self._computed or self._matrix is None:
            logger.warning("Similarity matrix not computed yet")
            return []

        # 查找索引
        idx = self._id_to_index.get(video_id, self._video_id_to_index.get(video_id))
        if idx is None:
            logger.warning(f"Video not found: {video_id}")
            return []

        neighbors = []
        for j in range(self.size):
            if j == idx:
                continue
            similarity = self.get_by_indices(idx, j)
            if similarity >= threshold:
                neighbors.append((self.fingerprints[j].video_id, similarity))

        # 按相似度降序排序
        neighbors.sort(key=lambda x: x[1], reverse=True)
        return neighbors

    def to_dense_matrix(self) -> np.ndarray:
        """转换为密集矩阵形式

        Returns:
            n x n 的对称相似度矩阵
        """
        if not self._computed or self._matrix is None:
            raise RuntimeError("Similarity matrix not computed yet")

        n = self.size
        dense = np.eye(n, dtype=np.float64)  # 对角线为 1

        for i in range(n):
            for j in range(i + 1, n):
                sim = self._matrix[self._get_flat_index(i, j)]
                dense[i, j] = sim
                dense[j, i] = sim  # 对称

        return dense

    def get_statistics(self) -> dict:
        """获取相似度矩阵统计信息

        Returns:
            包含统计信息的字典
        """
        if not self._computed or self._matrix is None:
            return {"computed": False}

        return {
            "computed": True,
            "size": self.size,
            "total_pairs": len(self._matrix),
            "mean": float(np.mean(self._matrix)),
            "std": float(np.std(self._matrix)),
            "min": float(np.min(self._matrix)),
            "max": float(np.max(self._matrix)),
            "median": float(np.median(self._matrix)),
        }


__all__ = [
    "BaseComparator",
    "HashComparator",
    "DTWComparator",
    "SimilarityMatrix",
    "hamming_distance",
    "hash_similarity",
]
