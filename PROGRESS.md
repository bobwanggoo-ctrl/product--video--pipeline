# Product Video Pipeline - 项目进度总览

## 流水线流程 / Pipeline Flow

```
输入: 产品卖点文案 (文本) / Amazon 商品链接 + 产品原图 (规划中)
         │
         ▼
┌─────────────────────────┐
│ 步骤2: 技能1             │
│ 卖点 → 分镜脚本          │──── 规则: storyboard_rules.md 三层控制: 硬约束/软引导/自由发挥
│ (15个镜头, 4-5组场景)    │     输出: JSON (提示词 + 运动提示)
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ 步骤4: 技能2             │
│ 分镜 → 画面帧            │──── AI导航 图像生成 (GROUP_ID=3)
│ (提示词 → AI生图)        │     
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ 步骤3: 技能3             │
│ 合规性检查               │──── AI导航 Gemini-3-flash Vision (GROUP_ID=13)
│ (产品还原度校验)          │     输出: PASS / WARN / FAIL
└────────────┬────────────┘     FAIL → 重新生成画面帧
             │
             ▼
┌─────────────────────────┐
│ 步骤5: 技能4             │
│ 画面帧 → 视频片段         │──── Kling AI kling-v2-5 图生视频
│ (图片 + 运动 → 视频)     │     运动规划器: 镜头类型 → 运动参数
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ 步骤7: 技能5             │
│ 自动剪辑                 │──── FFmpeg + Storyboard 分析
│ (分析 + 剪辑 + 导出)     │     LLM: AI导航优先 / tu-zi备选
└────────────┬────────────┘     ffmpeg: 拼接 + 转场 + BGM
             │                  输出: 成片 + 剪映JSON + FCPXML v1.9
             ▼
输出: 最终视频 (.mp4) + 剪映 JSON + FCPXML v1.9
```

## 各步骤进度 / Step-by-Step Progress

| 步骤 | 名称 | 状态 | 说明 |
|------|------|------|------|
| 1 | 项目骨架搭建 | ✅ 完成 | 目录结构、配置、数据模型、工具函数、流水线编排器 |
| 2 | 技能1: 卖点→分镜 | ✅ 完成 | 迁移优化转换器、拆分规则、添加运动提示、验证器、Type A/B 测试通过 |
| 3 | 技能3: 合规性检查 | ✅ 完成 | Gemini Vision 全品类合规检查 + 排版建议 + Error_Keywords 闭环 |
| 4 | 技能2: 分镜→画面帧 | ⬚ 待开发 | AI导航 图像生成 (GROUP_ID=3) + 三层提示词控制 |
| 5 | 技能4: 画面帧→视频 | 🔧 运镜规划器完成 | 场景感知运镜选择 + 三层结构，Kling API 客户端已完成，待接入编排器 |
| 6 | 流水线编排器 | ✅ 完成 | 串联所有技能、半自动模式、选材逻辑、Skill 3 集成、checkpoint 恢复 |
| 7 | 技能5: 自动剪辑 | ✅ 完成 | Module A(分析+决策) + Module B(组装+导出) + FCPXML 转场/字幕/BGM + 字体扫描 + 排版建议集成 |
| 8 | API 配置与统一 | ✅ 完成 | 清理废弃 API，统一 LLM 路由 (AI导航优先 + tu-zi备选)，Kling 客户端 |
| 9 | 端到端测试与优化 | ⬚ 待开发 | 全流水线测试、错误处理、用户体验打磨 |

### 技能5 详细进度

| Phase | 内容 | 状态 |
|-------|------|------|
| 1 | 数据模型补充（ClipAnalysis/TimelineClip 加字段） | ✅ 完成 |
| 2 | editing_rules.md + subtitle_rules.md | ✅ 完成 |
| 3 | video_analyzer.py + bgm_scanner.py | ✅ 完成 |
| 4 | llm_editor.py（剪辑决策 + 时长校验） | ✅ 完成 |
| 5 | subtitle_gen.py（SRT 生成） | ✅ 完成 |
| 6 | ffmpeg_assembler.py（变速 + per-clip 转场 + 字幕烧录 + BGM） | ✅ 完成 |
| 7 | edl_exporter.py（剪映 JSON + FCPXML v1.11） | ✅ 完成 |
| 8 | E2E 修复（FCPXML 转场/字幕/BGM + MP4 时长 + 字体扫描） | ✅ 完成 |
| 9 | 排版建议集成（Skill 3 LayoutHint → LLM 剪辑上下文） | ✅ 完成 |

