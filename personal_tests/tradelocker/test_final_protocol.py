"""Final protocol regressions; actual Reader/PTY with synthetic HTTP only."""
import unittest
from unittest.mock import patch

import test_demo_probe as harness


class DispatchPacingTests(unittest.TestCase):
    def test_delayed_durable_attempt_keeps_actual_dispatch_window(self):
        injection = '''
from personal_trading.tradelocker.journal import Journal
saved_append=Journal.append
def delayed_append(self,event):
    saved_append(self,event)
    if event['kind']=='HTTP_ATTEMPT' and event['number']==11:
        time.sleep(.6)
Journal.append=delayed_append
code=99'''
        driver = harness.DRIVER.replace('code=99', injection)
        with patch.object(harness, 'DRIVER', driver):
            code, output, evidence = harness.DemoProbeTests().run_pty('paced')
        self.assertEqual(code, 0, output)
        self.assertEqual(evidence['writes'], ['POST', 'DELETE'])
        times = evidence['http_times']
        peak = max(sum(t0 <= t < t0 + 1 for t in times) for t0 in times)
        self.assertLessEqual(peak, 10, {'peak':peak, 'times':times})
        self.assertEqual(sum(e['kind']=='HTTP_ATTEMPT' for e in evidence['journal']), evidence['probe_http'])


class DuplicateWireTests(unittest.TestCase):
    def test_real_entry_duplicate_status_never_admits_open(self):
        needle = '        response._content=json.dumps(parsed).encode()'
        replacement = '''        encoded=json.dumps(parsed)
        if method=='GET' and url.endswith(('/ordersHistory','/orders','/positions')):
            encoded=encoded.replace('"s": "ok"','"s":"error","s":"ok"')
        response._content=encoded.encode()'''
        self.assertEqual(harness.DRIVER.count(needle), 1)
        with patch.object(harness, 'DRIVER', harness.DRIVER.replace(needle, replacement)):
            code, output, evidence = harness.DemoProbeTests().run_pty('happy')
        self.assertEqual(code, 2, output)
        self.assertEqual(evidence['writes'], [])
        self.assertNotIn('CLOSE_ACK_FLAT_SNAPSHOT_OBSERVED_COSTS_UNKNOWN', output)


class AdditionalPTYTests(unittest.TestCase):
    def test_delayed_mutation_intents_keep_actual_dispatch_window(self):
        for operation in ('OPEN', 'CLOSE'):
            injection = '''
from personal_trading.tradelocker.journal import Journal
saved_append=Journal.append
def delayed_append(self,event):
    saved_append(self,event)
    if event['kind']=='INTENT' and event['operation']==%r:
        time.sleep(.6)
Journal.append=delayed_append
code=99''' % operation
            with self.subTest(operation=operation), patch.object(harness, 'DRIVER', harness.DRIVER.replace('code=99', injection)):
                code, output, evidence = harness.DemoProbeTests().run_pty('paced')
            self.assertEqual(code, 0, output)
            self.assertEqual(evidence['writes'], ['POST', 'DELETE'])
            times = evidence['http_times']
            self.assertLessEqual(max(sum(t0 <= t < t0+1 for t in times) for t0 in times), 10)
            self.assertEqual(sum(e['kind']=='HTTP_ATTEMPT' for e in evidence['journal']), evidence['probe_http'])

    def test_duplicate_nested_members_and_post_close_tables_never_prove_flat(self):
        for condition, expected in (("method=='GET' and url.endswith('/positions')", []),
                ("method=='GET' and url.endswith(('/ordersHistory','/orders','/positions')) and any(c[0]=='DELETE' for c in self.calls)", ['POST','DELETE'])):
            needle = '        response._content=json.dumps(parsed).encode()'
            replacement = '''        encoded=json.dumps(parsed)
        if %s:
            encoded='{"ignored":[{"key":0,"key":1}],'+encoded[1:]
        response._content=encoded.encode()''' % condition
            with self.subTest(condition=condition), patch.object(harness, 'DRIVER', harness.DRIVER.replace(needle, replacement)):
                code, output, evidence = harness.DemoProbeTests().run_pty('happy')
            self.assertEqual(code, 2, output)
            self.assertEqual(evidence['writes'], expected)
            self.assertNotIn('CLOSE_ACK_FLAT_SNAPSHOT_OBSERVED_COSTS_UNKNOWN', output)

    def test_duplicate_mutation_acknowledgements_stop_without_replay(self):
        for condition, expected in (("method=='POST' and url.endswith('/orders')", ['POST']),
                                    ("method=='DELETE'", ['POST','DELETE'])):
            needle = '        response._content=json.dumps(parsed).encode()'
            replacement = '''        encoded=json.dumps(parsed)
        if %s:
            encoded='{"ignored":{"key":0,"key":1},'+encoded[1:]
        response._content=encoded.encode()''' % condition
            with self.subTest(condition=condition), patch.object(harness, 'DRIVER', harness.DRIVER.replace(needle, replacement)):
                code, output, evidence = harness.DemoProbeTests().run_pty('happy')
            self.assertEqual(code, 2, output)
            self.assertEqual(evidence['writes'], expected)
            self.assertNotIn('CLOSE_ACK_FLAT_SNAPSHOT_OBSERVED_COSTS_UNKNOWN', output)
            self.assertEqual(sum(e['kind']=='ACK' for e in evidence['journal']), len(expected)-1)


