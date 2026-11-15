"""Paper-only non-writing inspection. Execution is intentionally unavailable."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import logging
import os
import sys

from .credentials import RuntimeCredentials
from .settings import PAPER_ENDPOINT, Settings
from .state import bound_executor


@contextmanager
def _quiet_framework():
    # Process-global controls: use this one-shot operation in a dedicated process.
    # Do not trust third-party exception/log text to redact credentials for us.
    previous = {name: os.environ.get(name) for name in ("LUMIBOT_TELEMETRY", "LUMIBOT_LOG_LEVEL")}
    disabled = logging.root.manager.disable
    try:
        os.environ.update(LUMIBOT_TELEMETRY="0", LUMIBOT_LOG_LEVEL="ERROR")
        logging.disable(sys.maxsize)
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            yield
    finally:
        logging.disable(disabled)
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _inspection_session():
    # A transport constraint, not a brokerage/execution implementation.
    from requests import Session

    class AccountReadSession(Session):
        def request(self, method, url, **kwargs):
            if method.upper() != "GET" or url != PAPER_ENDPOINT + "/v2/account":
                raise RuntimeError("inspection transport permits only the paper account GET")
            kwargs["allow_redirects"] = False
            kwargs["timeout"] = (3.05, 10)
            response = super().request(method, url, **kwargs)
            if 300 <= response.status_code < 400:
                response.close()
                raise RuntimeError("paper account redirects are forbidden")
            if 200 <= response.status_code < 300:
                # Validate wire booleans before the SDK can coerce 0/strings to False.
                raw = response.json()
                if (type(raw) is not dict
                        or any(raw.get(flag) is not False for flag in
                               ("trading_blocked", "account_blocked", "trade_suspended_by_user"))):
                    response.close()
                    raise RuntimeError("paper account eligibility flags must be explicit JSON false")
            return response

    session = AccountReadSession()
    session.trust_env = False  # no proxy/netrc discovery or ambient authentication
    return session


def inspect_paper_account(settings, *, state_directory, acknowledgement, environment):
    """Explicit library-only connectivity operation; never starts or returns a strategy.

    Caller must separately obtain approval for authenticated read-only access.
    The CLI intentionally cannot call this. No account/credential values leave it.
    Use in a dedicated process: output/logging suppression is process-global.
    """
    settings = Settings.from_mapping(settings.as_mapping())
    settings.require_inspection_authorization(acknowledgement)
    with bound_executor(state_directory, settings.identity), _quiet_framework():
        try:
            return _construct(settings, environment)
        except Exception:
            # Leave the handler before raising: `from None` alone still retains
            # the credential-bearing original in __context__ for error reporters.
            pass
        raise RuntimeError("paper inspection failed; no execution enabled")


def _construct(settings, environment):
    from importlib.metadata import version
    import lumibot
    if lumibot.__version__ != "4.6.64" or version("alpaca-py") != "0.42.0":
        raise RuntimeError("framework/SDK combination has not been qualified")
    credentials = RuntimeCredentials.from_environ(environment)
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import AccountStatus
    from lumibot.brokers import Alpaca
    from lumibot.data_sources import AlpacaData
    from lumibot.traders import Trader

    client = TradingClient(credentials.key, credentials.secret, paper=True, url_override=PAPER_ENDPOINT)
    client._session.close()
    client._session = _inspection_session()
    try:
        account = client.get_account()
        if (str(account.id) != settings.as_mapping()["expected_account_id"]
                or account.status != AccountStatus.ACTIVE
                or any(getattr(account, flag, None) is not False for flag in
                       ("trading_blocked", "account_blocked", "trade_suspended_by_user"))):
            raise RuntimeError("paper account identity or eligibility check failed")
        config = {"API_KEY": credentials.key, "API_SECRET": credentials.secret, "PAPER": True}
        data = AlpacaData(config)
        broker = Alpaca(config, data_source=data, trading_client=client,
                        connect_stream=False, start_orders_thread=False)
        trader = Trader(backtest=False, debug=False, quiet_logs=True)
        if broker.is_paper is not True or broker._orders_thread is not None or trader._strategies:
            raise RuntimeError("framework construction invariants failed")
        return {"phase": "PAPER_ACCOUNT_VERIFIED", "framework_constructed": True,
                "orders_enabled": False, "execution": "DISARMED",
                "reconciled": False, "config_identity": settings.identity}
    finally:
        client._session.close()
