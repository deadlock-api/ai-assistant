# Deadlock AI Assistant - Health check endpoint

from fastapi import APIRouter

from packages.integrations.redis_client import redis_ping

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str | dict[str, str]]:
    """Health check endpoint for load balancers and monitoring.

    Includes connectivity status for dependent services like Redis.
    The endpoint returns healthy as long as the API itself is running,
    even if dependencies are unavailable (they are reported in services).
    """
    redis_available = await redis_ping()

    return {
        "status": "healthy",
        "version": "1.0.0",
        "services": {
            "redis": "connected" if redis_available else "unavailable",
        },
    }
