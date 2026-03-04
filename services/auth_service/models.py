"""Auth service data models."""
import asyncio
from typing import Optional


class AuthState:
    """Mutable state for the auth flow, shared in-memory."""
    def __init__(self):
        self.pending_auth_code: Optional[str] = None
        self.auth_event: asyncio.Event = asyncio.Event()
        self.current_session = None
        self.current_auth_url: Optional[str] = None
