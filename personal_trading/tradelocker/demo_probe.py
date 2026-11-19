"""Owner-terminal empirical demo routing probe, NOT strategy/real qualification.

Official hash-gated SDK authenticates/selects accounts and validates read tables.
The original Reader dispatcher stays write-denying. DemoProbeTransport separately
sends only an exact, durable, terminal-approved market order and its exact linked
position close. No generic SDK unlock, retries, bulk close, or live origin exists.
This cooperative dedicated-process tool is not a hostile-Python sandbox.
"""
import argparse
from contextlib import contextmanager
from dataclasses import asdict
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import threading
import time

from personal_trading.tradelocker.cli import _open_terminal, hidden_prompt
from personal_trading.tradelocker.journal import Journal
from personal_trading.tradelocker.safety import canonical_id, digest, sdk_float, wire_decimal
from personal_trading.tradelocker.tl_candidate import (
    ENVIRONMENT, SERVER, SYMBOL, Reader, ReadOnlyError, decode_response, quiet_call)

BASE = ENVIRONMENT + '/backend-api'
WARNING = ('UNPROTECTED routing test: no TP/SL. Quantity is API-native raw qty, '
           'NOT asserted lots/ounces; scale/economics/permissions/session unknown. '
           'NO DOLLAR LOSS CAP. Broker may reject. No strategy/profit validation. '
           'Do not trade this account in another process. Stop means operator reconciliation, '
           'NOT automatic flattening. Close requires a second exact terminal acknowledgement.')


def _foreground(terminal):
    # Never accept a caller-supplied armed flag, JSON qualification or stdin pipe.
    if (__name__ != '__main__' or threading.active_count() != 1 or
            any(name == 'pytest' or name.startswith('_pytest') or name == 'unittest.runner'
                for name in sys.modules) or
            any(key in os.environ for key in ('PYTEST_CURRENT_TEST', 'PYTEST_VERSION', 'UNITTEST_CURRENT_TEST')) or
            not all(os.isatty(fd) for fd in (0, 1, 2)) or
            not os.isatty(terminal.fileno()) or
            os.tcgetpgrp(terminal.fileno()) != os.getpgrp()):
        raise ReadOnlyError('Foreground standalone owner terminal required')


def _quantity(text):
    # No conversion from minLot/lotSize/tickCost; owner explicitly supplies raw qty.
    value = wire_decimal(text)
    sdk_float(value)  # refuse lossy SDK-style decimal representation
    if len(text) > 40:
        raise ReadOnlyError('Raw quantity representation too long')
    return str(sdk_float(value))


def _ask(terminal, label):
    _foreground(terminal)
    terminal.write(label)
    terminal.flush()
    answer = terminal.readline()
    if not answer:
        raise ReadOnlyError('Terminal cancelled')
    return answer.rstrip('\r\n')


def _resolve(reader):
    """Explicit fresh HTTP, not SDK's day-cached instrument listing."""
    i = reader.identity
    raw = reader._read(lambda: reader._sdk._request('get', BASE+f'/trade/accounts/{i.account_id}/instruments'))
    if (type(raw) is not dict or raw.get('s') != 'ok' or type(raw.get('d')) is not dict or
            type(raw['d'].get('instruments')) is not list):
        raise ReadOnlyError('Fresh instruments envelope refused')
    rows = raw['d']['instruments']
    ids = [canonical_id(r['tradableInstrumentId']) for r in rows]
    if len(ids) != len(set(ids)):
        raise ReadOnlyError('Duplicate instrument identity')
    matches = [row for row in rows if row.get('name') == SYMBOL]
    if len(matches) != 1:
        raise ReadOnlyError('Exact unique fresh XAUUSD.R required')
    row = matches[0]
    routes = row['routes']
    if type(routes) is not list:
        raise ReadOnlyError('Fresh routes refused')
    route_ids = [canonical_id(r['id']) for r in routes]
    if len(route_ids) != len(set(route_ids)):
        raise ReadOnlyError('Duplicate fresh routes')
    by_type = lambda kind: [canonical_id(r['id']) for r in routes if r.get('type') == kind]
    if (canonical_id(row['tradableInstrumentId']) != str(i.instrument_id) or
            by_type('INFO') != [i.info_route_id] or by_type('TRADE') != [i.trade_route_id]):
        raise ReadOnlyError('Fresh instrument/routes changed; no write')


