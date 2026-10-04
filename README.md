# Video Deduplication - 视频去重工具

一个高效的命令行视频去重工具，通过感知哈希和视频指纹技术，智能识别并处理重复或高度相似的视频文件。

## 功能特性

- **智能扫描** - 递归扫描目录，支持 mp4/avi/mkv/mov/wmv/flv/webm/m4v/mpeg/mpg 等格式
- **特征提取** - 完整 SHA-256 精确匹配，使用有序采样和 pHash/dHash/aHash 内容特征
- **精准比对** - 按实际位数归一化的 DTW 和双向覆盖率校验，识别转码及缩放副本
- **智能选择** - 多维度评分自动选择最佳文件保留
- **多格式报告** - 支持 Text/HTML/JSON/Console 格式输出
- **安全操作** - 模拟运行模式，支持移动/删除/软链接操作

## 安装

```bash
# 克隆仓库
git clone https://github.com/wwyihihihi/video-deduption.git
cd video-deduption

# 安装依赖
pip install -e .

# 或使用 pip
pip install video-dedup
```

## 快速开始

```bash
# 扫描目录并检测重复视频
video-dedup scan /path/to/videos

# 生成 HTML 报告
video-dedup scan /path/to/videos --output report.html --format html

# 模拟去重 (不实际执行)
video-dedup dedupe /path/to/videos --dry-run

# 移动重复文件到指定目录
video-dedup dedupe /path/to/videos --action move --target ./duplicates/ --no-dry-run

# 删除重复文件
video-dedup dedupe /path/to/videos --action delete --no-dry-run

# 显示当前配置
video-dedup config
```

## 命令说明

### scan - 扫描检测

```bash
video-dedup scan <path> [options]

选项:
  --recursive/--no-recursive  递归扫描子目录 (默认: 递归)
  --threshold FLOAT           相似度阈值 (默认: 0.85)
  --output PATH               报告输出路径
  --format FORMAT             报告格式: text/html/json/console
  -v, --verbose               详细输出
```

### dedupe - 执行去重

```bash
video-dedup dedupe <path> [options]

选项:
  --action ACTION             操作类型: report/move/delete/link
  --target PATH               移动目标目录 (move 操作)
  --dry-run/--no-dry-run      默认模拟运行；后者实际执行
  --threshold FLOAT           相似度阈值
  -v, --verbose               详细输出
```

## 项目结构

```
video-deduplication/
├── docs/
│   ├── ARCHITECTURE.md      # 架构设计文档
│   ├── ALGORITHM.md         # 算法详细说明
│   ├── CHANGELOG.md         # 变更日志
│   └── TASKS.md             # 开发任务清单
├── src/video_dedup/
│   ├── scanner/             # 视频扫描模块
│   ├── extractor/           # 特征提取模块
│   ├── comparator/          # 相似度比对模块
│   ├── deduplicator/        # 去重逻辑模块
│   ├── reporter/            # 报告生成模块
│   ├── handler/             # 文件处理模块
│   ├── models.py            # 数据模型
│   ├── config.py            # 配置管理
│   ├── database.py          # 数据库管理
│   └── cli.py               # 命令行接口
├── tests/                   # 测试代码
├── pyproject.toml           # 项目配置
└── README.md
```

## 算法原理

### 感知哈希

| 算法 | 原理 | 特点 |
|------|------|------|
| pHash | DCT 变换 | 鲁棒性强，抗压缩 |
| dHash | 相邻像素差异 | 速度快 |
| aHash | 像素平均值 | 最简单 |

### 检测与执行结果

完整文件副本先按大小和 SHA-256 识别；正常视频再按时长和有序画面比较。
默认采样覆盖首尾，序列评分采用 DTW，内容匹配还要求两侧足够多的帧达到阈值。
损坏或定位异常的文件单独报告待确认，不自动处理。
音轨、裁剪和局部片段重复不在当前内容比较范围内。
详见 [ALGORITHM.md](docs/ALGORITHM.md)，默认配置以 `src/video_dedup/config.py` 为准。

`scan`、`report` 只读。`dedupe` 默认 `--action report` 且开启模拟运行。
实际移动、删除或创建链接必须指定相应 `--action` 并提供 `--no-dry-run`。
摘要一致和画面相似会使用不同的匹配类型；高画面相似度不会被描述为文件完全相同。
报告包含扫描、完成判定、待确认、失败以及操作结果，零重复也能输出报告。
读取或操作失败返回退出码 1；待确认项通过报告列出。

## 配置

`video-dedup config` 显示默认配置。当前配置文件加载、缓存读取及 LSH 加速尚未实现，
传入 `--config` 会明确报错，不再静默忽略；阈值通过 `--threshold` 设置。

## 开发进度

当前版本: **v0.4.0**

查看 [CHANGELOG.md](docs/CHANGELOG.md) 了解详细开发进展。

## 许可证

MIT License
