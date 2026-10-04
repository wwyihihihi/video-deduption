"""特征提取器测试"""

import pytest

from video_dedup.extractor import HashCalculator
from video_dedup.models import VideoFile


class TestHashCalculator:
    """哈希计算器测试"""

    def test_hamming_distance_same(self):
        """测试相同哈希的汉明距离"""
        calc = HashCalculator()
        # TODO: 实现测试
        pass

    def test_hamming_distance_different(self):
        """测试不同哈希的汉明距离"""
        # TODO: 实现测试
        pass
