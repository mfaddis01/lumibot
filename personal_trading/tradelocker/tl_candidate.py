"""Deny-by-default TradeLocker boundary. Dedicated single-threaded process only.

Official SDK constructors, authentication, account/config/table decoders and data
methods are used. Its private HTTP dispatcher is deliberately replaced: no
unpermitted mutations, debug payload logging, redirects, ambient proxy/netrc or
retries. Controller mutations currently require an active synthetic transport;
actual economics/units/permissions are unresolved and cannot be flag-armed.
"""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import metadata, util
from pathlib import Path
import hashlib
import io
import json
import logging
import re
import sys
import threading

ENVIRONMENT = 'https://demo.tradelocker.com'
SERVER = 'GATESFX'
SYMBOL = 'XAUUSD.R'
SDK_HASHES = {
    'tradelocker_api.py': '582b3d057dc115102ad4fc6b0d92f3480382bd54c37062d02e4cbaf6fb952b82',
    'utils.py': '4731a9e38e0e10d29000dfbad69c58482cfc773f5fb1ffd24c7be84a3de16b03',
    'types.py': '7b8cbfb739b6d1167a5fdb23551c37ef37ef1c49191f0f05ea88e0094f47e284',
    'exceptions.py': 'a6f934a5cd8b53b774ac87f6d4d229c323dddd6526b3a09b2eddd7e7d63f4fad',
    '__about__.py': '1112f76c34fdc1ed1eeccce6381e17f9ae14b71790f06295cf8f1ff20c7ccd28',
    '__init__.py': '03d82f0f61e6024ab7d8ee5e55032c75123c210ce7296ae4d153ee17b2a80e89',
}


class ReadOnlyError(RuntimeError):
    """Only sanitized, fixed messages cross the public API boundary."""

    def __init__(self, message, *, diagnostic_code=None):
        super().__init__(message)
        self.diagnostic_code = diagnostic_code if type(diagnostic_code) is str and re.fullmatch(
            r'(AUTH|REFRESH|ACCOUNT_LIST|CONFIG|INSTRUMENTS|STATE|POSITIONS|DETAILS|QUOTES|HISTORY)_HTTP_STATUS_[1-5][0-9]{2}',
            diagnostic_code) else 'UNCLASSIFIED'


class DisarmedError(ReadOnlyError):
    pass


def decode_response(response):
    """Validate raw JSON before SDK decoding; retain exact numeric lexemes.

    Object-pair validation applies recursively, including ignored nested fields.
    A decoded dict cannot reveal duplicate names already collapsed by the SDK.
    """
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ReadOnlyError('Duplicate response JSON member refused')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ReadOnlyError('Nonfinite response JSON number refused')

    return json.loads(response.content, object_pairs_hook=unique,
                      parse_float=Decimal, parse_constant=invalid_constant)


class _Discard(io.TextIOBase):
    def write(self, text):
        return len(text)


def quiet_call(function):
    """Never retain SDK exception context or captured output in returned errors.

    Global logging/output suppression makes this suitable ONLY for a dedicated
    process; refuse use with other Python threads rather than race their logs.
    """
    if threading.active_count() != 1:
        raise ReadOnlyError('Dedicated single-threaded process required')
    previous = logging.root.manager.disable
    failed = False
    diagnostic = None
    result = None
    sink = _Discard()
    try:
        logging.disable(sys.maxsize)
        with redirect_stdout(sink), redirect_stderr(sink):
            try:
                result = function()
            except ReadOnlyError as exc:
                diagnostic = exc.diagnostic_code
                failed = True
            except Exception as exc:
                # Pinned SDK wraps authentication failures in ValueError. Read
                # only our bounded diagnostic from its cause, never its text.
                cause = exc.__cause__
                if isinstance(cause, ReadOnlyError):
                    diagnostic = cause.diagnostic_code
                cause = None
                failed = True
    finally:
        logging.disable(previous)
    if failed:
        raise ReadOnlyError('Read-only SDK operation failed; no raw details retained', diagnostic_code=diagnostic)
    return result


def _positive(value):
    return type(value) is int and value > 0


@dataclass(frozen=True)
class Identity:
    environment: str
    server: str
    account_id: int = field(repr=False)
    acc_num: int = field(repr=False)
    currency: str
    symbol: str
    instrument_id: int
    info_route_id: str
    trade_route_id: str


