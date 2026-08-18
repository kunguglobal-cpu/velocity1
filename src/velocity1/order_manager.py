import asyncio
import logging
import math
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)


class OrderManager:
    def __init__(self, connector, symbol: str):
        self.connector = connector
        self.symbol = symbol
        self.active_position = None  # shape: dict returned from place_market_order or get_open_positions
        self._tick_task: Optional[asyncio.Task] = None
        self._subscribed = False

    async def start(self):
        # subscribe to ticks if not already
        if not self._subscribed:
            await self.connector.subscribe_ticks(self.symbol, self._on_tick)
            # subscribe to position updates to detect closes
            await self.connector.subscribe_positions(self._on_position_update)
            self._subscribed = True

    async def _on_tick(self, symbol: str, price: float, raw_event):
        # called on each price update; update trailing stop if we have an active position
        try:
            await self._update_trailing(price)
        except Exception:
            logger.exception("Error in tick handler")

    async def _on_position_update(self, position: dict):
        """Called on every position update from MetaApi websocket. Detects closed positions and triggers flip."""
        try:
            # MetaApi position object may include 'id', 'symbol', 'volume', 'side', 'state', 'closePrice'
            pos_id = position.get("id") or position.get("positionId")
            state = position.get("state") or position.get("status")
            # If position was closed and matches our active_position id, handle flip
            if state and str(state).lower() in ("closed", "filled") and self.active_position and pos_id == self.active_position.get("id"):
                # Build closed_position dict
                closed = {
                    "id": pos_id,
                    "direction": position.get("direction") or position.get("side") or self.active_position.get("direction"),
                    "volume": float(position.get("volume") or position.get("lots") or self.active_position.get("volume", 0.0)),
                    "close_price": float(position.get("closePrice") or position.get("price") or 0.0),
                    "profit": float(position.get("profit") or 0.0)
                }
                # clear active_position before flipping to avoid re-entrancy
                self.active_position = None
                await self.handle_position_closed(closed)
            # Also update local active position SL/entry price if updated
            if self.active_position and pos_id == self.active_position.get("id"):
                # update fields
                self.active_position.update({
                    "sl": position.get("sl") or self.active_position.get("sl"),
                    "entry_price": position.get("open_price") or self.active_position.get("entry_price")
                })
        except Exception:
            logger.exception("Error in position update handler")

    async def _update_trailing(self, price: float):
        if not self.active_position:
            return
        symbol_info = await self.connector.get_symbol_info(self.symbol)
        point = float(symbol_info["point"])
        trailing_distance = settings.trailing_stop_pips * point
        pos = self.active_position
        direction = pos.get("direction") or pos.get("orderDirection") or ("buy" if pos.get("side") == "buy" else "sell")
        position_id = pos.get("id") or pos.get("positionId") or pos.get("orderId")
        if not position_id:
            return

        if direction.lower() == "buy":
            new_sl = price - trailing_distance
            current_sl = float(pos.get("sl") or 0.0)
            # only move SL up (towards price)
            if new_sl > current_sl:
                logger.info("Updating SL for position %s to %s (price %s)", position_id, new_sl, price)
                await self.connector.modify_position_sl(position_id, new_sl)
                pos["sl"] = new_sl
        else:
            new_sl = price + trailing_distance
            current_sl = float(pos.get("sl") or 0.0)
            # only move SL down (towards price)
            if current_sl == 0.0 or new_sl < current_sl:
                logger.info("Updating SL for position %s to %s (price %s)", position_id, new_sl, price)
                await self.connector.modify_position_sl(position_id, new_sl)
                pos["sl"] = new_sl

    async def enter_market(self, direction: str, volume: float):
        # place market order and set initial SL at trailing distance behind current price
        symbol_info = await self.connector.get_symbol_info(self.symbol)
        point = float(symbol_info["point"])
        trailing_distance = settings.trailing_stop_pips * point

        # place market order
        order = await self.connector.place_market_order(self.symbol, volume, direction)
        # parse order/position data into active_position
        position = None
        if isinstance(order, dict):
            position = order.get("position") or order.get("order") or order
        position_id = None
        entry_price = None
        if position:
            position_id = position.get("id") or position.get("positionId") or position.get("orderId")
            entry_price = float(position.get("open_price") or position.get("price") or position.get("entry_price") or 0.0)
        # fallback: try to read last price from tick cache
        last_price = self.connector.get_last_price(self.symbol)
        if entry_price is None or entry_price == 0.0:
            entry_price = float(last_price) if last_price is not None else 0.0

        # set active_position structure
        self.active_position = {
            "id": position_id or (order.get("id") if isinstance(order, dict) else None),
            "direction": direction,
            "volume": volume,
            "entry_price": entry_price,
            "sl": 0.0,
        }

        # set initial SL based on entry_price and trailing_distance (if we have entry_price)
        if entry_price and entry_price > 0:
            if direction.lower() == "buy":
                sl = entry_price - trailing_distance
            else:
                sl = entry_price + trailing_distance
            try:
                if self.active_position.get("id"):
                    await self.connector.modify_position_sl(self.active_position.get("id"), sl)
                    self.active_position["sl"] = sl
            except Exception:
                logger.exception("Failed to set initial SL after market order")

        # ensure we are subscribed to ticks and positions
        if not self._subscribed:
            await self.start()
        return order

    async def handle_position_closed(self, closed_position: dict):
        # called when a position is observed closed (e.g., event listener)
        logger.info("Position closed: %s", closed_position)
        # compute closed notional and determine opposite
        symbol_info = await self.connector.get_symbol_info(self.symbol)
        contract_size = symbol_info["contract_size"]
        close_price = float(closed_position.get("close_price") or closed_position.get("price") or 0.0)
        closed_volume = float(closed_position.get("volume") or closed_position.get("lots") or 0.0)
        closed_direction = closed_position.get("direction") or closed_position.get("side") or self.active_position.get("direction") if self.active_position else closed_position.get("direction")
        closed_notional = closed_volume * contract_size * close_price

        # compute allowed notional (min of closed notional and max per trade)
        balance = await self.connector.get_balance()
        max_trade_notional = balance * (settings.max_trade_exposure_pct / 100.0)
        max_total_notional = balance * (settings.max_open_exposure_pct / 100.0)

        notional = min(closed_notional, max_trade_notional)

        # compute current open exposure and ensure total <= max_total_notional
        open_positions = await self.connector.get_open_positions(self.symbol)
        current_open = 0.0
        for p in open_positions:
            vol = float(p.get("volume") or p.get("lots") or 0.0)
            price = float(p.get("price") or p.get("open_price") or p.get("close_price") or 0.0)
            current_open += vol * contract_size * price
        # subtract closed position notional since it's closed
        # ensure new notional doesn't make total exceed max_total_notional
        available = max_total_notional - current_open
        notional = min(notional, max(0.0, available))

        # compute volume from notional
        from .risk import compute_trade_volume

        volume = compute_trade_volume(notional, close_price, contract_size, symbol_info["lot_step"], symbol_info["min_lot"])
        if volume <= 0:
            logger.warning("Computed volume is zero after flip sizing; not opening opposite")
            self.active_position = None
            return

        opposite = "sell" if closed_direction == "buy" else "buy"
        # place opposite market order
        await self.enter_market(opposite, volume)

    async def close_active(self):
        if not self.active_position:
            return
        await self.connector.close_position(self.active_position.get("id"))
        self.active_position = None
