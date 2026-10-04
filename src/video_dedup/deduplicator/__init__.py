"""去重逻辑模块

提供视频去重的核心功能:
- DuplicateGrouper: 重复视频分组器
- RepresentativeSelector: 代表文件选择器
- DeduplicationEngine: 去重引擎
"""

import logging
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime

from video_dedup.models import VideoFile, DuplicateGroup, DeduplicationReport
from video_dedup.comparator import SimilarityMatrix

logger = logging.getLogger(__name__)


class BaseGrouper(ABC):
    """分组器基类"""

    @abstractmethod
    def group(self, similarity_matrix: SimilarityMatrix) -> list[DuplicateGroup]:
        """根据相似度矩阵进行分组

        Args:
            similarity_matrix: 相似度矩阵

        Returns:
            重复视频组列表
        """
        pass


class UnionFind:
    """并查集实现

    使用路径压缩和按秩合并优化,用于高效合并相似视频组。
    """

    def __init__(self, n: int):
        """初始化并查集

        Args:
            n: 元素数量
        """
        self.parent = list(range(n))
        self.rank = [0] * n
        self._count = n  # 连通分量数量

    def find(self, x: int) -> int:
        """查找元素所属集合的代表元素 (带路径压缩)

        Args:
            x: 元素索引

        Returns:
            集合代表元素索引
        """
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: int, y: int) -> bool:
        """合并两个元素所属的集合 (按秩合并)

        Args:
            x: 第一个元素索引
            y: 第二个元素索引

        Returns:
            是否成功合并 (如果已在同一集合则返回 False)
        """
        px, py = self.find(x), self.find(y)
        if px == py:
            return False

        # 按秩合并
        if self.rank[px] < self.rank[py]:
            px, py = py, px
        self.parent[py] = px
        if self.rank[px] == self.rank[py]:
            self.rank[px] += 1

        self._count -= 1
        return True

    def connected(self, x: int, y: int) -> bool:
        """检查两个元素是否在同一集合中

        Args:
            x: 第一个元素索引
            y: 第二个元素索引

        Returns:
            是否在同一集合
        """
        return self.find(x) == self.find(y)

    @property
    def count(self) -> int:
        """获取当前连通分量数量"""
        return self._count


class DuplicateGrouper(BaseGrouper):
    """重复视频分组器

    使用完整连接分组，组内任意成员均直接达到相似度阈值。
    """

    # 相似度等级对应的判定原因
    SIMILARITY_REASONS = {
        "exact": "内容高度相似 (感知相似度 > 0.99，非文件摘要证明)",
        "high": "高度相似 (相似度 > 0.90)",
        "moderate": "相似 (相似度 > 0.85)",
        "low": "可能相似 (相似度 > 0.70)",
    }

    def __init__(self, threshold: float = 0.85):
        """初始化分组器

        Args:
            threshold: 相似度阈值,超过此值的视频对将被归为同一组
        """
        self.threshold = threshold
        logger.info(f"DuplicateGrouper initialized with threshold: {threshold}")

    def group(self, similarity_matrix: SimilarityMatrix) -> list[DuplicateGroup]:
        """对组内两两直接匹配的视频分组

        流程:
        1. 遍历相似度矩阵,找出所有超过阈值的视频对
        2. 只合并组内两两直接匹配的视频
        3. 生成 DuplicateGroup 列表
        4. 设置每个组的相似度和判定原因

        Args:
            similarity_matrix: 相似度矩阵

        Returns:
            重复视频组列表 (每组至少包含2个视频)
        """
        if not similarity_matrix.is_computed:
            logger.warning("Similarity matrix not computed, returning empty groups")
            return []

        n = similarity_matrix.size
        if n < 2:
            logger.info("Less than 2 videos, no duplicates possible")
            return []

        logger.info(f"Grouping {n} videos with threshold {self.threshold}")

        # 兼容矩阵入口采用完整连接分组：任意成员均须直接匹配。
        # A-B 与 B-C 达标不能自动推导 A-C 重复。
        groups_dict = {}
        pair_similarities = {}
        for i in range(n):
            for j in range(i + 1, n):
                pair_similarities[i, j] = similarity_matrix.get_by_indices(i, j)
            destination = next((key for key, indices in groups_dict.items()
                                if all(similarity_matrix.get_by_indices(i, j) >= self.threshold
                                       for j in indices)), None)
            if destination is None:
                groups_dict[i] = [i]
            else:
                groups_dict[destination].append(i)

        # 生成 DuplicateGroup 列表 (只保留有多个视频的组)
        groups: list[DuplicateGroup] = []
        for indices in groups_dict.values():
            if len(indices) < 2:
                continue

            # 获取视频 ID
            video_ids = [
                similarity_matrix.fingerprints[idx].video_id
                for idx in indices
            ]

            # 计算组内最低相似度
            min_similarity = 1.0
            for i, idx1 in enumerate(indices):
                for idx2 in indices[i + 1:]:
                    sim = pair_similarities.get((min(idx1, idx2), max(idx1, idx2)), 0.0)
                    min_similarity = min(min_similarity, sim)

            # 确定判定原因
            reason = self._get_similarity_reason(min_similarity)

            group = DuplicateGroup(
                video_ids=video_ids,
                similarity=min_similarity,
                reason=reason,
            )
            groups.append(group)

        # 按相似度降序排序
        groups.sort(key=lambda g: g.similarity, reverse=True)

        logger.info(f"Created {len(groups)} duplicate groups")
        return groups

    def _get_similarity_reason(self, similarity: float) -> str:
        """根据相似度获取判定原因

        Args:
            similarity: 相似度值

        Returns:
            判定原因字符串
        """
        if similarity > 0.99:
            return self.SIMILARITY_REASONS["exact"]
        elif similarity > 0.90:
            return self.SIMILARITY_REASONS["high"]
        elif similarity > 0.85:
            return self.SIMILARITY_REASONS["moderate"]
        else:
            return self.SIMILARITY_REASONS["low"]


