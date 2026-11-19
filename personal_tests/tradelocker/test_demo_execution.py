"""Synthetic HTTP only; tests run real SDK builders/decoders, never a fake broker."""
from dataclasses import replace
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from personal_trading.tradelocker.tl_candidate import ReadOnlyError
from test_safety import policy_fixture
SCRATCH=tempfile.gettempdir()


def approval(controller, action, label):
    from personal_trading.tradelocker.demo_execution import OwnerApproval
    return OwnerApproval(label, action.request_digest, controller.policy_digest,
        int(time.time())+60,
        'APPROVE ONE DEMO REQUEST '+action.request_digest+' POLICY '+controller.policy_digest)


class ExecutionTests(unittest.TestCase):
    def test_real_sdk_modify_tightens_only_then_cancel_owned_pending_order(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        from decimal import Decimal as D
        p,q=policy_fixture()
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            with Controller(reader,p,q,Path(root)/'state') as c:
                c.submit(approval(c,c.describe_submit(),'synthetic-submit-pending'))
                row=dict(wire.history[0],status='New',filledQty=0.,positionId=0)
                wire.history=[]
                wire.positions=[]
                wire.orders=[row]
                self.assertEqual(c.reconcile(),'PENDING_OBSERVED')
                for stop in (D('2480'),D('2491.005'),D('2501')):
                    with self.assertRaises(ReadOnlyError): c.describe_modify_stop(stop)
                action=c.describe_modify_stop(D('2491'))
                c.modify_stop(D('2491'),approval(c,action,'synthetic-modify-once'))
                self.assertEqual(c.reconcile(),'PENDING_OBSERVED')
                self.assertEqual(wire.orders[0]['stopLoss'],2491.)
                c.cancel_order(approval(c,c.describe_cancel(),'synthetic-cancel-once'))
                self.assertEqual(c.reconcile(),'TERMINAL_NO_POSITION_OBSERVED')
                writes=[x for x in wire.calls if x[0]!='GET' and '/auth/' not in x[1]]
                self.assertEqual([x[0] for x in writes],['POST','PATCH','DELETE'])
                self.assertTrue(writes[1][1].endswith('/trade/orders/901'))
                self.assertEqual(writes[1][2]['json'],{'stopLoss':2491.,'stopLossType':'absolute'})

    def test_lost_ack_never_replays_and_restart_cannot_open_new_risk(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        p,q=policy_fixture()
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            path=Path(root)/'state'
            with Controller(reader,p,q,path) as c:
                wire.after_write=lambda method,path: (_ for _ in ()).throw(TimeoutError('SYNTHETIC_PRIVATE'))
                caught=None
                try: c.submit(approval(c,c.describe_submit(),'synthetic-timeout-open'))
                except ReadOnlyError as exc: caught=exc
                self.assertIsNotNone(caught)
                self.assertIsNone(caught.__context__)
                self.assertNotIn('SYNTHETIC_PRIVATE',str(caught))
                self.assertEqual(c.status,'AMBIGUOUS_NO_REPLAY')
                self.assertEqual(c.reconcile(),'WAIT_RECONCILE_NO_REPLAY')
                with self.assertRaises(ReadOnlyError): c.submit(approval(c,c.describe_submit(),'synthetic-retry-open'))
                self.assertEqual(sum(x[0]=='POST' and '/auth/' not in x[1] for x in wire.calls),1)
            with Controller(reader,p,q,path) as c:
                with self.assertRaises(ReadOnlyError): c.submit(approval(c,c.describe_submit(),'synthetic-restart-open'))
                self.assertEqual(c.reconcile(),'WAIT_RECONCILE_NO_REPLAY')
                with self.assertRaises(ReadOnlyError): c.describe_close()

    def test_close_timeout_with_unchanged_position_is_never_repeated(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        p,q=policy_fixture()
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            with Controller(reader,p,q,Path(root)/'state') as c:
                c.submit(approval(c,c.describe_submit(),'synthetic-open-timeout-close'))
                c.reconcile()
                old_positions,old_orders=wire.positions.copy(),wire.orders.copy()
                def timeout(method,path):
                    wire.positions=old_positions
                    wire.orders=old_orders
                    raise TimeoutError('synthetic')
                wire.after_write=timeout
                with self.assertRaises(ReadOnlyError): c.close_position(approval(c,c.describe_close(),'synthetic-close-timeout'))
                self.assertEqual(c.reconcile(),'OPEN_OBSERVED')
                wire.after_write=None
                with self.assertRaises(ReadOnlyError): c.close_position(approval(c,c.describe_close(),'synthetic-close-retry'))
                self.assertEqual(sum(x[0]=='DELETE' for x in wire.calls),1)

    def test_modify_ack_requires_readback_and_protection_uses_tightened_stop(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        from decimal import Decimal as D
        p,q=policy_fixture()
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            with Controller(reader,p,q,Path(root)/'state') as c:
                c.submit(approval(c,c.describe_submit(),'synthetic-open-modify-observe'))
                wire.orders=[dict(wire.history[0],status='New',filledQty=0.,positionId=0)]
                wire.history=[]; wire.positions=[]
                self.assertEqual(c.reconcile(),'PENDING_OBSERVED')
                def not_applied(method,path): wire.orders[0]['stopLoss']=2490.
                wire.after_write=not_applied
                c.modify_stop(D('2491'),approval(c,c.describe_modify_stop(D('2491')),'synthetic-modify-no-readback'))
                self.assertEqual(c.reconcile(),'WAIT_RECONCILE_NO_REPLAY')
                wire.orders[0]['stopLoss']=2491.
                self.assertEqual(c.reconcile(),'PENDING_OBSERVED')

    def test_hold_limit_missing_protection_partial_fill_allow_only_exact_close(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        p,q=policy_fixture()
        for mode in ('hold','missing_protection','partial','slippage','loss'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
                with Controller(reader,p,q,Path(root)/'state') as c:
                    c.submit(approval(c,c.describe_submit(),'synthetic-open-risk-case'))
                    if mode=='hold':
                        with patch('personal_trading.tradelocker.demo_execution.time.time',return_value=time.time()+61):
                            self.assertEqual(c.reconcile(),'OPEN_LIMIT_BREACH_STOP_NEW_RISK')
                    elif mode=='missing_protection':
                        wire.positions[0]['stopLossId']=0
                        self.assertEqual(c.reconcile(),'OPEN_UNPROTECTED_STOP_NEW_RISK')
                    elif mode=='partial':
                        wire.positions[0]['qty']=.01
                        wire.history[0]['filledQty']=.01
                        for row in wire.orders: row['qty']=.01
                        self.assertEqual(c.reconcile(),'PARTIAL_POSITION_STOP_NEW_RISK')
                    elif mode=='slippage':
                        wire.positions[0]['avgPrice']=2502.
                        self.assertEqual(c.reconcile(),'OPEN_LIMIT_BREACH_STOP_NEW_RISK')
                    else:
                        wire.positions[0]['unrealizedPl']=-11.
                        self.assertEqual(c.reconcile(),'OPEN_LIMIT_BREACH_STOP_NEW_RISK')
                    c.close_position(approval(c,c.describe_close(),'synthetic-close-risk-case'))
                    self.assertEqual(c.reconcile(),'FLAT_SNAPSHOT_ONLY')

    def test_approval_binding_and_inherited_sdk_write_denial(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        from personal_trading.tradelocker.tl_candidate import quiet_call
        p,q=policy_fixture()
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            with Controller(reader,p,q,Path(root)/'state') as c:
                good=approval(c,c.describe_submit(),'synthetic-valid-permit')
                for bad in (None, replace(good,expires_at=1),replace(good,request_digest='a'*64),
                            replace(good,policy_digest='b'*64),replace(good,acknowledgement='--arm')):
                    with self.assertRaises(ReadOnlyError): c.submit(bad)
                count=len(wire.calls)
                sdk=reader._sdk
                for call in (lambda:sdk.create_order(456,.02,'buy',type_='market',validity='IOC'),
                             lambda:sdk.delete_order(901),lambda:sdk.modify_order(901,{'qty':'1'}),
                             lambda:sdk.close_position(position_id=789,close_quantity=.02),
                             lambda:sdk.delete_all_orders(),lambda:sdk.close_all_positions()):
                    with self.assertRaises(ReadOnlyError): quiet_call(call)
                self.assertEqual(len(wire.calls),count)
                c.submit(good)
                c.reconcile()
                count=len(wire.calls)
                with self.assertRaises(ReadOnlyError):
                    c.close_position(replace(good,request_digest=c.describe_close().request_digest))
                self.assertFalse(any(x[0]=='DELETE' for x in wire.calls[count:]))

    def test_exact_permit_mismatch_refuses_before_http_and_durable_intent_precedes_write(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        p,q=policy_fixture()
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            with Controller(reader,p,q,Path(root)/'state') as c:
                real=reader._sdk.create_order
                def altered(**kwargs): return real(**dict(kwargs,quantity=.03))
                with patch.object(reader._sdk,'create_order',altered):
                    with self.assertRaises(ReadOnlyError): c.submit(approval(c,c.describe_submit(),'synthetic-wrong-request'))
                self.assertFalse(any(x[0]=='POST' and '/auth/' not in x[1] for x in wire.calls))
                self.assertEqual(c.status,'AMBIGUOUS_NO_REPLAY')
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            state=Path(root)/'state'
            with Controller(reader,p,q,state) as c:
                def inspect(method,path):
                    import json
                    data=json.loads((state/'journal.json').read_text())
                    kinds=[e['kind'] for e in data['events']]
                    self.assertEqual(kinds[-3:],['INTENT','RATE','DISPATCH'])
                    self.assertEqual(data['events'][-1]['request_digest'],c.describe_submit().request_digest)
                wire.after_write=inspect
                c.submit(approval(c,c.describe_submit(),'synthetic-durability-open'))

    def test_restart_reconciles_known_order_then_exact_close_without_new_risk(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        p,q=policy_fixture()
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            state=Path(root)/'state'
            with Controller(reader,p,q,state) as c:
                c.submit(approval(c,c.describe_submit(),'synthetic-pre-restart-open'))
            with Controller(reader,p,q,state) as c:
                self.assertTrue(c.journal.restarted)
                self.assertEqual(c.reconcile(),'OPEN_OBSERVED')
                with self.assertRaises(ReadOnlyError): c.submit(approval(c,c.describe_submit(),'synthetic-after-restart-open'))
                c.close_position(approval(c,c.describe_close(),'synthetic-after-restart-close'))
                self.assertEqual(c.reconcile(),'FLAT_SNAPSHOT_ONLY')

    def test_durable_admission_delay_cannot_use_stale_preflight_at_http_boundary(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        p,q=policy_fixture()
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            with Controller(reader,p,q,Path(root)/'state') as c:
                real_append=c.journal.append
                clock=time.monotonic
                delayed=[False]
                def append(event):
                    real_append(event)
                    if event['kind']=='INTENT': delayed[0]=True
                with patch.object(c.journal,'append',append),patch('personal_trading.tradelocker.demo_execution.time.monotonic',side_effect=lambda:clock()+(10 if delayed[0] else 0)):
                    with self.assertRaises(ReadOnlyError):
                        c.submit(approval(c,c.describe_submit(),'synthetic-delayed-admission'))
                self.assertFalse(any(x[0]=='POST' and '/auth/' not in x[1] for x in wire.calls))

    def test_explicit_rate_budget_reserves_reconciliation_and_exact_close(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        from personal_trading.tradelocker.safety import RateLimits
        from decimal import Decimal as D
        p,q=policy_fixture()
        q=replace(q,rate_limits=RateLimits(24,D('60'),6,6,'SYNTHETIC aggregate request budget',True))
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            with Controller(reader,p,q,Path(root)/'state') as c:
                c.submit(approval(c,c.describe_submit(),'synthetic-budget-open'))
                self.assertEqual(c.reconcile(),'OPEN_OBSERVED')
                for _ in range(30):
                    try: reader.quotes()
                    except ReadOnlyError: break
                else: self.fail('Ordinary polling must not be unlimited')
                self.assertEqual(c.reconcile(),'OPEN_OBSERVED')
                c.close_position(approval(c,c.describe_close(),'synthetic-budget-close'))
                self.assertEqual(c.reconcile(),'FLAT_SNAPSHOT_ONLY')
                count=len(wire.calls)
                with self.assertRaises(ReadOnlyError): reader.quotes()
                self.assertEqual(len(wire.calls),count)

    def test_real_sdk_open_reconcile_close_under_synthetic_http_boundary(self):
        from personal_trading.tradelocker.demo_execution import Controller, synthetic_reader
        from tradelocker.tradelocker_api import TLAPI
        p,q=policy_fixture()
        with tempfile.TemporaryDirectory(prefix='hermes-demo-',dir=SCRATCH) as root, synthetic_reader() as (reader,wire):
            self.assertIsInstance(reader._sdk,TLAPI)
            with Controller(reader,p,q,Path(root)/'state') as c:
                action=c.describe_submit()
                c.submit(approval(c,action,'synthetic-open-once'))
                self.assertEqual(c.status,'ACKNOWLEDGED_NOT_FILLED')
                self.assertEqual(c.reconcile(),'OPEN_OBSERVED')
                self.assertEqual(c.position_id,'789')
                action=c.describe_close()
                c.close_position(approval(c,action,'synthetic-close-once'))
                self.assertEqual(c.status,'ACKNOWLEDGED_NOT_CLOSED')
                self.assertEqual(c.reconcile(),'FLAT_SNAPSHOT_ONLY')
                writes=[call for call in wire.calls if call[0] != 'GET' and '/auth/' not in call[1]]
                self.assertEqual([x[0] for x in writes],['POST','DELETE'])
                self.assertEqual(writes[0][2]['json']['stopLoss'],2490.0)
                self.assertEqual(writes[0][2]['json']['stopLossType'],'absolute')
                self.assertEqual(writes[1][2]['json'],{'qty':'0.02'})
                with self.assertRaises(ReadOnlyError):
                    c.submit(approval(c,c.describe_submit(),'synthetic-another-open'))


if __name__=='__main__': unittest.main()
