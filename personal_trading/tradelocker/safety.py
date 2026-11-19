"""Explicit bounded-demo policy; no owner strategy or contract defaults.

Qualification is currently an OFFLINE SYNTHETIC MODEL only. Real REST quantity
units versus lots, account economics, permission polarity/type map and session
semantics remain unresolved. No boolean/evidence-string assertion lifts this
source gate. A separately reviewed actual qualifier/conversion is still needed.
Dollar limits are preflight estimates, NOT a guaranteed stop-fill/loss bound.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import time
from personal_trading.tradelocker.tl_candidate import Identity, ENVIRONMENT, SERVER, SYMBOL, ReadOnlyError


@dataclass(frozen=True)
class RateLimits:
    """Synthetic aggregate window only, NOT actual REST per-route config."""
    requests: int
    window_seconds: Decimal
    reconciliation_reserve: int
    protective_reserve: int
    evidence: str
    synthetic: bool


@dataclass(frozen=True)
class Qualification:
    binding: Identity
    min_lot: Decimal
    max_lot: Decimal
    lot_step: Decimal
    tick_size: Decimal
    usd_per_price_per_lot: Decimal
    exposure_usd_per_lot: Decimal
    margin_usd_per_lot: Decimal
    cost_reserve_usd: Decimal
    demo_eligible: bool
    open_allowed: bool
    modify_allowed: bool
    close_allowed: bool
    protection_mapping_qualified: bool
    evidence: str
    synthetic: bool
    cancel_allowed: bool
    protective_orders_allowed: bool
    session_open: bool
    quantity_unit: str
    valid_until: int
    rate_limits: RateLimits


@dataclass(frozen=True)
class RiskPolicy:
    binding: Identity
    side: str
    quantity: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    max_exposure_usd: Decimal
    max_loss_usd: Decimal
    max_spread: Decimal
    max_slippage: Decimal
    max_quote_age_seconds: Decimal
    max_snapshot_age_seconds: Decimal
    max_hold_seconds: Decimal


def positive(value):
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise ReadOnlyError('Explicit positive finite Decimal required')
    return value


def wire_decimal(value):
    failed = False
    result = None
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
    except (ValueError, ArithmeticError):
        failed = True
    if failed:
        raise ReadOnlyError('Invalid numeric observation')
    return result


def canonical_id(value):
    if type(value) is int and 0 < value <= 9223372036854775807:
        return str(value)
    if type(value) is str and value.isascii() and value.isdecimal() and not value.startswith('0') and int(value) <= 9223372036854775807:
        return value
    raise ReadOnlyError('Canonical positive identifier required')


def digest(value):
    def encode(obj):
        if type(obj) is Decimal:
            return str(obj)
        raise TypeError('Unsupported digest value')
    if hasattr(value, '__dataclass_fields__'):
        value = asdict(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False, default=encode).encode()).hexdigest()


def validate_policy(policy, qualification):
    if type(policy) is not RiskPolicy or type(qualification) is not Qualification:
        raise ReadOnlyError('Typed policy and qualification required')
    if qualification.synthetic is not True:
        raise ReadOnlyError('Actual execution unqualified: REST units, economics, permissions and session gates unresolved')
    limits=qualification.rate_limits
    if type(limits) is not RateLimits or limits.synthetic is not True or type(limits.evidence) is not str or not limits.evidence.strip():
        raise ReadOnlyError('Explicit synthetic rate budget required; actual config remains unknown')
    positive(limits.window_seconds)
    for value in (limits.requests,limits.reconciliation_reserve,limits.protective_reserve):
        if type(value) is not int or value<=0:
            raise ReadOnlyError('Explicit positive request capacity and reserves required')
    if limits.requests<=limits.reconciliation_reserve+limits.protective_reserve:
        raise ReadOnlyError('Request budget must retain both reserves')
    i = policy.binding
    if type(i) is not Identity or i != qualification.binding or (i.environment, i.server, i.symbol, i.currency) != (ENVIRONMENT, SERVER, SYMBOL, 'USD'):
        raise ReadOnlyError('Exact qualified demo identity required')
    for value in (i.account_id, i.acc_num, i.instrument_id):
        if type(value) is not int:
            raise ReadOnlyError('Exact integer binding required')
        canonical_id(value)
    for value in (i.info_route_id, i.trade_route_id):
        canonical_id(value)
    if i.info_route_id == i.trade_route_id or policy.side not in ('buy', 'sell'):
        raise ReadOnlyError('Explicit routes and side required')
    for field in policy.__dataclass_fields__:
        if field not in ('binding', 'side'):
            positive(getattr(policy, field))
    for field in ('min_lot', 'max_lot', 'lot_step', 'tick_size', 'usd_per_price_per_lot',
                  'exposure_usd_per_lot', 'margin_usd_per_lot', 'cost_reserve_usd'):
        positive(getattr(qualification, field))
    for field in ('demo_eligible', 'open_allowed', 'modify_allowed', 'close_allowed', 'protection_mapping_qualified', 'cancel_allowed', 'protective_orders_allowed', 'session_open'):
        if getattr(qualification, field) is not True:
            raise ReadOnlyError('Unknown or denied economics/permission/eligibility gate')
    if type(qualification.synthetic) is not bool or type(qualification.evidence) is not str or not qualification.evidence.strip():
        raise ReadOnlyError('Explicit qualification provenance required')
    if qualification.quantity_unit != 'synthetic_sdk_quantity_equals_fixture_lots' or type(qualification.valid_until) is not int or qualification.valid_until<=0:
        raise ReadOnlyError('Explicit synthetic fixture units and qualification expiry required')
    qty, q = policy.quantity, qualification
    if q.min_lot > q.max_lot or not q.min_lot <= qty <= q.max_lot or qty % q.lot_step or (qty-q.min_lot) % q.lot_step:
        raise ReadOnlyError('Quantity violates qualified lot grid')
    for price in (policy.stop_loss, policy.take_profit):
        if price % q.tick_size:
            raise ReadOnlyError('Price violates qualified tick grid')
    for number in (qty, policy.stop_loss, policy.take_profit):
        sdk_float(number)
    if qty*q.exposure_usd_per_lot > policy.max_exposure_usd:
        raise ReadOnlyError('Exposure cap exceeded')
    return policy


def sdk_float(number):
    positive(number)
    result = float(number)
    if Decimal(str(result)) != number:
        raise ReadOnlyError('Pinned SDK decimal representation would change')
    return result


def validate_market(policy, qualification, *, bid, ask, quote_age, snapshot_age, free_margin):
    validate_policy(policy, qualification)
    if qualification.valid_until<=time.time():
        raise ReadOnlyError('Qualification expired; no new risk')
    for number in (bid, ask):
        positive(number)
    if bid > ask or ask-bid > policy.max_spread:
        raise ReadOnlyError('Spread/crossed quote gate')
    if not Decimal(0) <= quote_age <= policy.max_quote_age_seconds or not Decimal(0) <= snapshot_age <= policy.max_snapshot_age_seconds:
        raise ReadOnlyError('Stale observation gate')
    if free_margin < policy.quantity*qualification.margin_usd_per_lot:
        raise ReadOnlyError('Free margin gate')
    entry = ask if policy.side == 'buy' else bid
    if not ((policy.stop_loss < bid <= ask < policy.take_profit) if policy.side == 'buy' else (policy.take_profit < bid <= ask < policy.stop_loss)):
        raise ReadOnlyError('Protective prices must straddle current market')
    estimate = ((abs(entry-policy.stop_loss)+policy.max_slippage)*policy.quantity*
                qualification.usd_per_price_per_lot + qualification.cost_reserve_usd)
    if estimate > policy.max_loss_usd:
        raise ReadOnlyError('Estimated loss including explicit slippage/cost reserve exceeds cap')
