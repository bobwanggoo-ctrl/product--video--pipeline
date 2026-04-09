# Product Video Pipeline

从产品卖点文案自动生成 20-30 秒产品短视频的全流程管道。

## 技术栈

- **Python 3.13** + Pydantic v2
- **LLM**: AI导航 Gemini-3-flash (主) / tu-zi OpenAI 兼容 (备)
- **生图**: AI导航 IMAGE API
- **生视频**: Kling AI v2.5 (图生视频)
- **视频处理**: FFmpeg (拼接/转场/字幕/BGM)
- **侵权检测**: Google Cloud Vision API (Logo/Web/IP)
- **导出格式**: MP4 + SRT + 剪映 JSON + FCPXML v1.9

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

### 1. 环境准备

```bash
# Python 3.13+
python3 --version

# 安装依赖
pip install -r requirements.txt

# FFmpeg (macOS)
brew install ffmpeg

# 复制环境变量模板
cp .env.example .env
# 编辑 .env 填入 API Key
```

### 2. 配置 API Key

`.env` 中必须配置：

| Key | 用途 | 必需 |
|-----|------|------|
| `AI_NAV_TOKEN` | AI导航 (生图 + LLM + Vision) | 是 |
| `KLING_ACCESS_KEY` / `KLING_SECRET_KEY` | Kling AI 视频生成 | 是 |
| `REVERSE_PROMPT_API_KEY` | tu-zi LLM 备选 | 否 |
| `GOOGLE_VISION_API_KEY` | 侵权检测 | 否 (无则跳过) |

### 3. 准备输入

```
input/
├── Test_1/              # 输入目录
│   ├── sellpoint.txt    # 或 .docx，产品卖点文案
│   └── *.jpg/png        # 产品参考图 (1-6张)
├── music/               # BGM 库 (可选)
└── fonts/               # 字体目录 (可选)
```

### 4. 运行

```bash
# 半自动模式 (逐步确认)
python main.py

# 全自动模式 (无人值守)
python main.py --auto
```

### 5. 输出

```
output/{task_id}/
├── storyboard.json      # 分镜脚本
├── frames/              # 15 张帧图
├── videos/              # 8-11 个视频片段
├── final/
│   ├── final.mp4        # 成片 (字幕+BGM)
│   ├── subtitles_en.srt # 英文字幕
│   ├── subtitles_cn.srt # 中文字幕
│   ├── draft_content.json  # 剪映项目
│   └── project.fcpxml      # FCP 项目
├── trace/               # 调试追踪
└── checkpoint.json      # 断点恢复
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
└── input/                      # 输入素材
```

## Checkpoint 恢复

Pipeline 支持断点恢复。如果运行中断，再次启动 `python main.py` 会自动检测未完成任务并提示恢复。

## 关键技术决策

- **LLM 路由**: AI导航优先 → tu-zi 自动降级
- **合规检查**: Gemini Vision (质量/一致性/排版) + Google Vision API (侵权) 双层并行
- **视频分析**: FFmpeg + Storyboard，不依赖 VideoDB
- **变速**: 1.0-2.0x 加速 only (AI 视频帧率限制)
- **导出**: MP4 成品(字幕烧录+BGM) + 剪映/FCP (字幕/BGM 作独立轨道)
