"""Offline tests; no broker access. PTY children replace HTTP and deny sockets."""
import contextlib
import importlib.util
import io
import json
import socket
import sys
import unittest
from unittest.mock import patch


class DemoProbeTests(unittest.TestCase):
    def setUp(self):
        guards=contextlib.ExitStack()
        self.addCleanup(guards.close)
        guards.enter_context(patch.object(socket,'socket',side_effect=OSError('Offline socket creation denied')))
        guards.enter_context(patch.object(socket,'getaddrinfo',side_effect=OSError('Offline DNS denied')))

    def test_new_module_is_import_safe_and_refuses_imported_entry(self):
        spec = importlib.util.find_spec('personal_trading.tradelocker.demo_probe')
        self.assertIsNotNone(spec, 'separate owner-terminal demo probe is missing')
        from personal_trading.tradelocker import demo_probe as probe
        with unittest.mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('offline')), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(probe.main([]), 2)

    def test_real_entry_open_link_close_uses_only_mocked_http(self):
        code, output, evidence = self.run_pty('happy')
        self.assertEqual(code, 0, output)
        self.assertEqual(evidence['writes'], ['POST', 'DELETE'])
        self.assertEqual(evidence['bodies'][0]['qty'], '0.01')
        self.assertEqual(evidence['bodies'][1], {'qty': '0.01'})
        self.assertIn('CLOSE_ACK_FLAT_SNAPSHOT_OBSERVED_COSTS_UNKNOWN', output)
        self.assertNotIn('synthetic-password-marker', output)
        self.assertTrue(evidence['original_writes_blocked'])

    def test_broker_terminal_rejection_is_retained_without_raw_text(self):
        code, output, evidence = self.run_pty('terminal_rejected')
        self.assertEqual(code,0,output)
        self.assertEqual(evidence['writes'],['POST'])
        rejection=[e for e in evidence['journal'] if e['kind']=='ORDER_TERMINAL_OBSERVATION']
        self.assertEqual(rejection,[{'kind':'ORDER_TERMINAL_OBSERVATION','order_id':'901','status':'Rejected','filled_qty':'0'}])

    def test_final_post_durability_deadline_blocks_expired_dispatch(self):
        code, output, evidence = self.run_pty('late_intent')
        self.assertEqual(code,2,output)
        self.assertEqual(evidence['writes'],[])
        self.assertEqual(sum(e['kind']=='INTENT' for e in evidence['journal']),1)

    def test_raw_quantity_never_converts_lots_or_invents_tiny_default(self):
        from personal_trading.tradelocker import demo_probe as probe
        from personal_trading.tradelocker.tl_candidate import ReadOnlyError
        for text in ('0','-1','NaN','Infinity','0.010000000000000000001'):
            with self.subTest(text=text), self.assertRaises(ReadOnlyError):
                probe._quantity(text)
        self.assertEqual(probe._quantity('0.01'),'0.01')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            probe.parser().parse_args(['--email','synthetic@example.invalid'])

    def test_no_controlling_terminal_refuses_before_authentication(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory(prefix='probe-nontty-') as root:
            result=subprocess.run([sys.executable,'-B','-W','error','-c',DRIVER,root,'happy'],
                env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','TMPDIR':tempfile.gettempdir()},
                capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,2,result.stderr)
            from pathlib import Path
            evidence=json.loads((Path(root)/'evidence.json').read_text())
            self.assertEqual(evidence['probe_http'],0)

    def test_rate_windows_pace_all_http_and_total_count_includes_bootstrap(self):
        code, output, evidence = self.run_pty('paced')
        self.assertEqual(code, 0, output)
        attempts=[e for e in evidence['journal'] if e['kind']=='HTTP_ATTEMPT']
        self.assertEqual(len(attempts),evidence['probe_http'])
        self.assertLessEqual(evidence['probe_http'],70)
        times=evidence['http_times']
        self.assertGreaterEqual(times[10]-times[0],1.)
        self.assertGreaterEqual(times[20]-times[10],1.)

    def test_ack_id_already_in_initial_history_cannot_authorize_close(self):
        code, output, evidence = self.run_pty('reused_order_id')
        self.assertEqual(code, 2, output)
        self.assertEqual(evidence['writes'], ['POST'])

    def test_terminal_cancellation_sends_no_order(self):
        code, output, evidence = self.run_pty('happy', acknowledge=False)
        self.assertEqual(code, 2, output)
        self.assertEqual(evidence['writes'], [])

    def test_initial_unknown_or_nonflat_snapshots_refuse_all_writes(self):
        for scenario in ('malformed_initial','error_initial','existing_position','pending_initial',
                         'routes_changed','account_changed','missing_limits','runner','expired'):
            with self.subTest(scenario=scenario):
                code, output, evidence = self.run_pty(scenario)
                self.assertEqual(code, 2, output)
                self.assertEqual(evidence['writes'], [])

    def test_lost_ack_partial_unlinked_and_unknown_states_stop_before_close(self):
        for scenario in ('lost_open_ack','reject_open','pending','partial','unlinked','foreign',
                         'boolean_qty','lossy_qty','unknown_status','changed_close'):
            with self.subTest(scenario=scenario):
                code, output, evidence = self.run_pty(scenario)
                self.assertEqual(code, 2, output)
                self.assertEqual(evidence['writes'], ['POST'])
                self.assertIn('OPERATOR_RECONCILIATION_REQUIRED_NO_REPLAY', output)
                self.assertNotIn('secret-broker-text', output+json.dumps(evidence['journal']))
                self.assertEqual(sum(e['kind']=='INTENT' and e['operation']=='OPEN' for e in evidence['journal']), 1)

    def test_close_ack_is_not_close_proof_and_close_never_retries(self):
        for scenario in ('lost_close_ack','close_not_flat'):
            with self.subTest(scenario=scenario):
                code, output, evidence = self.run_pty(scenario)
                self.assertEqual(code, 2, output)
                self.assertEqual(evidence['writes'], ['POST','DELETE'])
                self.assertNotIn('CLOSE_ACK_FLAT_SNAPSHOT_OBSERVED_COSTS_UNKNOWN', output)

    def test_restart_and_missing_state_cannot_reset_attempt(self):
        for scenario in ('restart','missing_state'):
            with self.subTest(scenario=scenario):
                code, output, evidence = self.run_pty(scenario)
                self.assertEqual(code, 2, output)
                self.assertEqual(evidence['writes'], ['POST','DELETE'])
                self.assertEqual(evidence['restart_http'], 0)

    def test_acknowledgement_preview_cannot_hide_unresolved_economics(self):
        code, output, evidence = self.run_pty('happy')
        self.assertEqual(code, 0, output)
        approvals=[e['preview'] for e in evidence['journal'] if e['kind']=='OWNER_APPROVAL']
        self.assertEqual(len(approvals), 2)
        self.assertNotEqual(approvals[0]['action'], approvals[1]['action'])
        for approval in approvals:
            self.assertIn('NO DOLLAR LOSS CAP', approval['disclosure'])
            self.assertIn('NOT asserted lots/ounces', approval['disclosure'])
            self.assertEqual(approval['binding']['symbol'], 'XAUUSD.R')
            self.assertEqual(approval['binding']['account_id'], 123)
            self.assertEqual(approval['binding']['acc_num'], 7)
            self.assertIn('expires_at', approval)
        self.assertIn('synthetic@example.invalid', output)

    def run_pty(self, scenario, acknowledge=True):
        import json
        import os
        import pty
        import re
        import select
        import signal
        import tempfile
        import time
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix='probe-test-') as root:
            pid, fd = pty.fork()
            if pid == 0:
                os.execve(sys.executable, [sys.executable, '-B', '-W', 'error', '-c', DRIVER, root, scenario],
                          {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'TMPDIR': tempfile.gettempdir()})
            data = b''
            sent = set()
            status = None
            try:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if select.select([fd], [], [], .1)[0]:
                        try:
                            chunk = os.read(fd, 65536)
                        except OSError:
                            _, status = os.waitpid(pid, 0)
                            break
                        if not chunk:
                            _, status = os.waitpid(pid, 0)
                            break
                        data += chunk
                        text = data.decode(errors='replace')
                        for marker, answer in [('CONNECT ACK> ', 'CONNECT DEMO READS'),
                                ('password (hidden)> ', 'synthetic-password-marker'),
                                ('account number (hidden)> ', '1')]:
                            if marker in text and marker not in sent:
                                os.write(fd, (answer+'\n').encode())
                                sent.add(marker)
                        for token in re.findall(r'ACK TOKEN: ([^\r\n]+)', text):
                            if token not in sent and text.endswith('APPROVAL> '):
                                if scenario=='expired':
                                    time.sleep(1.1)
                                os.write(fd, ((token if acknowledge else 'NO')+'\n').encode())
                                sent.add(token)
                    waited, result = os.waitpid(pid, os.WNOHANG)
                    if waited:
                        status = result
                        break
                if status is None:
                    waited, result = os.waitpid(pid, os.WNOHANG)
                    if waited:
                        status = result
                self.assertIsNotNone(status, data.decode(errors='replace'))
            finally:
                if status is None:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                os.close(fd)
            evidence_path = Path(root)/'evidence.json'
            evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
            return os.waitstatus_to_exitcode(status), data.decode(errors='replace'), evidence


