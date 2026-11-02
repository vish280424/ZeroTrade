import os
import re
import logging
import bcrypt
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from kiteconnect import KiteConnect

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="ZeroTrade")

# ── Kite Connect ──────────────────────────────────────────────────────────────
kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
kite_state = {"access_token": None, "set_at": None}

# ── Session cookie ────────────────────────────────────────────────────────────
_signer = URLSafeTimedSerializer(os.getenv("SECRET_KEY", "change-me"))
COOKIE_NAME  = "zt_session"
SESSION_MAX_AGE = 12 * 3600  # 12 hours

def make_session() -> str:
    return _signer.dumps("user")

def get_session(request: Request) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        _signer.loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False

def require_auth(request: Request):
    if not get_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated.")

# ── Models ────────────────────────────────────────────────────────────────────
class TipRequest(BaseModel):
    tipText: str
    quantity: int | None = None

# ── Company name → NSE ticker lookup ─────────────────────────────────────────
COMPANY_LOOKUP = {
    # Pharma
    "ZYDUS LIFESCIENCES": "ZYDUSLIFE",
    "ZYDUS":              "ZYDUSLIFE",
    "SUN PHARMA":         "SUNPHARMA",
    "DR REDDYS":          "DRREDDY",
    "DR. REDDYS":         "DRREDDY",
    "CIPLA":              "CIPLA",
    "DIVI'S":             "DIVISLAB",
    "DIVIS":              "DIVISLAB",
    "LUPIN":              "LUPIN",
    "AUROBINDO":          "AUROPHARMA",
    # Banks
    "HDFC BANK":          "HDFCBANK",
    "ICICI BANK":         "ICICIBANK",
    "AXIS BANK":          "AXISBANK",
    "KOTAK BANK":         "KOTAKBANK",
    "STATE BANK":         "SBIN",
    "SBI":                "SBIN",
    "INDUSIND BANK":      "INDUSINDBK",
    "YES BANK":           "YESBANK",
    "BANK OF BARODA":     "BANKBARODA",
    "PUNJAB NATIONAL":    "PNB",
    # IT
    "TCS":                "TCS",
    "INFOSYS":            "INFY",
    "WIPRO":              "WIPRO",
    "HCL TECH":           "HCLTECH",
    "HCL TECHNOLOGIES":   "HCLTECH",
    "TECH MAHINDRA":      "TECHM",
    "MPHASIS":            "MPHASIS",
    # Auto
    "TATA MOTORS":        "TATAMOTORS",
    "MARUTI":             "MARUTI",
    "MARUTI SUZUKI":      "MARUTI",
    "BAJAJ AUTO":         "BAJAJ-AUTO",
    "HERO MOTOCORP":      "HEROMOTOCO",
    "MAHINDRA":           "M&M",
    "M&M":                "M&M",
    # Energy / Infra
    "RELIANCE":           "RELIANCE",
    "RELIANCE INDUSTRIES":"RELIANCE",
    "ONGC":               "ONGC",
    "NTPC":               "NTPC",
    "POWER GRID":         "POWERGRID",
    "BHEL":               "BHEL",
    "COAL INDIA":         "COALINDIA",
    "ADANI PORTS":        "ADANIPORTS",
    "ADANI ENTERPRISES":  "ADANIENT",
    "ADANI GREEN":        "ADANIGREEN",
    # FMCG
    "HINDUSTAN UNILEVER": "HINDUNILVR",
    "HUL":                "HINDUNILVR",
    "ITC":                "ITC",
    "NESTLE":             "NESTLEIND",
    "BRITANNIA":          "BRITANNIA",
    "DABUR":              "DABUR",
    "MARICO":             "MARICO",
    "GODREJ CONSUMER":    "GODREJCP",
    # Finance / Others
    "BAJAJ FINANCE":      "BAJFINANCE",
    "BAJAJ FINSERV":      "BAJAJFINSV",
    "HDFC LIFE":          "HDFCLIFE",
    "SBI LIFE":           "SBILIFE",
    "TITAN":              "TITAN",
    "ASIAN PAINTS":       "ASIANPAINT",
    "ULTRATECH":          "ULTRACEMCO",
    "GRASIM":             "GRASIM",
    "HINDALCO":           "HINDALCO",
    "TATA STEEL":         "TATASTEEL",
    "JSW STEEL":          "JSWSTEEL",
    "TATA CONSULTANCY":   "TCS",
    "INDIGO":             "INDIGO",
    "INTERGLOBE":         "INDIGO",
}