def _snapshot(reader, seconds):
    start = time.monotonic()
    sdk = reader._sdk
    history = reader._read(lambda: sdk.get_all_orders(history=True).to_dict('records'))
    orders = reader._read(lambda: sdk.get_all_orders(history=False).to_dict('records'))
    positions = reader.positions()
    for rows in (history, orders, positions):
        ids = [canonical_id(row['id']) for row in rows]
        if len(ids) != len(set(ids)):
            raise ReadOnlyError('Duplicate snapshot identifiers')
    if time.monotonic() - start >= seconds:
        raise ReadOnlyError('Snapshot freshness budget exceeded')
    return history, orders, positions, start + seconds


def _market_action(identity, side, qty):
    if side not in ('buy', 'sell'):
        raise ReadOnlyError('Explicit direction required')
    return {'operation':'OPEN', 'method':'POST',
            'url':BASE+f'/trade/accounts/{identity.account_id}/orders',
            'body':{'price':None, 'qty':qty, 'routeId':identity.trade_route_id,
                    'side':side, 'validity':'IOC', 'tradableInstrumentId':str(identity.instrument_id),
                    'type':'market', 'takeProfit':None, 'takeProfitType':None,
                    'stopLoss':None, 'stopLossType':None, 'stopPrice':None, 'strategyId':None}}


def _owned(row, identity, side):
    return (canonical_id(row['tradableInstrumentId']) == str(identity.instrument_id) and
            canonical_id(row['routeId']) == identity.trade_route_id and row['side'] == side)


def _linked(snapshot, identity, side, qty, order_id, baseline):
    """None means wholly unfilled/not yet linked. Contradictions always refuse."""
    history, orders, positions, _ = snapshot
    new_ids = {canonical_id(row['id']) for row in history} - baseline
    if new_ids - {order_id}:
        raise ReadOnlyError('Unrelated account history changed')
    matches = [r for r in history if canonical_id(r['id']) == order_id]
    if not matches and not positions:
        # Only the exact wholly-unfilled market order can remain pending.
        if orders and not (len(orders) == 1 and canonical_id(orders[0]['id']) == order_id and
                _owned(orders[0], identity, side) and orders[0]['type'] == 'market' and
                orders[0]['status'] == 'New' and wire_decimal(orders[0]['qty']) == wire_decimal(qty) and
                wire_decimal(orders[0]['filledQty']) == 0 and orders[0]['positionId'] == 0):
            raise ReadOnlyError('Unknown pending-order state')
        return None
    if len(matches) != 1:
        raise ReadOnlyError('Unlinked position; operator reconciliation required')
    row = matches[0]
    if not (_owned(row, identity, side) and row['type'] == 'market' and
            wire_decimal(row['qty']) == wire_decimal(qty)):
        raise ReadOnlyError('Order identity/quantity contradicted')
    if row['status'] in ('Rejected', 'Canceled', 'Cancelled') and not orders and not positions and wire_decimal(row['filledQty']) == 0:
        return 'TERMINAL_NO_FILL_OBSERVED'
    if (row['status'] != 'Filled' or wire_decimal(row['filledQty']) != wire_decimal(qty) or
            orders or len(positions) != 1):
        raise ReadOnlyError('Partial, unlinked or unknown fill; no further writes')
    position = positions[0]
    if not (_owned(position, identity, side) and
            canonical_id(position['id']) == canonical_id(row['positionId']) and
            wire_decimal(position['qty']) == wire_decimal(qty) and
            position['stopLossId'] == 0 and position['takeProfitId'] == 0):
        raise ReadOnlyError('Exact unprotected position linkage refused')
    return {'operation':'CLOSE', 'method':'DELETE',
            'url':BASE+'/trade/positions/'+canonical_id(position['id']),
            'body':{'qty':_quantity(str(position['qty']))}}


