# 架构设计文档

## 1. 系统架构

### 1.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI Layer                             │
│                      (cli.py)                                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Application Layer                         │
│                    (deduplicator/)                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │   Scanner   │→ │  Extractor  │→ │  Comparator │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
│         │                                    │              │
│         ▼                                    ▼              │
│  ┌─────────────┐                     ┌─────────────┐        │
│  │   Handler   │                     │   Reporter  │        │
│  └─────────────┘                     └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Storage Layer                            │
│              ┌─────────────────────────┐                    │
│              │   SQLite Database       │                    │
│              │   (metadata cache)      │                    │
│              └─────────────────────────┘                    │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 模块职责

| 模块 | 职责 | 核心类/函数 |
|------|------|-------------|
| scanner | 扫描目录，收集视频文件信息 | `VideoScanner`, `FileCollector` |
| extractor | 提取视频特征指纹 | `FrameExtractor`, `HashCalculator` |
| comparator | 比较视频相似度 | `SimilarityComparator`, `FingerprintMatcher` |
| deduplicator | 去重核心逻辑 | `DeduplicationEngine`, `DuplicateGrouper` |
| reporter | 生成报告 | `ReportGenerator`, `HTMLReporter` |
| handler | 文件操作处理 | `FileHandler`, `MoveStrategy`, `DeleteStrategy` |

## 2. 数据流设计

### 2.1 主流程

```
输入: 目录路径
     ↓
[Scanner] 扫描目录 → 获取视频文件列表
     ↓
[Extractor] 提取特征 → 生成视频指纹
     ↓
[Comparator] 两两比对 → 计算相似度矩阵
     ↓
[Deduplicator] 分组聚类 → 识别重复组
     ↓
[Reporter] 生成报告 → 输出结果
     ↓
[Handler] 执行操作 → 删除/移动文件
```

### 2.2 特征提取流程

```
视频文件
    ↓
FFmpeg/OpenCV 解码
    ↓
均匀采样关键帧 (N帧)
    ↓
每帧计算感知哈希 (pHash/dHash)
    ↓
组合为视频指纹向量
    ↓
存储到数据库
```

## 3. 数据模型

### 3.1 核心实体

```python
@dataclass
class VideoFile:
    """视频文件信息"""
    id: str                    # UUID
    path: Path                 # 文件路径
    size: int                  # 文件大小
    duration: float            # 时长(秒)
    resolution: Tuple[int,int] # 分辨率
    format: str                # 格式
    created_at: datetime       # 创建时间
    modified_at: datetime      # 修改时间
    checksum: str              # MD5校验和

@dataclass
class VideoFingerprint:
    """视频指纹"""
    video_id: str              # 关联视频ID
    frame_hashes: List[str]    # 关键帧哈希列表
    duration_hash: str         # 时长特征
    color_histogram: List[int] # 颜色直方图
    created_at: datetime       # 创建时间

@dataclass
class DuplicateGroup:
    """重复组"""
    id: str                    # 组ID
    videos: List[VideoFile]    # 组内视频
    similarity: float          # 相似度
    representative: VideoFile  # 代表文件(保留)
    reason: str                # 判定原因
```

### 3.2 数据库Schema

```sql
-- 视频文件表
CREATE TABLE video_files (
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
CREATE TABLE video_fingerprints (
    id TEXT PRIMARY KEY,
    video_id TEXT REFERENCES video_files(id),
    frame_hashes TEXT,          -- JSON array
    duration_hash TEXT,
    color_histogram TEXT,       -- JSON array
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 重复组表
CREATE TABLE duplicate_groups (
    id TEXT PRIMARY KEY,
    similarity REAL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 组成员关联表
CREATE TABLE group_members (
    group_id TEXT REFERENCES duplicate_groups(id),
    video_id TEXT REFERENCES video_files(id),
    is_representative BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (group_id, video_id)
);
```

## 4. 算法策略

### 4.1 快速筛选层 (粗筛)

1. **文件大小比对**: 大小差异超过阈值直接排除
2. **时长比对**: 时长差异超过阈值直接排除
3. **分辨率比对**: 分辨率完全不同降低优先级

### 4.2 精确比对层 (精筛)

1. **帧哈希比对**: 计算关键帧的汉明距离
2. **颜色直方图**: 比较整体色调分布
3. **时间序列比对**: DTW 动态时间规整

### 4.3 相似度评分

```
总相似度 = w1*帧相似度 + w2*时长相似度 + w3*色彩相似度

阈值设置:
- 完全相同: > 0.99
- 高度相似: > 0.85
- 可能相似: > 0.70
- 不相似: < 0.70
```

## 5. 性能优化

### 5.1 并行处理

- 使用 `concurrent.futures` 多线程/多进程并行提取特征
- 使用数据库批量插入减少 IO
- 使用内存缓存热点数据

### 5.2 增量扫描

- 记录文件修改时间，只处理新增/修改的文件
- 维护指纹缓存，避免重复计算

### 5.3 分块处理

- 大文件分块读取，避免内存溢出
- 支持断点续传

## 6. 扩展性设计

### 6.1 插件系统

```python
class ExtractorPlugin(ABC):
    @abstractmethod
    def extract(self, video_path: Path) -> Fingerprint:
        pass

class ComparatorPlugin(ABC):
    @abstractmethod
    def compare(self, fp1: Fingerprint, fp2: Fingerprint) -> float:
        pass
```

### 6.2 自定义策略

- 支持自定义保留策略 (最新、最大、最高清)
- 支持自定义处理动作 (删除、移动、标记)

## 7. 错误处理

| 错误类型 | 处理策略 |
|----------|----------|
| 文件读取失败 | 记录日志，跳过该文件 |
| 编解码错误 | 尝试备用解码器，记录警告 |
| 内存不足 | 降低并行度，启用分块处理 |
| 数据库错误 | 回滚事务，保存现场 |
