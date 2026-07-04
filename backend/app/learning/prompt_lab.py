"""Prompt A/B 实验室 - Prompt 版本的 A/B 测试。

用两个不同 prompt 分别调用 LLM，再用 judge prompt 让 LLM 评判哪个更好，
结果持久化到 AgentRun.result JSON 字段（agent_name="prompt_lab"）。
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.session import AgentRun
from app.pipeline.llm_client import LLMClient, get_llm_client

logger = logging.getLogger("app.learning.prompt_lab")


class PromptLab:
    """Prompt 版本 A/B 测试实验室。

    使用方式::

        lab = PromptLab(db, project_id)
        result = await lab.run_experiment(
            prompt_a="用华丽的辞藻描写...",
            prompt_b="用简洁有力的语言描写...",
            test_input="描写一个日落场景",
        )
        print(result["winner"])  # "A" / "B" / "tie"
    """

    def __init__(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        llm_client: Optional[LLMClient] = None,
    ):
        self.db = db
        self.project_id = project_id
        self.llm = llm_client or get_llm_client()

    # ------------------------------------------------------------------
    # 运行 A/B 实验
    # ------------------------------------------------------------------
    async def run_experiment(
        self,
        project_id: Optional[uuid.UUID],
        prompt_a: str,
        prompt_b: str,
        test_input: str,
        judge_prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        """运行 A/B 实验。

        Args:
            project_id: 项目 ID
            prompt_a: Prompt 版本 A
            prompt_b: Prompt 版本 B
            test_input: 测试输入（作为 user 消息）
            judge_prompt: 自定义评判 prompt（可选，使用默认评判逻辑）

        Returns:
            实验结果::

                {
                    "experiment_id": str,
                    "winner": "A" | "B" | "tie",
                    "scores": {"a": float, "b": float},
                    "response_a": str,
                    "response_b": str,
                    "judge_reasoning": str,
                    "input_tokens": int,
                    "output_tokens": int,
                    "cost": float,
                }
        """
        pid = project_id or self.project_id
        experiment_id = str(uuid.uuid4())

        # LLM 未配置时返回占位结果
        if not self.llm.is_configured:
            logger.warning("LLM 未配置，PromptLab 返回占位实验结果")
            return self._placeholder_result(
                experiment_id, prompt_a, prompt_b, test_input
            )

        # 1. 用 prompt_a 调用 LLM
        t0 = time.monotonic()
        resp_a = await self.llm.chat([
            {"role": "system", "content": prompt_a},
            {"role": "user", "content": test_input},
        ])

        # 2. 用 prompt_b 调用 LLM
        resp_b = await self.llm.chat([
            {"role": "system", "content": prompt_b},
            {"role": "user", "content": test_input},
        ])

        # 3. 评判
        scores, judge_reasoning = await self._judge(
            test_input, resp_a.content, resp_b.content, judge_prompt
        )

        # 确定获胜者
        score_a = scores.get("a", 0.0)
        score_b = scores.get("b", 0.0)
        if abs(score_a - score_b) < 0.5:
            winner = "tie"
        elif score_a > score_b:
            winner = "A"
        else:
            winner = "B"

        total_input = resp_a.input_tokens + resp_b.input_tokens
        total_output = resp_a.output_tokens + resp_b.output_tokens
        total_cost = resp_a.cost + resp_b.cost

        result = {
            "experiment_id": experiment_id,
            "winner": winner,
            "scores": {"a": score_a, "b": score_b},
            "response_a": resp_a.content,
            "response_b": resp_b.content,
            "judge_reasoning": judge_reasoning,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cost": total_cost,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }

        # 4. 持久化到 AgentRun
        await self._save_experiment(
            pid, experiment_id, prompt_a, prompt_b, test_input, result
        )

        logger.info(
            "PromptLab 实验 %s 完成: winner=%s scores=%.1f/%.1f",
            experiment_id, winner, score_a, score_b,
        )
        return result

    # ------------------------------------------------------------------
    # 列出实验记录
    # ------------------------------------------------------------------
    async def list_experiments(
        self,
        project_id: Optional[uuid.UUID] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """列出实验记录（从 AgentRun 中查询 agent_name="prompt_lab"）。"""
        pid = project_id or self.project_id
        stmt = (
            select(AgentRun)
            .where(
                AgentRun.project_id == pid,
                AgentRun.agent_name == "prompt_lab",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        runs = result.scalars().all()

        experiments = []
        for run in runs:
            r = run.result or {}
            experiments.append({
                "experiment_id": r.get("experiment_id", str(run.id)),
                "winner": r.get("winner", "unknown"),
                "scores": r.get("scores", {}),
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "cost": run.cost,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            })
        return experiments

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    async def _judge(
        self,
        test_input: str,
        response_a: str,
        response_b: str,
        custom_judge_prompt: Optional[str] = None,
    ) -> tuple[dict[str, float], str]:
        """用 LLM 评判两个响应的优劣。

        Returns:
            (scores, reasoning) - scores: {"a": float, "b": float}, reasoning: 评判理由
        """
        if custom_judge_prompt:
            system = custom_judge_prompt
        else:
            system = (
                "你是一个专业的文本质量评判员。你将对两个 AI 生成的文本进行评分。\n"
                "评分维度包括：相关性、流畅性、创意性、完整性，每项 0-10 分，总分 0-40 分。\n"
                "请以 JSON 格式输出结果，格式如下:\n"
                '{"a_score": <0-40的数字>, "b_score": <0-40的数字>, "reasoning": "<评判理由>"}\n'
                "只输出 JSON，不要输出其他内容。"
            )

        prompt = (
            f"测试输入:\n{test_input}\n\n"
            f"=== 版本 A 的输出 ===\n{response_a}\n\n"
            f"=== 版本 B 的输出 ===\n{response_b}\n\n"
            f"请评分并输出 JSON。"
        )

        resp = await self.llm.judge(prompt, system=system)
        if not resp.ok or not resp.content:
            return {"a": 0.0, "b": 0.0}, "评判失败（LLM 不可用）"

        # 解析 JSON
        scores, reasoning = self._parse_judge_response(resp.content)
        return scores, reasoning

    def _parse_judge_response(self, content: str) -> tuple[dict[str, float], str]:
        """解析 LLM 评判响应中的 JSON。"""
        # 尝试直接解析
        try:
            data = json.loads(content)
            return (
                {"a": float(data.get("a_score", 0)), "b": float(data.get("b_score", 0))},
                data.get("reasoning", ""),
            )
        except (json.JSONDecodeError, ValueError):
            pass

        # 尝试从文本中提取 JSON
        json_match = re.search(r'\{[^{}]*"a_score"[^{}]*\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return (
                    {"a": float(data.get("a_score", 0)), "b": float(data.get("b_score", 0))},
                    data.get("reasoning", content),
                )
            except (json.JSONDecodeError, ValueError):
                pass

        # 解析失败，返回默认值
        return {"a": 0.0, "b": 0.0}, content

    async def _save_experiment(
        self,
        project_id: uuid.UUID,
        experiment_id: str,
        prompt_a: str,
        prompt_b: str,
        test_input: str,
        result: dict[str, Any],
    ) -> None:
        """将实验结果持久化到 AgentRun。"""
        now = datetime.now(timezone.utc)
        run = AgentRun(
            project_id=project_id,
            session_id=None,
            agent_name="prompt_lab",
            status="success",
            started_at=now,
            finished_at=now,
            duration_ms=result.get("duration_ms", 0),
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            cost=result.get("cost", 0.0),
            result={
                "experiment_id": experiment_id,
                "winner": result["winner"],
                "scores": result["scores"],
                "judge_reasoning": result.get("judge_reasoning", ""),
                "prompt_a": prompt_a[:500],  # 截断存储
                "prompt_b": prompt_b[:500],
                "test_input": test_input[:500],
                "response_a_preview": result.get("response_a", "")[:500],
                "response_b_preview": result.get("response_b", "")[:500],
            },
        )
        self.db.add(run)
        await self.db.flush()

    def _placeholder_result(
        self,
        experiment_id: str,
        prompt_a: str,
        prompt_b: str,
        test_input: str,
    ) -> dict[str, Any]:
        """LLM 未配置时的占位实验结果。"""
        return {
            "experiment_id": experiment_id,
            "winner": "tie",
            "scores": {"a": 0.0, "b": 0.0},
            "response_a": "",
            "response_b": "",
            "judge_reasoning": "LLM 未配置，无法进行评判",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": 0.0,
            "duration_ms": 0,
        }
