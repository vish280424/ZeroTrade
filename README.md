# ZeroTrade

A self-hosted web app that parses plain-English trading tips and executes orders on Zerodha via Kite Connect API.

Paste a tip like "Buy RELIANCE above 2450, SL 2420, target 2500" — the app parses it, shows you a confirmation table, and places the order on Zerodha when you confirm.

---

## How Parsing Works

The app uses regex rules (no AI/LLM) to extract fields from your tip text:

| Field | Triggers | Example |
|---|---|---|
| Action | `buy`, `long`, `sell`, `short` | "**Buy** INFY..." |
| Symbol | ALL-CAPS token or company name lookup | "**RELIANCE**" or "**Zydus Lifesciences**" |
| Entry price | number after `above`, `below`, `at`, `cmp` | "above **2450**" |
| Stop loss | number after `sl`, `stoploss`, `stop-loss` | "SL **2420**" |
| Target | number after `target`, `tgt`, `t1`, `tp` | "target **2500**" |
| Quantity | number after `qty`, `quantity`, `shares`, `lots` | "qty **10**" (or use the UI input) |
| Product | `swing`, `delivery`, `cnc`, `positional`, `hold`, `days`, `weeks` → CNC; else MIS | "swing trade" |
| Exchange | `BSE` keyword → BSE; else NSE | "BSE" |

**Order type** is derived automatically:
- Entry + SL both present → **SL order** (triggers at entry price, limit buffer +0.2%)
- Entry only → **LIMIT order**
- Neither → **MARKET order**

**CNC trades** automatically get a **GTT OCO** (Good Till Triggered, One Cancels Other) placed after the entry — exits at SL or target automatically over multiple days.

---

## Tip Examples

---

### 1. Basic intraday buy with SL and target

```
Buy RELIANCE above 2450, SL 2420, target 2500
```

**Parsed:**
- Symbol: `RELIANCE`
- Action: `BUY`
- Entry: ₹2450 (triggers when price rises to 2450)
- Stop loss: ₹2420
- Target: ₹2500
- Product: `MIS` (intraday — auto square-off at day end)
- Order type: `SL` (limit price set to ₹2454.9, trigger at ₹2450)

**Executes:** SL-buy order on NSE. Position auto-closes at end of day if not triggered.

---

### 2. Intraday sell / short

```
Short HDFCBANK below 1650, SL 1670, target 1610
```

**Parsed:**
- Symbol: `HDFCBANK`
- Action: `SELL`
- Entry: ₹1650 (triggers when price falls to 1650)
- Stop loss: ₹1670
- Target: ₹1610
- Product: `MIS`
- Order type: `SL`

**Executes:** SL-sell order on NSE. For intraday short — Zerodha auto squares off at day end.

---

### 3. Buy at current market price

```
Buy WIPRO CMP, SL 480, target 510
```

**Parsed:**
- Symbol: `WIPRO`
- Action: `BUY`
- Entry: `null` (no price after CMP)
- Stop loss: ₹480
- Target: ₹510
- Product: `MIS`
- Order type: `MARKET`

**Executes:** Market order — buys immediately at best available price.

---

### 4. Limit buy (no SL in tip)

```
Buy INFY at 1800
```

**Parsed:**
- Symbol: `INFY`
- Action: `BUY`
- Entry: ₹1800
- Stop loss: `—`
- Target: `—`
- Product: `MIS`
- Order type: `LIMIT`

**Executes:** Limit order at ₹1800. No GTT placed (no SL/target given).

---

### 5. Swing / delivery trade with GTT OCO

```
Buy ZYDUS LIFESCIENCES above 1248, SL 1240, target 1340, swing trade
```

**Parsed:**
- Symbol: `ZYDUSLIFE` (via company name lookup)
- Action: `BUY`
- Entry: ₹1248
- Stop loss: ₹1240
- Target: ₹1340
- Product: `CNC` (delivery — triggered by "swing trade")
- Order type: `SL`

**Executes:**
1. SL-buy CNC entry order at ₹1248
2. GTT OCO placed automatically:
   - Leg 1: Sell at ₹1240 if price drops (stop loss)
   - Leg 2: Sell at ₹1340 if price rises (target)
   - Whichever hits first, the other is cancelled

