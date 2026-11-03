import os
import re
import logging
from typing import Optional
from fastapi import HTTPException
from app.config import DEFAULT_EXCHANGE, DEFAULT_QUANTITY

logger = logging.getLogger(__name__)

# ── Company name → NSE ticker lookup ─────────────────────────────────────────
COMPANY_LOOKUP: dict[str, str] = {
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
    "RELIANCE POWER":     "RPOWER",
    "RELIANCE INFRA":     "RELINFRA",
    "RELIANCE CAPITAL":   "RELCAPITAL",
    "RELIANCE INDUSTRIES":"RELIANCE",
    "RELIANCE":           "RELIANCE",
    "ONGC":               "ONGC",
    "NTPC":               "NTPC",
    "POWER GRID":         "POWERGRID",
    "BHEL":               "BHEL",
    "COAL INDIA":         "COALINDIA",
    "ADANI PORTS":        "ADANIPORTS",
    "ADANI ENTERPRISES":  "ADANIENT",
    "ADANI GREEN":        "ADANIGREEN",
    "ADANI":              "ADANIENT",
    # FMCG
    "HINDUSTAN UNILEVER": "HINDUNILVR",
    "HUL":                "HINDUNILVR",
    "ITC HOTELS":         "ITCHOTELS",
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
    "HDFC BANK":          "HDFCBANK",
    "HDFC":               "HDFCBANK",
    "SBI LIFE":           "SBILIFE",
    "TITAN":              "TITAN",
    "ASIAN PAINTS":       "ASIANPAINT",
    "ULTRATECH":          "ULTRACEMCO",
    "GRASIM":             "GRASIM",
    "HINDALCO":           "HINDALCO",
    "TATA STEEL":         "TATASTEEL",
    "TATA CONSULTANCY":   "TCS",
    "JSW STEEL":          "JSWSTEEL",
    "INDIGO":             "INDIGO",
    "INTERGLOBE":         "INDIGO",
}

_KEYWORDS: frozenset[str] = frozenset({
    "BUY", "SELL", "SHORT", "LONG", "ABOVE", "BELOW", "AT",
    "CMP", "SL", "TGT", "TARGET", "STOPLOSS", "QTY", "QUANTITY",
    "NSE", "BSE", "MIS", "CNC", "SWING", "DELIVERY", "INTRADAY",
    "POSITIONAL", "T1", "T2", "TP", "CALL", "PUT", "ENTRY",
    "BREAKOUT", "BREAKDOWN", "STRONG", "GOOD", "STOP", "LOSS",
    "HOLD", "DAYS", "WEEKS", "TERM", "TRADE",
})

_NUM = r"[\d,]+(?:\.\d+)?"

def _to_float(s: str) -> float:
    return float(s.replace(",", ""))

def parse_tip(tip_text: str, quantity_override: Optional[int] = None) -> dict:
    text  = tip_text.strip()
    upper = text.upper()

    # ── Action ────────────────────────────────────────────────────────────────
    upper_no_shortterm = re.sub(r"\bSHORT[\s\-]TERM\b", "", upper)
    if re.search(r"\b(SHORT|SELL)\b", upper_no_shortterm):
        action = "SELL"
    elif re.search(r"\b(BUY|LONG)\b", upper):
        action = "BUY"
    else:
        raise HTTPException(status_code=400, detail="Could not find BUY or SELL in tip.")

    # ── Symbol ────────────────────────────────────────────────────────────────
    symbol = None
    for name, ticker in sorted(COMPANY_LOOKUP.items(), key=lambda x: -len(x[0])):
        if re.search(r"(?<![A-Z])" + re.escape(name) + r"(?![A-Z])", upper):
            symbol = ticker
            break

    if not symbol:
        tokens = re.findall(r"[A-Z][A-Z0-9&\-]{1,}", upper)
        for tok in tokens:
            if tok not in _KEYWORDS:
                symbol = tok
                break

    if not symbol:
        raise HTTPException(status_code=400, detail="Could not extract a trading symbol from the tip.")

    # ── Entry price ───────────────────────────────────────────────────────────
    # Capture both the keyword and the price to know intent
    entry_match = re.search(rf"\b(above|below|at|cmp)\s+({_NUM})", text, re.IGNORECASE)
    entry_price   = _to_float(entry_match.group(2)) if entry_match else None
    entry_keyword = entry_match.group(1).lower() if entry_match else None

    # Fallback: bare number right after symbol (e.g. "SELL INFY 1800 SL 1820")
    if entry_price is None and symbol:
        bare_match = re.search(
            rf"\b{re.escape(symbol)}\s+({_NUM})\b", upper
        )
        if bare_match:
            entry_price   = _to_float(bare_match.group(1))
            entry_keyword = "at"  # treat as limit

    # ── Stop loss ─────────────────────────────────────────────────────────────
    sl_match = re.search(rf"\b(?:sl|stoploss|stop[-\s]?loss)\s*[:\-]?\s*({_NUM})", text, re.IGNORECASE)
    sl_price = _to_float(sl_match.group(1)) if sl_match else None

    # ── Target (T1 primary, T2 secondary) ────────────────────────────────────
    tgt_match = re.search(rf"\b(?:target|tgt|t1|tp)\s*[:\-]?\s*({_NUM})", text, re.IGNORECASE)
    tgt_price = _to_float(tgt_match.group(1)) if tgt_match else None

    t2_match  = re.search(rf"\bT2\s*[:\-]?\s*({_NUM})", text, re.IGNORECASE)
    t2_price  = _to_float(t2_match.group(1)) if t2_match else None

    # ── Quantity ──────────────────────────────────────────────────────────────
    qty_match = re.search(
        rf"\b(?:qty|quantity)\s*[:\-]?\s*(\d+)"
        rf"|\b(\d+)\s+(?:shares?|lots?)\b"
        rf"|\b(?:shares?|lots?)\s*[:\-]?\s*(\d+)"
        rf"|\b(?:buy|sell|short|long)\s+(\d+)\s+[A-Z]",  # BUY 10 TCS
        text, re.IGNORECASE,
    )
    if qty_match:
        raw = qty_match.group(1) or qty_match.group(2) or qty_match.group(3) or qty_match.group(4)
        parsed_qty = int(raw)
    else:
        parsed_qty = DEFAULT_QUANTITY
    quantity = quantity_override or parsed_qty

    # ── Exchange ──────────────────────────────────────────────────────────────
    exchange = "BSE" if re.search(r"\bBSE\b", upper) else DEFAULT_EXCHANGE

    # ── Product — "intraday" overrides everything; "short/long term" → CNC ───
    if re.search(r"\bintraday\b", text, re.IGNORECASE):
        product = "MIS"
    elif re.search(r"\b(swing|delivery|cnc|positional|hold|days|weeks|term)\b", text, re.IGNORECASE):
        product = "CNC"
    else:
        product = "MIS"

    # ── Order type ────────────────────────────────────────────────────────────
    # "above/below" = breakout → SL entry order (waits for price to reach entry)
    # "at/cmp" = buy now → LIMIT entry order (SL only goes into GTT for CNC)
    if entry_keyword in ("above", "below") and entry_price and sl_price:
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
        "entryKeyword":  entry_keyword,
        "stopLossPrice": sl_price,
        "targetPrice":   tgt_price,
        "target2Price":  t2_price,
        "quantity":      quantity,
        "product":       product,
        "orderType":     order_type,
        "rawTip":        tip_text,
    }
    logger.info(f"Parsed trade: {trade}")
    return trade