DRIVER = r'''
import json, os, runpy, socket, sys, time
from pathlib import Path
import requests
from personal_trading.tradelocker.demo_execution import SyntheticHTTP
root, scenario = Path(sys.argv[1]), sys.argv[2]
def audit(event, args):
    if event in ('socket.__new__', 'socket.connect', 'socket.bind', 'socket.getaddrinfo'):
        raise RuntimeError('OFFLINE_SOCKET_DENIED')
sys.addaudithook(audit)
class Wire(SyntheticHTTP):
    instrument_reads=0
    position_reads=0
    def request(self, method, url, **kw):
        http_times.append(time.monotonic())
        if url.endswith('/instruments'):
            self.instrument_reads+=1
        if url.endswith('/positions'):
            self.position_reads+=1
        if method=='DELETE' and scenario=='close_not_flat':
            previous=self.positions[:]
        if '/trade/instruments/' in url:
            response=requests.Response(); response.status_code=200
            response._content=json.dumps({'s':'ok','d':{'name':'XAUUSD.R','lotSize':100,'tickCost':0}}).encode()
            self.calls.append((method,url,kw)); return response
        response=super().request(method,url,**kw)
        parsed=json.loads(response.content)
        if url.endswith('/trade/config'):
            parsed['d']['rateLimits']=[{'rateLimitType':'GET_POSITIONS','measure':'SECONDS','intervalNum':1,'limit':10 if scenario=='paced' else 1000}]
        if method=='POST' and url.endswith('/orders'):
            self.history[0].update(qty=.01, filledQty=.01, stopLoss=0., takeProfit=0.)
            self.positions[0].update(qty=.01, stopLossId=0, takeProfitId=0)
            self.orders=[]
            if scenario=='lost_open_ack':
                raise TimeoutError('secret-broker-text')
            if scenario=='reject_open':
                response.status_code=400
                parsed={'s':'error','errmsg':'secret-broker-text'}
            if scenario=='pending':
                self.orders=[dict(self.history[0],status='New',filledQty=0.,positionId=0)]
                self.history=[]; self.positions=[]
            if scenario=='terminal_rejected':
                self.history[0].update(status='Rejected',filledQty=0.,positionId=0)
                self.positions=[]
            if scenario=='partial':
                self.history[0]['filledQty']=.005; self.positions[0]['qty']=.005
            if scenario=='unlinked':
                self.history=[]
            if scenario=='foreign':
                self.history.append(dict(self.history[0],id=999))
            if scenario=='boolean_qty':
                self.positions[0]['qty']=True
            if scenario=='lossy_qty':
                self.positions[0]['qty']='0.010000000000000000001'
            if scenario=='unknown_status':
                self.history[0]['status']='UNKNOWN'
        if method=='DELETE' and scenario=='lost_close_ack':
            raise TimeoutError('secret-broker-text')
        if method=='DELETE' and scenario=='close_not_flat':
            self.positions=previous
        if url.endswith('/positions') and self.position_reads==1:
            if scenario=='malformed_initial':
                parsed['d']['positions']={}
            if scenario=='error_initial':
                parsed['s']='error'
            if scenario=='existing_position':
                parsed['d']['positions']=[[789,456,18,'buy',.01,2500.,0,0,0.]]
        if url.endswith('/positions') and scenario=='account_changed' and self.position_reads==2:
            parsed['d']['positions']=[[789,456,18,'buy',.01,2500.,0,0,0.]]
        if url.endswith('/positions') and scenario=='changed_close' and self.position_reads==4:
            parsed['d']['positions'][0][4]=.005
        if url.endswith('/orders') and method=='GET' and scenario=='pending_initial':
            parsed['d']['orders']=[[900,456,18,.01,'buy','market','New',0.,0,0.,0.,0.,0.]]
        if url.endswith('/instruments') and self.instrument_reads==3 and scenario=='routes_changed':
            parsed['d']['instruments'][0]['routes'][1]['id']=19
        if url.endswith('/trade/config') and scenario=='missing_limits':
            parsed['d'].pop('rateLimits',None)
        response._content=json.dumps(parsed).encode()
        return response
http_times=[]
wire=Wire()
if scenario=='reused_order_id':
    wire.history=[dict(id=901,tradableInstrumentId=456,routeId=18,qty=.01,side='buy',
        type='market',status='Filled',filledQty=.01,positionId=789,stopPrice=0.,price=0.,stopLoss=0.,takeProfit=0.)]
requests.sessions.Session.request=lambda session, method, url, **kw:wire.request(method,url,**kw)
requests.Session.request=requests.sessions.Session.request
sys.argv=['demo_probe','--email','synthetic@example.invalid','--state-directory',str(root/'state'),
          '--quantity','0.01','--side','buy','--max-http','70','--poll-rounds','3',
          '--max-seconds','120','--approval-seconds','30','--snapshot-seconds','15']
code=99
restart_http=None
if scenario=='runner':
    import unittest
if scenario in ('expired','late_intent'):
    sys.argv[sys.argv.index('--approval-seconds')+1]='1'
if scenario=='late_intent':
    from personal_trading.tradelocker.journal import Journal
    append=Journal.append
    def slow_append(self,event):
        append(self,event)
        if event['kind']=='INTENT':
            time.sleep(1.1)
    Journal.append=slow_append
try:
    runpy.run_module('personal_trading.tradelocker.demo_probe',run_name='__main__')
except SystemExit as exc:
    code=exc.code
if scenario in ('restart','missing_state'):
    if scenario=='missing_state':
        (root/'state'/'journal.json').unlink()
    count=len(wire.calls)
    try:
        runpy.run_module('personal_trading.tradelocker.demo_probe',run_name='__main__')
    except SystemExit as exc:
        code=exc.code
    restart_http=len(wire.calls)-count
try:
    state=root/'state'/'journal.json'
    events=json.loads(state.read_text())['events'] if state.exists() else []
finally:
    probe_http=len(wire.calls)
    probe_times=http_times[:]
    writes=[c for c in wire.calls if '/auth/' not in c[1] and c[0]!='GET']
    # The original Reader dispatcher was never supplied an execution guard.
    from personal_trading.tradelocker.tl_candidate import Reader, quiet_call
    reader=quiet_call(lambda:Reader.connect('synthetic','synthetic',123,7))
    blocked=False
    try:
        quiet_call(lambda:reader._sdk.create_order(456,.01,'buy'))
    except Exception:
        blocked=True
    reader.close()
    (root/'evidence.json').write_text(json.dumps({'writes':[c[0] for c in writes],
        'bodies':[c[2]['json'] for c in writes], 'original_writes_blocked':blocked,
        'journal':events,'restart_http':restart_http,'probe_http':probe_http,'http_times':probe_times}))
sys.exit(code)
'''


if __name__ == '__main__':
    unittest.main()
