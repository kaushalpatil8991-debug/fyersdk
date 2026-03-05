"""Telegram message templates."""
from shared.models import TradeAlert


def trade_alert_message(alert: TradeAlert) -> str:
    return (
        f"<b>LARGE TRADE DETECTED</b>\n\n"
        f"<b>Symbol:</b> {alert.symbol}\n"
        f"<b>Sector:</b> {alert.sector}\n"
        f"<b>LTP:</b> Rs{alert.ltp:,.2f}\n"
        f"<b>Volume:</b> {alert.volume_spike:,}\n"
        f"<b>Value:</b> Rs{alert.trade_value_cr:.2f} Cr\n"
        f"<b>Type:</b> {alert.spike_type}\n"
        f"<b>Time:</b> {alert.timestamp.strftime('%H:%M:%S')}"
    )


def auth_required_message(auth_url: str, totp_code: str) -> str:
    return (
        f"<b>Fyers Authentication Required</b>\n\n"
        f"Click to authorize:\n{auth_url}\n\n"
        f"<b>TOTP Code:</b> <code>{totp_code}</code>\n\n"
        f"After authorizing, send the redirect URL here.\n"
        f"<i>URL resent every 5 minutes until authenticated.</i>"
    )


def auth_success_message() -> str:
    return "<b>Fyers Authentication Successful!</b>\nMonitoring started."


def auth_failure_message(error: str) -> str:
    return f"<b>Authentication Failed</b>\n\nError: {error}"


def hold_message(reason: str = "") -> str:
    msg = "<b>Detectors ON HOLD</b>\n\nAll monitoring stopped."
    if reason:
        msg += f"\n<b>Reason:</b> {reason}"
    msg += "\n\nSend /rst to restart."
    return msg


def restart_message() -> str:
    return "<b>Restarting...</b>\n\nRe-authenticating and restarting all detectors."


def restart_complete_message() -> str:
    return "<b>Restart Complete</b>\n\nAll detectors are back online."


def monitoring_started_message(fyers_count: int, penny_count: int) -> str:
    return (
        f"<b>Monitoring Started</b>\n\n"
        f"Fyers: {fyers_count} symbols\n"
        f"Penny: {penny_count} symbols"
    )


def summary_generating_message() -> str:
    return "<b>Generating summaries...</b>\n\nPlease wait."


def summary_date_prompt_message() -> str:
    return "<b>Send the date</b> (DD-MM-YYYY):"


def summary_date_invalid_message() -> str:
    return "<b>Invalid date format.</b>\n\nUse DD-MM-YYYY (e.g. 28-02-2026)"


def summary_date_confirm_message(date_str: str, day_name: str) -> str:
    return f"<b>Generating summaries for {date_str} ({day_name})...</b>\n\nPlease wait."
