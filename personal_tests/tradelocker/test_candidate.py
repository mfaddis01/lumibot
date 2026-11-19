"""Synthetic wire data; no account facts. Real SDK and LumiBot classes."""
import json
import socket
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
from personal_trading.tradelocker.runtime import configure
configure()
import jwt
import requests
from tradelocker.tradelocker_api import TLAPI
from personal_trading.tradelocker.tl_candidate import Reader, ReadOnlyError


class Wire:
    def __init__(self):
        self.calls = []
        self.accounts = [{'id': 123, 'accNum': 7, 'name': 'synthetic', 'currency': 'USD', 'accountBalance': 54321.25}]
        self.instruments = [{'tradableInstrumentId': 456, 'name': 'XAUUSD.R', 'type': 'CFD', 'routes': [{'id': 17, 'type': 'INFO'}, {'id': 18, 'type': 'TRADE'}]}]
        self.positions = []
        self.state = {'balance': 54321.25, 'openNetPnL': 0.0}
        self.details = {'name': 'XAUUSD.R', 'synthetic_unqualified': True}
        self.token = jwt.encode({'exp': 4102444800, 'sub': 'synthetic'}, 'synthetic-offline-only-hmac-key-32bytes-minimum', algorithm='HS256')

    def request(self, session, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        p = url.split('/backend-api', 1)[1]
        cols = lambda keys: {'columns': [{'id': k} for k in keys]}
        if p == '/auth/jwt/token' or p == '/auth/jwt/refresh':
            data = {'accessToken': self.token, 'refreshToken': self.token}
        elif p == '/auth/jwt/all-accounts':
            data = {'accounts': self.accounts}
        elif p == '/trade/config':
            data = {'s': 'ok', 'd': {'positionsConfig': cols(['id', 'tradableInstrumentId', 'routeId', 'side', 'qty', 'avgPrice']), 'accountDetailsConfig': cols(list(self.state)), 'limits': [{'limitType': 'QUOTES_HISTORY_BARS', 'limit': 1000}]}}
        elif p.endswith('/instruments'):
            data = {'s': 'ok', 'd': {'instruments': self.instruments}}
        elif p.endswith('/positions'):
            data = {'s': 'ok', 'd': {'positions': self.positions}}
        elif p.endswith('/state'):
            data = {'s': 'ok', 'd': {'accountDetailsData': list(self.state.values())}}
        elif p == '/trade/quotes':
            data = {'s': 'ok', 'd': {'bp': 2500.0, 'ap': 2500.5, 'bs': 1.0, 'as': 1.0}}
        elif p == '/trade/history':
            data = {'s': 'ok', 'd': {'barDetails': [{'t': 1700000000000, 'o': 2500.0, 'h': 2501.0, 'l': 2499.0, 'c': 2500.5, 'v': 2.0}]}}
        elif p == '/trade/instruments/' + str(self.instruments[0]['tradableInstrumentId']):
            data = {'s': 'ok', 'd': self.details}
        else:
            raise AssertionError('unexpected synthetic endpoint')
        response = requests.Response()
        response.status_code = 201 if p in ('/auth/jwt/token', '/auth/jwt/refresh') else 200
        response.url = url
        response._content = json.dumps(data).encode()
        return response


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.wire = Wire()
        self.socket_guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('socket prohibited'))
        self.socket_guard.start()
        wire = self.wire
        self.http = patch.object(requests.Session, 'request', lambda session, method, url, **kw: wire.request(session, method, url, **kw))
        self.http.start()
        self.addCleanup(self.http.stop)
        self.addCleanup(self.socket_guard.stop)

    def reader(self):
        return Reader.connect('synthetic-user', 'synthetic-password', 123, 7)

    def test_auth_status_failure_has_safe_code_without_exception_context(self):
        wire = self.wire
        def response(session, method, url, **kwargs):
            result = wire.request(session, method, url, **kwargs)
            result.status_code = 401
            result._content = b'SYNTHETIC_PRIVATE_RESPONSE'
            return result
        with patch.object(requests.Session, 'request', response):
            caught = None
            try:
                Reader.discover('synthetic-user', 'synthetic-password', lambda options: 1)
            except ReadOnlyError as exc:
                caught = exc
            self.assertIsNotNone(caught)
            self.assertEqual(getattr(caught, 'diagnostic_code', None), 'AUTH_HTTP_STATUS_401')
            self.assertIsNone(caught.__context__)
            self.assertNotIn('SYNTHETIC_PRIVATE_RESPONSE', str(caught))

    def test_documented_auth_created_response_reaches_account_selection(self):
        wire = self.wire
        def response(session, method, url, **kwargs):
            result = wire.request(session, method, url, **kwargs)
            if url.endswith('/auth/jwt/token'):
                result.status_code = 201
            return result
        offered = []
        with patch.object(requests.Session, 'request', response):
            reader = Reader.discover('synthetic-user', 'synthetic-password',
                                     lambda options: offered.append(options) or 1)
        self.addCleanup(reader.close)
        self.assertEqual(len(offered), 1)
        self.assertEqual(reader.identity.symbol, 'XAUUSD.R')

    def test_discovery_selects_returned_pair_before_any_account_read(self):
        self.wire.accounts.append({'id': 987654321, 'accNum': 912345, 'name': 'private-name', 'currency': 'USD'})
        offered = []
        def select(options):
            offered.extend(options)
            self.assertEqual([c[1].split('/backend-api', 1)[1] for c in self.wire.calls],
                             ['/auth/jwt/token', '/auth/jwt/all-accounts'])
            return 2
        reader = Reader.discover('synthetic-user', 'synthetic-password', select)
        self.addCleanup(reader.close)
        self.assertIsInstance(reader._sdk, TLAPI)
        self.assertEqual((reader.identity.account_id, reader.identity.acc_num), (987654321, 912345))
        self.assertEqual(len(offered), 2)
        for marker in ('987654321', '912345', 'private-name', 'synthetic-user', 'synthetic-password'):
            self.assertNotIn(marker, repr(offered))
        for method, url, kw in self.wire.calls:
            if '/trade/' in url:
                self.assertEqual(kw['headers']['accNum'], '912345')
                if '/accounts/' in url:
                    self.assertIn('/987654321/', url)
                self.assertEqual(method, 'GET')
        self.assertEqual(TLAPI._instances, {})

    def test_synthetic_contract_shape_uses_selected_account_routes(self):
        from synthetic_contract import contract_fixture
        observation = contract_fixture()
        self.wire.instruments = [{'name': observation['symbol'], 'type': observation['contract']['type'],
            'tradableInstrumentId': observation['tradableInstrumentId'],
            'routes': [{'type': kind, 'id': value} for kind, value in observation['routes'].items()]}]
        self.wire.details = observation['contract'].copy()
        reader = Reader.discover('synthetic', 'synthetic', lambda options: 1)
        self.addCleanup(reader.close)
        from personal_trading.tradelocker.lumi_adapter import TradeLockerBroker
        broker = TradeLockerBroker(reader)
        details = reader.details()
        self.assertEqual(details, observation['contract'])
        self.assertIsNone(details['baseCurrency'])
        self.assertEqual(details['tickCost'][0]['tickCost'], 0.0)
        self.assertEqual(details['type'], 'EQUITY_CFD')
        self.assertIsNone(broker.asset.multiplier)
        self.assertEqual(reader.identity.instrument_id, 654)
        self.assertEqual(self.wire.calls[-1][2]['params']['routeId'], '42')
        quote = broker.data_source.get_quote(broker.asset)
        self.assertEqual((quote.bid, quote.ask), (2500.0, 2500.5))
        self.assertEqual(self.wire.calls[-1][2]['params']['routeId'], '41')
        count = len(self.wire.calls)
        with self.assertRaises(ReadOnlyError):
            broker.submit_order(None)
        from personal_trading.tradelocker.tl_candidate import quiet_call
        with self.assertRaises(ReadOnlyError):
            quiet_call(lambda: reader._sdk.create_order(instrument_id=654, quantity=0.01,
                side='buy', type_='market', validity='IOC', position_netting=False))
        self.assertEqual(len(self.wire.calls), count)

    def test_details_reject_foreign_contract_name(self):
        reader = self.reader()
        self.addCleanup(reader.close)
        self.wire.details['name'] = 'XAUEUR.R'
        with self.assertRaises(ReadOnlyError):
            reader.details()

    def test_changed_trade_route_binding_refuses_without_http(self):
        reader = self.reader()
        self.addCleanup(reader.close)
        reader._sdk._bound_trade_route = '999'
        count = len(self.wire.calls)
        with self.assertRaises(ReadOnlyError):
            reader.details()
        self.assertEqual(len(self.wire.calls), count)

    def test_discovery_has_no_default_or_ambiguous_account_fallback(self):
        for choice in (None, '', '1', 0, -1, True, 1.0, 2):
            self.wire.calls.clear()
            with self.subTest(kind=type(choice).__name__), self.assertRaises(ReadOnlyError):
                Reader.discover('synthetic', 'synthetic', lambda options: choice)
            self.assertFalse(any('/trade/' in c[1] for c in self.wire.calls))
        self.wire.calls.clear()
        with self.assertRaises(ReadOnlyError):
            Reader.discover('synthetic', 'synthetic', None)
        self.assertEqual(len(self.wire.calls), 0)
        original = self.wire.accounts[0].copy()
        for rows in ([], [original, original.copy()],
                     [original, dict(original, id=999)], [original, dict(original, accNum=8)],
                     [dict(original, currency='EUR')], [dict(original, id=123.9)]):
            self.wire.accounts = rows
            self.wire.calls.clear()
            with self.assertRaises(ReadOnlyError):
                Reader.discover('synthetic', 'synthetic', lambda options: 1)
            self.assertFalse(any('/trade/' in c[1] for c in self.wire.calls))

    def test_discovery_callback_failure_is_sanitized_without_http_fallback(self):
        import contextlib
        import io
        marker = 'synthetic-secret-' + str(id(self))
        out, err = io.StringIO(), io.StringIO()
        def select(options):
            print(marker)
            raise RuntimeError(marker)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                Reader.discover('synthetic', marker, select)
            except ReadOnlyError as exc:
                self.assertIsNone(exc.__context__)
                self.assertIsNone(exc.__cause__)
                self.assertNotIn(marker, str(exc))
            else:
                self.fail('Selection failure must refuse')
        self.assertNotIn(marker, out.getvalue() + err.getvalue())
        self.assertFalse(any('/trade/' in c[1] for c in self.wire.calls))
        self.assertEqual(TLAPI._instances, {})

    def test_discovery_rejects_missing_or_duplicate_route_types_and_suffixes(self):
        original = self.wire.instruments[0].copy()
        for instrument in (dict(original, name='XAUUSD'), dict(original, name='XAUEUR.R'),
                           dict(original, routes=[{'id': 17, 'type': 'INFO'}]),
                           dict(original, routes=[{'id': 17, 'type': 'INFO'}, {'id': 18, 'type': 'TRADE'}, {'id': 19, 'type': 'INFO'}]),
                           dict(original, routes=[{'id': 17, 'type': 'INFO'}, {'id': 17, 'type': 'TRADE'}])):
            self.wire.instruments = [instrument]
            with self.assertRaises(ReadOnlyError):
                Reader.discover('synthetic', 'synthetic', lambda options: 1)

    def test_identity_repr_does_not_disclose_account_pair(self):
        self.wire.accounts = [{'id': 987654321, 'accNum': 912345, 'name': 'synthetic', 'currency': 'USD'}]
        reader = Reader.discover('synthetic', 'synthetic', lambda options: 1)
        self.addCleanup(reader.close)
        for marker in ('987654321', '912345'):
            self.assertNotIn(marker, repr(reader.identity))

    def test_real_sdk_jwt_expiry_and_refresh_are_offline(self):
        from tradelocker.utils import time_to_token_expiry
        expired = jwt.encode({'exp': 1, 'sub': 'synthetic'},
                             'synthetic-offline-only-hmac-key-32bytes-minimum', algorithm='HS256')
        self.assertGreater(time_to_token_expiry(self.wire.token), 0)
        self.assertLess(time_to_token_expiry(expired), 0)
        with self.assertRaises(jwt.DecodeError):
            time_to_token_expiry('not-a-token')
        reader = self.reader()
        reader._sdk._access_token = expired
        before = len(self.wire.calls)
        self.assertEqual(reader._sdk.get_access_token(), self.wire.token)
        self.assertEqual(len(self.wire.calls), before + 1)
        self.assertEqual(self.wire.calls[-1][1].split('/backend-api', 1)[1], '/auth/jwt/refresh')
        self.assertEqual(reader._sdk.get_refresh_token(), self.wire.token)

    def test_local_override_metadata_and_version_are_fail_closed(self):
        from personal_trading.tradelocker.tl_candidate import _sdk_class, metadata
        real_version = metadata.version
        for version in ('0.56.3', '0.56.2+unqualified'):
            with patch.object(metadata, 'version', return_value=version):
                with self.assertRaises(ReadOnlyError):
                    _sdk_class()
        def local_version(package):
            return '0.56.2+lumibot.jwt2151.1' if package == 'tradelocker' else real_version(package)
        with patch.object(metadata, 'version', side_effect=local_version), patch.object(metadata, 'requires', return_value=['PyJWT==2.8.0']):
            with self.assertRaises(ReadOnlyError):
                _sdk_class()

    def test_full_runtime_local_sdk_identity_requires_both_exact_overrides(self):
        from personal_trading.tradelocker.tl_candidate import _sdk_class, metadata
        actual_version=metadata.version
        actual_requires=metadata.requires('tradelocker')
        requirements=[r for r in actual_requires if not r.lower().startswith(('pyjwt','requests'))]
        requirements+=['PyJWT[crypto]==2.15.1','requests==2.34.2']
        def versions(name):
            return {'tradelocker':'0.56.2+lumibot.runtime.1','PyJWT':'2.15.1','requests':'2.34.2'}.get(name,actual_version(name))
        with patch.object(metadata,'version',side_effect=versions),patch.object(metadata,'requires',return_value=requirements):
            self.assertTrue(issubclass(_sdk_class(),TLAPI))
            for package,bad in [('PyJWT','2.8.0'),('requests','2.32.2'),('requests','2.34.3')]:
                with patch.object(metadata,'version',side_effect=lambda name:bad if name==package else versions(name)):
                    with self.assertRaises(ReadOnlyError): _sdk_class()
            for badreqs in (['PyJWT==2.15.1','requests==2.34.2'],
                            ['PyJWT[crypto]>=2.15.1','requests==2.34.2'],
                            ['PyJWT[crypto]==2.15.1','requests>=2.34.2'],
                            ['PyJWT[crypto]==2.15.1','requests==2.34.2','requests>=2']):
                with patch.object(metadata,'requires',return_value=badreqs):
                    with self.assertRaises(ReadOnlyError): _sdk_class()
        with patch.object(metadata,'version',return_value='0.56.2+lumibot.runtime.2'):
            with self.assertRaises(ReadOnlyError): _sdk_class()

    def test_sdk_errors_have_no_raw_context_or_output_and_no_retries(self):
        import contextlib
        import io
        import logging
        marker = 'synthetic-sensitive-' + str(id(self))
        previous = logging.root.manager.disable
        out, err = io.StringIO(), io.StringIO()
        calls = []
        def fail(*args, **kwargs):
            calls.append(1)
            print(marker)
            logging.critical(marker)
            raise RuntimeError(marker)
        with patch.object(requests.Session, 'request', fail), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                self.reader()
            except ReadOnlyError as exc:
                self.assertNotIn(marker, str(exc))
                self.assertIsNone(exc.__context__)
                self.assertIsNone(exc.__cause__)
            else:
                self.fail('SDK failure must not escape sanitization')
        self.assertEqual(calls, [1])
        self.assertNotIn(marker, out.getvalue() + err.getvalue())
        self.assertEqual(logging.root.manager.disable, previous)

    def test_ambiguous_binding_and_selector_mismatch_fail_closed(self):
        with self.assertRaises(ReadOnlyError):
            Reader.connect('synthetic', 'synthetic', 123, 8)
        for selectors in ((0, 7), (123, 0), (True, 7)):
            with self.assertRaises(ReadOnlyError):
                Reader.connect('synthetic', 'synthetic', *selectors)
        self.wire.instruments.append(self.wire.instruments[0].copy())
        with self.assertRaises(ReadOnlyError):
            self.reader()

    def test_reader_instrument_views_closed_state_and_redirect_refusal(self):
        reader = self.reader()
        self.assertEqual(reader.instruments()[0]['name'], 'XAUUSD.R')
        self.assertEqual(reader.details()['name'], 'XAUUSD.R')
        self.assertFalse(reader._sdk._session.trust_env)
        response = requests.Response()
        response.status_code = 302
        response.headers['Location'] = 'https://unapproved.invalid/'
        count = []
        def redirect(*args, **kwargs):
            count.append(1)
            return response
        with patch.object(requests.Session, 'request', redirect), self.assertRaises(ReadOnlyError):
            reader.quotes()
        self.assertEqual(count, [1])
        reader.close()
        with self.assertRaises(ReadOnlyError):
            reader.balances()
        self.assertIsNone(reader._sdk._credentials)
        self.assertEqual(reader._sdk._access_token, '')

    def test_sdk_numeric_coercion_cannot_change_account_or_instrument_identity(self):
        for field, value in [('id', 123.9), ('id', True), ('accNum', 7.2), ('id', '0123')]:
            with self.subTest(field=field, kind=type(value).__name__):
                self.wire.accounts[0] = {'id': 123, 'accNum': 7, 'name': 'synthetic', 'currency': 'USD'}
                self.wire.accounts[0][field] = value
                with self.assertRaises(ReadOnlyError):
                    self.reader()
        self.wire.accounts[0] = {'id': '123', 'accNum': '7', 'name': 'synthetic', 'currency': 'USD'}
        self.wire.instruments[0]['tradableInstrumentId'] = 456.9
        with self.assertRaises(ReadOnlyError):
            self.reader()

    def test_transport_refuses_writes_foreign_targets_and_unauthenticated_get(self):
        reader = self.reader()
        self.addCleanup(reader.close)
        sdk = reader._sdk
        count = len(self.wire.calls)
        base = 'https://demo.tradelocker.com/backend-api'
        attempts = [
            ('post', base + '/trade/accounts/123/orders', {}),
            ('patch', base + '/trade/positions/789', {}),
            ('delete', base + '/trade/accounts/123/orders', {}),
            ('get', 'https://live.tradelocker.com/backend-api/trade/config', {}),
            ('get', base + '/trade/accounts/999/positions', {}),
            ('get', base + '/trade/config', {'include_access_token': False}),
            ('get', base + '/trade/config', {'additional_params': {'unexpected': 'value'}}),
            ('get', base + '/trade/quotes', {'additional_params': {'tradableInstrumentId': 999, 'routeId': '17'}}),
        ]
        for method, url, kw in attempts:
            with self.subTest(method=method, path=url), self.assertRaises(ReadOnlyError):
                sdk._request(method, url, **kw)
        self.assertEqual(len(self.wire.calls), count)
        # Exercise the REAL SDK request builder against the deny boundary. No
        # HTTP write is sent, including when a caller bypasses Broker methods.
        from personal_trading.tradelocker.tl_candidate import quiet_call
        for instrument_id in (456, 999):
            with self.assertRaises(ReadOnlyError):
                quiet_call(lambda: sdk.create_order(instrument_id=instrument_id, quantity=0.02,
                    side='buy', type_='market', validity='IOC', position_netting=False))
        self.assertEqual(len(self.wire.calls), count)

    def test_real_lumibot_read_views_no_workers_or_orders(self):
        from personal_trading.tradelocker.lumi_adapter import TradeLockerBroker, TradeLockerData
        from lumibot.brokers.broker import Broker
        from lumibot.data_sources.data_source import DataSource
        from lumibot.entities import Asset, Bars, Position, Quote
        before = set(threading.enumerate())
        reader = self.reader()
        self.addCleanup(reader.close)
        broker = TradeLockerBroker(reader)
        self.assertIsInstance(broker, Broker)
        self.assertIsInstance(broker.data_source, DataSource)
        self.assertIsInstance(broker.data_source, TradeLockerData)
        self.assertEqual(set(threading.enumerate()), before)
        self.assertIsNone(broker._runtime_telemetry)
        self.assertIsNone(broker._orders_thread)
        asset = broker.asset
        self.assertIsInstance(asset, Asset)
        self.assertEqual(asset.asset_type, 'unknown')
        self.assertIsNone(asset.multiplier)
        quote = broker.data_source.get_quote(asset)
        self.assertIsInstance(quote, Quote)
        self.assertEqual((quote.bid, quote.ask, quote.price), (2500.0, 2500.5, None))
        bars = broker.data_source.get_historical_prices(asset, 1, 'minute', start_ms=1699999999000, end_ms=1700000001000)
        self.assertIsInstance(bars, Bars)
        self.assertEqual(bars.df.iloc[0]['close'], 2500.5)
        self.assertEqual(str(bars.df.index.tz), 'UTC')
        self.wire.positions = [[789, 456, 18, 'sell', 0.02, 2500.0]]
        positions = broker._pull_positions('inspection')
        self.assertIsInstance(positions[0], Position)
        self.assertEqual(positions[0].quantity, -0.02)
        self.assertEqual(positions[0].position_id, '789')
        self.assertEqual(broker.read_balances()['balance'], 54321.25)
        count = len(self.wire.calls)
        for method in ('submit_order', 'submit_orders', '_submit_order', 'cancel_order', 'cancel_orders', 'cancel_open_orders', 'modify_order', '_modify_order', 'sell_all', 'close_position', '_start_orders_thread', '_run_stream'):
            with self.assertRaises(ReadOnlyError):
                getattr(broker, method)(None)
        self.assertEqual(len(self.wire.calls), count)
        with self.assertRaises(ReadOnlyError):
            broker.data_source.get_quote(Asset('XAUUSD.R'))
        with self.assertRaises(ReadOnlyError):
            broker._get_balances_at_broker(None, None)

    def test_supervised_bridge_keeps_generic_order_hooks_disabled(self):
        from decimal import Decimal as D
        import tempfile
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        from test_safety import policy_fixture
        from test_demo_execution import approval
        from personal_trading.tradelocker.lumi_adapter import SupervisedDemoBroker
        p,q=policy_fixture()
        for pending in (False,True):
            with tempfile.TemporaryDirectory(prefix='hermes-bridge-',dir=tempfile.gettempdir()) as root, synthetic_reader() as (reader,wire):
                with Controller(reader,p,q,Path(root)/'state') as c:
                    before=set(threading.enumerate())
                    broker=SupervisedDemoBroker(c)
                    self.assertEqual(set(threading.enumerate()),before)
                    self.assertIsNone(broker.asset.multiplier)
                    with self.assertRaises(ReadOnlyError): broker.submit_order(None)
                    broker.submit_demo(approval(c,c.describe_submit(),'synthetic-bridge-submit'))
                    if pending:
                        wire.orders=[dict(wire.history[0],status='New',filledQty=0.,positionId=0)]
                        wire.history=[]; wire.positions=[]
                        self.assertEqual(broker.reconcile_demo(),'PENDING_OBSERVED')
                        broker.modify_demo_stop(D('2491'),approval(c,c.describe_modify_stop(D('2491')),'synthetic-bridge-modify'))
                        broker.cancel_demo(approval(c,c.describe_cancel(),'synthetic-bridge-cancel'))
                        self.assertEqual(broker.reconcile_demo(),'TERMINAL_NO_POSITION_OBSERVED')
                    else:
                        self.assertEqual(broker.reconcile_demo(),'OPEN_OBSERVED')
                        broker.close_demo(approval(c,c.describe_close(),'synthetic-bridge-close'))
                        self.assertEqual(broker.reconcile_demo(),'FLAT_SNAPSHOT_ONLY')

    def test_real_sdk_constructor_and_account_selection(self):
        before = set(threading.enumerate())
        reader = self.reader()
        self.addCleanup(reader.close)
        self.assertIsInstance(reader._sdk, TLAPI)
        self.assertEqual(reader.identity.account_id, 123)
        self.assertEqual(reader.identity.acc_num, 7)
        self.assertEqual(reader.identity.symbol, 'XAUUSD.R')
        self.assertEqual(reader.identity.instrument_id, 456)
        self.assertEqual(reader.identity.info_route_id, '17')
        self.assertEqual(reader.identity.trade_route_id, '18')
        self.assertEqual(reader.balances()['balance'], 54321.25)
        self.assertEqual(reader.positions(), [])
        self.assertEqual(set(threading.enumerate()), before)
        self.assertEqual(TLAPI._instances, {})
        for method, url, kw in self.wire.calls:
            self.assertFalse(kw['allow_redirects'])
            self.assertEqual(kw['timeout'], (5, 15))
            if '/trade/' in url:
                self.assertEqual(kw['headers']['accNum'], '7')
                self.assertIn('/123/', url) if '/accounts/' in url else None
                self.assertEqual(method, 'GET')


if __name__ == '__main__':
    unittest.main()
