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
        # Reset singleton so a fresh instance is created
        data_ws.FyersDataSocket._instance = None
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
            finally:
                data_ws.FyersDataSocket._instance = None
                self.ws = None


class TickDispatcher:
    """Single shared WebSocket that routes ticks to the correct detector(s).

    FyersDataSocket is a singleton — only one WS connection per process.
    This dispatcher subscribes to all symbols and forwards each tick
    to the detector(s) that own that symbol.
    """

    def __init__(self, client_id: str, access_token: str, detectors: list):
        self._symbol_map: dict[str, list] = {}
        all_symbols: list[str] = []

        for det in detectors:
            for sym in det.config.symbols:
                self._symbol_map.setdefault(sym, []).append(det)
                if sym not in all_symbols:
                    all_symbols.append(sym)

        self._ws = WebSocketManager(
            client_id, access_token, self._on_tick, all_symbols
        )
        log.info(f"TickDispatcher: {len(all_symbols)} symbols, "
                 f"{len(detectors)} detectors")

    def _on_tick(self, *args):
        message = args[-1] if args else None
        if not isinstance(message, dict):
            return
        symbol = message.get("symbol")
        if symbol:
            for det in self._symbol_map.get(symbol, []):
                det.on_tick(*args)

    def connect(self):
        self._ws.connect()

    def close(self):
        self._ws.close()
