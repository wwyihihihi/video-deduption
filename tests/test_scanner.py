"""视频扫描器测试"""

from video_dedup.config import ScannerConfig
from video_dedup.scanner import VideoScanner


class TestVideoScanner:
    """视频扫描器测试"""

    def test_is_video_file_mp4(self, temp_dir):
        """测试 MP4 文件识别"""
        scanner = VideoScanner(ScannerConfig(min_file_size=0))
        video_file = temp_dir / "test.mp4"
        video_file.touch()
        assert scanner.is_video_file(video_file)

    def test_is_video_file_txt(self, temp_dir):
        """测试非视频文件识别"""
        scanner = VideoScanner(ScannerConfig(min_file_size=0))
        text_file = temp_dir / "test.txt"
        text_file.touch()
        assert not scanner.is_video_file(text_file)

    def test_scan_empty_dir(self, temp_dir):
        """测试空目录扫描"""
        scanner = VideoScanner(ScannerConfig(min_file_size=0))
        result = scanner.scan(temp_dir)
        assert result.total_files == 0
        assert result.video_files == 0

    def test_supported_formats(self):
        """测试支持的视频格式"""
        config = ScannerConfig()
        expected_formats = [".mp4", ".avi", ".mkv", ".mov"]
        for fmt in expected_formats:
            assert fmt in config.supported_formats
