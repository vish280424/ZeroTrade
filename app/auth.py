import logging
import bcrypt
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional
from fastapi import HTTPException, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.config import (
    APP_USERNAME, APP_PASSWORD_HASH, SECRET_KEY,
    KITE_API_SECRET, kite, kite_state,
)

logger = logging.getLogger(__name__)

COOKIE_NAME     = "zt_session"
SESSION_MAX_AGE = 12 * 3600

_signer = URLSafeTimedSerializer(SECRET_KEY)

# ── Rate limiting ─────────────────────────────────────────────────────────────
_MAX_ATTEMPTS  = 5
_LOCKOUT_MINS  = 15
# { ip: {"count": int, "locked_until": datetime | None} }
_login_attempts: dict = defaultdict(lambda: {"count": 0, "locked_until": None})

def _check_rate_limit(ip: str) -> Optional[str]:
    """Returns an error message if the IP is rate-limited, else None."""
    record = _login_attempts[ip]
    if record["locked_until"] and datetime.now() < record["locked_until"]:
        remaining = int((record["locked_until"] - datetime.now()).total_seconds() / 60) + 1
        return f"Too many failed attempts. Try again in {remaining} minute(s)."
    if record["locked_until"] and datetime.now() >= record["locked_until"]:
        # Lockout expired — reset
        _login_attempts[ip] = {"count": 0, "locked_until": None}
    return None

def _record_failure(ip: str):
    record = _login_attempts[ip]
    record["count"] += 1
    if record["count"] >= _MAX_ATTEMPTS:
        record["locked_until"] = datetime.now() + timedelta(minutes=_LOCKOUT_MINS)
        logger.warning(f"Login locked for IP {ip} after {_MAX_ATTEMPTS} failed attempts.")

def _record_success(ip: str):
    _login_attempts[ip] = {"count": 0, "locked_until": None}


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


def require_auth(request: Request) -> None:
    if not get_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated.")


# ── Login page ────────────────────────────────────────────────────────────────
def login_page(error: str = "") -> HTMLResponse:
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


def login_submit(request: Request, username: str, password: str) -> RedirectResponse:
    ip = request.client.host

    # Check rate limit first
    err = _check_rate_limit(ip)
    if err:
        return RedirectResponse(f"/login?error={err.replace(' ', '+')}", status_code=303)

    if not APP_PASSWORD_HASH:
        return RedirectResponse(
            "/login?error=APP_PASSWORD_HASH+not+set.+Run+setup_password.py",
            status_code=303,
        )

    valid = (username == APP_USERNAME
             and bcrypt.checkpw(password.encode(), APP_PASSWORD_HASH.encode()))
    if not valid:
        _record_failure(ip)
        record = _login_attempts[ip]
        remaining = _MAX_ATTEMPTS - record["count"]
        if remaining > 0:
            msg = f"Invalid+username+or+password.+{remaining}+attempt(s)+remaining."
        else:
            msg = f"Too+many+failed+attempts.+Try+again+in+{_LOCKOUT_MINS}+minutes."
        return RedirectResponse(f"/login?error={msg}", status_code=303)

    _record_success(ip)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(COOKIE_NAME, make_session(), httponly=True, samesite="lax", max_age=SESSION_MAX_AGE)
    return resp


def logout() -> RedirectResponse:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ── Zerodha OAuth ─────────────────────────────────────────────────────────────
def kite_login_redirect() -> RedirectResponse:
    return RedirectResponse(kite.login_url())


def kite_callback(request_token: str, status: str) -> HTMLResponse:
    if status != "success":
        raise HTTPException(status_code=400, detail=f"Zerodha login failed: {status}")
    try:
        data = kite.generate_session(request_token, api_secret=KITE_API_SECRET)
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


def auth_status() -> dict:
    return {
        "tokenSet":   bool(kite_state["access_token"]),
        "tokenSetAt": kite_state["set_at"],
        "message":    "Zerodha connected. Ready to trade."
                      if kite_state["access_token"]
                      else "Zerodha not connected. Click 'Connect Zerodha'.",
    }
