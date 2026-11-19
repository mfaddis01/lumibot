"""Pure offline preparation. No SDK/network calls or execution/arming capability.

Supplied metadata can be synthetic: these functions validate a translation, NOT
instrument eligibility, risk policy, consent, live state or execution readiness.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType

from personal_trading.tradelocker.tl_candidate import ENVIRONMENT, SERVER, SYMBOL, ReadOnlyError


@dataclass(frozen=True)
class OfflineTranslation:
    binding: object
    sdk_kwargs: object
    execution_enabled: bool = False
    quantity_unit: str = 'lots_from_supplied_metadata_unqualified'


def _decimal(value):
    failed = False
    result = None
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError()
    except (ValueError, InvalidOperation):
        failed = True
    if failed:
        raise ReadOnlyError('Positive finite decimal required')
    return result


def prepare_market_translation(identity, details, *, quantity, side):
    """No defaults for owner direction or quantity; never produces an armed plan.

    Zero- and minLot-anchored increment tests must agree; refuse ambiguous grids.
    Exposure valuation/cap and exact one-use consent are separate unmet gates.
    """
    if (identity.environment, identity.server, identity.symbol, identity.currency) != (ENVIRONMENT, SERVER, SYMBOL, 'USD'):
        raise ReadOnlyError('Only exact demo account-bound instrument allowed')
    if details.get('name') != SYMBOL or details.get('quotingCurrency') != 'USD':
        raise ReadOnlyError('Exact supplied instrument metadata required')
    if side not in {'buy', 'sell'} or not isinstance(quantity, (str, Decimal)):
        raise ReadOnlyError('Explicit decimal text quantity and buy/sell required')
    if any(k not in details for k in ('minLot', 'maxLot', 'lotStep', 'lotSize')):
        raise ReadOnlyError('Sizing metadata is incomplete')
    minimum, maximum, step, lot_size = (_decimal(details[k]) for k in ('minLot', 'maxLot', 'lotStep', 'lotSize'))
    qty = _decimal(quantity)
    if minimum > maximum or qty < minimum or qty > maximum or qty % step or (qty - minimum) % step:
        raise ReadOnlyError('Quantity violates supplied lot grid')
    # Pinned SDK accepts float. Refuse values whose decimal spelling changes.
    if Decimal(str(float(qty))) != qty:
        raise ReadOnlyError('Quantity is not losslessly representable for pinned SDK')
    kwargs = MappingProxyType({'instrument_id': identity.instrument_id, 'quantity': float(qty),
        'side': side, 'type_': 'market', 'validity': 'IOC', 'position_netting': False})
    return OfflineTranslation(identity, kwargs)


def reconcile_open_observation(plan, order_id, history, positions):
    """Pure rehearsal: ack -> filled history -> exact linked position observation.

    Missing/partial/ambiguous evidence ALWAYS waits for reconciliation, never a
    resend. Caller-supplied snapshots are not proof of freshness/completeness.
    """
    wait = 'WAIT_RECONCILE_NO_REPLAY'
    failed = False
    result = wait
    try:
        matches = [r for r in history if str(r['id']) == str(order_id)]
        if len(matches) == 1:
            order = matches[0]
            linked = [p for p in positions if str(p['id']) == str(order['positionId'])]
            if len(linked) == 1:
                position = linked[0]
                expected_qty = Decimal(str(plan.sdk_kwargs['quantity']))
                expected_side = plan.sdk_kwargs['side']
                exact = (
                    order['status'] == 'Filled'
                    and _decimal(order['filledQty']) == expected_qty
                    and _decimal(position['qty']) == expected_qty
                    and order['tradableInstrumentId'] == plan.binding.instrument_id
                    and position['tradableInstrumentId'] == plan.binding.instrument_id
                    and str(position['routeId']) == plan.binding.trade_route_id
                    and order['side'] == position['side'] == expected_side
                )
                if exact:
                    result = 'OPEN_OBSERVED'
    except (KeyError, TypeError, ValueError, ReadOnlyError):
        failed = True
    return wait if failed else result


def reconcile_flat_observation(positions, open_orders):
    """Flat account snapshot only, NOT close execution/fill/latency proof."""
    if type(positions) is not list or type(open_orders) is not list:
        return 'WAIT_RECONCILE_NO_REPLAY'
    return 'FLAT_SNAPSHOT_ONLY' if not positions and not open_orders else 'WAIT_RECONCILE_NO_REPLAY'