# ── Regex-based tip parser ────────────────────────────────────────────────────
_NUM = r"[\d,]+(?:\.\d+)?"

def _num(s: str) -> float:
    return float(s.replace(",", ""))

def parse_tip(tip_text: str, quantity_override: int | None = None) -> dict:
    text  = tip_text.strip()
    upper = text.upper()

    if re.search(r"\b(SHORT|SELL)\b", upper):
        action = "SELL"
    elif re.search(r"\b(BUY|LONG)\b", upper):
        action = "BUY"
    else:
        raise HTTPException(status_code=400, detail="Could not find BUY or SELL in tip.")

    symbol = None
    for name, ticker in sorted(COMPANY_LOOKUP.items(), key=lambda x: -len(x[0])):
        if name in upper:
            symbol = ticker
            break

    if not symbol:
        _KEYWORDS = {"BUY", "SELL", "SHORT", "LONG", "ABOVE", "BELOW", "AT",
                     "CMP", "SL", "TGT", "TARGET", "STOPLOSS", "QTY",
                     "QUANTITY", "NSE", "BSE", "MIS", "CNC", "SWING",
                     "DELIVERY", "INTRADAY", "POSITIONAL", "T1", "T2"}
        tokens = re.findall(r"[A-Z][A-Z0-9&\-]{1,}", upper)
        for tok in tokens:
            if tok not in _KEYWORDS:
                symbol = tok
                break

    if not symbol:
        raise HTTPException(status_code=400, detail="Could not extract a trading symbol from the tip.")

    entry_match = re.search(rf"\b(?:above|below|at|cmp|buy|sell)\s+({_NUM})", text, re.IGNORECASE)
    entry_price = _num(entry_match.group(1)) if entry_match else None

    sl_match  = re.search(rf"\b(?:sl|stoploss|stop[-\s]?loss)\s*[:\-]?\s*({_NUM})", text, re.IGNORECASE)
    sl_price  = _num(sl_match.group(1)) if sl_match else None

    tgt_match = re.search(rf"\b(?:target|tgt|t1|tp)\s*[:\-]?\s*({_NUM})", text, re.IGNORECASE)
    tgt_price = _num(tgt_match.group(1)) if tgt_match else None

    qty_match = re.search(rf"\b(?:qty|quantity|shares?|lots?)\s*[:\-]?\s*(\d+)", text, re.IGNORECASE)
    quantity  = quantity_override or (int(qty_match.group(1)) if qty_match else int(os.getenv("DEFAULT_QUANTITY", 1)))

    exchange = "BSE" if re.search(r"\bBSE\b", upper) else os.getenv("DEFAULT_EXCHANGE", "NSE")

    if re.search(r"\b(swing|delivery|cnc|positional|hold|days|weeks)\b", text, re.IGNORECASE):
        product = "CNC"
    else:
        product = "MIS"

    if entry_price and sl_price:
        order_type = "SL"
    elif entry_price:
        order_type = "LIMIT"
    else:
        order_type = "MARKET"

    trade = {
        "symbol":        symbol,
        "action":        action,
        "exchange":      exchange,
        "entryPrice":    entry_price,
        "stopLossPrice": sl_price,
        "targetPrice":   tgt_price,
        "quantity":      quantity,
        "product":       product,
        "orderType":     order_type,
        "rawTip":        tip_text,
    }
    logger.info(f"Parsed trade: {trade}")
    return trade

