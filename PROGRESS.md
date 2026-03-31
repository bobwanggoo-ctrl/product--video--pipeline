# Product Video Pipeline - Progress Overview

## Pipeline Flow

```
INPUT: Product Selling Points (Text)
         │
         ▼
┌─────────────────────────┐
│ Step 2: Skill 1         │
│ Sellpoint → Storyboard  │──── Rules: storyboard_rules.md
│ (15 shots, 4-5 groups)  │     Output: JSON with prompts + motion_hint
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ Step 4: Skill 2         │
│ Storyboard → Frame      │──── Gemini Image Gen (dual channel)
│ (prompt → AI image)     │     Three-layer control: hard/soft/free
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ Step 3: Skill 3         │
│ Compliance Check        │──── Gemini Vision multimodal compare
│ (product fidelity)      │     Output: PASS / WARN / FAIL
└────────────┬────────────┘     FAIL → re-generate frame
             │
             ▼
┌─────────────────────────┐
│ Step 5: Skill 4         │
│ Frame → Video           │──── Kling AI image2video
│ (image + motion → clip) │     motion_planner maps shot_type → motion
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│ Step 7: Skill 5         │
│ Auto Editor             │──── VideoDB: video → text description
│ (analyze + edit + EDL)  │     LLM: editing decisions
└────────────┬────────────┘     ffmpeg: assemble .mp4 clips
             │                  Output: final video + EDL
             ▼
OUTPUT: Final Video (.mp4) + EDL Timeline
```

## Step-by-Step Progress

| Step | Name | Status | Description |
|------|------|--------|-------------|
| 1 | Project Skeleton | DONE | Directory structure, config, models, utils, pipeline orchestrator |
| 2 | Skill 1: Sellpoint → Storyboard | TODO | Migrate + optimize converter, split rules, add motion_hint |
| 3 | Skill 3: Compliance Checker | TODO | Gemini Vision multimodal comparison, PASS/WARN/FAIL |
| 4 | Skill 2: Storyboard → Frame | TODO | Image generation + three-layer prompt control |
| 5 | Skill 4: Frame → Video | TODO | Kling AI integration + motion planner |
| 6 | Pipeline Orchestrator | TODO | Wire up all skills, semi-auto mode, state management |
| 7 | Skill 5: Auto Editor | TODO | VideoDB analysis, LLM editing, ffmpeg assembly, EDL export |
| 8 | E2E Testing & Optimization | TODO | Full pipeline test, error handling, UX polish |

## Architecture

```
main.py
  └── pipeline/orchestrator.py (Mode B: semi-auto, step-by-step with user confirm)
        ├── skills/sellpoint_to_storyboard/  (Skill 1)
        ├── skills/storyboard_to_frame/      (Skill 2)
        ├── skills/compliance_checker/       (Skill 3)
        ├── skills/frame_to_video/           (Skill 4)
        └── skills/auto_editor/              (Skill 5)

Shared:
  ├── models/          (Pydantic data models)
  ├── utils/           (LLM client, ffmpeg wrapper, JSON repair)
  └── config/          (Environment variables)
```

## Key Technical Decisions

- **LLM**: Gemini (primary, dual-auth) + DeepSeek (fallback)
- **Image Gen**: Gemini Image Gen (dual channel)
- **Video Gen**: Kling AI (image2video)
- **Video Analysis**: VideoDB (video → text descriptions for LLM)
- **Video Processing**: ffmpeg (assembly, transitions, BGM mixing)
- **EDL Format**: CMX 3600
- **Data Validation**: Pydantic v2
- **Mode**: Semi-auto (Mode B) first, full-auto (Mode A) later
- **Kling Output**: Consistent parameters, no preprocessing needed
