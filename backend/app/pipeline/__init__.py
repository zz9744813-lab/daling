"""Pipeline package with a lazy public orchestrator export.

Keeping this module import-light prevents service modules that only need the
LLM client from recursively importing the orchestrator and autonomous-learning
service during application startup.
"""

from typing import Any

__all__ = ["PipelineOrchestrator"]


def __getattr__(name: str) -> Any:
    if name == "PipelineOrchestrator":
        from app.pipeline.orchestrator import PipelineOrchestrator

        return PipelineOrchestrator
    raise AttributeError(name)
