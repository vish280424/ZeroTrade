import os
import json
import re
import requests
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from kiteconnect import KiteConnect

load_dotenv()

app = FastAPI(title="Intraday Trader")

# ── Kite Connect instance ────────────────────────────────────────────────────
kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))

# In-memory token state
token_state = {
    "access_token": None,
    "set_at": None,
}

# ── Models ───────────────────────────────────────────────────────────────────
class TipRequest(BaseModel):
    tipText: str

# ── LLM parsing ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
You are a financial trading assistant. Your sole task is to parse raw trading tip text
into structured JSON. Return ONLY a valid JSON object — no explanations, no markdown fences.

Output schema:
{
  "symbol": "NSE/BSE ticker in UPPERCASE (e.g. RELIANCE, INFY)",
  "action": "BUY or SELL exactly",
  "exchange": "NSE or BSE (default NSE)",
  "entryPrice": "number or null",
  "stopLossPrice": "number or null",
  "targetPrice": "number or null",
  "quantity": "integer or null",
  "isValid": "true if confident, false if ambiguous"
}

Rules:
- Entry price from: 'above', 'below', 'at', 'CMP', or bare number after symbol.
- SL / stoploss / stop-loss → stopLossPrice.
- TGT / target / T1 / T2 → targetPrice (use T1 if multiple).
- quantity / qty → quantity.
- Absent fields → null. Output ONLY the JSON object.
""".strip()

def parse_tip(tip_text: str) -> dict:
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
            "Content-Type": "application/json",
        },
        json={
            "model": os.getenv("GROQ_MODEL", "llama3-8b-8192"),
            "temperature": 0.1,
            "max_completion_tokens": 512,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": tip_text},
            ],
        },
        timeout=30,
    )

    if not resp.ok:
        raise HTTPException(status_code=502, detail=f"Groq API error: {resp.status_code} {resp.text}")

    content = resp.json()["choices"][0]["message"]["content"]

    # Strip markdown fences if LLM adds them despite instructions
    content = re.sub(r"```json\s*(.*?)```", r"\1", content, flags=re.DOTALL)
    content = re.sub(r"```\s*(.*?)```",    r"\1", content, flags=re.DOTALL)
    content = content.strip()

    try:
        trade = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail=f"LLM returned non-JSON: {content}")

    if not trade.get("isValid", True):
        raise HTTPException(status_code=400, detail="LLM flagged the tip as invalid or ambiguous.")
    if not trade.get("symbol"):
        raise HTTPException(status_code=400, detail="Could not extract a trading symbol from the tip.")
    if trade.get("action") not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail=f"Action must be BUY or SELL, got: {trade.get('action')}")

    # Apply defaults
    trade.setdefault("exchange", os.getenv("DEFAULT_EXCHANGE", "NSE"))
    trade["exchange"] = trade["exchange"] or os.getenv("DEFAULT_EXCHANGE", "NSE")
    if not trade.get("quantity"):
        trade["quantity"] = int(os.getenv("DEFAULT_QUANTITY", 1))

    # Derive order type
    if trade.get("entryPrice") and trade.get("stopLossPrice"):
        trade["orderType"] = "SL"
    elif trade.get("entryPrice"):
        trade["orderType"] = "LIMIT"
    else:
        trade["orderType"] = "MARKET"

    trade["rawTip"] = tip_text
    return trade

# ── Order placement ──────────────────────────────────────────────────────────
def place_order(trade: dict) -> str:
    if not token_state["access_token"]:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated. Open http://localhost:8080/auth/login to log in first."
        )

    order_params = {
        "tradingsymbol":  trade["symbol"].upper(),
        "exchange":       trade["exchange"],
        "transaction_type": trade["action"],
        "quantity":       trade["quantity"],
        "product":        kite.PRODUCT_MIS,
        "validity":       kite.VALIDITY_DAY,
    }

    if trade["orderType"] == "SL":
        order_params["order_type"]    = kite.ORDER_TYPE_SL
        order_params["price"]         = trade["entryPrice"]
        order_params["trigger_price"] = trade["stopLossPrice"]
    elif trade["orderType"] == "LIMIT":
        order_params["order_type"] = kite.ORDER_TYPE_LIMIT
        order_params["price"]      = trade["entryPrice"]
    else:
        order_params["order_type"] = kite.ORDER_TYPE_MARKET

    try:
        order_id = kite.place_order(variety=kite.VARIETY_REGULAR, **order_params)
        return order_id
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Kite order failed: {str(e)}")

# ── Auth endpoints ────────────────────────────────────────────────────────────
@app.get("/auth/login")
def auth_login():
    return RedirectResponse(kite.login_url())

@app.get("/auth/callback")
def auth_callback(request_token: str, status: str = ""):
    if status != "success":
        raise HTTPException(status_code=400, detail=f"Login failed. Status: {status}")
    try:
        data = kite.generate_session(request_token, api_secret=os.getenv("KITE_API_SECRET"))
        token_state["access_token"] = data["access_token"]
        token_state["set_at"] = datetime.now().strftime("%H:%M:%S")
        kite.set_access_token(data["access_token"])
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;background:#0f1117;color:#e2e8f0;display:flex;
          align-items:center;justify-content:center;height:100vh;margin:0">
          <div style="text-align:center">
            <h2 style="color:#34d399">Login successful!</h2>
            <p style="color:#94a3b8;margin-top:8px">
              Logged in as <b style="color:#f1f5f9">{data.get('user_name','')}</b>
              &nbsp;·&nbsp; Token valid till midnight
            </p>
            <a href="/" style="display:inline-block;margin-top:20px;padding:10px 24px;
              background:#2563eb;color:#fff;border-radius:8px;text-decoration:none">
              Go to Dashboard
            </a>
          </div>
        </body></html>
        """)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Session generation failed: {str(e)}")

@app.get("/auth/status")
def auth_status():
    is_set = bool(token_state["access_token"])
    return {
        "tokenSet": is_set,
        "tokenSetAt": token_state["set_at"],
        "message": "Token is active. Ready to trade." if is_set
                   else "Not authenticated. Open /auth/login to log in.",
    }

# ── Trade endpoints ───────────────────────────────────────────────────────────
@app.post("/api/trade/parse-only")
def parse_only(req: TipRequest):
    return parse_tip(req.tipText)

@app.post("/api/trade/parse-and-execute")
def parse_and_execute(req: TipRequest):
    trade    = parse_tip(req.tipText)
    order_id = place_order(trade)
    return {
        "success":    True,
        "parsedTrade": trade,
        "orderId":    order_id,
        "orderStatus": "PLACED",
    }

# ── Serve UI ──────────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory="static", html=True), name="static")
