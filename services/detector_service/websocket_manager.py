"""Fyers WebSocket connection management."""
import time
from fyers_apiv3.FyersWebsocket import data_ws
from shared.logger import get_logger

log = get_logger("websocket")


class WebSocketManager:
    def __init__(self, client_id: str, access_token: str,
                 on_message_callback, symbols: list[str]):
        self.client_id = client_id
        self.access_token = access_token
        self.on_message = on_message_callback
        self.symbols = symbols
        self.ws = None

    def connect(self):
        self.ws = data_ws.FyersDataSocket(
            access_token=f"{self.client_id}:{self.access_token}",
            log_path="",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_message=self.on_message,
        )
        self.ws.connect()
        time.sleep(3)
        self.ws.subscribe(symbols=self.symbols, data_type="SymbolUpdate")
        log.info(f"WebSocket connected, subscribed to {len(self.symbols)} symbols")

    def close(self):
        if self.ws:
            try:
                self.ws.close_connection()
            except Exception:
                pass