class RepresentativeSelector:
    """代表文件选择器

    从重复视频组中选择一个"最佳"文件作为保留的代表文件。
    选择策略按优先级:
    1. 分辨率最高
    2. 文件大小适中 (不选最大也不选最小)
    3. 文件名规范 (无 "副本"、"copy"、"(1)" 等)
    4. 修改时间最新
    """

    # 不规范的文件名模式
    IRREGULAR_PATTERNS = [
        r"副本",           # 中文 "副本"
        r"copy",           # 英文 "copy"
        r"\(\d+\)",        # "(1)", "(2)" 等
        r"复件",           # 中文 "复件"
        r"_\d+$",          # "_1", "_2" 等后缀
        r"-\d+$",          # "-1", "-2" 等后缀
    ]

    def __init__(self):
        """初始化选择器"""
        self._compiled_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.IRREGULAR_PATTERNS
        ]

    def select(self, videos: list[VideoFile]) -> VideoFile:
        """从重复组中选择保留的代表文件

        Args:
            videos: 候选视频列表

        Returns:
            选中的代表文件

        Raises:
            ValueError: 当视频列表为空时
        """
        if not videos:
            raise ValueError("Video list cannot be empty")

        if len(videos) == 1:
            return videos[0]

        # 计算每个视频的评分
        scored_videos = [(video, self._score_video(video, videos)) for video in videos]

        # 按评分降序排序
        scored_videos.sort(key=lambda x: (-x[1], str(x[0].path.resolve()).casefold(), str(x[0].path)))

        best_video = scored_videos[0][0]
        best_score = scored_videos[0][1]

        logger.debug(
            f"Selected representative: {best_video.path} (score: {best_score:.2f})"
        )

        return best_video

    def _score_video(self, video: VideoFile, all_videos: list[VideoFile]) -> float:
        """计算视频的综合评分

        评分维度 (每项满分 25 分,总分 100 分):
        1. 分辨率评分: 分辨率越高分数越高
        2. 文件大小评分: 适中的文件大小得分最高
        3. 文件名评分: 规范的文件名得分高
        4. 修改时间评分: 最新的文件得分高

        Args:
            video: 待评分的视频
            all_videos: 所有候选视频 (用于相对评分)

        Returns:
            综合评分 (0-100)
        """
        score = 0.0

        # 1. 分辨率评分 (0-25)
        score += self._score_resolution(video, all_videos)

        # 2. 文件大小评分 (0-25)
        score += self._score_file_size(video, all_videos)

        # 3. 文件名规范性评分 (0-25)
        score += self._score_filename(video)

        # 4. 修改时间评分 (0-25)
        score += self._score_modified_time(video, all_videos)

        return score

    def _score_resolution(self, video: VideoFile, all_videos: list[VideoFile]) -> float:
        """分辨率评分

        分辨率越高得分越高,使用相对排名计算。

        Args:
            video: 待评分的视频
            all_videos: 所有候选视频

        Returns:
            分辨率评分 (0-25)
        """
        # 计算所有视频的分辨率 (像素数)
        resolutions = [v.width * v.height for v in all_videos]
        max_res = max(resolutions) if resolutions else 1
        video_res = video.width * video.height

        if max_res == 0:
            return 12.5  # 默认中等分数

        # 归一化到 0-25
        return (video_res / max_res) * 25

    def _score_file_size(self, video: VideoFile, all_videos: list[VideoFile]) -> float:
        """文件大小评分

        文件大小适中得分最高。计算方法:
        - 距离平均值越近得分越高
        - 最大和最小的文件得分较低

        Args:
            video: 待评分的视频
            all_videos: 所有候选视频

        Returns:
            文件大小评分 (0-25)
        """
        if len(all_videos) < 2:
            return 25.0

        sizes = [v.size for v in all_videos]
        min_size = min(sizes)
        max_size = max(sizes)
        avg_size = sum(sizes) / len(sizes)

        if max_size == min_size:
            return 25.0  # 所有文件大小相同

        # 计算与平均值的相对距离
        # 距离平均值越近得分越高
        distance = abs(video.size - avg_size)
        max_distance = max(abs(max_size - avg_size), abs(min_size - avg_size))

        if max_distance == 0:
            return 25.0

        # 归一化: 距离越大分数越低
        normalized = 1.0 - (distance / max_distance)
        return normalized * 25

    def _score_filename(self, video: VideoFile) -> float:
        """文件名规范性评分

        检查文件名是否包含不规范的标记,如:
        - "副本"、"copy"
        - "(1)", "(2)" 等
        - "_1", "_2" 后缀等

        Args:
            video: 待评分的视频

        Returns:
            文件名评分 (0-25)
        """
        filename = video.path.stem.lower()

        # 检查是否匹配不规范模式
        for pattern in self._compiled_patterns:
            if pattern.search(filename):
                # 每匹配一个模式扣 5 分
                return max(0, 25 - 5)

        # 文件名规范,得满分
        return 25.0

    def _score_modified_time(self, video: VideoFile, all_videos: list[VideoFile]) -> float:
        """修改时间评分

        修改时间越新得分越高。

        Args:
            video: 待评分的视频
            all_videos: 所有候选视频

        Returns:
            修改时间评分 (0-25)
        """
        # 获取所有有修改时间的视频
        videos_with_time = [v for v in all_videos if v.modified_at is not None]

        if not videos_with_time:
            return 12.5  # 没有修改时间信息,返回中等分数

        times = [v.modified_at for v in videos_with_time if v.modified_at]
        if not times:
            return 12.5

        min_time = min(times)
        max_time = max(times)

        if video.modified_at is None:
            return 12.5

        if max_time == min_time:
            return 25.0

        # 归一化: 时间越新分数越高
        time_range = (max_time - min_time).total_seconds()
        if time_range == 0:
            return 25.0

        video_time_offset = (video.modified_at - min_time).total_seconds()
        return (video_time_offset / time_range) * 25


