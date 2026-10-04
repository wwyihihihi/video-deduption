# 本次修改（2026-10-02）

- 统一三个 CLI 入口，接入完整 SHA-256 和版本化有序指纹。
- 修复全帧组合平均漏判、十六进制字符距离、固定哈希截断及乱序场景采样。
- 对内容匹配启用时长容差、DTW 路径归一化和双向帧覆盖率。
- 精确重复与内容重复分别说明，采样异常独立报告待确认。
- 阻止链式合并和分析后变化文件操作，链接创建失败保留源文件。
- 报告增加文件状态、诊断和执行计数；零重复也可输出报告。
- 数据库存储增量扩展，不启用 CLI 缓存或 LSH。

# 变更日志

## [0.4.0] - 2026-03-27

### Added

#### Reporter 模块 ✅
- **TextReporter**: 文本报告生成器
  - 完整的重复组信息展示
  - 文件大小、分辨率、时长格式化
  - 保留/删除标记
- **HTMLReporter**: HTML 报告生成器
  - 美观的渐变色 CSS 样式
  - 响应式设计
  - 分组卡片展示
  - 汇总统计区域
- **JSONReporter**: JSON 报告生成器
  - 完整结构化数据输出
- **ConsoleReporter**: 控制台报告生成器
  - 使用 rich 库美化输出
  - 表格形式展示
  - 彩色高亮

#### Handler 模块 ✅
- **HandlerResult**: 处理结果统计
- **ReportHandler**: 仅报告模式
- **MoveHandler**: 移动重复文件
  - 自动处理文件名冲突
  - 创建目标目录
- **DeleteHandler**: 删除重复文件
  - 可选备份功能
- **LinkHandler**: 创建软链接
- **get_handler**: 工厂函数

#### CLI 接口 ✅
- **scan**: 完整扫描流程
  - 进度条显示
  - 多格式报告输出
- **dedupe**: 去重操作
  - 支持 report/move/delete/link 操作
  - --dry-run 模拟模式
- **report**: 报告生成
- **config**: 配置显示
- **extract**: 单视频特征提取
- **--verbose/--debug**: 日志控制

---

## [0.3.0] - 2026-03-27

### Added

#### Scanner 模块 ✅
- 目录递归扫描 (`VideoScanner.scan()`)
- 视频格式识别 (mp4/avi/mkv/mov/wmv/flv/webm/m4v/mpeg/mpg)
- OpenCV 元数据提取 (时长、分辨率、帧率)
- 文件大小过滤
- 排除目录模式
- MD5 校验和计算

#### Extractor 模块 ✅
- **FrameExtractor**: 关键帧提取
  - 均匀采样法
  - 场景变化检测法
- **HashCalculator**: 哈希计算
  - pHash (感知哈希)
  - dHash (差异哈希)
  - aHash (平均哈希)
- **VideoFingerprintExtractor**: 视频指纹提取

#### Comparator 模块 ✅
- **HashComparator**: 哈希比较器
  - 汉明距离计算
  - 多种比对策略
- **DTWComparator**: DTW 比较器
- **SimilarityMatrix**: 相似度矩阵
  - 对称矩阵优化

#### Deduplicator 模块 ✅
- **UnionFind**: 并查集
- **DuplicateGrouper**: 重复分组
- **RepresentativeSelector**: 代表文件选择
- **DeduplicationEngine**: 去重引擎

### Design Decisions
- Python 3.10+
- OpenCV 视频处理
- SQLite 元数据存储
- 分层架构: CLI → Application → Storage

---

## 版本规划

### [0.5.0] - 下一步
- [ ] 单元测试完善
- [ ] 性能优化 (并行处理)
- [ ] 增量扫描支持

### [1.0.0] - 待开发
- [ ] 完整功能测试
- [ ] 文档完善
- [ ] 打包发布