Position can be held for days/weeks. No manual exit needed.

---

### 6. Delivery trade with just keyword "delivery"

```
Buy TATASTEEL 145, SL 140, TGT 158, delivery
```

**Parsed:**
- Symbol: `TATASTEEL`
- Action: `BUY`
- Entry: ₹145
- Stop loss: ₹140
- Target: ₹158
- Product: `CNC` (triggered by "delivery")
- Order type: `SL`

**Executes:** CNC SL-buy + GTT OCO for automated exit.

---

### 7. Specifying quantity in tip text

```
SELL INFY 1800 SL 1820 TGT 1750 qty 10
```

**Parsed:**
- Symbol: `INFY`
- Action: `SELL`
- Entry: ₹1800
- Stop loss: ₹1820
- Target: ₹1750
- Quantity: `10` (from `qty 10`)
- Product: `MIS`
- Order type: `SL`

**Note:** If you also set a quantity in the UI input field, the UI value overrides the tip text value.

---

### 8. BSE exchange

```
Buy TATAMOTORS above 950, SL 930, target 980, BSE
```

**Parsed:**
- Symbol: `TATAMOTORS`
- Exchange: `BSE` (triggered by "BSE" keyword)
- Everything else as normal

**Executes:** Order placed on BSE instead of NSE.

---

### 9. Company full name (lookup table)

These full names are automatically mapped to their NSE ticker:

| You type | Resolved to |
|---|---|
| Zydus Lifesciences | ZYDUSLIFE |
| Sun Pharma | SUNPHARMA |
| HDFC Bank | HDFCBANK |
| Reliance Industries | RELIANCE |
| Hindustan Unilever | HINDUNILVR |
| Tata Motors | TATAMOTORS |
| Bajaj Finance | BAJFINANCE |
| Tech Mahindra | TECHM |

If the name is not in the lookup table, the parser falls back to the first ALL-CAPS token in the tip.

---

### 10. CNC with "hold for days" phrasing

```
Buy SUNPHARMA above 1700 SL 1680 target 1780 hold for 3 days
```

**Parsed:**
- Symbol: `SUNPHARMA`
- Product: `CNC` (triggered by "days")
- Entry: ₹1700, SL: ₹1680, Target: ₹1780

**Executes:** CNC entry + GTT OCO for automated multi-day exit.

---

## Order Type Reference

| Scenario | Order placed |
|---|---|
| Entry + SL given | SL order (trigger = entry, limit = entry + 0.2%) |
| Entry only | LIMIT order |
| No entry | MARKET order |
| CNC + SL + target | Entry order + GTT OCO |
| MIS any type | Entry order only (no GTT) |

---

## Setup

### Prerequisites
- Python 3.11+
- Zerodha Kite Connect subscription (~₹2000/year)
- Kite Connect app created at [developers.kite.trade](https://developers.kite.trade)

### Local setup

```bash
# 1. Clone and install
git clone https://github.com/yourname/ZeroTrade.git
cd ZeroTrade
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env — fill in KITE_API_KEY, KITE_API_SECRET

# 3. Set app password
python setup_password.py
# Paste the output hash into .env as APP_PASSWORD_HASH

# 4. Run
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Open [http://localhost:8080](http://localhost:8080)

### Deploy to AWS EC2 (for static IP + Zerodha whitelist)

```bash
# From your local machine — copy .env to server
scp .env ubuntu@<your-ec2-ip>:~/ZeroTrade/.env

# On the server
bash setup_server.sh
```

See `setup_server.sh` for full details.

---

## Project Structure

```
ZeroTrade/
├── main.py              # FastAPI app — parsing, order placement, auth
├── setup_password.py    # One-time script to generate APP_PASSWORD_HASH
├── setup_server.sh      # Server deployment script (AWS/Ubuntu)
├── requirements.txt
├── .env.example         # Template — copy to .env and fill in values
├── .env                 # Your secrets — never committed to git
└── static/
    └── index.html       # Single-page UI
```
