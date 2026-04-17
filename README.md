# Product Video Pipeline

从产品卖点文案自动生成 20-30 秒产品短视频的全流程管道。

## 快速开始（给同事）

**三步上手**：

1. `git clone` 本仓库到本地（或直接下载 zip 解压）
2. 双击根目录的：
   - **macOS**: `开始使用.command`（首次若提示"无法打开"，请**右键 → 打开 → 打开**）
   - **Windows**: `开始使用.bat`（首次若 SmartScreen 弹蓝色警告，请点**更多信息 → 仍要运行**）
3. 首次会弹出 GUI 向导让你填 API Key，填完即自动进入应用

首次大约需要 5 分钟安装依赖，之后双击秒开。

### 准备输入

```
input/
├── 你的产品名/             # 自由命名
│   ├── sellpoint.txt      # 或 .docx，产品卖点文案
│   └── *.jpg/png          # 产品参考图 (1-6 张)
├── music/                  # BGM 库 (可选)
└── fonts/                  # 字体库 (可选，项目已内置思源黑体兜底)
```

### 常见问题

| 问题 | 解决 |
|---|---|
| Mac：双击提示"无法打开，来自身份不明的开发者" | 右键 → 打开 → 打开（首次一次性操作） |
| Mac：未装 Homebrew，bootstrap 卡住 | 按脚本提示复制指令到终端运行，装完重跑 |
| Windows：SmartScreen 蓝色警告 | 点"更多信息" → "仍要运行" |
| Windows：`winget` 命令不存在 | 老版本 Win10，按脚本打开的 FFmpeg 下载页手动装 |
| 安装到一半失败想重来 | 删掉根目录的 `.setup_done` 和 `.venv/` 再双击 |
| 需要重新改 Key | 编辑根目录 `.env` 文件，或删 `.setup_done` 再双击 |

### 仅开发者（手动 CLI 模式）

```bash
# 激活 venv 后
python main.py          # 半自动，逐步确认
python main.py --auto   # 全自动，无人值守
```

---

## 技术栈

- **Python 3.10+** + Pydantic v2
- **LLM**: AI 导航 Gemini-3-flash（主）/ tu-zi OpenAI 兼容（备）
- **生图**: AI 导航 IMAGE API
- **生视频**: Kling AI v2.5（图生视频）
- **视频处理**: FFmpeg（拼接/转场/字幕/BGM）
- **侵权检测**: Google Cloud Vision API（可选）
- **导出格式**: MP4 + SRT + 剪映 JSON + FCPXML v1.9
- **GUI**: PySide6（Qt6）
- **内置字体**: 思源黑体 CN Regular + Bold（`assets/fonts/source-han-sans/`，SIL OFL 协议）

## 流水线

```
卖点文案 + 参考图
    ↓
Skill 1  卖点 → 15 镜分镜 JSON           (~30s)
    ↓
Skill 2  分镜 → 15 张 AI 帧图            (~3-5min)
    ↓
Skill 3  合规检查 (质量+侵权, 双层并行)    (~1-2min)
    ↓
选材     规则筛选, 选 ~11 镜进入生成
    ↓
Skill 4  帧图 → 5s 视频 (Kling AI)       (~5-10min)
    ↓
Skill 5  自动剪辑 → 成片 + 字幕 + 导出     (~1-2min)
    ↓
输出: final.mp4 + SRT + 剪映JSON + FCPXML
      总耗时 ~10-20 分钟
```

## 快速开始

### Key 说明

`.env` 中需要配置（首次向导会引导你填）：

| Key | 用途 | 必需 |
|-----|------|------|
| `AI_NAV_TOKEN` | AI 导航（生图 + LLM） | 是 |
| `KLING_ACCESS_KEY` / `KLING_SECRET_KEY` | Kling AI 视频生成 | 是 |
| `GOOGLE_VISION_API_KEY` | 侵权检测 | 否（无则跳过此步） |
| `REVERSE_PROMPT_API_KEY` | tu-zi LLM 备选 | 否 |

### 输出

```
output/{task_id}/
├── {task_id}.mp4           # 成片（字幕+BGM）
├── {task_id}.fcpxml        # Final Cut Pro 项目
└── 附件/
    ├── storyboard.json     # 分镜脚本
    ├── frames/             # 15 张帧图
    ├── videos/             # 8-11 个视频片段
    ├── subtitles_en.srt
    ├── subtitles_cn.srt
    ├── draft_content.json  # 剪映项目
    └── checkpoint.json     # 断点恢复
```

## 目录结构

```
├── main.py                     # 入口 (--auto 全自动)
├── pipeline/
│   ├── orchestrator.py         # 编排器 (checkpoint/resume)
│   └── frame_selector.py      # 选材逻辑
├── skills/
│   ├── sellpoint_to_storyboard/  # Skill 1
│   ├── storyboard_to_frame/     # Skill 2
│   ├── compliance_checker/      # Skill 3 (Gemini Vision + Google Vision API)
│   ├── frame_to_video/          # Skill 4 (运镜规划 + Kling)
│   └── auto_editor/             # Skill 5 (剪辑+字幕+BGM+导出)
├── models/                     # Pydantic 数据模型
├── utils/                      # LLM/API 客户端, FFmpeg 封装
├── config/                     # 环境变量配置
├── tests/                      # E2E 测试 (6 文件)
└── input/                      # 输入素材（素材、音乐、字体、参考图）
assets/                         # 项目资产（纳入 git）
└── fcp_titles/                 # FCP .moti 模板（084 SDMAC 遮罩 + Social Media Titles）
```

## Checkpoint 恢复

Pipeline 支持断点恢复。如果运行中断，再次启动 `python main.py` 会自动检测未完成任务并提示恢复。

## 关键技术决策

- **LLM 路由**: tu-zi 主路（AI导航 GROUP_ID=13 备用，厂商不稳定时自动降级）
- **合规检查**: tu-zi Vision (质量/一致性/排版，双模型路由) + Google Vision API (侵权) 双层并行
- **FCP 字幕模板**: assets/fcp_titles/，084 SDMAC 黄色遮罩 (#100-117) + Social Media Titles (10个)；同事 git clone 后自动安装到本机 Motion Templates
- **视频分析**: FFmpeg + Storyboard，不依赖 VideoDB
- **变速**: 1.0-2.0x 加速 only (AI 视频帧率限制)
- **导出**: MP4 成品(字幕烧录+BGM) + 剪映/FCP (字幕/BGM 作独立轨道)
