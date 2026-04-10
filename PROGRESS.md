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
| 3 | 技能3: 合规性检查 | ✅ 完成 | Gemini Vision 质量审核 + Google Vision API 侵权检测（双层并行） |
| 4 | 技能2: 分镜→画面帧 | ✅ 完成 | AI导航 图像生成 (GROUP_ID=3) + 批量提交/轮询 + 参考图上传 |
| 5 | 技能4: 画面帧→视频 | ✅ 完成 | 场景感知运镜选择 + 三层结构 + Kling API 客户端 + 编排器集成 + 分批/补拍 |
| 6 | 流水线编排器 | ✅ 完成 | 串联所有技能、半自动模式、选材逻辑、Skill 3 集成、checkpoint 恢复 |
| 7 | 技能5: 自动剪辑 | ✅ 完成 | Module A(分析+决策) + Module B(组装+导出) + FCPXML 转场/字幕/BGM + 字体扫描 + 排版建议集成 |
| 8 | API 配置与统一 | ✅ 完成 | 清理废弃 API，统一 LLM 路由 (AI导航优先 + tu-zi备选)，Kling 客户端 |
| 9 | 端到端测试 | ✅ 完成 | 6 个测试文件 (1538 行)，全流水线 + checkpoint 恢复 + Skill 5 专项 + Trace 系统 |
| 10 | Claude Code Skills | ✅ 完成 | 6 个 Skill（Skill 1-5 + 全流程编排）+ symlink 规则同步 |
| 11 | 字幕定位统一 | ✅ 完成 | MP4 drawtext + FCPXML Title 统一接入 layout_hints (subtitle_position) |

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
| 6 | Error_Keywords → Skill 2 negative prompt 闭环 | ✅ 完成 | generator.py 接收 error_keywords 拼接到 prompt |

### API 配置状态

| API | 用途 | 状态 | 备注 |
|-----|------|------|------|
| AI导航 GROUP_ID=3 | 图像生成 (技能2) | ✅ 已配置 | 异步任务模式 |
| AI导航 GROUP_ID=13 | Gemini-3-flash LLM + Vision | ⚠️ 不稳定 | 模型厂商问题，Vision 任务不 dispatch；LLM 降级到 tu-zi |
| tu-zi (Reverse Prompt) | LLM 主路 + Vision 主路 | ✅ 已配置 | OpenAI 兼容；Vision 双路由：gemini-3-flash-preview → gemini-2.5-flash-lite |
| Kling AI | kling-v2-5 图生视频 (技能4) | ✅ 已配置 | JWT 认证，std模式，5s，16:9 |
| Google Vision API | 侵权检测 (技能3) | ✅ 已配置 | Logo+Web反向搜图+IP标签，可选 |

**关键决策：**
- LLM 路由：AI导航 (Gemini-3-flash) 优先 → tu-zi (Reverse Prompt) 自动降级备选
- 视频分析：FFmpeg + Storyboard（不用 VideoDB，降级方案：加 LLM 故事脚本 → VideoDB）
- Vision 质量检测：功能已完成，但 tu-zi 响应慢，默认关闭，换快 API 后可启用
- 变速：1.0-2.0x 加速only，禁止慢放（AI 视频帧率不够）
- 变速后时长 ≥ 1.5s（防止切得太快）
- 输出：MP4 成品(字幕烧录+BGM) + 剪映 JSON + FCPXML v1.11（字幕/BGM 作独立轨道）
- 合规检查：双层并行 — Gemini Vision 质量审核（产品一致性+融合度+逻辑+AI质量+排版） + Google Vision API 侵权检测（Logo+Web+IP），Error_Keywords 回传 Skill 2 negative prompt

## 下一步 / Next Steps

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P1 | 全自动模式 UI 暴露 | ✅ main.py --auto 参数已支持 |
| P1 | Error_Keywords 闭环 | ✅ Skill 3 → Skill 2 negative prompt 已完成 |
| P1 | FCP Title 模板集成 | ✅ assets/fcp_titles/（084 SDMAC #100-117 + Social Media Titles 10个），字幕黄色遮罩 + 场景动态模板 |
| P2 | Amazon 链接输入 | 自动抓取商品信息 + 图片作为输入源 |
| P2 | 合规检查并发优化 | 当前 MAX_WORKERS=1（顺序跑），待 LLM 稳定后改为 3（约10分钟→3分钟） |
| P3 | 侵权检测增强 | ✅ Google Cloud Vision API 已集成（Logo + Web反向搜图 + IP标签） |

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
