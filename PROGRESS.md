# Product Video Pipeline - 项目进度总览

## 流水线流程 / Pipeline Flow

```
输入: 产品卖点文案 (文本)
         │
         ▼
┌─────────────────────────┐
│ 步骤2: 技能1             │
│ 卖点 → 分镜脚本          │──── 规则: storyboard_rules.md
│ (15个镜头, 4-5组场景)    │     输出: JSON (提示词 + 运动提示)
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ 步骤4: 技能2             │
│ 分镜 → 画面帧            │──── Gemini 图像生成 (双通道)
│ (提示词 → AI生图)        │     三层控制: 硬约束/软引导/自由发挥
└────────────┬────────────┘
             │
             ▼
┌───────────���─────────────┐
│ 步骤3: 技能3             │
│ 合规性检查               │──── Gemini Vision 多模态比对
│ (产品还原度校验)          │     输出: PASS / WARN / FAIL
└────────────┬────────────┘     FAIL → 重新生成画面帧
             │
             ▼
┌─────────────────────────┐
│ 步骤5: 技能4             │
│ 画面帧 → 视频片段         │──── Kling AI 图生视频
│ (图片 + 运动 → 视频)     │     运动规划器: 镜头类型 → 运动参数
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ 步骤7: 技能5             │
│ 自动剪辑                 │──── VideoDB: 视频 → ���本描述
│ (分析 + 剪辑 + EDL)      │     LLM: 剪辑决策
└────────────┬────────────┘     ffmpeg: 拼接 .mp4 片段
             │                  输出: 成片 + EDL 时间线
             ▼
输出: 最终视频 (.mp4) + EDL 时间线
```

## 各步骤进度 / Step-by-Step Progress

| 步骤 | 名称 | 状态 | 说明 |
|------|------|------|------|
| 1 | 项目骨架搭建 | ✅ 完成 | 目录结构、配置、数据模型、工具函数、流水线编排器 |
| 2 | 技能1: 卖点→分镜 | ✅ 完成 | 迁移优化转换器、拆分规则、添加运动提示、验证器、Type A/B 测试通过 |
| 3 | 技能3: 合规性检查 | ⬚ 待开发 | Gemini Vision 多模态比对，PASS/WARN/FAIL 判定 |
| 4 | 技能2: 分镜→画面帧 | ⬚ 待开发 | AI 图像生成 + 三层提示词控制 |
| 5 | 技能4: 画面帧→视频 | 🔧 运镜规划器完成 | 场景感知运镜选择 + 三层结构(镜头/主体/背景)，Kling API 调用待接入 |
| 6 | 流水线编排器 | ⬚ 待开发 | 串联所有技能、半自动模式、状态管理（需所有 Skill 跑通后提炼） |
| 7 | 技能5: 自动剪辑 | 🔨 开发中 | Module A(FFmpeg分析+LLM决策) + Module B(组装+导出)，详见下方 |
| 8 | 端到端测试与优化 | ⬚ 待开发 | 全流水线测试、错误处理、用户体验打磨 |

### 技能5 详细进度

| Phase | 内容 | 状态 |
|-------|------|------|
| 1 | 数据模型补充（ClipAnalysis/TimelineClip 加字段） | ✅ 完成 |
| 2 | editing_rules.md + subtitle_rules.md | ✅ 完成 |
| 3 | video_analyzer.py + bgm_scanner.py | ✅ 完成 |
| 4 | llm_editor.py（剪辑决策 + 时长校验） | ✅ 完成 |
| 5 | subtitle_gen.py（SRT 生成） | ✅ 完成 |
| 6 | ffmpeg_assembler.py（变速 + per-clip 转场 + 字幕烧录 + BGM） | ✅ 完成 |
| 7 | edl_exporter.py（剪映 JSON + FCPXML） | ✅ 完成 |
| 8 | 合并测试（端到端） | ⬚ 待测试 |

**关键决策：**
- 视频分析：FFmpeg + Storyboard（不用 VideoDB，降级方案：加 LLM 故事脚本 → VideoDB）
- 变速：1.0-2.0x 加速only，禁止慢放（AI 视频帧率不够）
- 变速后时长 ≥ 1.5s（防止切得太快）
- 输出：MP4 成品(字幕烧录+BGM) + 剪映 JSON + FCPXML（字幕/BGM 作独立轨道）

## 架构 / Architecture

```
main.py
  └── pipeline/orchestrator.py (Mode B: 半自动模式, 逐步确认)
        ├── skills/sellpoint_to_storyboard/  (技能1: 卖点→分镜)
        ├── skills/storyboard_to_frame/      (技能2: 分镜→画面帧)
        ├── skills/compliance_checker/       (技能3: 合规性检查)
        ├── skills/frame_to_video/           (技能4: 画面帧→视频)
        └── skills/auto_editor/              (技能5: 自动剪辑)

共享模块:
  ├── models/          (Pydantic 数据模型)
  ├── utils/           (LLM 客户端, ffmpeg 封装, JSON 修复)
  └── config/          (环境变量配置)
```

## 关键技术选型 / Key Technical Decisions

| 类别 | 选型 | 备注 |
|------|------|------|
| LLM | Gemini (主力, 双认证) + DeepSeek (备选) | |
| 图像生成 | Gemini Image Gen | 双通道 |
| 视频生成 | Kling AI | 图生视频 |
| 视频分析 | VideoDB | 视频→文本描述供 LLM 分析 |
| 视频处理 | ffmpeg | 拼接、转场、BGM 混音 |
| EDL 格式 | CMX 3600 | |
| 数据校验 | Pydantic v2 | |
| 运行模式 | 先做半自动 (Mode B)，再做全自动 (Mode A) | |
| Kling 输出 | 统一参数，无需预处理 | |