class DemoProbeTransport:
    """Isolated two-operation transport; Reader inherited writes remain blocked.

    Every request is counted before dispatch. Once config is known, conservatively
    apply EVERY returned rate window to ALL probe HTTP (including refresh). This
    is stricter than per-route rates and avoids guessing close-route mappings.
    Bootstrap is bounded by the same owner count/deadline and has no retries.
    """
    def __init__(self, terminal, journal, args):
        _foreground(terminal)
        self.terminal, self.journal, self.args = terminal, journal, args
        self.deadline = time.monotonic() + args.max_seconds
        self.limits = []
        self.times = []
        self.http_attempts = 0
        self.reader = None
        self.network = None
        self.used = set()

    def _check(self):
        _foreground(self.terminal)
        self.journal.require_open()
        if self.journal.restarted or time.monotonic() >= self.deadline:
            raise ReadOnlyError('Restart or total elapsed-time gate; never replay')
        if self.reader is not None:
            i = self.reader.identity
            if (i.environment, i.server, i.symbol, i.currency) != (ENVIRONMENT, SERVER, SYMBOL, 'USD'):
                raise ReadOnlyError('Exact demo identity refused')
            self.reader._read(lambda: None)
            if self.reader._sdk._execution_guard is not None:
                raise ReadOnlyError('Reader execution guard must remain unset')

    def configure(self, config):
        raw = config.get('rateLimits')
        if type(raw) is not list or not raw:
            raise ReadOnlyError('Returned rate-limit array required')
        limits = []
        for row in raw:
            if (type(row) is not dict or type(row.get('rateLimitType')) is not str or
                    row.get('measure') not in ('SECONDS', 'MINUTES') or
                    type(row.get('intervalNum')) is not int or row['intervalNum'] <= 0 or
                    type(row.get('limit')) is not int or row['limit'] <= 0):
                raise ReadOnlyError('Unknown returned rate limit')
            limits.append((row['intervalNum'] * (60 if row['measure'] == 'MINUTES' else 1), row['limit']))
        self.limits = limits
        self.journal.append({'kind':'CONFIG_RATE_WINDOWS', 'windows':limits,
                             'admission':'ALL_WINDOWS_CONSERVATIVELY_APPLIED_TO_ALL_HTTP'})

    def _admit(self):
        # Durable attempt accounting is separate from dispatch timestamps: fsync
        # (and mutation INTENT/token preparation) may block for an entire window.
        self._check()
        if self.http_attempts >= self.args.max_http:
            raise ReadOnlyError('Owner HTTP budget exhausted')
        self.http_attempts += 1
        self.journal.append({'kind':'HTTP_ATTEMPT', 'number':self.http_attempts})

    def _dispatch(self, session, method, url, *, approval=None, freshness=None, **kwargs):
        # No durability or token work after this final pacing boundary. Bootstrap,
        # reads/refresh and mutations all share the same actual dispatch history.
        while True:
            self._check()
            now = time.monotonic()
            delay = 0.
            for window, count in self.limits:
                recent = [t for t in self.times if now-t < window]
                if len(recent) >= count:
                    delay = max(delay, recent[-count]+window-now+.001)
            if not delay:
                break
            if now+delay >= self.deadline:
                raise ReadOnlyError('Config pacing exceeds total time budget')
            time.sleep(delay)
        if approval is not None and (time.time() >= approval['expires_at'] or time.monotonic() >= freshness):
            raise ReadOnlyError('Final approval/snapshot expired; intent consumed, no replay')
        self.times.append(time.monotonic())
        return self.network(session, method, url, **kwargs)

    @contextmanager
    def connection(self):
        # Reader constructs requests.Session itself. Dedicated single-threaded
        # scope installs a bounded session type ONLY during its construction.
        # Its original dispatcher and all SDK/source gates are left untouched.
        import requests
        original = requests.Session
        self.network = original.request
        transport = self
        class ProbeSession(original):
            def request(session, method, url, **kwargs):
                if (not url.startswith(BASE+'/') or
                        (method != 'GET' and not (method == 'POST' and
                         url in {BASE+'/auth/jwt/token', BASE+'/auth/jwt/refresh'}))):
                    raise ReadOnlyError('Probe session permits only original bounded Reader reads/auth')
                transport._admit()
                return transport._dispatch(session, method, url, **kwargs)
        requests.Session = ProbeSession
        try:
            yield
        finally:
            requests.Session = original

    def approve(self, action):
        self._check()
        expiry = int(time.time()) + self.args.approval_seconds
        preview = {'binding':asdict(self.reader.identity), 'action':action, 'expires_at':expiry,
                   'owner_budgets':{key:getattr(self.args, key) for key in
                       ('max_http', 'poll_rounds', 'max_seconds', 'approval_seconds', 'snapshot_seconds')},
                   'disclosure':WARNING, 'demo_evidence':'fixed demo origin PLUS owner selected-account attestation; not independent account proof'}
        token = 'APPROVE DEMO UNPROTECTED NO_DOLLAR_CAP '+digest(preview)
        self.terminal.write('\nEXACT REQUEST PREVIEW '+json.dumps(preview, sort_keys=True, allow_nan=False)+'\n')
        self.terminal.write('By typing this token, I confirm this exact selected account is DEMO, exclusive to this probe.\nACK TOKEN: '+token+'\n')
        if _ask(self.terminal, 'APPROVAL> ') != token:
            raise ReadOnlyError('Exact acknowledgement refused')
        self.journal.append({'kind':'OWNER_APPROVAL', 'preview':preview, 'digest':digest(preview)})
        return preview

    def write(self, action, approval, freshness):
        self._check()
        operation = action['operation']
        # Derive allowed OPEN internally. CLOSE additionally requires observed
        # linked proof held by the caller and exact re-read before this method.
        expected = _market_action(self.reader.identity, self.args.side, self.args.quantity)
        if operation == 'CLOSE':
            observed = [e for e in self.journal.events if e['kind'] == 'LINKED_POSITION']
            expected = observed[-1]['action'] if observed else None
        if (operation not in ('OPEN', 'CLOSE') or operation in self.used or action != expected or
                approval.get('action') != action or approval.get('binding') != asdict(self.reader.identity) or
                not any(e['kind'] == 'OWNER_APPROVAL' and e['digest'] == digest(approval) for e in self.journal.events) or
                any(e['kind'] == 'INTENT' and e['operation'] == operation for e in self.journal.events)):
            raise ReadOnlyError('No exact one-use approved probe operation')
        # Refresh, if required, happens BEFORE final approval/freshness consumption.
        headers = quiet_call(lambda:self.reader._sdk._get_headers(include_access_token=True, include_acc_num=True))
        self._admit()
        self.used.add(operation)
        self.journal.append({'kind':'INTENT', 'operation':operation, 'action':action,
                             'approval_digest':digest(approval)})
        # This is the ONLY mutating call. No redirects, environment proxies, SDK
        # write inheritance, netting, zero-quantity/full-close shortcut or retries.
        response = self._dispatch(self.reader._sdk._session, action['method'], action['url'],
                                approval=approval, freshness=freshness,
                                headers=headers, params=None, json=action['body'],
                                timeout=(5, 15), allow_redirects=False)
        status = response.status_code
        self.journal.append({'kind':'BROKER_RESPONSE', 'operation':operation,
                             'http_status':status if type(status) is int and 100 <= status <= 599 else None})
        if status not in ((200, 201) if operation == 'OPEN' else (200,)):
            raise ReadOnlyError('Broker rejected/unknown HTTP outcome; no replay')
        raw = decode_response(response)
        if type(raw) is not dict or raw.get('s') != 'ok':
            self.journal.append({'kind':'BROKER_RESULT', 'operation':operation, 'result':'NOT_OK'})
            raise ReadOnlyError('Broker did not acknowledge success; no replay')
        result = canonical_id(raw['d']['orderId']) if operation == 'OPEN' else True
        self.journal.append({'kind':'ACK', 'operation':operation, 'order_id':result if operation == 'OPEN' else None})
        return result


