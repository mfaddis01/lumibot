"""Secret-free configuration declarations, not an order-risk enforcement engine."""
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

PAPER_ENDPOINT = "https://paper-api.alpaca.markets"
_FIELDS = {"paper", "endpoint", "armed", "expected_account_id", "policy_sha256", "risk"}
_RISK_FIELDS = {"symbols", "asset_types", "max_order_notional", "max_gross_exposure",
                "max_daily_loss", "max_open_orders", "max_option_contracts"}


def _validate(value):
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError("configuration requires exactly the documented fields")
    if value["paper"] is not True:
        raise ValueError("paper must be explicitly true")
    if value["endpoint"] != PAPER_ENDPOINT:
        raise ValueError("endpoint must match the allowed paper origin")
    if type(value["armed"]) is not bool:
        raise ValueError("armed must be explicitly boolean")
    try:
        account = value["expected_account_id"]
        if type(account) is not str or str(UUID(account)) != account:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("expected_account_id must be a canonical UUID") from None
    policy = value["policy_sha256"]
    if type(policy) is not str or not re.fullmatch(r"[0-9a-f]{64}", policy):
        raise ValueError("policy_sha256 must identify the reviewed policy")
    risk = value["risk"]
    if type(risk) is not dict or set(risk) != _RISK_FIELDS:
        raise ValueError("risk requires exactly the documented explicit limits and scope")
    symbols, assets = risk["symbols"], risk["asset_types"]
    if (type(symbols) is not list or not symbols
            or any(type(s) is not str or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", s) for s in symbols)
            or len(set(symbols)) != len(symbols)):
        raise ValueError("risk symbols must be unique explicit uppercase underlying symbols")
    if (type(assets) is not list or not assets
            or any(type(a) is not str or a not in {"stock", "option"} for a in assets)
            or len(set(assets)) != len(assets)):
        raise ValueError("risk asset_types must explicitly select stock and/or option")
    money = {}
    for name in ("max_order_notional", "max_gross_exposure", "max_daily_loss"):
        raw = risk[name]
        try:
            if type(raw) is not str:
                raise ValueError
            amount = Decimal(raw)
            if not amount.is_finite() or amount <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            raise ValueError("risk monetary limits must be positive finite decimal strings") from None
        money[name] = amount
    if (money["max_order_notional"] > money["max_gross_exposure"]
            or money["max_daily_loss"] > money["max_gross_exposure"]):
        raise ValueError("risk order and loss limits cannot exceed gross exposure")
    if type(risk["max_open_orders"]) is not int or risk["max_open_orders"] <= 0:
        raise ValueError("risk max_open_orders must be an explicit positive integer")
    contracts = risk["max_option_contracts"]
    if type(contracts) is not int or contracts < 0 or ("option" in assets and contracts == 0):
        raise ValueError("risk option scope requires a positive explicit contract limit")
    if "option" not in assets and contracts != 0:
        raise ValueError("risk contract limit must be zero when options are excluded")


@dataclass(frozen=True, repr=False, init=False)
class Settings:
    _document: str

    def __init__(self, value):
        _validate(value)
        object.__setattr__(self, "_document", json.dumps(value, sort_keys=True, separators=(",", ":")))

    @classmethod
    def from_mapping(cls, value):
        return cls(value)

    @classmethod
    def load(cls, path):
        def unique_fields(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ValueError("duplicate configuration field")
                result[key] = item
            return result
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read(65537)
            if len(text) > 65536:
                raise ValueError("configuration too large")
            return cls.from_mapping(json.loads(text, object_pairs_hook=unique_fields))
        except (OSError, ValueError, UnicodeError, RecursionError):
            # `from None` hides display, but retains the raw document in __context__.
            pass
        raise ValueError("configuration invalid or unreadable")

    def as_mapping(self):
        return json.loads(self._document)

    @property
    def identity(self):
        import hashlib
        return hashlib.sha256(self._document.encode("utf-8")).hexdigest()

    def require_inspection_authorization(self, acknowledgement):
        if not self.as_mapping()["armed"]:
            raise ValueError("disarmed: no broker construction or connectivity permitted")
        if acknowledgement != self.identity:
            raise ValueError("explicit acknowledgement must match the complete configuration identity")