# ── Order placement ───────────────────────────────────────────────────────────
def place_order(trade: dict) -> dict:
    if not kite_state["access_token"]:
        raise HTTPException(
            status_code=401,
            detail="Zerodha not connected. Click 'Connect Zerodha' first."
        )

    product = kite.PRODUCT_MIS if trade.get("product") == "MIS" else kite.PRODUCT_CNC

    order_params = {
        "tradingsymbol":    trade["symbol"].upper(),
        "exchange":         trade["exchange"],
        "transaction_type": trade["action"],
        "quantity":         trade["quantity"],
        "product":          product,
        "validity":         kite.VALIDITY_DAY,
    }

    if trade["orderType"] == "SL":
        order_params["order_type"]    = kite.ORDER_TYPE_SL
        order_params["trigger_price"] = trade["entryPrice"]
        order_params["price"]         = round(trade["entryPrice"] * 1.002, 1)
    elif trade["orderType"] == "LIMIT":
        order_params["order_type"] = kite.ORDER_TYPE_LIMIT
        order_params["price"]      = trade["entryPrice"]
    else:
        order_params["order_type"] = kite.ORDER_TYPE_MARKET

    logger.info(f"Placing order: {order_params}")
    try:
        order_id = kite.place_order(variety=kite.VARIETY_REGULAR, **order_params)
        logger.info(f"Order placed: {order_id}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Kite order failed: {str(e)}")

    result = {"orderId": order_id}

    if (trade.get("product") == "CNC"
            and trade.get("stopLossPrice")
            and trade.get("targetPrice")):
        result["gttId"] = place_gtt_oco(trade)

    return result


def place_gtt_oco(trade: dict) -> int:
    sl_price  = trade["stopLossPrice"]
    tgt_price = trade["targetPrice"]
    ltp       = trade.get("entryPrice") or (sl_price + tgt_price) / 2

    gtt_params = {
        "trigger_type":   kite.GTT_TYPE_OCO,
        "tradingsymbol":  trade["symbol"].upper(),
        "exchange":       trade["exchange"],
        "trigger_values": [sl_price, tgt_price],
        "last_price":     ltp,
        "orders": [
            {
                "transaction_type": kite.TRANSACTION_TYPE_SELL,
                "quantity":   trade["quantity"],
                "product":    kite.PRODUCT_CNC,
                "order_type": kite.ORDER_TYPE_LIMIT,
                "price":      sl_price,
            },
            {
                "transaction_type": kite.TRANSACTION_TYPE_SELL,
                "quantity":   trade["quantity"],
                "product":    kite.PRODUCT_CNC,
                "order_type": kite.ORDER_TYPE_LIMIT,
                "price":      tgt_price,
            },
        ],
    }

    logger.info(f"Placing GTT OCO: {gtt_params}")
    try:
        gtt_id = kite.place_gtt(**gtt_params)["trigger_id"]
        logger.info(f"GTT OCO placed: {gtt_id}")
        return gtt_id
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GTT OCO failed: {str(e)}")

