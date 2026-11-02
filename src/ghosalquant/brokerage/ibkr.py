from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
import hashlib
import logging

from ib_insync import (
    IB,
    Option,
    Stock,
    MarketOrder,
    LimitOrder,
    StopOrder,
    StopLimitOrder,
    Trade as IBTrade,
    Fill as IBFill,
    Contract
)

from .broker_base import (
    Broker,
    cancels_remainder_after_first_fill,
    order_expires_at,
    order_liquidity,
    order_slot,
)
from ..core import metadata_keys
from ..core.contracts import Contract as TradedContract, ContractRegistry, Right
from ..core.types import Fill, Liquidity, MarketEvent, OpenOrderView, Order, SignalType
from ..state.json_store import StateCodec, VersionedJsonStateStore
from ..util.config import BrokerMode

logger = logging.getLogger(__name__)


class IBKRBroker(Broker):
    """
    Live and paper trading through Interactive Brokers.

    Resting orders are reported and cancellable (``OpenOrderQueries``), read from what IB says it
    is working rather than from this process's memory, so an order placed before a restart is
    still visible. The exposure and cash-allocation risk rules need that: they measure committed
    capital from ``RiskContext.open_orders``, which stays empty for a broker that cannot report.

    Executions are also deduplicated by ``execId`` here. ``ib_insync`` already does this inside
    its wrapper, so this is a second line of defence over behaviour that is not part of its
    documented contract, not a fix for anything observed.

    With ``state_path`` set the broker survives a restart in two ways. Submitted orders are kept,
    so a resting order still reports the metadata its strategy set rather than a reconstruction —
    without it the cash-allocation buckets and ``order_slot`` matching quietly stop working. And
    an order is written down *before* it is sent, so an acknowledgement lost to a timeout leaves a
    record behind: ``indeterminate_orders()`` reports it on the next start instead of the order
    being forgotten or blindly sent again.

    Orders it placed follow a simulated broker's rules. A time to live, an event age or a first
    fill that asks for it each end in a cancel at IB, and a taker goes out immediate-or-cancel.
    """

    def __init__(
            self,
            host: str = "127.0.0.1",
            port: int = 7497,
            client_id: int = 1,
            mode: BrokerMode = BrokerMode.PAPER,
            connect_timeout_s: float = 5.0,
            handshake_timeout_s: float = 5.0,
            # Default to sending stock order to SMART
            contract_overrides: Iterable[Contract] | None = None,
            state_path: str | Path | None = None,
            contracts: ContractRegistry | None = None,
            max_order_age_seconds: float | None = None,
            max_order_age_events: int | None = None,
    ) -> None:
        super().__init__()
        if max_order_age_seconds is not None and max_order_age_seconds <= 0:
            raise ValueError("max_order_age_seconds must be > 0 when set")
        if max_order_age_events is not None and max_order_age_events <= 0:
            raise ValueError("max_order_age_events must be > 0 when set")

        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self.mode = mode
        self.connect_timeout_s = float(connect_timeout_s)
        self.handshake_timeout_s = float(handshake_timeout_s)

        # lookup for contract overrides
        self.contract_map = {c.symbol: c for c in (contract_overrides or [])}
        self.contracts = contracts if contracts is not None else ContractRegistry()
        self.max_order_age_seconds = max_order_age_seconds
        self.max_order_age_events = max_order_age_events

        self._store = (
            None if state_path is None
            else VersionedJsonStateStore(
                state_path, codec=StateCodec(to_dict=dict, from_dict=dict), schema_version=1
            )
        )
        self._seen_exec_ids: set[str] = set()
        self._orders_by_id: dict[str, Order] = {}
        self._cancels_sent: set[str] = set()
        self._event_ages: dict[str, int] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._fill_queue: list[Fill] = []
        self._events_wired = False
        self._restore()

    # ----- Managing Connection ----- #
    async def connect(self) -> None:
        if self.ib.isConnected():
            return

        await self.ib.connectAsync(
            host=self.host,
            port=self.port,
            clientId=self.client_id
        )
        # A cancel sent on an earlier connection may never have arrived.
        self._cancels_sent.clear()

        if not self._events_wired:
            self.ib.execDetailsEvent += self._on_exec_details
            self._events_wired = True

    async def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    # ----- Execution ----- #
    def submit(self, order: Order) -> str | None:
        if not self.ib.isConnected():
            raise RuntimeError("IBKRBroker is not connected. Call connect() before submitting orders.")

        contract = self.contract_map.get(
            order.symbol,
            ib_contract(self.contracts.get(order.symbol)),
        )
        action = "BUY" if order.side is SignalType.BUY else "SELL"
        limit = order.declared_limit_price
        # A taker never rests, so IB cancels whatever it cannot fill at once.
        tif = "IOC" if order_liquidity(order) is Liquidity.TAKER else ""
        if limit is None:
            ib_order = MarketOrder(action, float(order.quantity), tif=tif)
        else:
            ib_order = LimitOrder(action, float(order.quantity), float(limit), tif=tif)

        # Written down before the network call: a lost acknowledgement then leaves a known
        # unknown rather than a forgotten order.
        key = _idempotency_key(order)
        self._pending[key] = _as_payload(order)
        self._save()

        try:
            trade: IBTrade = self.ib.placeOrder(contract, ib_order)
        except Exception:
            # The venue may or may not have it. Leave the record for the next start to report.
            logger.exception("IBKRBroker could not confirm submission of %s", order.symbol)
            raise

        order_id = getattr(trade.order, "orderId", None)
        self._pending.pop(key, None)
        if not order_id:
            self._save()
            return None
        broker_order_id = str(order_id)
        # Kept so a resting order reports the metadata the strategy set, not a reconstruction.
        self._orders_by_id[broker_order_id] = order
        self._save()
        return broker_order_id

    def indeterminate_orders(self) -> tuple[Order, ...]:
        """
        Orders written down but never acknowledged, from this run or a previous one.

        Each may or may not be live at the venue. Resolve them against ``open_order_views`` and
        the account's executions before trading; resending blind is how one order becomes two.
        """
        return tuple(_from_payload(payload) for payload in self._pending.values())

    def account_snapshot(self) -> tuple[float, dict[str, tuple[float, float]]]:
        """Cash and open positions as IB reports them, shaped for ``BasicPortfolio.adopt``."""
        cash = 0.0
        for value in self.ib.accountValues():
            if getattr(value, "tag", None) == "TotalCashValue":
                cash = float(getattr(value, "value", 0.0) or 0.0)
                break
        positions = {}
        for position in self.ib.positions():
            quantity = float(getattr(position, "position", 0.0) or 0.0)
            if quantity == 0.0:
                continue
            symbol = ib_symbol(getattr(position, "contract", None))
            # IB reports avgCost per contract, so an option's already carries its multiplier.
            avg_cost = float(getattr(position, "avgCost", 0.0) or 0.0)
            positions[symbol] = (quantity, avg_cost / self.contracts.get(symbol).multiplier)
        return cash, positions

    def get_fills(self) -> list[Fill]:
        fills, self._fill_queue = self._fill_queue, []
        return fills

    # ----- OpenOrderQueries ----- #
    def open_order_views(self, symbol: str | None = None) -> tuple[OpenOrderView, ...]:
        """Resting orders as IB reports them, which includes any placed before this process started."""
        views = []
        for trade in self.ib.openTrades():
            contract_symbol = ib_symbol(trade.contract)
            if symbol is not None and contract_symbol != symbol:
                continue
            remaining = float(trade.remaining())
            if remaining <= 0.0:
                continue
            order = self._order_for(trade, contract_symbol, remaining)
            views.append(
                OpenOrderView(
                    broker_order_id=_order_id(trade),
                    order=order,
                    remaining_quantity=remaining,
                    submitted_at=_trade_time(trade),
                    limit_price=_limit_price(trade),
                    liquidity=order_liquidity(order),
                )
            )
        return tuple(views)

    def cancel_open_orders(
            self,
            symbol: str,
            side: SignalType | None = None,
            order_slots: Iterable[str] | None = None,
    ) -> int:
        slots = None if order_slots is None else set(order_slots)
        cancelled = 0
        for trade in self.ib.openTrades():
            remaining = float(trade.remaining())
            if ib_symbol(trade.contract) != symbol or remaining <= 0.0:
                continue
            if side is not None and _side_of(getattr(trade.order, "action", "")) is not side:
                continue
            if slots is not None and order_slot(self._order_for(trade, symbol, remaining)) not in slots:
                continue
            if self._cancel(trade):
                cancelled += 1
        return cancelled

    # ----- Order lifecycle ----- #
    def on_market_event(self, event: MarketEvent) -> MarketEvent:
        if self.ib.isConnected():
            # Also catches a part fill that happened while this process was down.
            self._cancel_remainders(event.symbol)
            self._age_orders(event.symbol)
        return super().on_market_event(event)

    def next_order_expiry_at(self) -> datetime | None:
        return min((expires_at for _, _, expires_at in self._expiring_trades()), default=None)

    def expire_orders(self, *, now: datetime) -> list[str]:
        """
        Cancel this broker's orders past their time to live, returning the ids expired.

        IB confirms a cancel later, so an expired order stays in ``open_order_views`` until it
        does, and a fill already on its way still arrives.
        """
        expired = []
        for order_id, trade, expires_at in self._expiring_trades():
            if now >= expires_at:
                self._cancel(trade)
                expired.append(order_id)
        return expired

    # ----- Helpers ----- #
    def _on_exec_details(self, _trade: IBTrade, fill: IBFill) -> None:
        """
        Callback for execDetailsEvent, when a fill occurs.
        """
        execution = fill.execution
        contract = fill.contract

        exec_id = str(getattr(execution, "execId", "") or "")
        if exec_id and exec_id in self._seen_exec_ids:
            logger.debug("IBKRBroker ignoring execution %s: already seen", exec_id)
            return
        if exec_id:
            self._seen_exec_ids.add(exec_id)

        side = SignalType.BUY if execution.side == "BOT" else SignalType.SELL
        symbol = ib_symbol(contract)

        self._fill_queue.append(
            Fill(
                time_stamp=fill.time if isinstance(fill.time, datetime) else datetime.now(timezone.utc),
                symbol=symbol,
                side=side,
                quantity=float(getattr(execution, "shares", 0.0)),
                price=float(getattr(execution, "price", 0.0)),
                broker_order_id=str(getattr(execution, "orderId", "")) or None,
                execution_id=exec_id or None,
                commission=_commission(fill),
                liquidity=_realised_liquidity(execution),
            )
        )
        self._cancel_remainders(symbol)

    def _cancel(self, trade: IBTrade) -> bool:
        """Cancel once per connection: ib_insync takes any error from IB as the order being cancelled."""
        order_id = _order_id(trade)
        if order_id in self._cancels_sent:
            return False
        self.ib.cancelOrder(trade.order)
        if order_id:
            self._cancels_sent.add(order_id)
        return True

    def _own_working_trades(self, symbol: str | None = None) -> Iterator[tuple[str, IBTrade, Order]]:
        """Open orders this broker placed. Nothing else on the account is its to cancel."""
        for trade in self.ib.openTrades():
            contract_symbol = ib_symbol(trade.contract)
            if symbol is not None and contract_symbol != symbol:
                continue
            order_id = _order_id(trade)
            order = self._orders_by_id.get(order_id)
            if order is None or float(trade.remaining()) <= 0.0 or not _describes(order, trade, contract_symbol):
                continue
            yield order_id, trade, order

    def _expiring_trades(self) -> list[tuple[str, IBTrade, datetime]]:
        if not self.ib.isConnected():
            return []
        expiring = []
        for order_id, trade, order in self._own_working_trades():
            expires_at = order_expires_at(order, self.max_order_age_seconds)
            if expires_at is not None and order_id not in self._cancels_sent:
                expiring.append((order_id, trade, expires_at))
        return expiring

    def _cancel_remainders(self, symbol: str) -> None:
        for _, trade, order in self._own_working_trades(symbol):
            if cancels_remainder_after_first_fill(order) and _partly_filled(trade):
                self._cancel(trade)

    def _age_orders(self, symbol: str) -> None:
        if self.max_order_age_events is None:
            return
        for order_id, trade, _ in self._own_working_trades(symbol):
            self._event_ages[order_id] = self._event_ages.get(order_id, 0) + 1
            if self._event_ages[order_id] >= self.max_order_age_events:
                self._cancel(trade)

    def _restore(self) -> None:
        if self._store is None:
            return
        stored = self._store.load() or {}
        self._pending = {
            str(key): dict(payload) for key, payload in (stored.get("pending") or {}).items()
        }
        self._orders_by_id = {
            str(order_id): _from_payload(payload)
            for order_id, payload in (stored.get("orders") or {}).items()
        }

    def _save(self) -> None:
        if self._store is None:
            return
        self._store.save({
            "pending": self._pending,
            "orders": {
                order_id: _as_payload(order) for order_id, order in self._orders_by_id.items()
            },
        })

    def _order_for(self, trade: IBTrade, symbol: str, remaining: float) -> Order:
        """The order we submitted, or one rebuilt from IB for anything this process did not place."""
        known = self._orders_by_id.get(_order_id(trade))
        # IB order ids restart with the session, so only trust a stored one that still describes
        # what the venue reports.
        if known is not None and _describes(known, trade, symbol):
            return known
        limit = _limit_price(trade)
        return Order(
            time_stamp=_trade_time(trade),
            symbol=symbol,
            side=_side_of(getattr(trade.order, "action", "BUY")),
            quantity=float(getattr(trade.order, "totalQuantity", remaining) or remaining),
            ref_price=limit if limit else 0.0,
            limit_price=limit,
        )


