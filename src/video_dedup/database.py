"""数据库管理模块"""

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional
import json

from video_dedup.models import VideoFile, VideoFingerprint, DuplicateGroup


class Database:
    """SQLite 数据库管理"""

    SCHEMA = """
    -- 视频文件表
    CREATE TABLE IF NOT EXISTS video_files (
        id TEXT PRIMARY KEY,
        path TEXT UNIQUE NOT NULL,
        size INTEGER,
        duration REAL,
        width INTEGER,
        height INTEGER,
        format TEXT,
        created_at TIMESTAMP,
        modified_at TIMESTAMP,
        checksum TEXT,
        indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 视频指纹表
    CREATE TABLE IF NOT EXISTS video_fingerprints (
        id TEXT PRIMARY KEY,
        video_id TEXT REFERENCES video_files(id) ON DELETE CASCADE,
        frame_hashes TEXT,
        duration_hash TEXT,
        color_histogram TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 重复组表
    CREATE TABLE IF NOT EXISTS duplicate_groups (
        id TEXT PRIMARY KEY,
        similarity REAL,
        reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- 组成员关联表
    CREATE TABLE IF NOT EXISTS group_members (
        group_id TEXT REFERENCES duplicate_groups(id) ON DELETE CASCADE,
        video_id TEXT REFERENCES video_files(id) ON DELETE CASCADE,
        is_representative BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (group_id, video_id)
    );

    -- 索引
    CREATE INDEX IF NOT EXISTS idx_video_path ON video_files(path);
    CREATE INDEX IF NOT EXISTS idx_fingerprint_video ON video_fingerprints(video_id);
    CREATE INDEX IF NOT EXISTS idx_group_members_video ON group_members(video_id);
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """建立数据库连接"""
        self._conn = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _create_tables(self) -> None:
        """创建表结构"""
        self._conn.executescript(self.SCHEMA)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(video_fingerprints)")}
        if "feature_data" not in columns:
            self._conn.execute("ALTER TABLE video_fingerprints ADD COLUMN feature_data TEXT DEFAULT '{}'")
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(video_files)")}
        if "checksum_algorithm" not in columns:
            self._conn.execute("ALTER TABLE video_files ADD COLUMN checksum_algorithm TEXT DEFAULT ''")
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(duplicate_groups)")}
        if "match_type" not in columns:
            self._conn.execute("ALTER TABLE duplicate_groups ADD COLUMN match_type TEXT DEFAULT 'content'")
        self._conn.commit()

    # VideoFile 操作
    def insert_video(self, video: VideoFile) -> None:
        """插入视频记录"""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO video_files
            (id, path, size, duration, width, height, format, created_at, modified_at, checksum, checksum_algorithm)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                video.id,
                str(video.path),
                video.size,
                video.duration,
                video.width,
                video.height,
                video.format,
                video.created_at,
                video.modified_at,
                video.checksum,
                video.checksum_algorithm,
            ),
        )
        self._conn.commit()

    def get_video(self, video_id: str) -> Optional[VideoFile]:
        """获取视频记录"""
        row = self._conn.execute(
            "SELECT * FROM video_files WHERE id = ?", (video_id,)
        ).fetchone()
        return self._row_to_video(row) if row else None

    def get_video_by_path(self, path: Path) -> Optional[VideoFile]:
        """根据路径获取视频记录"""
        row = self._conn.execute(
            "SELECT * FROM video_files WHERE path = ?", (str(path),)
        ).fetchone()
        return self._row_to_video(row) if row else None

    def get_all_videos(self) -> list[VideoFile]:
        """获取所有视频记录"""
        rows = self._conn.execute("SELECT * FROM video_files").fetchall()
        return [self._row_to_video(row) for row in rows]

    def _row_to_video(self, row: sqlite3.Row) -> VideoFile:
        """将数据库行转换为 VideoFile 对象"""
        return VideoFile(
            id=row["id"],
            path=Path(row["path"]),
            size=row["size"],
            duration=row["duration"],
            width=row["width"],
            height=row["height"],
            format=row["format"],
            created_at=row["created_at"],
            modified_at=row["modified_at"],
            checksum=row["checksum"],
            checksum_algorithm=row["checksum_algorithm"],
        )

    # VideoFingerprint 操作
    def insert_fingerprint(self, fp: VideoFingerprint) -> None:
        """插入指纹记录"""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO video_fingerprints
            (id, video_id, frame_hashes, duration_hash, color_histogram, feature_data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                fp.id,
                fp.video_id,
                json.dumps(fp.frame_hashes),
                fp.duration_hash,
                json.dumps(fp.color_histogram),
                json.dumps({"feature_version": fp.feature_version, "hash_method": fp.hash_method,
                            "hash_bits": fp.hash_bits, "sample_positions": fp.sample_positions,
                            "frame_positions": fp.frame_positions, "errors": fp.errors}),
            ),
        )
        self._conn.commit()

    def get_fingerprint(self, video_id: str) -> Optional[VideoFingerprint]:
        """获取视频指纹"""
        row = self._conn.execute(
            "SELECT * FROM video_fingerprints WHERE video_id = ?", (video_id,)
        ).fetchone()
        if not row:
            return None
        return VideoFingerprint(
            **json.loads(row["feature_data"] or "{}"),
            id=row["id"],
            video_id=row["video_id"],
            frame_hashes=json.loads(row["frame_hashes"]),
            duration_hash=row["duration_hash"],
            color_histogram=json.loads(row["color_histogram"]),
            created_at=row["created_at"],
        )

    # DuplicateGroup 操作
    def insert_group(self, group: DuplicateGroup) -> None:
        """插入重复组"""
        self._conn.execute(
            "INSERT INTO duplicate_groups (id, similarity, reason, match_type) VALUES (?, ?, ?, ?)",
            (group.id, group.similarity, group.reason, group.match_type),
        )

        for video_id in group.video_ids:
            is_rep = video_id == group.representative_id
            self._conn.execute(
                "INSERT INTO group_members (group_id, video_id, is_representative) VALUES (?, ?, ?)",
                (group.id, video_id, is_rep),
            )
        self._conn.commit()

    def get_all_groups(self) -> list[DuplicateGroup]:
        """获取所有重复组"""
        groups = []
        rows = self._conn.execute("SELECT * FROM duplicate_groups").fetchall()
        for row in rows:
            members = self._conn.execute(
                """
                SELECT video_id, is_representative
                FROM group_members WHERE group_id = ?
                """,
                (row["id"],),
            ).fetchall()

            video_ids = [m["video_id"] for m in members]
            rep_id = next(
                (m["video_id"] for m in members if m["is_representative"]), None
            )

            groups.append(
                DuplicateGroup(
                    id=row["id"],
                    video_ids=video_ids,
                    similarity=row["similarity"],
                    representative_id=rep_id,
                    reason=row["reason"],
                    match_type=row["match_type"],
                    created_at=row["created_at"],
                )
            )
        return groups

    def clear_groups(self) -> None:
        """清空重复组"""
        self._conn.execute("DELETE FROM group_members")
        self._conn.execute("DELETE FROM duplicate_groups")
        self._conn.commit()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


__all__ = ["Database"]
