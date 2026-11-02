import logging
from fastapi import HTTPException
from app.config import kite, kite_state

logger = logging.getLogger(__name__)


def place_order(trade: dict) -> dict:
    if not kite_state["access_token"]:
        raise HTTPException(
            status_code=401,
            detail="Zerodha not connected. Click 'Connect Zerodha' first.",
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
        # BUY SL: limit above trigger; SELL SL: limit below trigger
        buf = 1.002 if trade["action"] == "BUY" else 0.998
        order_params["price"] = round(trade["entryPrice"] * buf, 1)
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

    result: dict = {"orderId": order_id}

    if (trade.get("product") == "CNC"
            and trade.get("stopLossPrice")
            and trade.get("targetPrice")):
        result["gttId"] = _place_gtt_oco(trade)

    return result


def _place_gtt_oco(trade: dict) -> int:
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