class DeduplicationEngine:
    """去重引擎

    协调分组器和选择器,执行完整的去重分析流程。
    """

    def __init__(
        self,
        grouper: BaseGrouper | None = None,
        threshold: float = 0.85,
    ):
        """初始化去重引擎

        Args:
            grouper: 分组器实例 (可选)
            threshold: 相似度阈值
        """
        self.grouper = grouper or DuplicateGrouper(threshold=threshold)
        self.selector = RepresentativeSelector()
        logger.info("DeduplicationEngine initialized")

    def analyze(self, path, config, recursive: bool = True) -> DeduplicationReport:
        """所有 CLI 入口共用的只读检测流程，不执行文件操作。"""
        from video_dedup.scanner import VideoScanner, file_signature
        from video_dedup.extractor import VideoFingerprintExtractor
        from video_dedup.comparator import DTWComparator
        import math

        scanner = VideoScanner(config.scanner)
        scan = scanner.scan(path, recursive)
        report = DeduplicationReport(total_videos=scan.video_files,
                                     video_map={v.id: v for v in scan.videos})
        report.failures.extend({"stage": "scan", "path": str(path), "reason": reason}
                               for reason in scan.errors)
        for video in scan.videos:
            report.file_statuses[video.id] = "unique"

        def fail(video, stage, reason):
            report.file_statuses[video.id] = "failed"
            report.failures.append({"video_id": video.id, "path": str(video.path),
                                    "stage": stage, "reason": str(reason)})

        def unchanged(video):
            try:
                return file_signature(video.path) == video.file_signature
            except OSError:
                return False

        by_size = defaultdict(list)
        for video in scan.videos:
            by_size[video.size].append(video)
        clusters = []
        for videos in by_size.values():
            by_digest = defaultdict(list)
            for video in videos:
                if not unchanged(video):
                    fail(video, "checksum", "文件在扫描后发生变化或无法读取")
                    continue
                if len(videos) == 1:
                    clusters.append([video])
                    continue
                video.checksum = scanner.calculate_checksum(video.path, algorithm="sha256")
                video.checksum_algorithm = "sha256" if video.checksum else ""
                if not video.checksum or not unchanged(video):
                    fail(video, "checksum", "SHA-256 读取失败或文件在计算期间变化")
                    continue
                by_digest[video.checksum].append(video)
            clusters.extend(by_digest.values())

        extractor = VideoFingerprintExtractor(config.extractor)
        comparator = DTWComparator(config.comparator)
        fingerprints = {}
        representatives = {}
        usable = []
        for index, members in enumerate(clusters):
            representative = self.selector.select(members)
            representatives[index] = representative
            try:
                fp = extractor.extract(representative)
            except Exception as error:
                fp = None
                extraction_error = str(error)
            else:
                extraction_error = "无法提取视频指纹"
            if not all(unchanged(video) for video in members):
                for video in members:
                    fail(video, "extract", "文件或同组代表在特征提取期间变化，请重新扫描")
                continue
            if fp is not None:
                fingerprints[index] = fp
                for video in members:
                    report.fingerprint_details[video.id] = {
                        "feature_version": fp.feature_version,
                        "source_video_id": representative.id,
                        "hash_count": fp.hash_count, "hash_bits": fp.hash_bits,
                        "complete": fp.complete, "sample_positions": fp.sample_positions,
                        "errors": fp.errors,
                    }
            usable.append(index)
            # 字节完全相同可独立证明重复，内容解码失败不推翻摘要证据。
            if fp is None or not fp.complete:
                if len(members) == 1:
                    report.file_statuses[representative.id] = "pending_review"
                    report.pending_review.append({
                        "video_id": representative.id, "path": str(representative.path),
                        "reason": extraction_error if fp is None else "采样不完整或定位失败",
                        "sample_positions": fp.sample_positions if fp else [],
                        "errors": fp.errors if fp else [extraction_error], "candidates": [],
                    })

        def duration_matches(a, b):
            return (math.isfinite(a.duration) and math.isfinite(b.duration)
                    and min(a.duration, b.duration) > 0
                    and abs(a.duration - b.duration) / max(a.duration, b.duration)
                    <= config.comparator.duration_tolerance)

        accepted = {}
        complete = [i for i in usable if i in fingerprints and fingerprints[i].complete
                    and math.isfinite(representatives[i].duration)
                    and representatives[i].duration > 0]
        for pos, i in enumerate(complete):
            for j in complete[pos + 1:]:
                if not duration_matches(representatives[i], representatives[j]):
                    continue
                result = comparator.compare_with_details(fingerprints[i], fingerprints[j])
                if (result.similarity >= config.comparator.similarity_threshold
                        and result.details["matching_ratio"] >= config.comparator.min_matching_frames):
                    accepted[min(i, j), max(i, j)] = result.similarity

        for pending in report.pending_review:
            i = next(index for index in usable if representatives[index].id == pending["video_id"])
            fp = fingerprints.get(i)
            if fp is None or not fp.frame_hashes:
                continue
            candidates = []
            for j in complete:
                if i == j or not duration_matches(representatives[i], representatives[j]):
                    continue
                result = comparator.compare_partial(fp, fingerprints[j])
                if result.details["compared_frames"]:
                    candidates.append({"video_id": representatives[j].id,
                                       "path": str(representatives[j].path),
                                       "similarity": result.similarity, **result.details})
            pending["candidates"] = sorted(candidates, key=lambda c: -c["similarity"])[:5]

        stable_usable = []
        for i in usable:
            if all(unchanged(video) for video in clusters[i]):
                stable_usable.append(i)
            else:
                for video in clusters[i]:
                    fail(video, "analyze", "文件在分析期间发生变化，请重新扫描")
        usable = stable_usable
        report.pending_review = [item for item in report.pending_review
                                 if report.file_statuses[item["video_id"]] != "failed"]

        # 按保留评分挑中心，每个成员必须直接与中心匹配；禁止链式合并。
        remaining = set(usable)
        while remaining:
            center_video = self.selector.select([representatives[i] for i in sorted(remaining)])
            center = next(i for i in remaining if representatives[i].id == center_video.id)
            selected = [center] + sorted(i for i in remaining if i != center
                         and (min(i, center), max(i, center)) in accepted)
            remaining.difference_update(selected)
            members = [v for i in selected for v in clusters[i]]
            if len(members) < 2:
                continue
            # 中心代表已是所有候选中评分最高者，因此最终保留文件不会换中心。
            similarity = min((accepted[min(center, i), max(center, i)] for i in selected
                              if i != center), default=1.0)
            match_type = "exact" if len(selected) == 1 else "content"
            report.groups.append(DuplicateGroup(
                video_ids=[v.id for v in members], representative_id=center_video.id,
                similarity=similarity, match_type=match_type,
                reason="完整 SHA-256 和文件大小一致" if match_type == "exact"
                else "时长及有序画面匹配，每个成员直接匹配保留文件",
            ))
            for video in members:
                report.file_statuses[video.id] = "duplicate"
            report.total_duplicates += len(members) - 1
            report.space_savings += sum(v.size for v in members if v.id != center_video.id)
        report.duplicate_groups = len(report.groups)
        report.analyzed_videos = sum(status in ("unique", "duplicate")
                                     for status in report.file_statuses.values())
        return report

    def deduplicate(
        self,
        videos: list[VideoFile],
        similarity_matrix: SimilarityMatrix
    ) -> DeduplicationReport:
        """执行去重分析

        流程:
        1. 使用 DuplicateGrouper 分组
        2. 对每个组使用 RepresentativeSelector 选择保留文件
        3. 计算可节省空间 (删除文件的总大小)
        4. 生成 DeduplicationReport

        Args:
            videos: 视频文件列表
            similarity_matrix: 相似度矩阵

        Returns:
            去重报告
        """
        logger.info(f"Starting deduplication for {len(videos)} videos")

        # 创建视频 ID 到视频对象的映射
        video_map: dict[str, VideoFile] = {v.id: v for v in videos}

        # 1. 分组
        groups = self.grouper.group(similarity_matrix)
        logger.info(f"Found {len(groups)} duplicate groups")

        # 2. 对每个组选择代表文件
        total_duplicates = 0
        total_space_savings = 0

        for group in groups:
            # 获取组内的视频对象
            group_videos = [video_map[vid] for vid in group.video_ids if vid in video_map]

            if len(group_videos) < 2:
                continue

            # 选择代表文件
            representative = self.selector.select(group_videos)
            group.representative_id = representative.id

            # 计算该组可节省的空间 (删除其他文件的总大小)
            group_savings = sum(
                v.size for v in group_videos if v.id != representative.id
            )
            total_space_savings += group_savings
            total_duplicates += len(group_videos) - 1

            logger.debug(
                f"Group {group.id[:8]}...: {len(group_videos)} videos, "
                f"representative: {representative.path.name}, "
                f"savings: {group_savings / (1024*1024):.2f} MB"
            )

        # 3. 生成报告
        report = DeduplicationReport(
            total_videos=len(videos),
            duplicate_groups=len(groups),
            total_duplicates=total_duplicates,
            space_savings=total_space_savings,
            groups=groups,
            video_map=video_map,
            generated_at=datetime.now(),
        )

        logger.info(
            f"Deduplication completed: {len(groups)} groups, "
            f"{total_duplicates} duplicates, "
            f"{total_space_savings / (1024*1024):.2f} MB potential savings"
        )

        return report


__all__ = [
    "BaseGrouper",
    "DuplicateGrouper",
    "UnionFind",
    "RepresentativeSelector",
    "DeduplicationEngine",
]