def _sdk_class():
    sdk_version = metadata.version('tradelocker')
    if sdk_version not in {'0.56.2', '0.56.2+lumibot.jwt2151.1', '0.56.2+lumibot.runtime.1'}:
        raise ReadOnlyError('Unqualified SDK version')
    if sdk_version == '0.56.2+lumibot.jwt2151.1':
        # Explicit local packaging derivative, not a new official SDK release.
        if (metadata.version('PyJWT') != '2.15.1' or
                'PyJWT[crypto]==2.15.1' not in (metadata.requires('tradelocker') or [])):
            raise ReadOnlyError('Unqualified local SDK dependency override')
    if sdk_version == '0.56.2+lumibot.runtime.1':
        # Explicit full-runtime LOCAL metadata derivative, not an official
        # compatible SDK release. Source remains gated by all six hashes below.
        requirements=metadata.requires('tradelocker') or []
        jwt_requirements=[r for r in requirements if re.match(r'(?i)^pyjwt(?:\[|[ <=>!~;]|$)',r)]
        requests_requirements=[r for r in requirements if re.match(r'(?i)^requests(?:\[|[ <=>!~;]|$)',r)]
        if (metadata.version('PyJWT')!='2.15.1' or metadata.version('requests')!='2.34.2' or
                jwt_requirements!=['PyJWT[crypto]==2.15.1'] or requests_requirements!=['requests==2.34.2']):
            raise ReadOnlyError('Unqualified full-runtime local SDK dependency overrides')
    root = Path(util.find_spec('tradelocker').origin).parent
    if any(hashlib.sha256((root / f).read_bytes()).hexdigest() != h for f, h in SDK_HASHES.items()):
        raise ReadOnlyError('Unqualified SDK source')
    from tradelocker.tradelocker_api import TLAPI
    import requests

    class ReadOnlySDK(TLAPI):
        def __new__(cls, *args, **kwargs):
            # Official multiton keys retain constructor passwords indefinitely.
            # Use real __init__, but deliberately do not use that global cache.
            return object.__new__(cls)

        def __init__(self, username, password, account_id=0, acc_num=0, selector=None):
            self._selector = selector
            self._expected_account_id = account_id
            self._expected_acc_num = acc_num
            self._bound_instrument = None
            self._bound_info_route = None
            self._bound_trade_route = None
            self._execution_reads = False
            self._execution_guard = None
            self._session = requests.Session()
            self._session.trust_env = False
            try:
                super().__init__(environment=ENVIRONMENT, username=username, password=password,
                                 server=SERVER, account_id=account_id, acc_num=acc_num,
                                 log_level='critical', disk_cache_location=None)
                if (self.account_id, self.acc_num) != (self._expected_account_id, self._expected_acc_num):
                    raise ReadOnlyError('Account selection mismatch')
            except BaseException:
                self._session.close()
                self._credentials = None
                self._access_token = self._refresh_token = ''
                raise
            finally:
                self._selector = None

        def _set_account_id_and_acc_num(self, account_id, acc_num):
            # Called by the REAL SDK constructor after password authentication.
            # Replace its first-account fallback, not the constructor/auth flow.
            accounts = self.get_all_accounts()
            if accounts.empty or accounts['id'].duplicated().any() or accounts['accNum'].duplicated().any():
                raise ReadOnlyError('Unique returned account identities required')
            if self._selector is not None:
                if account_id != 0 or acc_num != 0:
                    raise ReadOnlyError('Mixed account selection modes refused')
                def mask(value):
                    text = str(value)
                    return '****' + (text[-2:] if len(text) > 2 else '')
                options = tuple(f'[{n}] accountId {mask(row.id)} / accNum {mask(row.accNum)}'
                                for n, row in enumerate(accounts.itertuples(), 1))
                choice = self._selector(options)
                if type(choice) is not int or not 1 <= choice <= len(accounts):
                    raise ReadOnlyError('Explicit returned-account selection required')
                row = accounts.iloc[choice - 1]
                account_id, acc_num = int(row['id']), int(row['accNum'])
            if not _positive(account_id) or not _positive(acc_num):
                raise ReadOnlyError('Explicit positive account pair required')
            rows = accounts[(accounts['id'] == account_id) & (accounts['accNum'] == acc_num)]
            if len(rows) != 1 or rows.iloc[0]['currency'] != 'USD':
                raise ReadOnlyError('Exact unique USD account pair required')
            self._expected_account_id, self._expected_acc_num = account_id, acc_num
            # Use the official explicit-ID selector only AFTER checking the pair.
            super()._set_account_id_and_acc_num(account_id, acc_num)

        def _request(self, request_type, url, additional_headers=None, additional_params=None,
                     include_acc_num=True, include_access_token=True, json_data=None,
                     retry_request=True):
            base = ENVIRONMENT + '/backend-api'
            auth = {base + '/auth/jwt/token', base + '/auth/jwt/refresh'}
            common = {base + '/auth/jwt/all-accounts', base + '/trade/config'}
            account = base + f'/trade/accounts/{self._expected_account_id}'
            reads = common | {account + suffix for suffix in ('/instruments', '/state', '/positions')}
            if self._execution_reads:
                reads |= {account + '/orders', account + '/ordersHistory'}
            instrument = self._bound_instrument
            if instrument is not None:
                reads |= {base + '/trade/quotes', base + '/trade/history', base + f'/trade/instruments/{instrument}'}
            if additional_headers is not None or self.environment != ENVIRONMENT or self._base_url != base:
                raise DisarmedError('Request boundary refused')
            execution_guard = None
            if request_type == 'post' and url in auth:
                if include_acc_num or include_access_token or not isinstance(json_data, dict):
                    raise DisarmedError('Authentication boundary refused')
                if url.endswith('/token') and (set(json_data) != {'email', 'password', 'server'} or json_data['server'] != SERVER):
                    raise DisarmedError('Authentication boundary refused')
                if url.endswith('/refresh') and set(json_data) != {'refreshToken'}:
                    raise DisarmedError('Authentication boundary refused')
            elif request_type == 'get' and url in reads and json_data is None:
                if not include_access_token:
                    raise DisarmedError('Authenticated reads required')
                allowed_params = set()
                if url == base + '/trade/quotes':
                    allowed_params = {'tradableInstrumentId', 'routeId'}
                elif url == base + '/trade/history':
                    allowed_params = {'tradableInstrumentId', 'routeId', 'resolution', 'from', 'to'}
                elif url == base + f'/trade/instruments/{instrument}':
                    allowed_params = {'routeId', 'locale'}
                if set(additional_params or {}) != allowed_params:
                    raise DisarmedError('Unexpected read parameters')
                if url != base + '/auth/jwt/all-accounts':
                    if not include_acc_num or self.account_id != self._expected_account_id or self.acc_num != self._expected_acc_num:
                        raise DisarmedError('Account binding refused')
                if url in {base + '/trade/quotes', base + '/trade/history'}:
                    if not additional_params or additional_params.get('tradableInstrumentId') != instrument or str(additional_params.get('routeId')) != self._bound_info_route:
                        raise DisarmedError('Instrument binding refused')
                if url == base + f'/trade/instruments/{instrument}':
                    if not additional_params or str(additional_params.get('routeId')) != self._bound_info_route:
                        raise DisarmedError('Instrument binding refused')
                    # Pinned SDK 0.56.2 builds details with get_info_route_id.
                    # Retain its real method/decoder, but correct ONLY this wire
                    # route to the selected account's returned TRADE route.
                    additional_params = dict(additional_params, routeId=self._bound_trade_route)
            elif request_type in {'post', 'patch', 'delete'} and self._execution_guard is not None:
                if not include_acc_num or not include_access_token or additional_params:
                    raise DisarmedError('Execution request options refused')
                execution_guard = self._execution_guard
            else:
                raise DisarmedError('Only bounded authentication and approved GET reads are available')
            # Header construction can refresh a token (HTTP). Do that BEFORE
            # final permit consumption/deadline checking, never after it.
            headers = self._get_headers(include_access_token=include_access_token, include_acc_num=include_acc_num)
            params = self._get_params(additional_params)
            if self._execution_guard is not None:
                self._execution_guard.admit_http()
            if execution_guard is not None:
                execution_guard.consume(request_type, url, json_data)
            response = self._session.request(request_type.upper(), url,
                headers=headers, params=params, json=json_data,
                timeout=(5, 15), allow_redirects=False)
            # Authentication may return Created (documented and observed by the
            # standalone checker); keep all read endpoints restricted to 200.
            success_statuses = (200, 201) if request_type == 'post' else (200,)
            if response.status_code not in success_statuses:
                labels = {'/auth/jwt/token': 'AUTH', '/auth/jwt/refresh': 'REFRESH',
                          '/auth/jwt/all-accounts': 'ACCOUNT_LIST', '/trade/config': 'CONFIG',
                          '/trade/quotes': 'QUOTES', '/trade/history': 'HISTORY'}
                label = labels.get(url[len(base):])
                if label is None:
                    label = 'DETAILS' if '/trade/instruments/' in url else {
                        'instruments': 'INSTRUMENTS', 'state': 'STATE', 'positions': 'POSITIONS'
                    }.get(url.rsplit('/', 1)[-1], 'CONFIG')
                code = f'{label}_HTTP_STATUS_{response.status_code}' if type(response.status_code) is int else None
                raise ReadOnlyError('Read-only HTTP request failed', diagnostic_code=code)
            raw = decode_response(response)
            parsed = self._get_response_json(response)
            if request_type == 'get' and url == base + '/trade/config':
                if type(parsed) is not dict or parsed.get('s') != 'ok' or type(parsed.get('d')) is not dict:
                    raise ReadOnlyError('Configuration envelope refused')
            if request_type == 'post' and url == account + '/orders':
                from personal_trading.tradelocker.safety import canonical_id
                canonical_id(parsed['d']['orderId'])
            # SDK pandas astype(int64) silently truncates fractional IDs. Validate
            # the wire shape BEFORE allowing the real SDK table converters.
            def wire_id(value):
                if type(value) is int and 0 < value <= 9223372036854775807:
                    return value
                if type(value) is str and value.isascii() and value.isdecimal() and value[0:1] != '0':
                    numeric = int(value)
                    if numeric <= 9223372036854775807:
                        return numeric
                raise ReadOnlyError('Noncanonical wire identifier refused')
            if url == base + '/auth/jwt/all-accounts':
                for row in parsed['accounts']:
                    wire_id(row['id'])
                    wire_id(row['accNum'])
            if url == account + '/instruments':
                for row in parsed['d']['instruments']:
                    wire_id(row['tradableInstrumentId'])
                    for route in row['routes']:
                        wire_id(route['id'])
            if request_type == 'get' and self._execution_reads and url in {account + '/orders', account + '/ordersHistory', account + '/positions'}:
                # Strict raw decoding above retains numeric lexemes before the
                # SDK's float decoding and table conversion can lose evidence.
                # Only a successful, schema-validated ARRAY can prove no rows.
                # Empty mappings used to bypass every row check below.
                key = url.rsplit('/', 1)[-1]
                if (type(raw) is not dict or raw.get('s') != 'ok' or
                        type(raw.get('d')) is not dict or
                        type(raw['d'].get(key)) is not list):
                    raise ReadOnlyError('Execution table envelope refused')
                columns = self._get_column_names(key + 'Config')
                required = {'id', 'tradableInstrumentId', 'routeId', 'side', 'qty'}
                required |= ({'avgPrice', 'stopLossId', 'takeProfitId', 'unrealizedPl'}
                             if key == 'positions' else
                             {'type', 'status', 'filledQty', 'positionId', 'stopPrice',
                              'price', 'stopLoss', 'takeProfit'})
                if (type(columns) is not list or
                        any(type(c) is not str or not c for c in columns) or
                        len(columns) != len(set(columns)) or not required <= set(columns)):
                    raise ReadOnlyError('Execution table schema refused')
                # Validate raw identifiers before SDK astype(int64) can truncate.
                for row in raw['d'][key]:
                    if not isinstance(row, list) or len(row) != len(columns):
                        raise ReadOnlyError('Execution table shape refused')
                    record = dict(zip(columns, row))
                    # The pinned SDK fillna(0).astype(float) loses missingness,
                    # booleans and decimal precision. Refuse before conversion;
                    # post-SDK checks cannot reconstruct the original evidence.
                    from personal_trading.tradelocker.safety import wire_decimal
                    numeric_fields = (('qty', 'avgPrice', 'unrealizedPl') if key == 'positions'
                                      else ('qty', 'filledQty', 'stopPrice', 'price', 'stopLoss', 'takeProfit'))
                    for field in numeric_fields:
                        value = record[field]
                        if type(value) not in (int, float, str, Decimal):
                            raise ReadOnlyError('Invalid raw numeric observation')
                        exact = wire_decimal(value)
                        if wire_decimal(float(value)) != exact:
                            raise ReadOnlyError('Lossy SDK numeric observation refused')
                    for field in ('id', 'tradableInstrumentId', 'routeId'):
                        wire_id(record[field])
                    for field in ('positionId', 'stopLossId', 'takeProfitId'):
                        if field in record and not (type(record[field]) is int and record[field] == 0):
                            wire_id(record[field])
            return parsed

    return ReadOnlySDK