# ── App login / logout ────────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
def login_page(error: str = ""):
    err_html = f'<p style="color:#f87171;margin-top:12px;font-size:13px">{error}</p>' if error else ""
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>ZeroTrade · Login</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      background:#0f1117;color:#e2e8f0;min-height:100vh;display:flex;
      align-items:center;justify-content:center}}
    .card{{background:#1e2330;border:1px solid #2d3748;border-radius:12px;
      padding:36px 32px;width:100%;max-width:380px}}
    h2{{font-size:22px;font-weight:700;color:#f8fafc;margin-bottom:6px}}
    p{{font-size:13px;color:#64748b;margin-bottom:28px}}
    label{{display:block;font-size:12px;font-weight:600;color:#94a3b8;
      text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}}
    input{{width:100%;background:#0f1117;border:1px solid #2d3748;border-radius:8px;
      color:#f1f5f9;font-size:15px;padding:12px 14px;outline:none;
      transition:border-color .2s;margin-bottom:18px}}
    input:focus{{border-color:#3b82f6}}
    button{{width:100%;padding:13px;border-radius:8px;border:none;
      background:#2563eb;color:#fff;font-size:15px;font-weight:700;
      cursor:pointer;transition:background .15s}}
    button:hover{{background:#1d4ed8}}
  </style>
</head>
<body>
  <div class="card">
    <h2>ZeroTrade</h2>
    <p>Sign in to continue</p>
    <form method="post" action="/login">
      <label>Username</label>
      <input name="username" type="text" autocomplete="username" required autofocus/>
      <label>Password</label>
      <input name="password" type="password" autocomplete="current-password" required/>
      <button type="submit">Sign in</button>
      {err_html}
    </form>
  </div>
</body>
</html>""")

@app.post("/login")
def login_submit(username: str = Form(...), password: str = Form(...)):
    expected_user = os.getenv("APP_USERNAME", "admin")
    password_hash = os.getenv("APP_PASSWORD_HASH", "")

    if not password_hash:
        return RedirectResponse(
            "/login?error=APP_PASSWORD_HASH+not+set.+Run+setup_password.py",
            status_code=303
        )

    valid = (username == expected_user
             and bcrypt.checkpw(password.encode(), password_hash.encode()))
    if not valid:
        return RedirectResponse("/login?error=Invalid+username+or+password", status_code=303)

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME, make_session(),
        httponly=True, samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return resp

@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp

# ── Middleware: protect all routes ────────────────────────────────────────────
@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path   = request.url.path
    public = path.startswith("/login") or path == "/auth/callback"
    if not public and not get_session(request):
        if path.startswith("/api/") or path.startswith("/auth/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Not authenticated."}, status_code=401)
        return RedirectResponse("/login")
    response = await call_next(request)
    if response.status_code >= 400:
        logger.warning(f"{request.method} {path} → {response.status_code}")
    return response

# ── Zerodha auth endpoints ────────────────────────────────────────────────────
@app.get("/auth/login")
def kite_login(request: Request):
    require_auth(request)
    return RedirectResponse(kite.login_url())

@app.get("/auth/callback")
def kite_callback(request_token: str, status: str = ""):
    if status != "success":
        raise HTTPException(status_code=400, detail=f"Zerodha login failed: {status}")
    try:
        data = kite.generate_session(request_token, api_secret=os.getenv("KITE_API_SECRET"))
        kite_state["access_token"] = data["access_token"]
        kite_state["set_at"]       = datetime.now().strftime("%H:%M:%S")
        kite.set_access_token(data["access_token"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Session generation failed: {str(e)}")

    return HTMLResponse(f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;background:#0f1117;color:#e2e8f0;
  display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
  <div style="text-align:center">
    <h2 style="color:#34d399">Zerodha connected!</h2>
    <p style="color:#94a3b8;margin-top:8px">
      Logged in as <b style="color:#f1f5f9">{data.get('user_name','')}</b>
      &nbsp;·&nbsp; Token valid till midnight
    </p>
    <a href="/" style="display:inline-block;margin-top:20px;padding:10px 24px;
      background:#2563eb;color:#fff;border-radius:8px;text-decoration:none">
      Go to Dashboard
    </a>
  </div>
</body></html>""")

@app.get("/auth/status")
def auth_status(request: Request):
    require_auth(request)
    is_set = bool(kite_state["access_token"])
    return {
        "tokenSet":   is_set,
        "tokenSetAt": kite_state["set_at"],
        "message":    "Zerodha connected. Ready to trade." if is_set
                      else "Zerodha not connected. Click 'Connect Zerodha'.",
    }

# ── Trade endpoints ───────────────────────────────────────────────────────────
@app.post("/api/trade/parse-only")
def parse_only(req: TipRequest, request: Request):
    require_auth(request)
    return parse_tip(req.tipText, req.quantity)

@app.post("/api/trade/parse-and-execute")
def parse_and_execute(req: TipRequest, request: Request):
    require_auth(request)
    trade  = parse_tip(req.tipText, req.quantity)
    result = place_order(trade)
    return {
        "success":     True,
        "parsedTrade": trade,
        "orderId":     result["orderId"],
        "gttId":       result.get("gttId"),
        "orderStatus": "PLACED",
    }

@app.get("/api/balance")
def get_balance(request: Request):
    require_auth(request)
    if not kite_state["access_token"]:
        raise HTTPException(status_code=401, detail="Zerodha not connected.")
    try:
        margins = kite.margins()
        equity  = margins.get("equity", {})
        return {
            "available": equity.get("available", {}).get("live_balance", 0),
            "used":      equity.get("utilised", {}).get("debits", 0),
            "currency":  "INR",
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Kite margins failed: {str(e)}")

# ── Serve UI (must be last) ───────────────────────────────────────────────────
app.mount("/", StaticFiles(directory="static", html=True), name="static")
