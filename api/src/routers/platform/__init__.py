"""
Platform Admin Routers

Provides API endpoints for platform administration including:
- Process pool management and monitoring
- Queue status and stuck execution history
"""

from src.routers.platform.workers import (
    queue_router,
    router as workers_router,
    stuck_router,
)
from src.routers.platform.app_service import router as app_service_router

__all__ = [
    "workers_router",
    "queue_router",
    "stuck_router",
    "app_service_router",
]