def _order_id(trade: IBTrade) -> str:
    return str(getattr(trade.order, "orderId", "") or "")


def _partly_filled(trade: IBTrade) -> bool:
    # After a restart IB may report the fill on the order status without its executions.
    status = getattr(trade, "orderStatus", None)
    return float(trade.filled()) > 0.0 or float(getattr(status, "filled", 0.0) or 0.0) > 0.0


def _describes(order: Order, trade: IBTrade, symbol: str) -> bool:
    return (
        order.symbol == symbol
        and order.side is _side_of(getattr(trade.order, "action", "BUY"))
        and abs(float(order.quantity) - float(getattr(trade.order, "totalQuantity", 0.0) or 0.0)) < 1e-9
    )


def _idempotency_key(order: Order) -> str:
    key = (order.metadata or {}).get(metadata_keys.IDEMPOTENCY_KEY)
    if key is not None:
        return str(key)
    return hashlib.sha256(
        f"{order.time_stamp.isoformat()}\0{order.symbol}\0{order.side.value}\0"
        f"{order.quantity}\0{order.declared_limit_price}".encode("utf-8")
    ).hexdigest()


def _as_payload(order: Order) -> dict[str, Any]:
    return {
        "time_stamp": order.time_stamp.isoformat(),
        "symbol": order.symbol,
        "side": order.side.value,
        "quantity": float(order.quantity),
        "ref_price": float(order.ref_price),
        "metadata": dict(order.metadata or {}),
        "limit_price": order.limit_price,
        "liquidity": None if order.liquidity is None else order.liquidity.value,
    }


