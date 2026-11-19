"""Fixture-only full installed-runtime imports and idle constructors; NEVER runs trading.

Run in the assigned fresh dedicated venv with -B -W error. All network, worker
starts and subprocesses are denied before third-party imports. Actual candidate
Reader and SupervisedDemoBroker run over C's synthetic HTTP boundary only.
Strategy/Trader stay idle; no trading requests, hooks or approvals are invoked.
"""
import hashlib
import importlib.metadata as metadata
import inspect
import json
import logging
from decimal import Decimal as D
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import urlparse

from personal_trading.tradelocker import runtime

# Explicit idle-probe-only configuration; never a strategy runner mode.
runtime.SAFE_ENV.update({
    'LUMIBOT_SCHEDULED_EXECUTION': '1',
    'OPENBLAS_NUM_THREADS': '1',
    'OMP_NUM_THREADS': '1',
})
runtime.configure()
# runtime.configure verifies the installed distribution and removes source shadows.

blocked = []
started = []
denied_ipv6_probes = []


def audit(event, args):
    # urllib3 imports a loopback capability probe and catches its failure.
    # Deny the bind (no socket exposure), but account for this exact known probe
    # separately from external connections or unexpected IO.
    if event == 'socket.bind' and args[1] == ('::1', 0):
        caller = sys._getframe(1)
        if (caller.f_code.co_name == '_has_ipv6'
                and caller.f_code.co_filename.endswith('/urllib3/util/connection.py')):
            denied_ipv6_probes.append('urllib3._has_ipv6 loopback bind DENIED')
            raise AssertionError('Loopback bind prohibited')
    if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo',
                 'subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'}:
        blocked.append(event)
        raise AssertionError('External IO/process prohibited in offline probe')


def no_thread(*args, **kwargs):
    started.append('Thread.start')
    raise AssertionError('Worker prohibited in offline probe')