class Reader:
    def __init__(self, sdk, identity):
        self._sdk = sdk
        self.identity = identity
        self._closed = False

    @classmethod
    def discover(cls, username, password, selector):
        if not callable(selector):
            raise ReadOnlyError('Explicit account selector required')
        return cls._connect(username, password, 0, 0, selector)

    @classmethod
    def connect(cls, username, password, account_id, acc_num):
        if not _positive(account_id) or not _positive(acc_num):
            raise ReadOnlyError('Explicit positive accountId and accNum are required')
        return cls._connect(username, password, account_id, acc_num, None)

    @classmethod
    def _connect(cls, username, password, account_id, acc_num, selector):
        def construct():
            nonlocal account_id, acc_num
            sdk = _sdk_class()(username, password, account_id, acc_num, selector)
            try:
                account_id, acc_num = sdk.account_id, sdk.acc_num
                accounts = sdk.get_all_accounts()
                rows = accounts[(accounts['id'] == account_id) & (accounts['accNum'] == acc_num)]
                if len(rows) != 1 or len(accounts[accounts['id'] == account_id]) != 1:
                    raise ReadOnlyError('Ambiguous account binding')
                currency = str(rows.iloc[0]['currency'])
                if currency != 'USD':
                    raise ReadOnlyError('Only explicit USD account mapping is qualified')
                instruments = sdk.get_all_instruments()
                matches = instruments[instruments['name'] == SYMBOL]
                if len(matches) != 1:
                    raise ReadOnlyError('Exact unique symbol required')
                row = matches.iloc[0]
                instrument_id = int(row['tradableInstrumentId'])
                if instrument_id <= 0:
                    raise ReadOnlyError('Invalid instrument identity')
                routes = row['routes']
                ids = lambda kind: [str(r['id']) for r in routes if r['type'] == kind]
                info, trade = ids('INFO'), ids('TRADE')
                if len(info) != 1 or len(trade) != 1 or not info[0].isdigit() or not trade[0].isdigit() or info[0] == trade[0]:
                    raise ReadOnlyError('Unique explicit INFO and TRADE routes required')
                sdk._bound_instrument, sdk._bound_info_route = instrument_id, info[0]
                sdk._bound_trade_route = trade[0]
                identity = Identity(ENVIRONMENT, SERVER, account_id, acc_num, currency,
                                    SYMBOL, instrument_id, info[0], trade[0])
                return cls(sdk, identity)
            except BaseException:
                sdk._session.close()
                raise
        return quiet_call(construct)

    def _read(self, function):
        if self._closed:
            raise ReadOnlyError('Reader is closed')
        i = self.identity
        if (self._sdk.account_id, self._sdk.acc_num, self._sdk._bound_instrument, self._sdk._bound_info_route, self._sdk._bound_trade_route) != (i.account_id, i.acc_num, i.instrument_id, i.info_route_id, i.trade_route_id):
            raise ReadOnlyError('Binding changed')
        return quiet_call(function)

    def balances(self):
        return self._read(self._sdk.get_account_state)

    def positions(self):
        return self._read(lambda: self._sdk.get_all_positions().to_dict('records'))

    def instruments(self):
        return self._read(lambda: self._sdk.get_all_instruments().to_dict('records'))

    def details(self):
        def fetch():
            details = self._sdk.get_instrument_details(self.identity.instrument_id)
            if not isinstance(details, dict) or details.get('name') != self.identity.symbol:
                raise ReadOnlyError('Exact selected instrument details required')
            return details
        return self._read(fetch)

    def quotes(self):
        return self._read(lambda: self._sdk.get_quotes(self.identity.instrument_id))

    def bars(self, resolution, start_ms, end_ms):
        if resolution not in {'1m', '5m', '15m', '30m', '1H', '4H', '1D'} or not _positive(start_ms) or not _positive(end_ms) or start_ms >= end_ms:
            raise ReadOnlyError('Explicit valid history bounds/resolution required')
        return self._read(lambda: self._sdk.get_price_history(self.identity.instrument_id,
            resolution=resolution, start_timestamp=start_ms, end_timestamp=end_ms))

    def close(self):
        self._sdk._session.close()
        self._closed = True
        self._sdk._credentials = None
        self._sdk._access_token = ''
        self._sdk._refresh_token = ''
