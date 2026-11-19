import contextlib
import io
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from personal_trading.tradelocker import cli


class CLITests(unittest.TestCase):
    def test_status_and_rehearsal_are_dependency_free_and_offline(self):
        for command in ('status', 'prepare'):
            out = io.StringIO()
            with patch.object(socket.socket, 'connect', side_effect=AssertionError('socket prohibited')), contextlib.redirect_stdout(out):
                self.assertEqual(cli.main([command]), 0)
            self.assertIn('disarmed', out.getvalue())
        self.assertNotIn('password', cli.parser().format_help().lower())
        self.assertNotIn('--arm', cli.parser().format_help())

    def test_hidden_prompt_refuses_without_controlling_tty(self):
        with patch('builtins.open', side_effect=OSError('synthetic-no-tty')):
            with self.assertRaises(cli.PromptError):
                cli.hidden_prompt('Synthetic: ')

    def test_hidden_prompt_disables_echo_and_restores_terminal(self):
        import termios
        class Terminal:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def fileno(self): return 9
            def write(self, value): return len(value)
            def flush(self): pass
            def readline(self): return 'synthetic-input\n'
        old = [0, 0, 0, termios.ECHO | termios.ICANON, 0, 0, []]
        modes = []
        with patch.object(cli, '_open_terminal', return_value=Terminal()), patch.object(termios, 'tcgetattr', return_value=old), patch.object(termios, 'tcsetattr', side_effect=lambda fd, when, mode: modes.append(mode)):
            self.assertEqual(cli.hidden_prompt('Synthetic: '), 'synthetic-input')
        self.assertEqual(len(modes), 2)
        self.assertFalse(modes[0][3] & termios.ECHO)
        self.assertEqual(modes[1], old)

    def test_real_controlling_tty_hidden_prompts_do_not_echo(self):
        import os
        import pty
        import select
        import signal
        import termios
        import time
        pid, fd = pty.fork()
        if pid == 0:
            try:
                with open('/dev/tty', 'rb', buffering=0) as tty:
                    before = termios.tcgetattr(tty.fileno())
                first = cli.hidden_prompt('SYNTHETIC EMAIL: ')
                second = cli.hidden_prompt('SYNTHETIC PASSWORD: ')
                with open('/dev/tty', 'rb', buffering=0) as tty:
                    restored = termios.tcgetattr(tty.fileno()) == before
                passed = first == 'synthetic-email-marker' and second == 'synthetic-password-marker' and restored
                print('PTY_PASS' if passed else 'PTY_BAD', flush=True)
                os._exit(0 if passed else 1)
            except BaseException:
                print('PTY_FAILED', flush=True)
                os._exit(1)
        output = b''
        sent = set()
        status = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if select.select([fd], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output += chunk
                    for prompt, answer in ((b'SYNTHETIC EMAIL: ', b'synthetic-email-marker'),
                                           (b'SYNTHETIC PASSWORD: ', b'synthetic-password-marker')):
                        if prompt in output and prompt not in sent:
                            self.assertFalse(termios.tcgetattr(fd)[3] & termios.ECHO)
                            os.write(fd, answer + b'\n')
                            sent.add(prompt)
                if b'PTY_PASS' in output or b'PTY_FAILED' in output or b'PTY_BAD' in output:
                    break
            self.assertIn(b'PTY_PASS', output)
            self.assertNotIn(b'synthetic-email-marker', output)
            self.assertNotIn(b'synthetic-password-marker', output)
        finally:
            child, status = os.waitpid(pid, os.WNOHANG)
            if child == 0:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(fd)

    def test_real_cli_read_path_in_fresh_process_with_synthetic_http_only(self):
        import json
        import subprocess
        root = Path(__file__).resolve().parents[2]
        code = '''
import sys, socket, threading
from unittest.mock import patch
sys.path.insert(0, 'personal_tests/tradelocker')
from test_candidate import Wire
import requests
from personal_trading.tradelocker import cli
wire = Wire()
import json
from pathlib import Path
from synthetic_contract import contract_fixture
observation = contract_fixture()
wire.details = observation['contract']
wire.instruments = [{'name': observation['symbol'], 'type': observation['contract']['type'],
    'tradableInstrumentId': observation['tradableInstrumentId'],
    'routes': [{'type': kind, 'id': value} for kind, value in observation['routes'].items()]}]
answers = iter(['synthetic-password', '2'])
wire.accounts.append({'id': 987654321, 'accNum': 912345, 'name': 'private-name', 'currency': 'USD'})
labels = []
def prompt(label):
    labels.append(label)
    return next(answers)
before = set(threading.enumerate())
with patch.object(socket.socket, 'connect', side_effect=AssertionError('socket prohibited')), patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS prohibited')), patch.object(requests.Session, 'request', lambda session, method, url, **kw: wire.request(session, method, url, **kw)), patch.object(cli, 'hidden_prompt', prompt):
    result = cli.main(['read', '--email', 'synthetic-user', '--start-ms', '1699999999000', '--end-ms', '1700000001000'])
assert result == 0
assert len(labels) == 2
assert '[1]' in labels[-1] and '[2]' in labels[-1] and '****' in labels[-1]
for marker in ('987654321', '912345', 'private-name'):
    assert marker not in repr(labels)
for method, url, kw in wire.calls:
    if '/trade/' in url:
        assert kw['headers']['accNum'] == '912345'
        if '/accounts/' in url:
            assert '/987654321/' in url
assert set(threading.enumerate()) == before
'''
        result = subprocess.run([sys.executable, '-B', '-W', 'error', '-c', code], cwd=root,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(data['execution'], 'disarmed')
        self.assertEqual(data['bars_returned'], 1)
        self.assertEqual(data['balance'], 54321.25)
        details = data['instrument_fields_unqualified']
        self.assertEqual(details['type'], 'EQUITY_CFD')
        self.assertIsNone(details['baseCurrency'])
        self.assertEqual(details['tickCost'], [{'leftRangeLimit': None, 'tickCost': 0.0}])
        self.assertEqual(details['tickSize'], [{'leftRangeLimit': None, 'tickSize': 0.02}])
        for marker in ('987654321', '912345', 'private-name'):
            self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertIn('Broker-issued trading email: synthetic-user', result.stdout)
        self.assertNotIn('synthetic-password', result.stdout + result.stderr)

    def test_cli_selection_refuses_defaults_without_account_reads(self):
        import subprocess
        root = Path(__file__).resolve().parents[2]
        code = '''
import sys, socket
from unittest.mock import patch
sys.path.insert(0, 'personal_tests/tradelocker')
from test_candidate import Wire
import requests
from personal_trading.tradelocker import cli
wire = Wire()
wire.accounts[0].update(id=987654321, accNum=912345, name='private-name')
answers = iter(['synthetic-password', SELECTION])
with patch.object(socket.socket, 'connect', side_effect=AssertionError('socket prohibited')), patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS prohibited')), patch.object(requests.Session, 'request', lambda session, method, url, **kw: wire.request(session, method, url, **kw)), patch.object(cli, 'hidden_prompt', lambda label: next(answers)):
    result = cli.main(['read', '--email', 'synthetic-user'])
assert result == 1
assert [c[1].split('/backend-api', 1)[1] for c in wire.calls] == ['/auth/jwt/token', '/auth/jwt/all-accounts']
'''
        for choice in ('', '0', '01', '2', ' 1', '987654321'):
            with self.subTest(choice=choice):
                result = subprocess.run([sys.executable, '-B', '-W', 'error', '-c', code.replace('SELECTION', repr(choice))],
                    cwd=root, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('execution remains disarmed', result.stderr)
                for marker in ('synthetic-password', '987654321', '912345', 'private-name'):
                    self.assertNotIn(marker, result.stdout + result.stderr)

    def test_explicit_visible_email_and_safe_failure_diagnostic_contract(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(['read'])
        self.assertEqual(cli.parser().parse_args(['read', '--email', 'synthetic@example.invalid']).email,
                         'synthetic@example.invalid')
        self.assertEqual(cli.safe_diagnostic('AUTH_HTTP_STATUS_401'), 'AUTH_HTTP_STATUS_401')
        self.assertEqual(cli.safe_diagnostic('ACCOUNT_LIST_HTTP_STATUS_403'), 'ACCOUNT_LIST_HTTP_STATUS_403')
        self.assertEqual(cli.safe_diagnostic('secret-token'), 'UNCLASSIFIED')
        self.assertEqual(cli.safe_diagnostic(None), 'UNCLASSIFIED')

    def test_smoke_cli_is_synthetic_real_sdk_and_cannot_arm(self):
        import json
        import subprocess
        root=Path(__file__).resolve().parents[2]
        result=subprocess.run([sys.executable,'-B','-W','error','-m','personal_trading.tradelocker','smoke'],cwd=root,
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(data['evidence'],'SYNTHETIC_HTTP_ONLY')
        self.assertEqual(data['execution'],'disarmed')
        self.assertEqual(data['mock_write_methods'],['POST','DELETE','POST','PATCH','DELETE'])
        self.assertEqual(data['open_close'][-1],'FLAT_SNAPSHOT_ONLY')
        self.assertEqual(data['pending_modify_cancel'][-1],'TERMINAL_NO_POSITION_OBSERVED')
        self.assertFalse(data['account_calls_performed'])
        result=subprocess.run([sys.executable,'-B','-W','error','-m','personal_trading.tradelocker','smoke','--arm'],cwd=root,
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,2)

    def test_cli_has_no_order_commands(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['order'])

if __name__ == '__main__':
    unittest.main()