sys.addaudithook(audit)
before = set(threading.enumerate())
with patch.object(threading.Thread, 'start', no_thread):
    import lumibot
    from lumibot.brokers.broker import Broker
    from lumibot.data_sources.data_source import DataSource
    from lumibot.strategies import Strategy
    from lumibot.traders import Trader
    from tradelocker.tradelocker_api import TLAPI
    from personal_trading.tradelocker.tl_candidate import Reader, ReadOnlyError, _sdk_class, SDK_HASHES
    from personal_trading.tradelocker.lumi_adapter import SupervisedDemoBroker
    from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
    from personal_trading.tradelocker.safety import Qualification, RiskPolicy, RateLimits

    # Attest every imported framework module so a partial source path cannot
    # masquerade as the installed full-code wheel.
    distribution = metadata.distribution('lumibot')
    installed_root = Path(distribution.locate_file('lumibot')).resolve()
    assert Path(lumibot.__file__).resolve().parent == installed_root
    manifest = json.loads(distribution.read_text('source-manifest.json'))
    for relative, digest in manifest['files'].items():
        if relative.startswith('lumibot/'):
            assert hashlib.sha256(Path(distribution.locate_file(relative)).read_bytes()).hexdigest() == digest
    for cls in (Broker, DataSource, Strategy, Trader):
        assert Path(inspect.getfile(cls)).resolve().is_relative_to(installed_root)
    sdk_root = Path(inspect.getfile(TLAPI)).parent
    for relative, digest in SDK_HASHES.items():
        assert hashlib.sha256((sdk_root / relative).read_bytes()).hexdigest() == digest

    assert metadata.version('tradelocker') == '0.56.2+lumibot.runtime.1'
    # Exercise C's accepted exact installed-version/metadata/source-hash gate;
    # never patch that gate or the Reader/SDK/broker/runner implementations.
    assert issubclass(_sdk_class(), TLAPI)
    hooks = []

    def no_hook(*args, **kwargs):
        hooks.append('strategy hook')
        raise AssertionError('Strategy lifecycle prohibited in idle probe')

    logging.disable(sys.maxsize)
    with tempfile.TemporaryDirectory(prefix='hermes-runtime-idle-',
                                      dir=tempfile.gettempdir()) as scratch:
        with synthetic_reader() as (reader, wire):
            assert type(reader) is Reader and isinstance(reader._sdk, TLAPI)
            identity = reader.identity
            # Explicit SYNTHETIC constructor inputs, copied from C's fixture
            # contract, not an owner policy or real economics/permission proof.
            qualification = Qualification(
                identity, D('.01'), D('1'), D('.01'), D('.01'), D('10'),
                D('3000'), D('20'), D('2'), True, True, True, True, True,
                'SYNTHETIC idle runtime constructor fixture', True, True, True,
                True, 'synthetic_sdk_quantity_equals_fixture_lots', int(time.time()) + 300,
                RateLimits(200, D('60'), 30, 30, 'SYNTHETIC constructor budget', True))
            policy = RiskPolicy(identity, 'buy', D('.02'), D('2490'), D('2520'),
                                D('100'), D('10'), D('1'), D('1'), D('5'), D('5'), D('60'))
            with Controller(reader, policy, qualification, Path(scratch) / 'state') as controller:
                broker = SupervisedDemoBroker(controller)
                assert broker.reader is reader and broker.controller is controller
                quote = broker.data_source.get_quote(broker.asset)
                assert (quote.bid, quote.ask) == (2500.0, 2500.5)
                assert broker.read_balances()['balance'] == 54321.25
                assert reader.positions() == []
                calls_before_refusal = len(wire.calls)
                try:
                    broker.submit_order(None)
                except ReadOnlyError:
                    pass
                else:
                    raise AssertionError('Generic framework order route must stay disabled')
                assert len(wire.calls) == calls_before_refusal
                with patch.object(Strategy, 'initialize', no_hook), \
                     patch.object(Strategy, 'on_trading_iteration', no_hook):
                    strategy = Strategy(
                        broker=broker, data_source=broker.data_source, name='OfflineConstructorOnly',
                        synchronize_broker_on_start=False, risk_free_rate=0,
                        benchmark_asset=None, analyze_backtest=False,
                        should_backup_variables_to_database=False,
                        should_send_summary_to_discord=False, save_logfile=False)
                    trader = Trader(strategies=[], logfile='', quiet_logs=True)
                assert isinstance(broker, Broker) and isinstance(broker.data_source, DataSource)
                assert isinstance(strategy, Strategy) and isinstance(trader, Trader)
                assert broker._orders_thread is None and broker._runtime_telemetry is None
                assert strategy._executor_instance is None
                assert not trader._strategies and not trader._pool and not hooks
                assert controller.status == 'DISARMED_UNTIL_EXACT_APPROVAL'
                assert not any(event['kind'] in {'INTENT', 'DISPATCH'}
                               for event in controller.journal.events)
            synthetic_calls = []
            trade_writes = []
            for method, url, _ in wire.calls:
                parsed = urlparse(url)
                assert parsed.scheme == 'https' and parsed.netloc == 'demo.tradelocker.com'
                synthetic_calls.append(method + ' ' + parsed.path)
                if method != 'GET' and not parsed.path.startswith('/backend-api/auth/'):
                    trade_writes.append(method)
            assert not trade_writes
        assert reader._closed and reader._sdk._credentials is None
        assert reader._sdk._access_token == reader._sdk._refresh_token == ''
    assert not TLAPI._instances
    assert set(threading.enumerate()) == before
    assert not blocked and not started, {'denied_events': blocked, 'denied_worker_starts': started}
    evidence = {
        'framework_distribution': metadata.version('lumibot'),
        'sdk_distribution': metadata.version('tradelocker'),
        'requests': metadata.version('requests'),
        'PyJWT': metadata.version('PyJWT'),
        'installed_framework_import': True,
        'framework_module': 'installed distribution: lumibot/__init__.py',
        'real_sdk_constructor': True,
        'real_broker_data_strategy_trader_constructors': True,
        'external_connections': 0,
        'denied_ipv6_loopback_probes': len(denied_ipv6_probes),
        'workers_started': len(started),
        'strategies_registered': len(trader._strategies),
        'strategy_executor_built': strategy._executor_instance is not None,
        'broker_telemetry': broker._runtime_telemetry,
        'synthetic_http_calls': synthetic_calls,
        'adapter_version_gate': 'ACCEPTED_EXACT_LOCAL_IDENTITY',
        'real_candidate_reader': type(reader) is Reader,
        'real_supervised_demo_broker': type(broker) is SupervisedDemoBroker,
        'reader_closed_and_tokens_cleared': reader._closed and not reader._sdk._access_token
            and not reader._sdk._refresh_token and reader._sdk._credentials is None,
        'strategy_hooks_called': len(hooks),
        'synthetic_trade_write_methods': trade_writes,
        'account_calls_performed': False,
        'actual_execution': 'blocked_unqualified_contract',
        'scope': 'installed runtime + actual candidate Reader/supervised bridge + idle constructors over SYNTHETIC_HTTP_ONLY; no runner execution or account proof',
    }
print(json.dumps(evidence, indent=2, sort_keys=True))
