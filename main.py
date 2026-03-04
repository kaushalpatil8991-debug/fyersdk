"""
Fyers Volume Spike Detector - Main Entry Point
FastAPI health server + Telegram webhook + dual detector orchestration
"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from datetime import datetime
import logging
import uvicorn

from shared.config_loader import load_config
from shared.logger import get_logger
from services.auth_service.models import AuthState
from services.auth_service.server import init_auth_router
from services.auth_service.tools import register_telegram_webhook, register_bot_commands
from services.supervisor_service.ochestrator import Orchestrator
from services.supervisor_service.health_monitor import start_self_ping

log = get_logger("main")

config = load_config()
auth_state = AuthState()
orchestrator = Orchestrator(config, auth_state)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    log.info("Starting up...")

    # Register Telegram webhook and bot commands on login bot
    login_bot_token = config.telegram.login.bot_token
    register_telegram_webhook(login_bot_token)
    register_bot_commands(login_bot_token)

    # Start self-ping for Render keep-alive
    start_self_ping()

    # Start orchestrator in background task
    task = asyncio.create_task(orchestrator.run())

    yield

    # Shutdown
    log.info("Shutting down...")
    if orchestrator.fyers_controller:
        orchestrator.fyers_controller.stop()
    if orchestrator.penny_controller:
        orchestrator.penny_controller.stop()
    task.cancel()


app = FastAPI(title="Fyers Volume Spike Detector", lifespan=lifespan)

# Mount the webhook router (with orchestrator for /hld and /rst commands)
app.include_router(init_auth_router(
    auth_state,
    orchestrator=orchestrator,
    login_sender=orchestrator.login_sender,
))


@app.get("/health")
async def health_check():
    return {
        "status": "on_hold" if orchestrator.on_hold else "healthy",
        "timestamp": datetime.now().isoformat(),
        "fyers_running": (orchestrator.fyers_controller.is_running
                         if orchestrator.fyers_controller else False),
        "penny_running": (orchestrator.penny_controller.is_running
                         if orchestrator.penny_controller else False),
    }


if __name__ == "__main__":
    # Suppress "Invalid HTTP request received" warnings from scanners/bots
    logging.getLogger("uvicorn.error").setLevel(logging.ERROR)
    uvicorn.run(app, host="0.0.0.0", port=config.port)
