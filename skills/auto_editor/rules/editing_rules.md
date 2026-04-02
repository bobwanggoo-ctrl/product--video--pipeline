# 自动剪辑规则

---

## SECTION A: 硬约束 (Hard Constraints - 必须严格遵守)

### A1. 总时长与结构
- 成片总时长：**20-30 秒**（含转场时间）。
- 三段式结构：
  - **开头 Hook**（2-3 秒）：最强视觉冲击的镜头 + 痛点字幕，必须在黄金三秒内抓住注意力。
  - **主体卖点展示**（15-22 秒）：按卖点优先级排列核心镜头。
  - **结尾 CTA**（3-5 秒）：产品全貌或套装展示，配合行动号召字幕。

### A2. 片段选择
- 从 15 个候选片段中选取 **8-12 个**用于成片（其余标记为 rejected）。
- **每个 shot_id 只能使用一次，绝对不得重复选用同一个片段。**
- 每个选中片段的变速后展示时长 **≥ 1.5 秒**。
- 标记为 `is_rejected=true` 的废片不得使用。
- 如果片段附带 quality_score（视觉质量评分），优先选用高分片段，低分片段应标记为 rejected 并注明理由。

### A3. 变速约束
- speed_factor 取值范围：**1.0 / 1.25 / 1.5 / 1.75 / 2.0**。
- **严禁低于 1.0x**：AI 生成视频帧率不足，慢放会抽帧卡顿。
- **变速后时长约束**：`trim后时长 / speed_factor ≥ 1.5 秒`。
  - 违反时必须降低 speed_factor 直到满足约束。
  - 例：2s 素材 × 2.0x = 1s（违规）→ 应使用 1.25x（= 1.6s）。

### A4. 转场规则
- 转场类型：`cut`（硬切）、`dissolve`（溶解）、`fade`（淡入淡出）。
- 比例约束：约 **80% cut / 15% dissolve / 5% fade**。
- 转场时长：dissolve 和 fade 为 0.3-0.5 秒，cut 为 0 秒。
- 结尾最后一个镜头的 transition_out 必须为 `fade`（淡出到黑场）。

### A5. 景别节奏
- **禁止连续 3 个相同景别**（如连续 3 个 Medium）。
- 全景（Wide）后建议接中景（Medium）或近景（Close）。
- 连续 2 个中景后必须接全景或特写/微距，打破视觉惯性。
- 特写/微距（Close/Macro）不宜连续出现超过 2 个。

### A6. 音频
- **全部剥离原声**：所有素材进入剪辑流程时必须无音频。
- 统一混入 BGM，BGM 时长必须 ≥ 成片总时长。
- BGM 最后 1.5 秒执行淡出。

### A7. 时长校验公式
对每个选中片段，LLM 必须计算并输出：
```
trimmed_duration = usable_end - usable_start
display_duration = trimmed_duration / speed_factor
```
所有 `display_duration` 之和 - 转场重叠时间 = 总时长，必须在 20-30 秒内。

### A8. 输出格式
必须严格按照指定的 JSON schema 输出，不要包含其它内容。不要输出 markdown 代码块标记。

---

## SECTION B: 软引导 (Soft Guidance - 建议遵循)

### B1. 黄金三秒策略
- 开头 Hook 必须选择**视觉冲击力最强**的镜头（通常是全景+人物大动作，或产品核心功能特写）。
- 第一条字幕必须直击痛点（如 "4-Pack Christmas Gnomes" 或 "UPF 50+ Sun Protection"）。
- 假设观众在静音环境下刷视频，仅凭画面+字幕就能在 3 秒内知道产品解决什么问题。

### B2. 速度变化策略（视觉爽感）
- 利用速度对比制造节奏感：
  - **常规展示**（人物互动、产品使用）→ 1.0x 原速。
  - **全景过渡、场景切换**→ 1.5x-2.0x 加速，压缩无效信息。
  - **核心卖点特写**→ 1.0x 原速保留细节。
- 不要让整个视频都是同一速度，速度变化本身就是一种视觉刺激。

### B3. 每 2-3 秒视觉变化
- 每 2-3 秒必须发生至少一次视觉变化，可以是：
  - 景别切换（Wide → Medium → Close）
  - 速度变化（1.0x → 1.5x）
  - 转场效果（dissolve）
  - 场景组切换（客厅 → 餐厅）
- 这是对抗观众"划走冲动"的核心手段。

### B4. 运镜方向连贯（低优先级参考）
- 相邻镜头的运镜方向尽量一致：
  - 上一镜向右平移 → 下一镜也向右或推进（不要突然反向）。
  - 上一镜推进 → 下一镜可以环绕或继续推进（不要突然拉远）。
- 运镜方向信息从 `motion_prompt` 字段获取。
- 此规则为参考项，**最终以整体叙事逻辑和卖点展示顺序为准**。

### B5. 叙事逻辑
- 优先按照 Storyboard 的 scene_group 顺序组织（保持场景连贯性）。
- 同一 scene_group 内的镜头尽量相邻放置。
- 但如果某个 scene_group 的镜头不适合放在开头 Hook，可以打乱顺序。

### B6. 字幕节奏
- 不是每个镜头都需要字幕，**只在传达关键卖点时出字幕**。
- 字幕出现频率：约每 3-5 秒一条。
- 字幕展示时长：1.5-3 秒（与对应片段的 display_duration 匹配）。

---

## SECTION C: 输出格式 (Output Schema)

你必须只输出一个合法的 JSON 对象，格式如下：

```json
{
  "structure": {
    "hook": "开头策略说明（选了哪个镜头、为什么）",
    "body": "主体安排说明",
    "closing": "结尾策略说明"
  },
  "bgm_choice": "BGM 文件名",
  "bgm_reason": "选择理由",
  "rejected_shots": [
    {"shot_id": 99, "reason": "拒绝理由"}
  ],
  "clips": [
    {
      "shot_id": 1,
      "trim_start": 0.0,
      "trim_end": 5.0,
      "speed_factor": 1.0,
      "display_duration": 2.5,
      "transition_in": "fade",
      "transition_out": "cut",
      "transition_duration": 0.4,
      "subtitle_text": "4-Pack Christmas Gnomes",
      "subtitle_text_cn": "4只装圣诞侏儒摆件",
      "subtitle_style": "title"
    }
  ],
  "total_duration": 25.0,
  "duration_breakdown": "各片段时长计算明细"
}
```

**关键要求：**
1. clips 数组中的顺序即为成片播放顺序。
2. 每个 clip 必须包含完整的 trim/speed/transition/subtitle 信息。
3. subtitle_text 为英文主体（面向海外用户），subtitle_text_cn 为中文回译。
4. subtitle_style 为 "title"（开头/结尾大字）或 "selling_point"（卖点小字）。
5. total_duration 必须在 20-30 秒内，且等于所有 display_duration 之和减去转场重叠。
6. duration_breakdown 用文字说明每个片段的时长计算过程，便于校验。
7. **不是每个 clip 都需要字幕**：至少 1-3 个 clip 的 subtitle_text 和 subtitle_text_cn 为空字符串 ""。纯视觉过渡镜头、快节奏剪辑段落不需要字幕，让画面自己说话。
8. **每个 shot_id 只能使用一次**，不得重复选用同一个片段。
