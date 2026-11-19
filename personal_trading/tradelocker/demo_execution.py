"""One supervised demo attempt; never an autonomous strategy or live CLI.

Real SDK builds every request. Only a durable, exact one-use request permit may
cross its deny-by-default dispatcher. Broker rejection/timeout/malformed reply
are ambiguous, NEVER replayed. Python callers and state ancestors are trusted;
this is a cooperative controller, not a sandbox for hostile code in-process.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal as D
import json
from pathlib import Path
import time

from personal_trading.tradelocker.journal import Journal
from personal_trading.tradelocker.safety import (canonical_id, digest, positive, sdk_float, validate_market,
                    validate_policy, wire_decimal)
from personal_trading.tradelocker.tl_candidate import ENVIRONMENT, Reader, ReadOnlyError, quiet_call

BASE = ENVIRONMENT + '/backend-api'
WAIT = 'WAIT_RECONCILE_NO_REPLAY'


@dataclass(frozen=True)
class Action:
    operation: str
    method: str
    url: str
    body_json: str

    @property
    def body(self):
        return json.loads(self.body_json)

    @property
    def request_digest(self):
        return digest({'operation': self.operation, 'method': self.method,
                       'url': self.url, 'body': self.body})


@dataclass(frozen=True)
class OwnerApproval:
    approval_id: str
    request_digest: str
    policy_digest: str
    expires_at: int
    acknowledgement: str


def _action(operation, method, url, body):
    return Action(operation, method, url, json.dumps(body, sort_keys=True, allow_nan=False))


class Controller:
    def __init__(self, reader, policy, qualification, state_directory):
        validate_policy(policy, qualification)
        if reader.identity != policy.binding or reader._closed:
            raise ReadOnlyError('Reader/policy identity mismatch')
        sdk = reader._sdk
        if sdk._execution_guard is not None or sdk._execution_reads:
            raise ReadOnlyError('Reader already controlled')
        self._transport = sdk._session.request
        self._synthetic_wire = getattr(self._transport, '__self__', None)
        if qualification.synthetic and (type(self._synthetic_wire) is not SyntheticHTTP or not self._synthetic_wire.active):
            raise ReadOnlyError('Synthetic qualification requires active offline HTTP boundary')
        if not qualification.synthetic and type(self._synthetic_wire) is SyntheticHTTP:
            raise ReadOnlyError('Synthetic boundary cannot qualify actual broker execution')
        self.reader, self.policy, self.qualification = reader, policy, qualification
        self.policy_digest = digest({'policy': asdict(policy), 'qualification': asdict(qualification)})
        self.journal = Journal(state_directory, self.policy_digest)
        self._permit = None
        self._request_purpose='ordinary'
        self._closed = False
        sdk._execution_reads = True
        sdk._execution_guard = self

    def _last(self, kind):
        return next((e for e in reversed(self.journal.events) if e['kind'] == kind), None)

    @property
    def status(self):
        event = self._last('STATUS')
        return event['status'] if event else 'DISARMED_UNTIL_EXACT_APPROVAL'

    @property
    def position_id(self):
        event = self._last('POSITION')
        return event['position_id'] if event else None

    def _status(self, status):
        self.journal.append({'kind':'STATUS','status':status})
        return status

    def describe_submit(self):
        p, i = self.policy, self.policy.binding
        return _action('SUBMIT', 'post', BASE+f'/trade/accounts/{i.account_id}/orders', {
            'price':None, 'qty':str(sdk_float(p.quantity)), 'routeId':i.trade_route_id,
            'side':p.side, 'validity':'IOC', 'tradableInstrumentId':str(i.instrument_id),
            'type':'market', 'takeProfit':sdk_float(p.take_profit), 'takeProfitType':'absolute',
            'stopLoss':sdk_float(p.stop_loss), 'stopLossType':'absolute',
            'stopPrice':None, 'strategyId':None})

    def _snapshot(self):
        self._require_open()
        start = time.monotonic()
        self._admission_deadline=start+float(self.policy.max_snapshot_age_seconds)
        sdk = self.reader._sdk
        # Unfiltered reads: foreign pending orders/positions must not be hidden.
        history = self.reader._read(lambda:sdk.get_all_orders(history=True).to_dict('records'))
        orders = self.reader._read(lambda:sdk.get_all_orders(history=False).to_dict('records'))
        positions = self.reader.positions()
        age = D(str(time.monotonic()-start))
        if age > self.policy.max_snapshot_age_seconds:
            raise ReadOnlyError('Snapshot collection exceeded freshness budget')
        for rows in (history, orders, positions):
            ids = [canonical_id(row['id']) for row in rows]
            if len(ids) != len(set(ids)):
                raise ReadOnlyError('Duplicate broker identifiers')
        return history, orders, positions, age

    def _require_open(self):
        if self._closed or self.reader._closed or self.reader.identity != self.policy.binding:
            raise ReadOnlyError('Controller is closed or binding changed')
        self.journal.require_open()
        # Reuse the read boundary's exact SDK binding checks without HTTP.
        self.reader._read(lambda:None)
        if self.qualification.synthetic and (self.reader._sdk._session.request is not self._transport or not self._synthetic_wire.active):
            raise ReadOnlyError('Offline HTTP boundary changed')

    def submit(self, approval):
        self._require_open()
        if self.journal.restarted or self._last('INTENT') or self._last('STOP'):
            raise ReadOnlyError('Single attempt or restart stop-new-risk gate; never resubmit')
        snapshot_start=time.monotonic()
        _, orders, positions, age = self._snapshot()
        if orders or positions:
            raise ReadOnlyError('Fresh entire account must start flat with no pending orders')
        balances = self.reader.balances()
        start = time.monotonic()
        quote = self.reader.quotes()
        self._admission_deadline=min(self._admission_deadline,start+float(self.policy.max_quote_age_seconds))
        quote_age = D(str(time.monotonic()-start))
        validate_market(self.policy, self.qualification,
            bid=wire_decimal(quote['bp']), ask=wire_decimal(quote['ap']),
            quote_age=quote_age, snapshot_age=D(str(time.monotonic()-snapshot_start)),
            free_margin=wire_decimal(balances.get('availableMargin')))
        self.journal.append({'kind':'REFERENCE','entry':str(quote['ap'] if self.policy.side=='buy' else quote['bp'])})
        p = self.policy
        self._execute(self.describe_submit(), approval, lambda:self.reader._sdk.create_order(
            instrument_id=p.binding.instrument_id, quantity=sdk_float(p.quantity), side=p.side,
            type_='market', validity='IOC', position_netting=False,
            stop_loss=sdk_float(p.stop_loss), stop_loss_type='absolute',
            take_profit=sdk_float(p.take_profit), take_profit_type='absolute'))

    def _execute(self, action, approval, function):
        self._require_open()
        if any(e['kind']=='INTENT' and e['operation']==action.operation for e in self.journal.events):
            raise ReadOnlyError('Operation already attempted; reconcile only, never replay')
        if self._permit is not None or type(approval) is not OwnerApproval:
            raise ReadOnlyError('Exact one-use owner approval required')
        import re
        expected = 'APPROVE ONE DEMO REQUEST '+action.request_digest+' POLICY '+self.policy_digest
        if (not isinstance(approval.approval_id,str) or not re.fullmatch('[A-Za-z0-9_-]{8,80}',approval.approval_id) or
                approval.request_digest != action.request_digest or approval.policy_digest != self.policy_digest or
                type(approval.expires_at) is not int or approval.expires_at <= time.time() or
                approval.acknowledgement != expected or
                any(e.get('approval_id') == approval.approval_id for e in self.journal.events)):
            raise ReadOnlyError('Missing, mismatched, expired or consumed exact owner approval')
        if self.status == 'AMBIGUOUS_NO_REPLAY':
            raise ReadOnlyError('Reconcile ambiguous outcome before any further mutation')
        self.journal.append({'kind':'INTENT','operation':action.operation,
            'request_digest':action.request_digest,'approval_id':approval.approval_id,
            'body':action.body,'timestamp':time.time()})
        self._permit = action
        self._approval_expiry=approval.expires_at
        self._dispatched = False
        failed = False
        result = None
        try:
            result = quiet_call(function)
        except Exception:
            failed = True
        finally:
            # Unused permits are revoked too, even for local SDK validation errors.
            self._permit = None
        if failed or not self._dispatched or (action.operation == 'SUBMIT' and (type(result) is not int or result <= 0)) or (action.operation != 'SUBMIT' and result is not True):
            self.journal.append({'kind':'STOP','reason':'AMBIGUOUS'})
            self._status('AMBIGUOUS_NO_REPLAY')
            raise ReadOnlyError('Execution result ambiguous; reconcile only, never replay')
        self.journal.append({'kind':'ACK','operation':action.operation,
                             'order_id':str(result) if action.operation=='SUBMIT' else None})
        if action.operation == 'SUBMIT':
            self._status('ACKNOWLEDGED_NOT_FILLED')
        elif action.operation == 'CLOSE':
            self._status('ACKNOWLEDGED_NOT_CLOSED')
        else:
            self._status('ACKNOWLEDGED_RECONCILE_REQUIRED')

    def consume(self, method, url, body):
        """Called only at final SDK HTTP boundary, never a broad SDK unlock."""
        self._require_open()
        action = self._permit
        if action is None or (method, url, body) != (action.method, action.url, action.body):
            raise ReadOnlyError('No exact durable controller permit for this request')
        intent = self._last('INTENT')
        if intent is None or intent['request_digest'] != action.request_digest:
            raise ReadOnlyError('Durable intent binding refused')
        self._permit = None  # one use, including failed HTTP attempts
        self.journal.append({'kind':'DISPATCH','request_digest':action.request_digest})
        # Durability work may itself consume the freshness/approval budget.
        # Recheck after fsync, at the final boundary, before any HTTP write.
        if time.monotonic()>self._admission_deadline or time.time()>=self._approval_expiry:
            raise ReadOnlyError('Final request freshness/approval deadline expired')
        if action.operation=='SUBMIT' and time.time()>=self.qualification.valid_until:
            raise ReadOnlyError('Final qualification deadline expired')
        self._dispatched = True

    def admit_http(self):
        """Durable synthetic aggregate budget, not real REST qualification.

        Includes reads/refresh and failed attempts. Never waits or retries.
        Preserves window usage across restart; rollback counts conservatively.
        REST limits are not a daily-trade allowance or owner risk policy.
        """
        self._require_open()
        limits=self.qualification.rate_limits
        now=time.time()
        used=sum(e['kind']=='RATE' and now-e['timestamp']<float(limits.window_seconds)
                 for e in self.journal.events)
        purpose=self._request_purpose
        if self._permit is not None and self._permit.operation in ('CLOSE','CANCEL'):
            purpose='protective'
        capacity=limits.requests
        if purpose!='protective':
            capacity-=limits.protective_reserve
        if purpose=='ordinary':
            capacity-=limits.reconciliation_reserve
        if used>=capacity:
            raise ReadOnlyError('Explicit request budget exhausted; reserved capacity protected')
        self.journal.append({'kind':'RATE','timestamp':now,'purpose':purpose})

    @contextmanager
    def _purpose(self,purpose):
        previous=self._request_purpose
        self._request_purpose=purpose
        try:
            yield
        finally:
            self._request_purpose=previous

    def _owned(self, row):
        i = self.policy.binding
        return (canonical_id(row['tradableInstrumentId']) == str(i.instrument_id) and
                canonical_id(row['routeId']) == i.trade_route_id and row['side']==self.policy.side)

    def reconcile(self):
        failed=False
        result=None
        try:
            intent=self._last('INTENT')
            purpose='protective' if self._request_purpose=='protective' or (intent and intent['operation'] in ('CLOSE','CANCEL')) else 'reconcile'
            with self._purpose(purpose):
                result=quiet_call(self._reconcile)
        except Exception:
            failed=True
        return self._status(WAIT) if failed else result

    def _effective_stop(self):
        observed=self._last('STOP_OBSERVED')
        return wire_decimal(observed['stop_loss']) if observed else self.policy.stop_loss

    def _requested_stop(self):
        intent=next((e for e in reversed(self.journal.events) if e['kind']=='INTENT' and e['operation']=='MODIFY'),None)
        return wire_decimal(intent['body']['stopLoss']) if intent else self.policy.stop_loss

    def _observe_stop(self, stop):
        observed=self._last('STOP_OBSERVED')
        if observed is None or wire_decimal(observed['stop_loss'])!=stop:
            self.journal.append({'kind':'STOP_OBSERVED','stop_loss':str(stop)})

    def _reconcile(self, *, for_cancel=False):
        failed = False
        snapshots = None
        try:
            snapshots = self._snapshot()
        except Exception:
            failed = True
        if failed:
            return self._status(WAIT)
        history, orders, positions, _ = snapshots
        ack = next((e for e in reversed(self.journal.events) if e['kind']=='ACK' and e['operation']=='SUBMIT'),None)
        if ack is None:
            return self._status(WAIT)
        if self._last('INTENT')['operation'] == 'CLOSE' and not positions and not orders:
            return self._status('FLAT_SNAPSHOT_ONLY')
        matches = [r for r in history if canonical_id(r['id']) == ack['order_id']]
        if len(matches) == 1:
            row=matches[0]
            if self._owned(row) and row['status']=='Filled':
                if not positions and not orders and self._native_exit(row,history):
                    return self._status('NATIVE_EXIT_OBSERVED_COSTS_UNVERIFIED')
                linked=[p for p in positions if canonical_id(p['id'])==canonical_id(row['positionId'])]
                if len(linked)==1 and len(positions)==1 and self._owned(linked[0]):
                    pos=linked[0]
                    qty=wire_decimal(pos['qty'])
                    if D(0)<qty<=self.policy.quantity and wire_decimal(row['filledQty'])==qty:
                        self.journal.append({'kind':'POSITION','position_id':canonical_id(pos['id']),
                                             'quantity':str(qty)})
                        protected=self._protected(pos,orders,qty)
                        if not protected:
                            self.stop_new_risk('PROTECTION_NOT_OBSERVED')
                            return self._status('OPEN_UNPROTECTED_STOP_NEW_RISK')
                        if qty!=self.policy.quantity:
                            self.stop_new_risk('PARTIAL_FILL')
                            return self._status('PARTIAL_POSITION_STOP_NEW_RISK')
                        if self._risk_breached(pos):
                            self.stop_new_risk('HOLD_OR_LOSS_LIMIT')
                            return self._status('OPEN_LIMIT_BREACH_STOP_NEW_RISK')
                        return self._status('OPEN_OBSERVED')
            if self._owned(row) and row['status'] in ('Canceled','Cancelled','Rejected') and not positions and not orders:
                return self._status('TERMINAL_NO_POSITION_OBSERVED')
        pending=[r for r in orders if canonical_id(r['id'])==ack['order_id']]
        if (len(pending)==1 and len(orders)==1 and not positions and self._owned(pending[0]) and
                pending[0]['status']=='New' and pending[0]['type']=='market' and pending[0]['positionId']==0 and
                wire_decimal(pending[0]['qty'])==self.policy.quantity and wire_decimal(pending[0]['filledQty'])==0):
            stop=wire_decimal(pending[0]['stopLoss'])
            if stop not in (self.policy.stop_loss,self._requested_stop()) or wire_decimal(pending[0]['takeProfit'])!=self.policy.take_profit:
                return self._status(WAIT)
            self._observe_stop(stop)
            if stop!=self._requested_stop():
                # Intent is not application evidence. The exact wholly-unfilled
                # order can still be cancelled, never modified a second time.
                return self._status('PENDING_CANCEL_ONLY' if for_cancel else WAIT)
            return self._status('PENDING_OBSERVED')
        return self._status(WAIT)

    def _protected(self,pos,orders,qty):
        expected_side='sell' if self.policy.side=='buy' else 'buy'
        if len(orders)!=2 or not pos.get('stopLossId') or not pos.get('takeProfitId'):
            return False
        for id_key,kind,price_key,expected_price in (
            ('stopLossId','stop','stopPrice',self._requested_stop()),
            ('takeProfitId','limit','price',self.policy.take_profit)):
            matches=[o for o in orders if canonical_id(o['id'])==canonical_id(pos[id_key])]
            if len(matches)!=1:
                return False
            o=matches[0]
            if not (canonical_id(o['positionId'])==canonical_id(pos['id']) and
                canonical_id(o['tradableInstrumentId'])==str(self.policy.binding.instrument_id) and
                canonical_id(o['routeId'])==self.policy.binding.trade_route_id and
                o['side']==expected_side and o['type']==kind and wire_decimal(o['qty'])==qty and
                # Only the synthetic New/zero-fill mapping is qualified. A
                # terminal/unknown row or partially consumed leg cannot cover
                # the entire observed position, even on the active endpoint.
                o['status']=='New' and wire_decimal(o['filledQty'])==0 and
                wire_decimal(o[price_key])==expected_price):
                return False
        self._observe_stop(self._requested_stop())
        protection={'kind':'PROTECTION_OBSERVED','position_id':canonical_id(pos['id']),
                    'entry_order_id':self._order_id(),'quantity':str(qty),
                    'stop_order_id':canonical_id(pos['stopLossId']),
                    'target_order_id':canonical_id(pos['takeProfitId']),
                    'stop_loss':str(self._requested_stop()),'take_profit':str(self.policy.take_profit)}
        if self._last('PROTECTION_OBSERVED')!=protection:
            self.journal.append(protection)
        return True

    def _native_exit(self, entry, history):
        # Account emptiness is checked by the caller, never used as a fill or
        # cost observation. Require the exact previously observed working pair,
        # now one wholly filled and its wholly-unfilled sibling cancelled.
        proof=self._last('PROTECTION_OBSERVED')
        position=self._last('POSITION')
        if proof is None or position is None:
            return False
        qty=wire_decimal(proof['quantity'])
        if not (proof['entry_order_id']==canonical_id(entry['id']) and
                proof['position_id']==canonical_id(entry['positionId'])==position['position_id'] and
                D(0)<qty<=self.policy.quantity and wire_decimal(position['quantity'])==qty and
                entry['type']=='market' and wire_decimal(entry['qty'])==self.policy.quantity and
                wire_decimal(entry['filledQty'])==qty):
            return False
        ids={proof['entry_order_id'],proof['stop_order_id'],proof['target_order_id']}
        if len(ids)!=3 or {canonical_id(r['id']) for r in history
                           if r['positionId']!=0 and canonical_id(r['positionId'])==proof['position_id']}!=ids:
            return False
        children=[]
        expected_side='sell' if self.policy.side=='buy' else 'buy'
        for id_key,kind,price_key,price in (
            ('stop_order_id','stop','stopPrice',proof['stop_loss']),
            ('target_order_id','limit','price',proof['take_profit'])):
            rows=[r for r in history if canonical_id(r['id'])==proof[id_key]]
            if len(rows)!=1:
                return False
            row=rows[0]
            if not (canonical_id(row['positionId'])==proof['position_id'] and
                    canonical_id(row['tradableInstrumentId'])==str(self.policy.binding.instrument_id) and
                    canonical_id(row['routeId'])==self.policy.binding.trade_route_id and
                    row['side']==expected_side and row['type']==kind and
                    wire_decimal(row['qty'])==qty and wire_decimal(row[price_key])==wire_decimal(price)):
                return False
            children.append(row)
        filled=[r for r in children if r['status']=='Filled' and wire_decimal(r['filledQty'])==qty]
        cancelled=[r for r in children if r['status'] in ('Canceled','Cancelled') and wire_decimal(r['filledQty'])==0]
        if len(filled)!=1 or len(cancelled)!=1:
            return False
        event={'kind':'NATIVE_EXIT','position_id':proof['position_id'],
               'exit_order_id':canonical_id(filled[0]['id']),
               'cancelled_order_id':canonical_id(cancelled[0]['id']),'quantity':str(qty),
               'exit_kind':filled[0]['type'],'fill_price':None,'costs':None,'pnl':None,
               'evidence':'LINKED_ORDER_HISTORY_AND_FLAT_SNAPSHOT'}
        if self._last('NATIVE_EXIT')!=event:
            self.journal.append(event)
        return True

    def _risk_breached(self,pos):
        intent=next(e for e in self.journal.events if e['kind']=='INTENT' and e['operation']=='SUBMIT')
        elapsed=D(str(time.time()-intent['timestamp']))
        if elapsed<0 or elapsed>self.policy.max_hold_seconds:
            return True
        if 'unrealizedPl' not in pos:
            return True  # unknown loss observation is not zero risk
        if wire_decimal(pos['unrealizedPl']) < -self.policy.max_loss_usd:
            return True
        reference=self._last('REFERENCE')
        if reference is None:
            return True
        adverse=wire_decimal(pos['avgPrice'])-wire_decimal(reference['entry'])
        if self.policy.side=='sell':
            adverse=-adverse
        return adverse>self.policy.max_slippage

    def describe_close(self):
        event=self._last('POSITION')
        if event is None:
            raise ReadOnlyError('No exact linked position observed')
        return _action('CLOSE','delete',BASE+'/trade/positions/'+event['position_id'],
                       {'qty':str(sdk_float(D(event['quantity'])))})

    def _order_id(self):
        ack=next((e for e in reversed(self.journal.events) if e['kind']=='ACK' and e['operation']=='SUBMIT'),None)
        if ack is None:
            raise ReadOnlyError('No acknowledged owned order')
        return ack['order_id']

    def describe_modify_stop(self, stop_loss):
        positive(stop_loss)
        p=self.policy
        if stop_loss % self.qualification.tick_size:
            raise ReadOnlyError('Stop price violates tick grid')
        if not ((p.stop_loss < stop_loss < p.take_profit) if p.side=='buy' else (p.take_profit < stop_loss < p.stop_loss)):
            raise ReadOnlyError('Only tighter protective stop allowed')
        start=time.monotonic()
        quote=self.reader.quotes()
        self._admission_deadline=min(getattr(self,'_admission_deadline',start),start+float(p.max_quote_age_seconds))
        if not ((stop_loss < wire_decimal(quote['bp'])) if p.side=='buy' else (stop_loss > wire_decimal(quote['ap']))):
            raise ReadOnlyError('Tighter stop must stay on protective side of market')
        return _action('MODIFY','patch',BASE+'/trade/orders/'+self._order_id(),
                       {'stopLoss':sdk_float(stop_loss),'stopLossType':'absolute'})

    def modify_stop(self, stop_loss, approval):
        if self.reconcile()!='PENDING_OBSERVED' or self._last('STOP'):
            raise ReadOnlyError('Only exact unfilled owned pending order may be tightened')
        action=self.describe_modify_stop(stop_loss)
        self._execute(action,approval,lambda:self.reader._sdk.modify_order(
            order_id=int(self._order_id()),modification_params=action.body))

    def describe_cancel(self):
        return _action('CANCEL','delete',BASE+'/trade/orders/'+self._order_id(),None)

    def cancel_order(self,approval):
        with self._purpose('protective'):
            observed=quiet_call(lambda:self._reconcile(for_cancel=True))
        if observed not in ('PENDING_OBSERVED','PENDING_CANCEL_ONLY'):
            raise ReadOnlyError('Fresh exact unfilled owned order required before cancel')
        self._execute(self.describe_cancel(),approval,
                      lambda:self.reader._sdk.delete_order(int(self._order_id())))

    def close_position(self,approval):
        with self._purpose('protective'):
            status=self.reconcile()
        if status not in ('OPEN_OBSERVED','OPEN_UNPROTECTED_STOP_NEW_RISK','PARTIAL_POSITION_STOP_NEW_RISK','OPEN_LIMIT_BREACH_STOP_NEW_RISK'):
            raise ReadOnlyError('Fresh exact order-to-position link required before close')
        event=self._last('POSITION')
        action=self.describe_close()
        self._execute(action,approval,lambda:self.reader._sdk.close_position(
            position_id=int(event['position_id']),close_quantity=sdk_float(D(event['quantity']))))

    def stop_new_risk(self,reason='OWNER_STOP'):
        # Fixed allowlist, never log arbitrary caller text or secrets.
        reason=reason if reason in ('OWNER_STOP','PROTECTION_NOT_OBSERVED','PARTIAL_FILL','HOLD_OR_LOSS_LIMIT') else 'OWNER_STOP'
        self.journal.append({'kind':'STOP','reason':reason})

    def close(self):
        if not self._closed:
            self._permit=None
            self.reader._sdk._execution_guard=None
            self.reader._sdk._execution_reads=False
            self.journal.close()
            self._closed=True

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()


class SyntheticHTTP:
    """Explicit synthetic fixture at HTTP boundary, never a broker replacement."""
    def __init__(self):
        import jwt
        self.active=True
        self.calls=[]
        self.token=jwt.encode({'exp':4102444800,'sub':'synthetic'},
            'synthetic-offline-only-hmac-key-32bytes-minimum',algorithm='HS256')
        self.order_columns=['id','tradableInstrumentId','routeId','qty','side','type',
            'status','filledQty','positionId','stopPrice','price','stopLoss','takeProfit']
        self.position_columns=['id','tradableInstrumentId','routeId','side','qty','avgPrice','stopLossId','takeProfitId','unrealizedPl']
        self.positions=[]
        self.orders=[]
        self.history=[]
        self.after_write=None

    def request(self,method,url,**kwargs):
        import requests
        if not self.active:
            raise AssertionError('Offline fixture expired')
        self.calls.append((method,url,kwargs))
        p=url.split('/backend-api',1)[1]
        cols=lambda keys:{'columns':[{'id':k} for k in keys]}
        if p in ('/auth/jwt/token','/auth/jwt/refresh'):
            data={'accessToken':self.token,'refreshToken':self.token}
        elif p=='/auth/jwt/all-accounts':
            data={'accounts':[{'id':123,'accNum':7,'currency':'USD','name':'SYNTHETIC'}]}
        elif p=='/trade/accounts/123/instruments':
            data={'s':'ok','d':{'instruments':[{'tradableInstrumentId':456,'name':'XAUUSD.R','routes':[{'id':17,'type':'INFO'},{'id':18,'type':'TRADE'}]}]}}
        elif p=='/trade/config':
            data={'s':'ok','d':{'positionsConfig':cols(self.position_columns),
                'ordersConfig':cols(self.order_columns),'ordersHistoryConfig':cols(self.order_columns),
                'accountDetailsConfig':cols(['balance','availableMargin']),
                'limits':[{'limitType':'QUOTES_HISTORY_BARS','limit':1000}]}}
        elif p=='/trade/accounts/123/state':
            data={'s':'ok','d':{'accountDetailsData':[54321.25,1000.0]}}
        elif p=='/trade/quotes':
            data={'s':'ok','d':{'bp':2500.0,'ap':2500.5,'bs':1.0,'as':1.0}}
        elif method=='GET' and p.endswith(('/positions','/orders','/ordersHistory')):
            key=p.rsplit('/',1)[-1]
            rows={'positions':self.positions,'orders':self.orders,'ordersHistory':self.history}[key]
            keys=self.position_columns if key=='positions' else self.order_columns
            data={'s':'ok','d':{key:[[row[k] for k in keys] for row in rows]}}
        elif method=='POST' and p=='/trade/accounts/123/orders':
            body=kwargs['json']
            self.history=[dict(id=901,tradableInstrumentId=456,routeId=18,qty=.02,side=body['side'],
                type='market',status='Filled',filledQty=.02,positionId=789,stopPrice=0.,price=0.,
                stopLoss=body['stopLoss'],takeProfit=body['takeProfit'])]
            self.positions=[dict(id=789,tradableInstrumentId=456,routeId=18,side=body['side'],qty=.02,
                avgPrice=2500.5,stopLossId=902,takeProfitId=903,unrealizedPl=0.)]
            other='sell' if body['side']=='buy' else 'buy'
            self.orders=[dict(self.history[0],id=902,side=other,type='stop',status='New',filledQty=0.,stopPrice=body['stopLoss']),
                         dict(self.history[0],id=903,side=other,type='limit',status='New',filledQty=0.,price=body['takeProfit'])]
            data={'s':'ok','d':{'orderId':'901'}}
        elif method=='DELETE' and p=='/trade/positions/789':
            self.positions=[]
            self.orders=[]
            data={'s':'ok'}
        elif method=='PATCH' and p=='/trade/orders/901':
            self.orders[0]['stopLoss']=kwargs['json']['stopLoss']
            data={'s':'ok'}
        elif method=='DELETE' and p=='/trade/orders/901':
            self.history=[dict(self.orders[0],status='Cancelled')]
            self.orders=[]
            data={'s':'ok'}
        else:
            raise AssertionError('Unexpected synthetic endpoint')
        response=requests.Response()
        response.status_code=201 if method=='POST' else 200
        response.url=url
        response._content=json.dumps(data).encode()
        if method!='GET' and '/auth/' not in p and self.after_write is not None:
            self.after_write(method,p)
        return response


@contextmanager
def synthetic_reader():
    """No sockets/DNS; real SDK constructor, request builders and table models."""
    import requests
    import socket
    from unittest.mock import patch
    wire=SyntheticHTTP()
    reader=None
    try:
        with patch.object(socket.socket,'connect',side_effect=AssertionError('Offline sockets blocked')), \
             patch.object(socket,'getaddrinfo',side_effect=AssertionError('Offline DNS blocked')), \
             patch.object(requests.Session,'request',lambda session,method,url,**kwargs:wire.request(method,url,**kwargs)):
            reader=Reader.connect('synthetic-user','synthetic-password',123,7)
            reader._sdk._session.request=wire.request
            yield reader,wire
    finally:
        wire.active=False
        if reader is not None:
            reader.close()


def run_synthetic_smoke():
    """Explicitly synthetic numbers/approval, usable ONLY inside mocked HTTP.

    Not an owner policy example, broker proof or an actual account call. Generic
    framework order hooks remain disabled even on the supervised bridge.
    """
    import tempfile
    from personal_trading.tradelocker.safety import Qualification, RiskPolicy, RateLimits
    from personal_trading.tradelocker.tl_candidate import Identity, SERVER, SYMBOL
    from personal_trading.tradelocker.lumi_adapter import SupervisedDemoBroker
    identity=Identity(ENVIRONMENT,SERVER,123,7,'USD',SYMBOL,456,'17','18')
    q=Qualification(identity,D('.01'),D('1'),D('.01'),D('.01'),D('10'),D('3000'),
        D('20'),D('2'),True,True,True,True,True,'SYNTHETIC smoke fixture',True,
        True,True,True,'synthetic_sdk_quantity_equals_fixture_lots',int(time.time())+300,
        RateLimits(200,D('60'),30,30,'SYNTHETIC aggregate smoke budget',True))
    p=RiskPolicy(identity,'buy',D('.02'),D('2490'),D('2520'),D('100'),D('10'),
        D('1'),D('1'),D('5'),D('5'),D('60'))
    result={'evidence':'SYNTHETIC_HTTP_ONLY','execution':'disarmed',
        'actual_execution':'blocked_unqualified_contract','account_calls_performed':False,
        'quantity_unit':q.quantity_unit,'mock_write_methods':[]}
    def synthetic_approval(controller,action,label):
        # Never expose this as a live approval facility.
        return OwnerApproval('synthetic-smoke-'+label,action.request_digest,
            controller.policy_digest,int(time.time())+60,
            'APPROVE ONE DEMO REQUEST '+action.request_digest+' POLICY '+controller.policy_digest)
    with tempfile.TemporaryDirectory(prefix='hermes-smoke-',dir=tempfile.gettempdir()) as root:
        for pending in (False,True):
            with synthetic_reader() as (reader,wire), Controller(reader,p,q,Path(root)/str(pending)) as c:
                broker=quiet_call(lambda:SupervisedDemoBroker(c))
                stages=[]
                broker.submit_demo(synthetic_approval(c,c.describe_submit(),'open'))
                stages.append(c.status)
                if pending:
                    wire.orders=[dict(wire.history[0],status='New',filledQty=0.,positionId=0)]
                    wire.history=[]
                    wire.positions=[]
                    stages.append(broker.reconcile_demo())
                    broker.modify_demo_stop(D('2491'),synthetic_approval(c,c.describe_modify_stop(D('2491')),'modify'))
                    stages.append(broker.reconcile_demo())
                    broker.cancel_demo(synthetic_approval(c,c.describe_cancel(),'cancel'))
                    stages.append(broker.reconcile_demo())
                else:
                    stages.append(broker.reconcile_demo())
                    broker.close_demo(synthetic_approval(c,c.describe_close(),'close'))
                    stages.append(c.status)
                    stages.append(broker.reconcile_demo())
                result['pending_modify_cancel' if pending else 'open_close']=stages
                result['mock_write_methods'].extend(m for m,u,_ in wire.calls if m!='GET' and '/auth/' not in u)
    return result
