"""Prompt 模板统一导出。"""
from app.prompts.templates.chapter_plan import CHAPTER_PLAN_SYSTEM, CHAPTER_PLAN_USER
from app.prompts.templates.continuity import CONTINUITY_SYSTEM, CONTINUITY_USER
from app.prompts.templates.critic import CRITIC_SYSTEM, CRITIC_USER
from app.prompts.templates.draft import DRAFT_SYSTEM, DRAFT_USER, SCENE_DRAFT_SYSTEM
from app.prompts.templates.outline import OUTLINE_SYSTEM, OUTLINE_USER
from app.prompts.templates.rewriter import REWRITER_SYSTEM, REWRITER_USER
from app.prompts.templates.summary import SUMMARY_SYSTEM, SUMMARY_USER
from app.prompts.templates.world_bible import WORLD_BIBLE_SYSTEM, WORLD_BIBLE_USER

__all__ = [
    "WORLD_BIBLE_SYSTEM",
    "WORLD_BIBLE_USER",
    "OUTLINE_SYSTEM",
    "OUTLINE_USER",
    "CHAPTER_PLAN_SYSTEM",
    "CHAPTER_PLAN_USER",
    "DRAFT_SYSTEM",
    "DRAFT_USER",
    "SCENE_DRAFT_SYSTEM",
    "CRITIC_SYSTEM",
    "CRITIC_USER",
    "CONTINUITY_SYSTEM",
    "CONTINUITY_USER",
    "REWRITER_SYSTEM",
    "REWRITER_USER",
    "SUMMARY_SYSTEM",
    "SUMMARY_USER",
]
