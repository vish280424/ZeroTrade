import logging
from typing import Optional
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.auth import (
    get_session, require_auth,
    login_page, login_submit, logout,
    kite_login_redirect, kite_callback, auth_status,
)
from app.parser import parse_tip
from app.orders import place_order
from app.config import kite, kite_state

logger = logging.getLogger(__name__)

app = FastAPI(title="ZeroTrade")


# ── Models ────────────────────────────────────────────────────────────────────
class TipRequest(BaseModel):
    tipText: str
    quantity: Optional[int] = None


# ── Middleware ────────────────────────────────────────────────────────────────
@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path   = request.url.path
    public = path.startswith("/login") or path == "/auth/callback"
    if not public and not get_session(request):
        if path.startswith("/api/") or path.startswith("/auth/"):
            return JSONResponse({"detail": "Not authenticated."}, status_code=401)
        return RedirectResponse("/login")
    response = await call_next(request)
    if response.status_code >= 400:
        logger.warning(f"{request.method} {path} → {response.status_code}")
    return response


# ── App auth routes ───────────────────────────────────────────────────────────
@app.get("/login")
def get_login(error: str = ""):
    return login_page(error)

@app.post("/login")
def post_login(request: Request, username: str = Form(...), password: str = Form(...)):
    return login_submit(request, username, password)

@app.get("/logout")
def get_logout():
    return logout()


# ── Zerodha auth routes ───────────────────────────────────────────────────────
@app.get("/auth/login")
def zerodha_login(request: Request):
    require_auth(request)
    return kite_login_redirect()

@app.get("/auth/callback")
def zerodha_callback(request_token: str, status: str = ""):
    return kite_callback(request_token, status)

@app.get("/auth/status")
def zerodha_status(request: Request):
    require_auth(request)
    return auth_status()


# ── Trade routes ──────────────────────────────────────────────────────────────
@app.post("/api/trade/parse-only")
def api_parse_only(req: TipRequest, request: Request):
    require_auth(request)
    return parse_tip(req.tipText, req.quantity)

@app.post("/api/trade/parse-and-execute")
def api_parse_and_execute(req: TipRequest, request: Request):
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


# ── Balance route ─────────────────────────────────────────────────────────────
@app.get("/api/balance")
def api_balance(request: Request):
    require_auth(request)
    if not kite_state["access_token"]:
        from fastapi import HTTPException
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
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail=f"Kite margins failed: {str(e)}")


# ── Serve UI (must be last) ───────────────────────────────────────────────────
app.mount("/", StaticFiles(directory="static", html=True), name="static")
