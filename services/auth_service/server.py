"""Telegram webhook endpoint for receiving auth codes and commands."""
from fastapi import APIRouter, Request
from shared.logger import get_logger
from services.telegram_service.command_parser import (
    extract_auth_code, parse_command, parse_date
)
from services.telegram_service.message_template import (
    hold_message, restart_message,
    summary_generating_message, summary_date_prompt_message,
    summary_date_invalid_message, summary_date_confirm_message,
)

log = get_logger("auth_webhook")

router = APIRouter()

_auth_state = None
_orchestrator = None
_login_sender = None
_pending_summary_date = False


def init_auth_router(auth_state, orchestrator=None, login_sender=None):
    global _auth_state, _orchestrator, _login_sender
    _auth_state = auth_state
    _orchestrator = orchestrator
    _login_sender = login_sender
    return router


@router.get("/auth/callback")
async def auth_callback(request: Request):
    """Handle Fyers redirect callback — extract auth_code from query params."""
    auth_code = request.query_params.get("auth_code")
    if auth_code and _auth_state:
        _auth_state.pending_auth_code = auth_code
        _auth_state.auth_event.set()
        log.info("Auth code received via callback!")
        return {"status": "auth_code_received"}
    return {"status": "no_auth_code"}


@router.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Receive Telegram webhook updates containing auth codes and commands."""
    global _pending_summary_date

    body = await request.json()
    log.info("Webhook received update")

    message = body.get("message", {})
    text = message.get("text", "")

    # Check for commands first
    command, rest = parse_command(text)

    if command == "/hld":
        return await _handle_hold(rest)

    if command == "/rst":
        return await _handle_restart()

    if command == "/snd":
        return await _handle_send_summary()

    if command == "/sdt":
        return await _handle_summary_date_prompt()

    # Check if we're waiting for a date (from /sdt)
    if _pending_summary_date and text.strip():
        return await _handle_summary_date_input(text)

    # Check for auth code
    if "auth_code=" in text:
        auth_code = extract_auth_code(text)
        if auth_code and _auth_state:
            _auth_state.pending_auth_code = auth_code
            _auth_state.auth_event.set()
            log.info("Auth code received via webhook!")
            return {"status": "auth_code_received"}

    return {"status": "ok"}


async def _handle_hold(reason: str):
    """Handle /hld command — stop all detectors."""
    if not _orchestrator:
        return {"status": "error", "message": "orchestrator not ready"}

    _orchestrator.hold()
    log.info(f"Hold command received. Reason: {reason or 'none given'}")

    if _login_sender:
        await _login_sender.send_async(hold_message(reason))

    return {"status": "hold_activated"}


async def _handle_restart():
    """Handle /rst command — restart all services and re-authenticate."""
    if not _orchestrator:
        return {"status": "error", "message": "orchestrator not ready"}

    if _login_sender:
        await _login_sender.send_async(restart_message())

    _orchestrator.request_restart()
    log.info("Restart command received")

    return {"status": "restart_requested"}


async def _handle_send_summary():
    """Handle /snd command — generate and send today's summaries."""
    if not _orchestrator:
        return {"status": "error", "message": "orchestrator not ready"}

    if _login_sender:
        await _login_sender.send_async(summary_generating_message())

    log.info("Send summary command received")
    await _orchestrator.fyers_summary.send_today()
    await _orchestrator.penny_summary.send_today()

    return {"status": "summary_sent"}


async def _handle_summary_date_prompt():
    """Handle /sdt command — prompt user for a date."""
    global _pending_summary_date
    _pending_summary_date = True

    if _login_sender:
        await _login_sender.send_async(summary_date_prompt_message())

    log.info("Summary date prompt sent, waiting for date input")
    return {"status": "awaiting_date"}


async def _handle_summary_date_input(text: str):
    """Handle date input after /sdt command."""
    global _pending_summary_date

    target_date = parse_date(text.strip())
    if not target_date:
        if _login_sender:
            await _login_sender.send_async(summary_date_invalid_message())
        log.warning(f"Invalid date input: {text}")
        # Stay in pending state so user can retry
        return {"status": "invalid_date"}

    _pending_summary_date = False
    date_str = target_date.strftime("%d-%m-%Y")
    day_name = target_date.strftime("%A")

    if _login_sender:
        await _login_sender.send_async(
            summary_date_confirm_message(date_str, day_name)
        )

    log.info(f"Generating summaries for {date_str} ({day_name})")
    await _orchestrator.fyers_summary.send_for_date(target_date)
    await _orchestrator.penny_summary.send_for_date(target_date)

    return {"status": "summary_sent", "date": date_str}
