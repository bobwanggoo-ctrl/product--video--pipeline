"""全链路 Trace 记录器。

记录 Pipeline 每个步骤的输入/输出/提示词/LLM回复，
生成结构化 trace 目录和可读的 trace_report.md。
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class TraceLogger:
    """全链路 trace 记录器。"""

    def __init__(self, trace_dir: str):
        self.trace_dir = Path(trace_dir)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._timers: dict[str, float] = {}
        self._elapsed: dict[str, float] = {}
        self._step_meta: dict[str, dict] = {}
        self._start_time = time.time()

    def step_dir(self, step_name: str) -> Path:
        """获取/创建步骤 trace 目录。"""
        d = self.trace_dir / step_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_text(self, step_name: str, filename: str, content: str):
        """保存文本文件。"""
        d = self.step_dir(step_name)
        target = d / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def save_json(self, step_name: str, filename: str, data):
        """保存 JSON 文件。"""
        d = self.step_dir(step_name)
        target = d / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        # 处理 Pydantic 模型
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        target.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def start_timer(self, step_name: str):
        """开始计时。"""
        self._timers[step_name] = time.time()

    def stop_timer(self, step_name: str) -> float:
        """停止计时，返回耗时秒数。"""
        start = self._timers.pop(step_name, time.time())
        elapsed = time.time() - start
        self._elapsed[step_name] = elapsed
        return elapsed

    def set_meta(self, step_name: str, meta: dict):
        """设置步骤的元数据。"""
        self._step_meta[step_name] = meta

    def save_step_trace(self, step_name: str, trace_data: dict):
        """保存步骤的完整 trace 数据（从 _trace 字段提取）。"""
        if not trace_data:
            return

        d = self.step_dir(step_name)

        # 保存文本字段
        text_fields = [
            "system_prompt", "user_prompt", "llm_response",
            "prompt_template",
        ]
        for field in text_fields:
            if field in trace_data and trace_data[field]:
                (d / f"{field}.txt").write_text(
                    str(trace_data[field]), encoding="utf-8"
                )

        # 保存 per-shot 数据
        if "per_shot" in trace_data:
            results_dir = d / "results"
            results_dir.mkdir(exist_ok=True)
            for shot_id, shot_data in trace_data["per_shot"].items():
                (results_dir / f"shot_{int(shot_id):02d}.json").write_text(
                    json.dumps(shot_data, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )

        # 保存 per-shot prompts
        if "per_shot_prompts" in trace_data:
            prompts_dir = d / "prompts"
            prompts_dir.mkdir(exist_ok=True)
            for shot_id, prompt in trace_data["per_shot_prompts"].items():
                (prompts_dir / f"shot_{int(shot_id):02d}.txt").write_text(
                    str(prompt), encoding="utf-8"
                )

        # 保存其他 JSON 数据
        json_fields = ["storyboard", "timeline", "selection_plan", "meta"]
        for field in json_fields:
            if field in trace_data and trace_data[field]:
                data = trace_data[field]
                if hasattr(data, "model_dump"):
                    data = data.model_dump()
                (d / f"{field}.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )

    def generate_report(self, run_id: str = "") -> str:
        """生成 trace_report.md 并保存。"""
        total_elapsed = time.time() - self._start_time
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"# E2E Trace Report — {run_id}",
            f"生成时间: {now}",
            f"总耗时: {total_elapsed:.0f} 秒 ({total_elapsed / 60:.1f} 分钟)",
            "",
        ]

        # 逐步骤生成报告
        step_configs = [
            ("step1_storyboard", "Step 1: 卖点→分镜 (Skill 1)"),
            ("step2_frames", "Step 2: 分镜→画面帧 (Skill 2)"),
            ("step3_compliance", "Step 3: 合规检查 (Skill 3)"),
            ("step4_selection", "Step 4: 选材"),
            ("step5_videos", "Step 5: 画面帧→视频 (Skill 4)"),
            ("step6_edit", "Step 6: 自动剪辑 (Skill 5)"),
        ]

        for step_name, title in step_configs:
            elapsed = self._elapsed.get(step_name, 0)
            meta = self._step_meta.get(step_name, {})
            step_path = self.trace_dir / step_name

            lines.append(f"## {title}")
            lines.append(f"- 耗时: {elapsed:.1f}s")

            if meta:
                for k, v in meta.items():
                    lines.append(f"- {k}: {v}")

            # 检查文件链接
            if (step_path / "system_prompt.txt").exists():
                lines.append(f"- [系统提示词]({step_name}/system_prompt.txt)")
            if (step_path / "user_prompt.txt").exists():
                lines.append(f"- [用户提示词]({step_name}/user_prompt.txt)")
            if (step_path / "llm_response.txt").exists():
                lines.append(f"- [LLM回复]({step_name}/llm_response.txt)")

            # per-shot 结果表格
            results_dir = step_path / "results"
            if results_dir.exists():
                shot_files = sorted(results_dir.glob("shot_*.json"))
                if shot_files:
                    lines.append("")
                    # 根据步骤类型生成不同表头
                    if "compliance" in step_name:
                        lines.append("| shot_id | 状态 | 摘要 | 关键词 |")
                        lines.append("|---------|------|------|--------|")
                        for sf in shot_files:
                            try:
                                sd = json.loads(sf.read_text(encoding="utf-8"))
                                sid = sf.stem.replace("shot_", "")
                                status = sd.get("Final_Status", sd.get("level", "?"))
                                summary = sd.get("Summary", sd.get("summary", ""))[:50]
                                kw = ", ".join(sd.get("Error_Keywords", sd.get("error_keywords", [])))
                                lines.append(f"| {sid} | {status} | {summary} | {kw} |")
                            except Exception:
                                pass
                    elif "videos" in step_name:
                        lines.append("| shot_id | motion_prompt | 结果 |")
                        lines.append("|---------|--------------|------|")
                        for sf in shot_files:
                            try:
                                sd = json.loads(sf.read_text(encoding="utf-8"))
                                sid = sf.stem.replace("shot_", "")
                                motion = sd.get("motion_prompt", "")[:40]
                                status = "✓" if sd.get("success") else "✗"
                                lines.append(f"| {sid} | {motion} | {status} |")
                            except Exception:
                                pass

            # per-shot prompts 表格
            prompts_dir = step_path / "prompts"
            if prompts_dir.exists() and "frames" in step_name:
                shot_files = sorted(prompts_dir.glob("shot_*.txt"))
                if shot_files:
                    lines.append("")
                    lines.append("| shot_id | 提示词(前60字) | 结果 |")
                    lines.append("|---------|---------------|------|")
                    for sf in shot_files:
                        sid = sf.stem.replace("shot_", "")
                        prompt = sf.read_text(encoding="utf-8")[:60].replace("\n", " ")
                        # 检查对应帧是否存在
                        frame_meta = meta.get("frame_paths", {})
                        result = "✓" if frame_meta.get(int(sid) if sid.isdigit() else sid) else "?"
                        lines.append(f"| {sid} | {prompt}... | {result} |")

            lines.append("")

        report = "\n".join(lines)
        report_path = self.trace_dir / "trace_report.md"
        report_path.write_text(report, encoding="utf-8")
        logger.info(f"[Trace] 报告已生成: {report_path}")
        return str(report_path)
