"""Execution repair regressions: actual pinned SDK, synthetic HTTP only."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from typing import Any

from personal_trading.tradelocker.demo_execution import Controller, SyntheticHTTP, synthetic_reader, WAIT
from personal_trading.tradelocker.tl_candidate import ReadOnlyError
from test_demo_execution import approval
from test_safety import policy_fixture


@contextmanager
def setup(transform=None, *, side='buy', rate_limits=None):
    original = SyntheticHTTP.request
    def request(wire, method, url, **kwargs):
        response = original(wire, method, url, **kwargs)
        if transform is not None:
            data = json.loads(response.content)
            encoded = transform(wire, method, url, data)
            response._content = encoded if encoded is not None else json.dumps(data).encode()
        return response
    p, q = policy_fixture()
    if side == 'sell': p = replace(p, side='sell', stop_loss=D('2510'), take_profit=D('2480'))
    if rate_limits is not None: q = replace(q, rate_limits=rate_limits)
    with patch.object(SyntheticHTTP, 'request', request), tempfile.TemporaryDirectory(prefix='execution-repairs-') as root, synthetic_reader() as (reader, fixture_wire):
        assert reader is not None
        wire: Any = fixture_wire  # Deliberately inject malformed raw observations.
        with Controller(reader, p, q, Path(root) / 'state') as c:
            yield c, reader, wire


def submit(c):
    c.submit(approval(c, c.describe_submit(), 'synthetic-repair-submit'))


def open_position(c):
    submit(c)
    assert c.reconcile() == 'OPEN_OBSERVED'


def pending(wire):
    wire.orders = [dict(wire.history[0], status='New', filledQty=0., positionId=0)]
    wire.history = []
    wire.positions = []


def writes(wire, method):
    return [call for call in wire.calls if call[0] == method and '/auth/' not in call[1]]


class ProtocolRepairs(unittest.TestCase):
    def test_exec01_config_error_status_never_supplies_flat_account_schema(self):
        def corrupt(wire, method, url, data):
            if method == 'GET' and url.endswith('/config'): data['s'] = 'error'
        with setup(corrupt) as (c, reader, wire):
            with self.assertRaises(ReadOnlyError): submit(c)
            self.assertEqual(writes(wire, 'POST'), [])

    def test_exec01_invalid_table_envelopes_never_admit_submit(self):
        for table in ('ordersHistory', 'orders', 'positions'):
            for mode in ('object', 'null', 'string', 'error', 'missing_status', 'bad_data'):
                def corrupt(wire, method, url, data):
                    if method == 'GET' and url.endswith('/' + table):
                        if mode in ('object', 'null', 'string'):
                            data['d'][table] = {'object': {}, 'null': None, 'string': ''}[mode]
                        elif mode == 'error': data['s'] = 'error'
                        elif mode == 'missing_status': del data['s']
                        elif mode == 'bad_data': data['d'] = []
                with self.subTest(table=table, mode=mode), setup(corrupt) as (c, reader, wire):
                    with self.assertRaises(ReadOnlyError): submit(c)
                    self.assertEqual(writes(wire, 'POST'), [])
                    self.assertFalse(any(e['kind'] == 'INTENT' for e in c.journal.events))

    def test_exec01_invalid_column_schema_never_admits_empty_account(self):
        for table in ('ordersHistory', 'orders', 'positions'):
            for mode in ('duplicate', 'missing'):
                def corrupt(wire, method, url, data):
                    if method == 'GET' and url.endswith('/config'):
                        cols = data['d'][table + 'Config']['columns']
                        if mode == 'duplicate': cols.append(dict(cols[0]))
                        else: cols[:] = [col for col in cols if col['id'] != 'qty']
                with self.subTest(table=table, mode=mode), setup(corrupt) as (c, reader, wire):
                    with self.assertRaises(ReadOnlyError): submit(c)
                    self.assertEqual(writes(wire, 'POST'), [])

    def test_exec01_malformed_close_snapshot_is_not_flat(self):
        active = [False]
        def corrupt(wire, method, url, data):
            if active[0] and method == 'GET' and url.endswith(('/ordersHistory', '/orders', '/positions')):
                data['d'][url.rsplit('/', 1)[-1]] = {}
        with setup(corrupt) as (c, reader, wire):
            open_position(c)
            c.close_position(approval(c, c.describe_close(), 'synthetic-repair-close'))
            active[0] = True
            self.assertEqual(c.reconcile(), WAIT)


class NumericRepairs(unittest.TestCase):
    def test_exec02_json_number_lexemes_are_checked_before_float_parsing_loss(self):
        for field, table, raw in (('qty', 'positions', '0.020000000000000000001'),
                                  ('unrealizedPl', 'positions', '0.020000000000000000001'),
                                  ('filledQty', 'orders', '1e-9999')):
            active = [False]
            def corrupt(wire, method, url, data):
                if active[0] and method == 'GET' and url.endswith('/' + table):
                    columns = wire.position_columns if table == 'positions' else wire.order_columns
                    data['d'][table][0][columns.index(field)] = 'RAW_DECIMAL'
                    return json.dumps(data).replace('"RAW_DECIMAL"', raw).encode()
            with self.subTest(field=field), setup(corrupt) as (c, reader, wire):
                open_position(c)
                if table == 'orders': pending(wire)
                active[0] = True
                self.assertEqual(c.reconcile(), WAIT)

    def test_exec02_unknown_loss_is_not_zero_even_with_default_warnings(self):
        import warnings
        for raw in (False, True, None, float('nan'), float('inf'), '-Infinity', '0.020000000000000000001'):
            with self.subTest(raw=raw), setup() as (c, reader, wire):
                open_position(c)
                wire.positions[0]['unrealizedPl'] = raw
                with warnings.catch_warnings():
                    warnings.simplefilter('default')
                    self.assertEqual(c.reconcile(), WAIT)
                with self.assertRaises(ReadOnlyError):
                    c.close_position(approval(c, c.describe_close(), 'synthetic-repair-loss-close'))
                self.assertEqual(writes(wire, 'DELETE'), [])

    def test_exec02_unknown_fill_never_proves_wholly_unfilled(self):
        import warnings
        for raw in (False, True, None, float('nan'), float('inf'), '-Infinity', '0.0000000000000000000000000000000000000000000000010000000000000001'):
            with self.subTest(raw=raw), setup() as (c, reader, wire):
                submit(c)
                pending(wire)
                wire.orders[0]['filledQty'] = raw
                with warnings.catch_warnings():
                    warnings.simplefilter('default')
                    self.assertEqual(c.reconcile(), WAIT)
                    with self.assertRaises(ReadOnlyError):
                        c.cancel_order(approval(c, c.describe_cancel(), 'synthetic-repair-fill-cancel'))
                self.assertEqual(writes(wire, 'DELETE'), [])

    def test_exec02_nonroundtrippable_quantity_never_creates_rounded_close(self):
        for raw in (False, None, float('nan'), float('inf'), '0.020000000000000000001'):
            with self.subTest(raw=raw), setup() as (c, reader, wire):
                submit(c)
                wire.positions[0]['qty'] = raw
                wire.history[0]['filledQty'] = raw
                for row in wire.orders: row['qty'] = raw
                self.assertEqual(c.reconcile(), WAIT)
                with self.assertRaises(ReadOnlyError): c.describe_close()
                self.assertEqual(writes(wire, 'DELETE'), [])


class ModifyCancelRepairs(unittest.TestCase):
    def test_exec03_cancel_uses_protective_reserve_after_ordinary_capacity_exhaustion(self):
        from personal_trading.tradelocker.safety import RateLimits
        limits = RateLimits(32, D('60'), 6, 6, 'SYNTHETIC repair reserve', True)
        with setup(rate_limits=limits) as (c, reader, wire):
            submit(c)
            pending(wire)
            action = c.describe_modify_stop(D('2491'))
            def timeout(method, path):
                if method == 'PATCH':
                    wire.orders[0]['stopLoss'] = 2490.
                    raise TimeoutError('synthetic')
            wire.after_write = timeout
            with self.assertRaises(ReadOnlyError):
                c.modify_stop(D('2491'), approval(c, action, 'synthetic-repair-budget-modify'))
            wire.after_write = None
            for _ in range(32):
                try: reader.quotes()
                except ReadOnlyError: break
            else: self.fail('Ordinary capacity was not bounded')
            c.cancel_order(approval(c, c.describe_cancel(), 'synthetic-repair-reserved-cancel'))
            self.assertEqual(len(writes(wire, 'DELETE')), 1)
            self.assertEqual(c.reconcile(), 'TERMINAL_NO_POSITION_OBSERVED')
            rate = c._last('RATE')
            assert rate is not None
            self.assertEqual(rate['purpose'], 'protective')

    def test_exec03_timedout_unapplied_modify_allows_separately_approved_exact_cancel(self):
        with setup() as (c, reader, wire):
            submit(c)
            pending(wire)
            self.assertEqual(c.reconcile(), 'PENDING_OBSERVED')
            action = c.describe_modify_stop(D('2491'))
            def timeout(method, path):
                if method == 'PATCH':
                    wire.orders[0]['stopLoss'] = 2490.
                    raise TimeoutError('synthetic unapplied PATCH')
            wire.after_write = timeout
            with self.assertRaises(ReadOnlyError):
                c.modify_stop(D('2491'), approval(c, action, 'synthetic-repair-modify'))
            self.assertEqual(c.reconcile(), WAIT)
            self.assertEqual(c._effective_stop(), D('2490'))  # observed, not requested
            wire.after_write = None
            cancel = c.describe_cancel()
            with self.assertRaises(ReadOnlyError):
                c.cancel_order(approval(c, action, 'synthetic-repair-wrong-cancel'))
            c.cancel_order(approval(c, cancel, 'synthetic-repair-exact-cancel'))
            self.assertEqual(c.reconcile(), 'TERMINAL_NO_POSITION_OBSERVED')
            with self.assertRaises(ReadOnlyError):
                c.modify_stop(D('2491'), approval(c, action, 'synthetic-repair-repeat-modify'))
            self.assertEqual(len(writes(wire, 'PATCH')), 1)
            self.assertEqual(len(writes(wire, 'DELETE')), 1)
            self.assertTrue(writes(wire, 'DELETE')[0][1].endswith('/trade/orders/901'))
            self.assertIsNone(writes(wire, 'DELETE')[0][2]['json'])

    def test_exec03_ambiguous_modify_never_admits_partial_or_foreign_cancel(self):
        for mode in ('partial', 'foreign', 'route', 'side', 'quantity', 'terminal'):
            with self.subTest(mode=mode), setup() as (c, reader, wire):
                submit(c)
                pending(wire)
                action = c.describe_modify_stop(D('2491'))
                def timeout(method, path):
                    if method == 'PATCH':
                        wire.orders[0]['stopLoss'] = 2490.
                        raise TimeoutError('synthetic')
                wire.after_write = timeout
                with self.assertRaises(ReadOnlyError):
                    c.modify_stop(D('2491'), approval(c, action, 'synthetic-repair-modify'))
                wire.after_write = None
                row = wire.orders[0]
                if mode == 'partial': row['filledQty'] = .01
                elif mode == 'foreign': row['id'] = 999
                elif mode == 'route': row['routeId'] = 19
                elif mode == 'side': row['side'] = 'sell'
                elif mode == 'quantity': row['qty'] = .03
                else: row['status'] = 'Rejected'
                with self.assertRaises(ReadOnlyError):
                    c.cancel_order(approval(c, c.describe_cancel(), 'synthetic-repair-block-cancel'))
                self.assertEqual(writes(wire, 'DELETE'), [])

    def test_exec03_applied_timeout_is_observed_but_modify_is_not_replayed(self):
        with setup() as (c, reader, wire):
            submit(c)
            pending(wire)
            action = c.describe_modify_stop(D('2491'))
            wire.after_write = lambda method, path: (_ for _ in ()).throw(TimeoutError('synthetic'))
            with self.assertRaises(ReadOnlyError):
                c.modify_stop(D('2491'), approval(c, action, 'synthetic-repair-applied-modify'))
            wire.after_write = None
            self.assertEqual(c.reconcile(), 'PENDING_OBSERVED')
            with self.assertRaises(ReadOnlyError):
                c.modify_stop(D('2491'), approval(c, action, 'synthetic-repair-repeat-modify'))
            c.cancel_order(approval(c, c.describe_cancel(), 'synthetic-repair-applied-cancel'))
            self.assertEqual(len(writes(wire, 'PATCH')), 1)
            self.assertEqual(len(writes(wire, 'DELETE')), 1)


class ProtectionRepairs(unittest.TestCase):
    def test_exec04_terminal_or_unknown_children_never_prove_working_protection(self):
        for leg in ('stop', 'limit'):
            for status in ('Rejected', 'Cancelled', 'Canceled', 'Filled', 'Expired', 'Unknown', ''):
                with self.subTest(leg=leg, status=status), setup() as (c, reader, wire):
                    open_position(c)
                    next(row for row in wire.orders if row['type'] == leg)['status'] = status
                    self.assertEqual(c.reconcile(), 'OPEN_UNPROTECTED_STOP_NEW_RISK')
                    c.close_position(approval(c, c.describe_close(), 'synthetic-repair-unprotected-close'))
                    self.assertEqual(c.reconcile(), 'FLAT_SNAPSHOT_ONLY')

    def test_exec04_remaining_executable_quantity_must_cover_exact_position(self):
        for leg in ('stop', 'limit'):
            for filled in (-.01, .01, .02, .03):
                with self.subTest(leg=leg, filled=filled), setup() as (c, reader, wire):
                    open_position(c)
                    next(row for row in wire.orders if row['type'] == leg)['filledQty'] = filled
                    self.assertEqual(c.reconcile(), 'OPEN_UNPROTECTED_STOP_NEW_RISK')
                    self.assertIsNotNone(c._last('STOP'))


NATIVE_EXIT = 'NATIVE_EXIT_OBSERVED_COSTS_UNVERIFIED'


def native_exit(wire, kind='limit'):
    wire.history.extend(dict(row, status='Filled' if row['type'] == kind else 'Cancelled',
                             filledQty=row['qty'] if row['type'] == kind else 0.) for row in wire.orders)
    wire.orders = []
    wire.positions = []


class NativeExitRepairs(unittest.TestCase):
    def test_exec05_sell_native_exits_and_exact_partial_position_quantities(self):
        for kind in ('stop', 'limit'):
            for partial in (False, True):
                with self.subTest(kind=kind, partial=partial), setup(side='sell') as (c, reader, wire):
                    submit(c)
                    qty = D('.01') if partial else D('.02')
                    if partial:
                        wire.positions[0]['qty'] = .01
                        wire.history[0]['filledQty'] = .01
                        for row in wire.orders: row['qty'] = .01
                    expected = 'PARTIAL_POSITION_STOP_NEW_RISK' if partial else 'OPEN_OBSERVED'
                    self.assertEqual(c.reconcile(), expected)
                    native_exit(wire, kind)
                    self.assertEqual(c.reconcile(), NATIVE_EXIT)
                    event = c._last('NATIVE_EXIT')
                    assert event is not None
                    self.assertEqual(D(event['quantity']), qty)
                    self.assertEqual(writes(wire, 'DELETE'), [])

    def test_exec05_linked_stop_or_target_exit_is_distinct_from_empty_snapshot(self):
        for kind, order_id, sibling_id in (('stop', '902', '903'), ('limit', '903', '902')):
            with self.subTest(kind=kind), setup() as (c, reader, wire):
                open_position(c)
                native_exit(wire, kind)
                self.assertEqual(c.reconcile(), NATIVE_EXIT)
                event = c._last('NATIVE_EXIT')
                assert event is not None
                self.assertEqual(event['position_id'], '789')
                self.assertEqual(event['exit_order_id'], order_id)
                self.assertEqual(event['cancelled_order_id'], sibling_id)
                self.assertEqual(D(event['quantity']), D('.02'))
                for field in ('fill_price', 'costs', 'pnl'):
                    self.assertIsNone(event[field])
                self.assertEqual(c.reconcile(), NATIVE_EXIT)
                self.assertEqual(sum(e['kind'] == 'NATIVE_EXIT' for e in c.journal.events), 1)
                self.assertEqual(writes(wire, 'DELETE'), [])
                with self.assertRaises(ReadOnlyError):
                    c.submit(approval(c, c.describe_submit(), 'synthetic-repair-no-second-cycle'))

    def test_exec05_flat_or_inconsistent_history_never_proves_native_exit(self):
        for mode in ('empty', 'missing_sibling', 'rejected_sibling', 'both_filled', 'partial',
                     'overfill', 'quantity', 'position', 'route', 'instrument', 'side', 'type',
                     'price', 'foreign_id', 'entry_position', 'working_sibling', 'remaining_position',
                     'extra_linked_fill'):
            with self.subTest(mode=mode), setup() as (c, reader, wire):
                open_position(c)
                old_position = wire.positions.copy()
                old_orders = wire.orders.copy()
                native_exit(wire)
                winner, sibling = wire.history[2], wire.history[1]
                if mode == 'empty': wire.history = wire.history[:1]
                elif mode == 'missing_sibling': wire.history.remove(sibling)
                elif mode == 'rejected_sibling': sibling['status'] = 'Rejected'
                elif mode == 'both_filled': sibling.update(status='Filled', filledQty=.02)
                elif mode == 'partial': winner['filledQty'] = .01
                elif mode == 'overfill': winner['filledQty'] = .03
                elif mode == 'quantity': winner['qty'] = .03
                elif mode == 'position': winner['positionId'] = 790
                elif mode == 'route': winner['routeId'] = 19
                elif mode == 'instrument': winner['tradableInstrumentId'] = 457
                elif mode == 'side': winner['side'] = 'buy'
                elif mode == 'type': winner['type'] = 'market'
                elif mode == 'price': winner['price'] = 2521.
                elif mode == 'foreign_id': winner['id'] = 999
                elif mode == 'entry_position': wire.history[0]['positionId'] = 790
                elif mode == 'working_sibling': wire.orders = [old_orders[0]]
                elif mode == 'remaining_position': wire.positions = old_position
                else: wire.history.append(dict(winner, id=999))
                self.assertNotEqual(c.reconcile(), NATIVE_EXIT)
                self.assertIsNone(c._last('NATIVE_EXIT'))

    def test_exec05_history_without_previously_observed_protection_stays_unresolved(self):
        with setup() as (c, reader, wire):
            submit(c)
            native_exit(wire)
            self.assertEqual(c.reconcile(), WAIT)
            self.assertIsNone(c._last('NATIVE_EXIT'))

    def test_exec05_linked_native_exit_survives_restart_without_new_risk(self):
        p, q = policy_fixture()
        with tempfile.TemporaryDirectory(prefix='execution-repair-restart-') as root, synthetic_reader() as (reader, wire):
            state = Path(root) / 'state'
            with Controller(reader, p, q, state) as c:
                open_position(c)
            native_exit(wire)
            with Controller(reader, p, q, state) as c:
                self.assertTrue(c.journal.restarted)
                self.assertEqual(c.reconcile(), NATIVE_EXIT)
                with self.assertRaises(ReadOnlyError):
                    c.submit(approval(c, c.describe_submit(), 'synthetic-repair-restart-submit'))
                self.assertEqual(len(writes(wire, 'POST')), 1)


if __name__ == '__main__':
    unittest.main()
