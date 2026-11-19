"""Real LumiBot base classes; inspection views only, never a strategy runner."""
from datetime import datetime, timezone
from decimal import Decimal
import math

from personal_trading.tradelocker.runtime import require_safe_runtime
require_safe_runtime()

from lumibot.brokers.broker import Broker
from lumibot.data_sources.data_source import DataSource
from lumibot.entities import Asset, Bars, Position, Quote
import pandas as pd

from personal_trading.tradelocker.tl_candidate import DisarmedError, ReadOnlyError, quiet_call


def disabled(*args, **kwargs):
    raise DisarmedError('Execution, streaming and strategy valuation are disarmed')


class TradeLockerAsset(Asset):
    """An UNKNOWN broker instrument, not stock/forex or qualified gold economics.

    Broker-native quantities are inspection-only. None multiplier deliberately
    makes generic share-price valuation unusable until contract qualification.
    Account/environment binding participates in equality/hash.
    """
    def __init__(self, identity):
        super().__init__(symbol=identity.symbol, asset_type=Asset.AssetType.UNKNOWN)
        self.binding = identity
        self.multiplier = None

    def __hash__(self):
        return hash((super().__hash__(), self.binding))

    def __eq__(self, other):
        return isinstance(other, TradeLockerAsset) and self.binding == other.binding and super().__eq__(other)

    def __ne__(self, other):
        return not self == other


def _number(value, *, positive=False):
    if isinstance(value, bool):
        raise ReadOnlyError('Invalid numeric data')
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ReadOnlyError('Invalid numeric data')
    return number


class TradeLockerData(DataSource):
    SOURCE = 'TRADELOCKER_READ_ONLY'
    def __init__(self, reader, asset):
        super().__init__(delay=0, max_workers=1, tzinfo=timezone.utc)
        self.reader = reader
        self.asset = asset

    def check_asset(self, asset):
        if not isinstance(asset, TradeLockerAsset) or asset != self.asset or asset.symbol != self.reader.identity.symbol or asset.asset_type != 'unknown' or asset.multiplier is not None:
            raise ReadOnlyError('Exact account-bound inspection asset required')

    def get_quote(self, asset, quote=None, exchange=None, **kwargs):
        self.check_asset(asset)
        if quote is not None or exchange is not None or kwargs:
            raise ReadOnlyError('Alternate quote/exchange options are not qualified')
        raw = self.reader.quotes()
        def convert():
            bid, ask = _number(raw['bp'], positive=True), _number(raw['ap'], positive=True)
            if bid > ask:
                raise ReadOnlyError('Crossed quote refused')
            # No last trade, exchange timestamp or real-time entitlement inferred.
            return Quote(asset, bid=bid, ask=ask, price=None,
                         bid_size=raw.get('bs'), ask_size=raw.get('as'),
                         raw_data=raw, timestamp=datetime.now(timezone.utc))
        return quiet_call(convert)

    get_last_price = disabled  # A midpoint is NOT a last trade.
    get_chains = disabled
    _get_or_create_thread_pool = disabled

    def get_historical_prices(self, asset, length, timestep='minute', timeshift=None,
                              quote=None, exchange=None, include_after_hours=True, **kwargs):
        self.check_asset(asset)
        if type(length) is not int or not 1 <= length <= 1000 or timeshift is not None or quote is not None or exchange is not None or include_after_hours is not True:
            raise ReadOnlyError('History options not qualified')
        resolutions = {'minute': '1m', 'hour': '1H', 'day': '1D'}
        if timestep not in resolutions or set(kwargs) != {'start_ms', 'end_ms'}:
            raise ReadOnlyError('Use minute/hour/day and explicit start_ms/end_ms')
        frame = self.reader.bars(resolutions[timestep], kwargs['start_ms'], kwargs['end_ms'])
        def convert():
            expected = ['t', 'o', 'h', 'l', 'c', 'v']
            if list(frame.columns) != expected:
                raise ReadOnlyError('Unexpected history schema')
            df = frame.rename(columns=dict(zip(expected, ['datetime', 'open', 'high', 'low', 'close', 'volume']))).copy()
            df['datetime'] = pd.to_datetime(df['datetime'], unit='ms', utc=True)
            if df['datetime'].duplicated().any() or not df['datetime'].is_monotonic_increasing:
                raise ReadOnlyError('Non-unique/unsorted history refused')
            for key in ['open', 'high', 'low', 'close', 'volume']:
                if not df[key].map(lambda x: math.isfinite(float(x))).all():
                    raise ReadOnlyError('Invalid history values')
            # No synthetic dividend/split fields or volume-unit assumption.
            df = df.set_index('datetime').tail(length)
            return Bars(df, self.SOURCE, asset, raw=frame, tzinfo=timezone.utc)
        return quiet_call(convert)


