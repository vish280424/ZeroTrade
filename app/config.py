import os
import logging
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Kite Connect ──────────────────────────────────────────────────────────────
kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
kite_state: dict = {"access_token": None, "set_at": None}

# ── App settings ──────────────────────────────────────────────────────────────
APP_USERNAME     = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD_HASH = os.getenv("APP_PASSWORD_HASH", "")
SECRET_KEY       = os.getenv("SECRET_KEY", "change-me")
KITE_API_SECRET  = os.getenv("KITE_API_SECRET", "")
DEFAULT_EXCHANGE = os.getenv("DEFAULT_EXCHANGE", "NSE")
DEFAULT_QUANTITY = int(os.getenv("DEFAULT_QUANTITY", 1))
