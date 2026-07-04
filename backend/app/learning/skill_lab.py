"""技能实验室 - Agent 技能改进实验。

对指定 Agent 技能运行一组测试用例，评分并记录改进幅度，
结果持久化到 AgentRun.result JSON 字段（agent_name="skill_lab"）。
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

logger = logging.getLogger("app.learning.skill_lab")


class SkillLab:
    """Agent 技能改进实验。

    使用方式::

        lab = SkillLab(db, project_id)
        result = await lab.run_skill_test(
            skill_name="chapter_writer",
            test_cases=[
                {"input": "写一段动作场景", "expected": "...", "criteria": "紧张感"},
                {"input": "写一段对话", "expected": "...", "criteria": "自然度"},
            ],
        )
        print(result["avg_score"])  # 平均分
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
    # 运行技能测试
    # ------------------------------------------------------------------
    async def run_skill_test(
        self,
        project_id: Optional[uuid.UUID],
        skill_name: str,
        test_cases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """运行技能测试。

        Args:
            project_id: 项目 ID
            skill_name: 技能名称（如 "chapter_writer", "dialogue_writer"）
            test_cases: 测试用例列表，每个元素::

                {
                    "input": "测试输入",
                    "expected": "期望输出（可选）",
                    "criteria": "评分标准",
                }

        Returns:
            测试结果::

                {
                    "skill_name": str,
                    "test_count": int,
                    "avg_score": float,
                    "improvement": float,
                    "results": [...],
                    "input_tokens": int,
                    "output_tokens": int,
                    "cost": float,
                }
        """
        pid = project_id or self.project_id
        test_id = str(uuid.uuid4())

        # LLM 未配置时返回占位结果
        if not self.llm.is_configured:
            logger.warning("LLM 未配置，SkillLab 返回占位测试结果")
            return self._placeholder_result(skill_name, test_cases, test_id)

        t0 = time.monotonic()
        total_input = 0
        total_output = 0
        total_cost = 0.0
        results: list[dict[str, Any]] = []
        scores: list[float] = []

        for i, case in enumerate(test_cases):
            case_input = case.get("input", "")
            expected = case.get("expected", "")
            criteria = case.get("criteria", "质量")

            # 1. 调用 LLM 生成响应
            system = f"你是一个擅长 {skill_name} 的 AI 写作助手。请根据要求完成创作。"
            resp = await self.llm.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": case_input},
            ])

            total_input += resp.input_tokens
            total_output += resp.output_tokens
            total_cost += resp.cost

            if not resp.ok:
                results.append({
                    "case_index": i,
                    "input": case_input[:200],
                    "response": "",
                    "score": 0.0,
                    "error": resp.error,
                })
                scores.append(0.0)
                continue

            # 2. 评判响应质量
            score, reasoning = await self._evaluate(
                case_input, resp.content, expected, criteria
            )
            scores.append(score)

            results.append({
                "case_index": i,
                "input": case_input[:200],
                "response": resp.content[:500],
                "score": score,
                "reasoning": reasoning,
                "criteria": criteria,
            })

        # 计算平均分与改进幅度
        avg_score = sum(scores) / len(scores) if scores else 0.0
        previous_avg = await self._get_previous_avg_score(pid, skill_name)
        improvement = avg_score - previous_avg if previous_avg is not None else 0.0

        result = {
            "test_id": test_id,
            "skill_name": skill_name,
            "test_count": len(test_cases),
            "avg_score": round(avg_score, 2),
            "previous_avg_score": previous_avg,
            "improvement": round(improvement, 2),
            "results": results,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cost": total_cost,
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }

        # 3. 持久化
        await self._save_test(pid, result)

        logger.info(
            "SkillLab 测试 %s 完成: skill=%s avg=%.2f improvement=%.2f",
            test_id, skill_name, avg_score, improvement,
        )
        return result

    # ------------------------------------------------------------------
    # 列出技能测试记录
    # ------------------------------------------------------------------
    async def list_skill_tests(
        self,
        project_id: Optional[uuid.UUID] = None,
        skill_name: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """列出技能测试记录。"""
        pid = project_id or self.project_id
        stmt = (
            select(AgentRun)
            .where(
                AgentRun.project_id == pid,
                AgentRun.agent_name == "skill_lab",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )
        if skill_name:
            # 在 Python 侧过滤（JSON 查询兼容性）
            pass

        result = await self.db.execute(stmt)
        runs = result.scalars().all()

        tests = []
        for run in runs:
            r = run.result or {}
            if skill_name and r.get("skill_name") != skill_name:
                continue
            tests.append({
                "test_id": r.get("test_id", str(run.id)),
                "skill_name": r.get("skill_name", "unknown"),
                "test_count": r.get("test_count", 0),
                "avg_score": r.get("avg_score", 0.0),
                "improvement": r.get("improvement", 0.0),
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "cost": run.cost,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            })
        return tests

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------
    async def _evaluate(
        self,
        case_input: str,
        response: str,
        expected: str,
        criteria: str,
    ) -> tuple[float, str]:
        """评判单个测试用例的响应质量。

        Returns:
            (score, reasoning) - score: 0-10, reasoning: 评判理由
        """
        system = (
            "你是一个专业的写作质量评估员。请对 AI 生成的文本进行评分。\n"
            f"评分标准: {criteria}\n"
            "评分范围: 0-10 分（10 分为完美）。\n"
            "请以 JSON 格式输出: "
            '{"score": <0-10的数字>, "reasoning": "<评判理由>"}\n'
            "只输出 JSON。"
        )

        prompt = f"输入:\n{case_input}\n\n"
        if expected:
            prompt += f"期望输出:\n{expected}\n\n"
        prompt += f"AI 实际输出:\n{response}\n\n请评分。"

        resp = await self.llm.judge(prompt, system=system)
        if not resp.ok or not resp.content:
            return 0.0, "评判失败"

        # 解析 JSON
        try:
            data = json.loads(resp.content)
            return float(data.get("score", 0)), data.get("reasoning", "")
        except (json.JSONDecodeError, ValueError):
            # 尝试提取数字
            match = re.search(r'"score"\s*:\s*([\d.]+)', resp.content)
            if match:
                return float(match.group(1)), resp.content
            return 0.0, resp.content

    async def _get_previous_avg_score(
        self,
        project_id: uuid.UUID,
        skill_name: str,
    ) -> Optional[float]:
        """获取上一次同技能测试的平均分（用于计算改进幅度）。"""
        stmt = (
            select(AgentRun)
            .where(
                AgentRun.project_id == project_id,
                AgentRun.agent_name == "skill_lab",
            )
            .order_by(AgentRun.created_at.desc())
            .limit(10)
        )
        result = await self.db.execute(stmt)
        runs = result.scalars().all()
        for run in runs:
            r = run.result or {}
            if r.get("skill_name") == skill_name and "avg_score" in r:
                return float(r["avg_score"])
        return None

    async def _save_test(
        self,
        project_id: uuid.UUID,
        result: dict[str, Any],
    ) -> None:
        """将测试结果持久化到 AgentRun。"""
        now = datetime.now(timezone.utc)
        run = AgentRun(
            project_id=project_id,
            session_id=None,
            agent_name="skill_lab",
            status="success",
            started_at=now,
            finished_at=now,
            duration_ms=result.get("duration_ms", 0),
            input_tokens=result.get("input_tokens", 0),
            output_tokens=result.get("output_tokens", 0),
            cost=result.get("cost", 0.0),
            result=result,
        )
        self.db.add(run)
        await self.db.flush()

    def _placeholder_result(
        self,
        skill_name: str,
        test_cases: list[dict[str, Any]],
        test_id: str,
    ) -> dict[str, Any]:
        """LLM 未配置时的占位测试结果。"""
        return {
            "test_id": test_id,
            "skill_name": skill_name,
            "test_count": len(test_cases),
            "avg_score": 0.0,
            "previous_avg_score": None,
            "improvement": 0.0,
            "results": [
                {
                    "case_index": i,
                    "input": c.get("input", "")[:200],
                    "response": "",
                    "score": 0.0,
                    "error": "LLM 未配置",
                }
                for i, c in enumerate(test_cases)
            ],
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": 0.0,
            "duration_ms": 0,
        }
