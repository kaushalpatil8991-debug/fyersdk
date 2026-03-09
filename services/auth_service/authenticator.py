"""Fyers authentication orchestrator using Telegram webhook."""
import asyncio
from fyers_apiv3 import fyersModel
from shared.logger import get_logger
from shared.config_loader import AppConfig
from shared.exceptions import AuthenticationError
from services.telegram_service import TelegramSender
from services.telegram_service.message_template import (
    auth_required_message, auth_success_message, auth_failure_message
)
from .token_manager import TokenManager
from .totp_handler import TOTPHandler
from .models import AuthState

log = get_logger("authenticator")


class FyersAuthenticator:
    def __init__(self, config: AppConfig, token_manager: TokenManager,
                 login_sender: TelegramSender, auth_state: AuthState):
        self.cfg = config.fyers
        self.token_manager = token_manager
        self.totp = TOTPHandler(self.cfg.totp_secret)
        self.login_sender = login_sender
        self.auth_state = auth_state
        self.access_token: str | None = None
        self.fyers_model = None
        self.is_authenticated = False
        self._cancel_event = asyncio.Event()

    def cancel_auth(self):
        """Cancel any in-progress authentication wait."""
        self._cancel_event.set()
        # Also wake up the auth_event wait so authenticate() can exit
        self.auth_state.auth_event.set()

    def reset_cancel(self):
        """Clear cancellation flag before a new auth attempt."""
        self._cancel_event.clear()

    def check_token_with_fyers(self) -> tuple[bool, str]:
        """Verify token by calling Fyers profile API."""
        if not self.access_token:
            return False, "No token"
        try:
            test_fyers = fyersModel.FyersModel(
                client_id=self.cfg.client_id,
                token=self.access_token, log_path=""
            )
            profile = test_fyers.get_profile()
            if profile and profile.get("s") == "ok":
                self.fyers_model = test_fyers
                return True, "Valid"
            return False, profile.get("message", "Invalid")
        except Exception as e:
            return False, str(e)

    async def authenticate(self) -> bool:
        """Full auth flow: check stored token -> if expired, webhook-based flow."""
        # 1) Try stored token
        valid, msg = self.token_manager.is_token_valid_by_time()
        if valid:
            token, _, _ = self.token_manager.load_token()
            self.access_token = token
            ok, fmsg = self.check_token_with_fyers()
            if ok:
                log.info("Using existing valid token from Supabase")
                self.is_authenticated = True
                await self.login_sender.send_async(auth_success_message())
                return True
            log.warning(f"Stored token invalid from Fyers: {fmsg}")

        # 2) Fresh auth via webhook
        log.info("Starting fresh authentication...")
        self.auth_state.auth_event.clear()
        self.auth_state.pending_auth_code = None

        # Create session once — reuse for resends so auth code matches this session
        session = self._create_session()
        await self._send_auth_msg(session)

        # 3) Wait for auth_code from webhook (with periodic resend)
        while True:
            try:
                await asyncio.wait_for(
                    self.auth_state.auth_event.wait(),
                    timeout=300  # 5 min
                )
            except asyncio.TimeoutError:
                if self._cancel_event.is_set():
                    log.info("Auth cancelled during timeout")
                    return False
                log.info("5 min elapsed, resending auth URL with fresh TOTP...")
                await self._send_auth_msg(session)
                self.auth_state.auth_event.clear()
                continue

            # Woke up — check if cancelled or got a real auth code
            if self._cancel_event.is_set():
                log.info("Auth cancelled")
                return False
            break

        auth_code = self.auth_state.pending_auth_code
        if not auth_code:
            error_msg = "No auth code received"
            await self.login_sender.send_async(auth_failure_message(error_msg))
            raise AuthenticationError(error_msg)

        # 4) Exchange code for token using the SAME session that generated the URL
        log.info(f"Auth code received ({auth_code[:20]}...), exchanging for token...")
        session.set_token(auth_code)
        response = session.generate_token()

        if response and response.get("s") == "ok":
            self.access_token = response["access_token"]
            self.token_manager.save_token(self.access_token)
            self.fyers_model = fyersModel.FyersModel(
                client_id=self.cfg.client_id,
                token=self.access_token, log_path=""
            )
            self.is_authenticated = True
            log.info("Authentication successful!")
            await self.login_sender.send_async(auth_success_message())
            return True

        error_msg = f"Token generation failed: {response}"
        await self.login_sender.send_async(auth_failure_message(error_msg))
        raise AuthenticationError(error_msg)

    def _create_session(self) -> fyersModel.SessionModel:
        """Create a new Fyers session and generate its auth URL."""
        session = fyersModel.SessionModel(
            client_id=self.cfg.client_id,
            secret_key=self.cfg.secret_key,
            redirect_uri=self.cfg.redirect_uri,
            response_type="code",
            grant_type="authorization_code"
        )
        self.auth_state.current_session = session
        self.auth_state.current_auth_url = session.generate_authcode()
        return session

    async def _send_auth_msg(self, session: fyersModel.SessionModel):
        """Send the auth URL + fresh TOTP to Telegram (reuses existing session)."""
        totp_code = self.totp.generate()
        msg = auth_required_message(self.auth_state.current_auth_url, totp_code)
        await self.login_sender.send_async(msg)