class TradeLockerBroker(Broker):
    """Callable read-only broker candidate. No Trader/Strategy is constructed."""
    REQUIRES_ORDERS_THREAD = False
    ORDER_SUPPORT = {name: 'unsupported' for name in ('market', 'limit', 'stop', 'stop_limit', 'trailing_stop', 'smart_limit', 'bracket', 'oto', 'oco', 'trailing_leg', 'stop_limit_leg')}

    def __init__(self, reader):
        self.reader = reader
        self.asset = TradeLockerAsset(reader.identity)
        data = TradeLockerData(reader, self.asset)
        super().__init__(name='TradeLockerReadOnly', data_source=data,
                         connect_stream=False, start_orders_thread=False,
                         max_workers=1, config={'MARKET': 'UNQUALIFIED'})

    def _start_runtime_telemetry(self):
        # Base constructor calls this even with streaming/order threads disabled.
        return None

    def read_balances(self):
        # Preserve broker column names. Cash/CFD notional/equity mapping is not
        # guessed; generic balance tuple/portfolio valuation remains disarmed.
        return self.reader.balances()

    def _pull_positions(self, strategy):
        rows = self.reader.positions()
        def convert():
            positions = []
            for row in rows:
                if int(row['tradableInstrumentId']) != self.reader.identity.instrument_id or str(row['routeId']) != self.reader.identity.trade_route_id:
                    raise ReadOnlyError('Foreign position requires separate qualification')
                side = row['side']
                qty = Decimal(str(row['qty']))
                if side not in {'buy', 'sell'} or not qty.is_finite() or qty < Decimal('0.000001'):
                    raise ReadOnlyError('Position quantity/side not representable')
                position = Position(strategy, self.asset, qty if side == 'buy' else -qty,
                                    avg_fill_price=_number(row['avgPrice'], positive=True),
                                    position_id=str(row['id']))
                position._raw = row
                position.quantity_unit = 'broker_native_unqualified'
                positions.append(position)
            return positions
        return quiet_call(convert)

    def _pull_position(self, strategy, asset):
        self.data_source.check_asset(asset)
        positions = self._pull_positions(strategy)
        if len(positions) > 1:
            raise ReadOnlyError('Hedged positions are not collapsed into one net position')
        return positions[0] if positions else None

    submit_order = disabled
    submit_orders = disabled
    _submit_order = disabled
    cancel_order = disabled
    cancel_orders = disabled
    cancel_open_orders = disabled
    modify_order = disabled
    _modify_order = disabled
    sell_all = disabled
    close_position = disabled
    _start_orders_thread = disabled
    _get_stream_object = disabled
    _register_stream_events = disabled
    _run_stream = disabled
    _get_balances_at_broker = disabled
    get_historical_account_value = disabled
    _parse_broker_order = disabled
    _pull_broker_order = disabled
    _pull_broker_all_orders = disabled


class SupervisedDemoBroker(TradeLockerBroker):
    """Explicit controller bridge; generic LumiBot trading hooks stay disabled.

    No framework Order, strategy sizing, equity/multiplier or automatic runner
    translation is inferred. Only reviewed controller policy drives this slice.
    Current controller qualification is offline/synthetic only.
    """
    def __init__(self, controller):
        from personal_trading.tradelocker.demo_execution import Controller
        if type(controller) is not Controller:
            raise DisarmedError('Explicit bounded demo controller required')
        controller._require_open()
        self.controller = controller
        super().__init__(controller.reader)

    def submit_demo(self, approval):
        return self.controller.submit(approval)

    def modify_demo_stop(self, stop_loss, approval):
        return self.controller.modify_stop(stop_loss, approval)

    def cancel_demo(self, approval):
        return self.controller.cancel_order(approval)

    def close_demo(self, approval):
        return self.controller.close_position(approval)

    def reconcile_demo(self):
        return self.controller.reconcile()