def _run(terminal, args, journal):
    transport = DemoProbeTransport(terminal, journal, args)
    terminal.write(WARNING+'\nBudgets '+json.dumps({key:getattr(args,key) for key in
        ('max_http','poll_rounds','max_seconds','approval_seconds','snapshot_seconds')})+'\n')
    terminal.write('Broker-issued trading email (visible): '+args.email+'\n')
    if _ask(terminal, 'CONNECT ACK> ') != 'CONNECT DEMO READS':
        raise ReadOnlyError('Connection acknowledgement refused')
    password = hidden_prompt('Broker-issued trading password (hidden)> ')
    def select(options):
        value = hidden_prompt('\nReturned accounts (no default):\n'+'\n'.join(options)+'\nSelect account number (hidden)> ')
        if not value.isascii() or not value.isdecimal() or value.startswith('0'):
            raise ReadOnlyError('Explicit returned-account choice required')
        return int(value)
    reader = None
    try:
        with transport.connection():
            reader = Reader.discover(args.email, password, select)
        del password
        transport.reader = reader
        reader._sdk._execution_reads = True  # repaired raw table validation, NOT write arming
        # Explicit fresh config, not cached configuration as rate evidence.
        config = reader._read(lambda:reader._sdk._request('get', BASE+'/trade/config'))['d']
        transport.configure(config)
        _resolve(reader)
        details = reader.details()
        journal.append({'kind':'CONTRACT_OBSERVATION', 'semantics':'UNQUALIFIED; no units/risk inferred',
                        'instrument_name':details['name']})
        initial = _snapshot(reader, args.snapshot_seconds)
        if initial[1] or initial[2]:
            raise ReadOnlyError('Entire selected account must be flat with zero pending orders')
        baseline = {canonical_id(row['id']) for row in initial[0]}
        action = _market_action(reader.identity, args.side, args.quantity)
        approval = transport.approve(action)
        _resolve(reader)
        fresh = _snapshot(reader, args.snapshot_seconds)
        if fresh[1] or fresh[2] or {canonical_id(r['id']) for r in fresh[0]} != baseline:
            raise ReadOnlyError('Account changed after approval; no write')
        order_id = transport.write(action, approval, fresh[3])
        if order_id in baseline:
            raise ReadOnlyError('Acknowledged identifier was already in initial history; no close')
        close = None
        for _ in range(args.poll_rounds):
            observed = _snapshot(reader, args.snapshot_seconds)
            close = _linked(observed, reader.identity, args.side, args.quantity, order_id, baseline)
            if close is not None:
                break
        if close == 'TERMINAL_NO_FILL_OBSERVED':
            row = next(r for r in observed[0] if canonical_id(r['id']) == order_id)
            journal.append({'kind':'ORDER_TERMINAL_OBSERVATION', 'order_id':order_id,
                            'status':row['status'], 'filled_qty':'0'})
            journal.append({'kind':'STATUS', 'status':close})
            terminal.write(close+'; no retries.\n')
            return 0
        if close is None:
            raise ReadOnlyError('Polling budget exhausted without exact linked fill')
        journal.append({'kind':'LINKED_POSITION', 'action':close})
        approval = transport.approve(close)
        _resolve(reader)
        observed = _snapshot(reader, args.snapshot_seconds)
        if _linked(observed, reader.identity, args.side, args.quantity, order_id, baseline) != close:
            raise ReadOnlyError('Linked position changed after close approval')
        transport.write(close, approval, observed[3])
        for _ in range(args.poll_rounds):
            final = _snapshot(reader, args.snapshot_seconds)
            if not final[1] and not final[2]:
                status = 'CLOSE_ACK_FLAT_SNAPSHOT_OBSERVED_COSTS_UNKNOWN'
                journal.append({'kind':'STATUS', 'status':status})
                terminal.write(status+'; NOT proof of fill price, PnL or profitability.\n')
                return 0
        raise ReadOnlyError('Close not observed flat within poll budget; no replay')
    finally:
        if reader is not None:
            reader.close()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--email', required=True, help='Explicit visible nonsecret broker trading email')
    result.add_argument('--state-directory', required=True, type=Path, help='Persistent private one-attempt journal; never reset/reuse another path to retry')
    result.add_argument('--quantity', required=True, help='Explicit positive API-native raw qty, NOT lots/ounces or a USD risk cap')
    result.add_argument('--side', required=True, choices=('buy','sell'))
    for name in ('max-http','poll-rounds','max-seconds','approval-seconds','snapshot-seconds'):
        result.add_argument('--'+name, required=True, type=int)
    return result


