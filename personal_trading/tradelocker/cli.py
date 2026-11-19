#!/usr/bin/env python3
"""Owner-terminal read-only command. Never invoke from chat/background worker."""
import argparse
import json
import re
import sys


class PromptError(RuntimeError):
    pass


def safe_diagnostic(value):
    if type(value) is str and re.fullmatch(r'(AUTH|REFRESH|ACCOUNT_LIST|CONFIG|INSTRUMENTS|STATE|POSITIONS|DETAILS|QUOTES|HISTORY)_HTTP_STATUS_[1-5][0-9]{2}', value):
        return value
    return 'UNCLASSIFIED'


def _open_terminal():
    import io
    # Text update-mode open requires seekability; a controlling TTY is not seekable.
    return io.TextIOWrapper(open('/dev/tty', 'r+b', buffering=0),
                            encoding='utf-8', line_buffering=True)


def hidden_prompt(label):
    """Linux controlling-TTY input, ECHO disabled, no stdin/echo fallback."""
    import termios
    failed = False
    value = None
    try:
        with _open_terminal() as terminal:
            fd = terminal.fileno()
            old = termios.tcgetattr(fd)
            new = old.copy()
            new[3] &= ~termios.ECHO
            try:
                termios.tcsetattr(fd, termios.TCSAFLUSH, new)
                terminal.write(label)
                terminal.flush()
                value = terminal.readline()
            finally:
                termios.tcsetattr(fd, termios.TCSAFLUSH, old)
                terminal.write('\n')
                terminal.flush()
    except (OSError, termios.error):
        failed = True
    if failed or not value or not value.rstrip('\r\n'):
        raise PromptError('Controlling terminal with hidden input is required')
    return value.rstrip('\r\n')