class RawResponseBoundaryTests(unittest.TestCase):
    def test_duplicates_rejected_before_sdk_on_all_auth_and_read_endpoints(self):
        from personal_trading.tradelocker.demo_execution import synthetic_reader
        from personal_trading.tradelocker.tl_candidate import ReadOnlyError
        from personal_trading.tradelocker.demo_probe import BASE
        cases = [
            ('post','/auth/jwt/token',dict(include_acc_num=False,include_access_token=False,json_data={'email':'synthetic','password':'synthetic','server':'GATESFX'})),
            ('post','/auth/jwt/refresh',dict(include_acc_num=False,include_access_token=False,json_data={'refreshToken':'synthetic'})),
            ('get','/auth/jwt/all-accounts',{}), ('get','/trade/config',{}),
            *[('get','/trade/accounts/123/'+suffix,{}) for suffix in ('instruments','state','positions','orders','ordersHistory')],
            ('get','/trade/quotes',dict(additional_params={'tradableInstrumentId':456,'routeId':'17'})),
            ('get','/trade/history',dict(additional_params={'tradableInstrumentId':456,'routeId':'17','resolution':'1m','from':1,'to':2})),
            ('get','/trade/instruments/456',dict(additional_params={'routeId':'17','locale':'en'})),
        ]
        with synthetic_reader() as (reader, wire):
            sdk=reader._sdk
            sdk._execution_reads=True
            original=sdk._session.request
            def valid_response(method, url, **kwargs):
                if url.endswith(('/trade/history','/trade/instruments/456')):
                    import json
                    import requests
                    response=requests.Response()
                    response.status_code=200
                    response._content=json.dumps({'s':'ok','d':{'name':'XAUUSD.R','barDetails':[]}}).encode()
                    return response
                return original(method,url,**kwargs)
            for method, path, options in cases:
                with patch.object(sdk._session,'request',valid_response):
                    positive=reader._read(lambda:sdk._request(method,BASE+path,**options))
                self.assertIs(type(positive),dict)
                for fragment in ('"s":"error","s":"ok",', '"ignored":[{"x":0,"x":1}],'):
                    received=[]
                    def corrupt(method, url, **kwargs):
                        response=valid_response(method,url,**kwargs)
                        response._content=('{'+fragment).encode()+response.content[1:]
                        received.append(url)
                        return response
                    with self.subTest(path=path,fragment=fragment), patch.object(sdk._session,'request',corrupt), patch.object(sdk,'_get_response_json', wraps=sdk._get_response_json) as decoder:
                        with self.assertRaises(ReadOnlyError):
                            reader._read(lambda:sdk._request(method,BASE+path,**options))
                        decoder.assert_not_called()
                        self.assertEqual(received,[BASE+path])

    def test_strict_decoder_keeps_exact_numeric_lexemes_and_unique_names(self):
        from decimal import Decimal
        from types import SimpleNamespace
        from personal_trading.tradelocker.tl_candidate import decode_response, ReadOnlyError
        good=b'{"s":"ok","d":{"qty":0.020000000000000000001,"tiny":1e-9999,"id":123,"a":1,"A":2}}'
        parsed=decode_response(SimpleNamespace(content=good))
        self.assertEqual(parsed['d']['qty'], Decimal('0.020000000000000000001'))
        self.assertEqual(parsed['d']['tiny'], Decimal('1e-9999'))
        self.assertIs(type(parsed['d']['id']), int)
        for raw in (b'{"x":0,"x":1}', b'{"a":[{"x":0,"\\u0078":1}]}',
                    b'{"n":NaN}', b'{"n":Infinity}', b'{"n":-Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ReadOnlyError):
                decode_response(SimpleNamespace(content=raw))

    def test_controller_mutation_duplicates_never_become_acknowledgements(self):
        from test_execution_repairs import setup, submit, writes
        from personal_trading.tradelocker.tl_candidate import ReadOnlyError
        def corrupt(wire, method, url, data):
            if method=='POST' and url.endswith('/orders'):
                import json
                return b'{"extra":{"x":0,"x":1},'+json.dumps(data).encode()[1:]
        with setup(corrupt) as (controller, reader, wire):
            with self.assertRaises(ReadOnlyError):
                submit(controller)
            self.assertEqual(len(writes(wire,'POST')),1)
            self.assertFalse(any(e['kind']=='ACK' for e in controller.journal.events))
            with self.assertRaises(ReadOnlyError):
                submit(controller)
            self.assertEqual(len(writes(wire,'POST')),1)


class Clock:
    def __init__(self):
        import time
        self.now=100.
        self.epoch=time.time()
        self.sleeps=[]
    def monotonic(self): return self.now
    def wall(self): return self.epoch+self.now-100.
    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now+=delay


from contextlib import contextmanager


@contextmanager
def transport_fixture():
    """Real Reader, Journal and transport; only clock/HTTP/TTY are fixtures."""

    import io
    import re
    import socket
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace
    import requests
    from personal_trading.tradelocker import demo_probe as probe
    from personal_trading.tradelocker.demo_execution import SyntheticHTTP
    from personal_trading.tradelocker.journal import Journal
    from personal_trading.tradelocker.safety import digest
    clock=Clock()
    wire=SyntheticHTTP()
    calls=[]
    def network(session, method, url, **kwargs):
        calls.append((clock.now,method,url))
        return wire.request(method,url,**kwargs)
    terminal=io.StringIO()
    args=SimpleNamespace(max_seconds=120,max_http=70,side='buy',quantity='0.01',approval_seconds=30,poll_rounds=3,snapshot_seconds=15)
    with tempfile.TemporaryDirectory(prefix='final-protocol-') as root, Journal(Path(root)/'state',digest({'fixture':'protocol'})) as journal, \
         patch.object(probe,'_foreground'), patch.object(probe,'_ask',lambda terminal,label:re.findall(r'ACK TOKEN: ([^\n]+)',terminal.getvalue())[-1]), \
         patch.object(socket.socket,'connect',side_effect=AssertionError('OFFLINE')), patch.object(socket,'getaddrinfo',side_effect=AssertionError('OFFLINE')), \
         patch.object(requests.Session,'request',network), patch.object(probe.time,'monotonic',clock.monotonic), \
         patch.object(probe.time,'time',clock.wall), patch.object(probe.time,'sleep',clock.sleep):
        transport=probe.DemoProbeTransport(terminal,journal,args)
        with transport.connection():
            reader=probe.Reader.connect('synthetic','synthetic',123,7)
        transport.reader=reader
        try:
            reader.positions()  # prime the SDK's lazy schema cache, all counted
            yield probe,transport,reader,wire,clock,calls
        finally:
            reader.close()
            wire.active=False


class DispatchBoundaryTests(unittest.TestCase):
    def test_bootstrap_durability_intent_token_prep_and_reads_share_dispatch_windows(self):
        with transport_fixture() as (probe, transport, reader, wire, clock, calls):
            bootstrap=len(calls)
            self.assertGreater(bootstrap,0)
            self.assertEqual(transport.http_attempts,bootstrap)
            transport.configure({'rateLimits':[{'rateLimitType':'ALL','measure':'SECONDS','intervalNum':1,'limit':1},
                                              {'rateLimitType':'ALL','measure':'SECONDS','intervalNum':2,'limit':1}]})
            action=probe._market_action(reader.identity,'buy','0.01')
            approval=transport.approve(action)
            append=transport.journal.append
            def slow_append(event):
                append(event)
                if event['kind'] in ('HTTP_ATTEMPT','INTENT'):
                    clock.now+=.6
            headers=reader._sdk._get_headers
            def slow_headers(**kwargs):
                clock.now+=.6
                return headers(**kwargs)
            with patch.object(transport.journal,'append',slow_append), patch.object(reader._sdk,'_get_headers',slow_headers):
                transport.write(action,approval,clock.now+15)
                reader.positions()
            self.assertEqual([c[1] for c in calls[bootstrap:]], ['POST','GET'])
            self.assertGreaterEqual(calls[bootstrap][0]-calls[bootstrap-1][0],2.)
            self.assertGreaterEqual(calls[bootstrap+1][0]-calls[bootstrap][0],2.)
            self.assertEqual(transport.times,[c[0] for c in calls])
            self.assertEqual(transport.http_attempts,len(calls))
            self.assertEqual(sum(e['kind']=='HTTP_ATTEMPT' for e in transport.journal.events),len(calls))

    def test_pacing_cannot_send_expired_approval_snapshot_or_total_deadline(self):
        from personal_trading.tradelocker.tl_candidate import ReadOnlyError
        for boundary in ('approval','snapshot','total'):
            with self.subTest(boundary=boundary), transport_fixture() as (probe,transport,reader,wire,clock,calls):
                transport.configure({'rateLimits':[{'rateLimitType':'ALL','measure':'SECONDS','intervalNum':2,'limit':1}]})
                if boundary=='approval': transport.args.approval_seconds=1
                action=probe._market_action(reader.identity,'buy','0.01')
                approval=transport.approve(action)
                freshness=clock.now+(.5 if boundary=='snapshot' else 15)
                if boundary=='total': transport.deadline=clock.now+.5
                before=len(calls)
                with self.assertRaises(ReadOnlyError): transport.write(action,approval,freshness)
                self.assertEqual(len(calls),before)
                self.assertEqual(transport.http_attempts,before+1)
                self.assertEqual(sum(e['kind']=='INTENT' for e in transport.journal.events),1)
                with self.assertRaises(ReadOnlyError): transport.write(action,approval,clock.now+15)
                self.assertEqual(len(calls),before)

    def test_token_refresh_and_following_write_are_both_paced(self):
        import jwt
        with transport_fixture() as (probe,transport,reader,wire,clock,calls):
            transport.configure({'rateLimits':[{'rateLimitType':'ALL','measure':'SECONDS','intervalNum':1,'limit':1}]})
            action=probe._market_action(reader.identity,'buy','0.01')
            approval=transport.approve(action)
            reader._sdk._access_token=jwt.encode({'exp':1,'sub':'synthetic'},
                'synthetic-offline-only-hmac-key-32bytes-minimum',algorithm='HS256')
            before=len(calls)
            transport.write(action,approval,clock.now+15)
            self.assertEqual([(c[1],c[2].split('/backend-api',1)[1]) for c in calls[before:]],
                [('POST','/auth/jwt/refresh'),('POST','/trade/accounts/123/orders')])
            self.assertGreaterEqual(calls[before][0]-calls[before-1][0],1.)
            self.assertGreaterEqual(calls[before+1][0]-calls[before][0],1.)
            self.assertEqual(transport.http_attempts,len(calls))

    def test_no_fairness_delay_and_owner_count_stays_independent_of_dispatches(self):
        from personal_trading.tradelocker.tl_candidate import ReadOnlyError
        with transport_fixture() as (probe,transport,reader,wire,clock,calls):
            transport.configure({'rateLimits':[{'rateLimitType':'ALL','measure':'SECONDS','intervalNum':1,'limit':1000}]})
            before=len(calls)
            transport.args.max_http=before+1
            reader.positions()
            self.assertEqual(clock.sleeps,[])
            with self.assertRaises(ReadOnlyError): reader.positions()
            self.assertEqual(len(calls),before+1)
            self.assertEqual(transport.http_attempts,before+1)


if __name__ == '__main__':
    unittest.main()
