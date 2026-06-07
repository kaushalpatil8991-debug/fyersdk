"""MCP server (Streamable HTTP) — news tools + Fyers detector tools.

Mounted into the main FastAPI app; reachable at /mcp (or /mcp-<secret>
when MCP_PATH_SECRET is set). Add it in Claude as a custom connector:
https://<your-app>.onrender.com/mcp
"""
import os

from mcp.server.fastmcp import FastMCP

from shared.logger import get_logger
from .news_tools import register_news_tools
from .fyers_tools import register_fyers_tools

log = get_logger("mcp_server")


def mcp_path() -> str:
    """Path where the MCP endpoint is served."""
    secret = os.getenv("MCP_PATH_SECRET", "").strip()
    return f"/mcp-{secret}" if secret else "/mcp"


def _news_sender(orchestrator):
    """Build a TelegramSender for the news bot from config or env."""
    from services.telegram_service import TelegramSender
    from shared.config_loader import TelegramChannel

    channel = None
    if orchestrator is not None:
        channel = orchestrator.config.telegram.news
    if channel is None:
        token = os.getenv("NEWS_BOT_TOKEN", "").strip()
        chat_id = os.getenv("NEWS_CHAT_ID", "").strip()
        if token and chat_id:
            channel = TelegramChannel(bot_token=token, chat_id=chat_id)
    return TelegramSender(channel) if channel else None


def build_mcp(orchestrator=None) -> FastMCP:
    """Create the FastMCP server with all tools registered.

    stateless_http + json_response keeps it simple and compatible with
    Render free tier (no sticky sessions) and Claude custom connectors.
    """
    mcp = FastMCP(
        "fyersdk",
        instructions=(
            "News + Fyers volume spike detector tools. "
            "Use fetch_news_headlines with any RSS URL (see list_news_feeds), "
            "fetch_article_content for full articles, and the get_* tools "
            "for detector status and volume spike summaries."
        ),
        stateless_http=True,
        json_response=True,
    )
    mcp.settings.streamable_http_path = mcp_path()

    register_news_tools(mcp, news_sender=_news_sender(orchestrator))
    register_fyers_tools(mcp, orchestrator)

    log.info(f"MCP server built, endpoint path: {mcp.settings.streamable_http_path}")
    return mcp
