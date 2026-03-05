"""TOTP generation for Fyers 2FA."""
import pyotp


class TOTPHandler:
    def __init__(self, secret: str):
        self._totp = pyotp.TOTP(secret)

    def generate(self) -> str:
        return self._totp.now()