def main(argv=None):
    if __name__ != '__main__':
        print('Probe requires explicit foreground module entry.', file=sys.stderr)
        return 2
    args = parser().parse_args(argv)
    journal = None
    try:
        with _open_terminal() as terminal:
            _foreground(terminal)
            if (not args.email or len(args.email) > 254 or not args.email.isascii() or
                    any(ord(c) < 33 or ord(c) > 126 for c in args.email) or
                    not 1 <= args.poll_rounds <= 20 or
                    not 20+6*args.poll_rounds <= args.max_http <= 500 or
                    not 1 <= args.approval_seconds <= 120 or
                    not 1 <= args.snapshot_seconds <= 60 or
                    not args.approval_seconds <= args.max_seconds <= 600):
                raise ReadOnlyError('Explicit bounded inputs refused')
            args.quantity = _quantity(args.quantity)
            # Do not inspect inherited credential values; disable all ambient
            # config/telemetry and proxies BEFORE third-party SDK imports.
            os.environ.clear()
            os.environ.update(PATH='/usr/bin:/bin', LANG='C.UTF-8')
            journal = Journal(args.state_directory, digest({'probe':'demo-routing/1','origin':ENVIRONMENT,'server':SERVER,'symbol':SYMBOL}))
            if journal.restarted:
                raise ReadOnlyError('Existing attempt is reconciliation-only; no restart/replay')
            return _run(terminal, args, journal)
    except (Exception, KeyboardInterrupt):
        if journal is not None:
            try:
                journal.append({'kind':'STOP', 'status':'OPERATOR_RECONCILIATION_REQUIRED_NO_REPLAY'})
            except Exception:
                pass
        print('Probe stopped/refused. OPERATOR_RECONCILIATION_REQUIRED_NO_REPLAY. '
              'No further writes; inspect private journal and broker UI. Never reset state to retry. '
              'No raw SDK/broker/credential details emitted.', file=sys.stderr)
        return 2
    finally:
        if journal is not None:
            journal.close()


if __name__ == '__main__':
    raise SystemExit(main())
