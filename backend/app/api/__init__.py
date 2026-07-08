"""API 路由包。"""
from app.api.routes_brain import router as brain_router
from app.api.routes_book_memory import router as book_memory_router
from app.api.routes_canon import router as canon_router
from app.api.routes_cockpit import router as cockpit_router
from app.api.routes_evolution import router as evolution_router
from app.api.routes_pipeline import router as pipeline_router
from app.api.routes_planning import router as planning_router
from app.api.routes_projects import router as projects_router
from app.api.routes_provider import router as provider_router
from app.api.routes_review_queue import router as review_queue_router
from app.api.routes_storyline import router as storyline_router
from app.api.routes_usage import router as usage_router
from app.api.continuous import router as continuous_router

__all__ = [
    "projects_router",
    "pipeline_router",
    "cockpit_router",
    "storyline_router",
    "brain_router",
    "review_queue_router",
    "evolution_router",
    "usage_router",
    "provider_router",
    "canon_router",
    "book_memory_router",
    "planning_router",
    "continuous_router",
]