### 技能3 详细进度

| Phase | 内容 | 状态 |
|-------|------|------|
| 1 | 数据模型（LayoutHint + error_keywords 扩展 ComplianceResult） | ✅ 完成 |
| 2 | Prompt 模板（全品类通用，基于合规性检查.docx + 排版维度） | ✅ 完成 |
| 3 | checker.py 核心逻辑（参考图压缩/缓存、Vision 调用、并发批量） | ✅ 完成 |
| 4 | orchestrator 集成（实调、传参、结果展示） | ✅ 完成 |
| 5 | Skill 5 排版建议传递链路 | ✅ 完成 |
| 6 | Error_Keywords → Skill 2 negative prompt 闭环 | ⬚ 待接入（Skill 2 消费端） |

### API 配置状态

| API | 用途 | 状态 | 备注 |
|-----|------|------|------|
| AI导航 GROUP_ID=3 | 图像生成 (技能2) | ✅ 已配置 | 异步任务模式 |
| AI导航 GROUP_ID=13 | Gemini-3-flash LLM + Vision | ✅ 已配置 | 技能1/3/5 共用 |
| tu-zi (Reverse Prompt) | LLM 备选路由 | ✅ 已配置 | OpenAI 兼容接口 |
| Kling AI | kling-v2-5 图生视频 (技能4) | ✅ 已配置 | JWT 认证，std模式，5s，16:9 |

**关键决策：**
- LLM 路由：AI导航 (Gemini-3-flash) 优先 → tu-zi (Reverse Prompt) 自动降级备选
- 视频分析：FFmpeg + Storyboard（不用 VideoDB，降级方案：加 LLM 故事脚本 → VideoDB）
- Vision 质量检测：功能已完成，但 tu-zi 响应慢，默认关闭，换快 API 后可启用
- 变速：1.0-2.0x 加速only，禁止慢放（AI 视频帧率不够）
- 变速后时长 ≥ 1.5s（防止切得太快）
- 输出：MP4 成品(字幕烧录+BGM) + 剪映 JSON + FCPXML v1.11（字幕/BGM 作独立轨道）
- 合规检查：Gemini Vision 全品类通用（产品一致性+AI质量+侵权），Error_Keywords 可回传生图

## 下一步 / Next Steps

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P0 | 全流程 E2E 联调 | 用真实输入跑完 Skill 1→2→3→4→5 全链路，验证各步骤串联 |
| P1 | Error_Keywords 闭环 | Skill 3 的 FAIL/WARN 关键词回传 Skill 2 作为 negative prompt 重新生图 |
| P1 | 侵权检测增强 | 当前 LLM 初筛，后续可接 Google Cloud Vision Logo Detection |
| P2 | FCP Title 模板集成 | input/fcp_titles/ 已有 3 套模板包，可丰富 FCPXML 字幕样式 |
| P2 | Amazon 链接输入 | 自动抓取商品信息 + 图片作为输入源 |
| P3 | 全自动模式 (Mode A) | 当前半自动逐步确认，后续支持一键全自动 |

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
  ├── utils/           (LLM 客户端, AI导航客户端, Kling 客户端, ffmpeg 封装, JSON 修复)
  └── config/          (环境变量配置)
```

## 关键技术选型 / Key Technical Decisions

| 类别 | 选型 | 备注 |
|------|------|------|
| LLM | AI导航 Gemini-3-flash (主力) + tu-zi (备选) | 异步任务模式，自动降级 |
| 图像生成 | AI导航 (GROUP_ID=3) | 异步任务模式 |
| 视频生成 | Kling AI kling-v2-5 | JWT 认证，std模式，5s，16:9 |
| 视频分析 | FFmpeg + Storyboard | 不用 VideoDB |
| 视频处理 | ffmpeg | 拼接、转场、BGM 混音 |
| 导出格式 | 剪映 JSON + FCPXML v1.9 | 字幕/BGM 独立轨道 |
| 数据校验 | Pydantic v2 | |
| 运行模式 | 先做半自动 (Mode B)，再做全自动 (Mode A) | |
| 输入源 | 文本卖点 (已有) / Amazon 链接抓取 (规划中) | defuddle.md 工具 |