def parser():
    result = argparse.ArgumentParser(description='Disarmed, demo-only TradeLocker/LumiBot inspection candidate')
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='Offline status; no dependencies or network')
    commands.add_parser('prepare', help='Offline controlled-demo prerequisites; never arms execution')
    commands.add_parser('smoke', help='Synthetic offline real-SDK open/reconcile/close and modify/cancel; never accounts')
    read = commands.add_parser('read', help='Owner-terminal hidden-input read-only account/data inspection')
    read.add_argument('--email', required=True,
                      help='Non-secret trading email, displayed locally; password is always hidden')
    read.add_argument('--start-ms', type=int)
    read.add_argument('--end-ms', type=int)
    read.add_argument('--length', type=int, default=10)
    read.add_argument('--timestep', choices=('minute', 'hour', 'day'), default='minute')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == 'status':
        print(json.dumps({'candidate': 'read-only', 'execution': 'disarmed', 'authenticated_verification': False, 'deployed': False}))
        return 0
    if args.command == 'prepare':
        print(json.dumps({'execution': 'disarmed', 'offline_preparation': True, 'required_before_actual_demo_test': [
            'Qualified exact XAUUSD.R instrument sizing, units and session/account eligibility',
            'Owner-selected direction, quantity and explicit exposure cap; no defaults',
            'Precise demo accountId plus accNum confirmation',
            'Explicit one-use consent to exact bounded open/reconcile/close test',
            'Independent review of future non-retrying execution and ambiguous-result reconciliation',
        ], 'live_or_funded': 'prohibited', 'armed_route': 'blocked: unqualified REST quantity/economics/permissions/session',
            'offline_synthetic_smoke': 'python -m personal_trading.tradelocker smoke'}))
        return 0
    if args.command == 'smoke':
        failed=False
        summary=None
        try:
            from personal_trading.tradelocker.runtime import configure
            configure()
            from personal_trading.tradelocker.demo_execution import run_synthetic_smoke
            summary=run_synthetic_smoke()
        except (Exception, KeyboardInterrupt):
            failed=True
        if failed:
            print('Synthetic smoke failed. Stage: offline SDK/controller; execution remains disarmed. No raw details emitted.',file=sys.stderr)
            return 1
        print(json.dumps(summary,allow_nan=False))
        return 0
    if (args.start_ms is None) != (args.end_ms is None):
        parser().error('Provide both --start-ms and --end-ms, or neither')
    if args.start_ms is not None and (args.start_ms <= 0 or args.start_ms >= args.end_ms):
        parser().error('History bounds must be positive and increasing')
    if not 1 <= args.length <= 1000:
        parser().error('Length must be between 1 and 1000')
    reader = None
    failed = False
    summary = None
    stage = 'initialization'
    diagnostic = 'UNCLASSIFIED'
    try:
        from personal_trading.tradelocker.runtime import configure
        configure()
        from personal_trading.tradelocker.tl_candidate import Reader, quiet_call
        def load_broker():
            from personal_trading.tradelocker.lumi_adapter import TradeLockerBroker
            return TradeLockerBroker
        Broker = quiet_call(load_broker)
        print('DEMO / GATESFX / exact XAUUSD.R / read-only; execution stays disarmed.', flush=True)
        username = args.email
        if not username or len(username) > 254 or not username.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in username):
            raise PromptError('Invalid trading email')
        print('Broker-issued trading email: ' + username, flush=True)
        stage = 'password prompt'
        password = hidden_prompt('Broker-issued trading password (hidden; not Apple password): ')
        def select_account(options):
            nonlocal stage
            stage = 'account selection'
            choice = hidden_prompt('\nReturned accounts (identifiers masked; no default):\n' +
                                   '\n'.join(options) + '\nSelect explicit listed number (hidden; blank cancels): ')
            # Refuse signs, whitespace, zero/leading-zero defaults and raw IDs.
            if not choice.isascii() or not choice.isdecimal() or choice.startswith('0'):
                raise PromptError('Explicit listed account number required')
            return int(choice)
        stage = 'authentication/account discovery'
        reader = Reader.discover(username, password, select_account)
        del username, password
        stage = 'LumiBot initialization'
        broker = quiet_call(lambda: Broker(reader))
        stage = 'account balance'
        balances = broker.read_balances()
        stage = 'positions'
        positions = reader.positions()
        stage = 'instrument details'
        details = reader.details()
        stage = 'quotes'
        quote = broker.data_source.get_quote(broker.asset)
        # Fixed whitelist, no SDK dumps, raw exceptions, usernames or tokens.
        fields = ('name', 'type', 'lotSize', 'lotStep', 'minLot', 'maxLot', 'baseCurrency', 'quotingCurrency', 'tickSize', 'tickCost', 'symbolStatus', 'tradeSessionId', 'tradeSessionStatusId')
        summary = {'environment': 'demo', 'server': 'GATESFX', 'symbol': reader.identity.symbol,
            'execution': 'disarmed', 'account_binding': 'exact accountId/accNum matched',
            'currency': reader.identity.currency, 'balance': balances.get('balance'),
            'positions_count': len(positions), 'bid': quote.bid, 'ask': quote.ask,
            'instrument_fields_unqualified': {k: details[k] for k in fields if k in details},
            'qualification': 'read-only observation only; no strategy/order eligibility proven'}
        if args.start_ms is not None:
            stage = 'history'
            bars = broker.data_source.get_historical_prices(broker.asset, args.length, args.timestep,
                start_ms=args.start_ms, end_ms=args.end_ms)
            summary['bars_returned'] = len(bars.df)
        encoded = json.dumps(summary, allow_nan=False)
    except (Exception, KeyboardInterrupt) as exc:
        diagnostic = safe_diagnostic(getattr(exc, 'diagnostic_code', None))
        failed = True
    finally:
        if reader is not None:
            reader.close()
    if failed:
        print(f'Read-only inspection failed or cancelled. Stage: {stage}; code: {diagnostic}. '
              'No raw SDK details emitted; execution remains disarmed.', file=sys.stderr)
        return 1
    print(encoded)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