def _from_payload(payload: Mapping[str, Any]) -> Order:
    liquidity = payload.get("liquidity")
    return Order(
        time_stamp=datetime.fromisoformat(str(payload["time_stamp"])),
        symbol=str(payload["symbol"]),
        side=SignalType(str(payload["side"])),
        quantity=float(payload["quantity"]),
        ref_price=float(payload["ref_price"]),
        metadata=dict(payload.get("metadata") or {}),
        limit_price=payload.get("limit_price"),
        liquidity=None if liquidity is None else Liquidity(str(liquidity)),
    )


def ib_contract(contract: TradedContract) -> Contract:
    """The IBKR contract for one of ours: an option when it has a right, a share otherwise."""
    if contract.right is None or contract.expiry is None or contract.strike is None:
        return Stock(contract.underlier, "SMART", "USD")
    return Option(
        contract.underlier,
        contract.expiry.strftime("%Y%m%d"),
        float(contract.strike),
        "C" if contract.right is Right.CALL else "P",
        "SMART",
        multiplier=str(int(contract.multiplier)),
        currency="USD",
    )


def ib_symbol(contract: Contract) -> str:
    """
    What we call an IBKR contract: an option's local symbol, which IB gives in the OCC form the
    registry is keyed by, and anything else's ticker. ``symbol`` on an option is its underlier.
    """
    if getattr(contract, "secType", None) == "OPT":
        local = str(getattr(contract, "localSymbol", "") or "").strip()
        if local:
            return local
    return getattr(contract, "symbol", None) or "UNKNOWN"


def _commission(fill: IBFill) -> float | None:
    report = getattr(fill, "commissionReport", None)
    commission = getattr(report, "commission", None)
    return None if commission is None else float(commission)


def _realised_liquidity(execution: Any) -> Liquidity | None:
    # IB's lastLiquidity: 1 added, 2 removed, 3 routed out to another venue.
    return {1: Liquidity.MAKER, 2: Liquidity.TAKER, 3: Liquidity.TAKER}.get(getattr(execution, "lastLiquidity", 0))


def _limit_price(trade: IBTrade) -> float | None:
    limit = getattr(trade.order, "lmtPrice", None)
    return float(limit) if limit else None


def _side_of(action: str) -> SignalType:
    return SignalType.SELL if str(action).upper() == "SELL" else SignalType.BUY


def _trade_time(trade: IBTrade) -> datetime:
    log = getattr(trade, "log", None) or ()
    stamp = getattr(log[0], "time", None) if log else None
    return stamp if isinstance(stamp, datetime) else datetime.now(timezone.utc)
