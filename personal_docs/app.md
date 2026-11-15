# Personal paper foundation

This overlay reuses the selected LumiBot source; it is **not an execution engine**.
It currently validates configuration offline and offers a separately authorized,
read-only construction/connection probe. **No strategy, budget, autonomous orders,
position management, or broker reconciliation is implemented.**

No target is deployed. No Oracle instance exists yet; target OS, architecture,
resources, runtime, account state and entitlements remain unknown. Fresh provisioning
requires a separately reviewed exact infrastructure plan. Local checks do not certify
an eventual target.

## Offline interface

Run from the release/source root containing `personal_trading`. Upstream `setup.py`
packages only `lumibot` and `lumibot.*`; its wheel alone does **not** include this
overlay. The separate artifact builder must carry `personal_trading` at the release
root/on the module path without modifying upstream core packaging:

```sh
python -m personal_trading validate --config /path/to/operator-settings.json
python -m personal_trading readiness --config /path/to/operator-settings.json
python -m personal_trading run --config /path/to/operator-settings.json
```

`validate` and `readiness` return 0 only for valid **local configuration**, report
`OFFLINE_VALIDATED`, `broker_checked: false`, `execution: DISARMED` and
`orders_enabled: false`. They need only Python's standard library. They do not
import LumiBot/Alpaca, read runtime credentials, load dotenv, contact a provider,
create state, or establish service/market readiness.

`run` deliberately returns **3**, even for an armed configuration. There is no
CLI switch that starts trading or connects to a broker. Invalid arguments or an
invalid/unreadable configuration return **2**, without echoing input values/paths.
Help is available with `--help`.

## Operator settings — no trading defaults

The JSON object must have exactly these keys; missing and unknown keys are rejected:

| Field | Required meaning |
| --- | --- |
| `paper` | JSON `true`, never a string or numeric substitute |
| `endpoint` | Exactly `https://paper-api.alpaca.markets` (origin only) |
| `armed` | Explicit JSON boolean; normally `false` |
| `expected_account_id` | Canonical lowercase account UUID, supplied by the owner |
| `policy_sha256` | Lowercase 64-hex digest of the proposed reviewed policy artifact |
| `risk` | Object with all fields below |

The policy hash is a **declaration/reference**, not proof of owner approval or
an implemented policy. No policy artifact is loaded/executed by this foundation.

| `risk` field | Validation, not runtime trading enforcement |
| --- | --- |
| `symbols` | Nonempty unique uppercase underlying-symbol list; no automatic universe |
| `asset_types` | Nonempty unique selection of `stock` and/or `option` |
| `max_order_notional` | Positive finite decimal **string**, in USD |
| `max_gross_exposure` | Positive finite decimal string, in USD |
| `max_daily_loss` | Positive finite decimal string, in USD |
| `max_open_orders` | Explicit positive integer, not boolean/string |
| `max_option_contracts` | Positive integer for option scope; exactly zero otherwise |

Order notional and daily loss declarations cannot exceed gross exposure. Symbols
are underlyings, not an options-contract whitelist. Options expiry, exercise,
assignment, spreads, entitlement, stale-data and actual proposal checks are NOT
implemented. Neither stock nor option trading is enabled by declaring scope.

Monetary limits require owner-selected semantics and implementation before execution
can exist; these fields alone do not enforce loss/exposure limits. There is no sample
production budget. Numerical values in `personal_tests` are explicitly synthetic
fixtures, not recommendations or approved limits. Configuration is limited to 65,536 decoded characters;
duplicate keys and invalid JSON fail closed. Do not put credentials in this file.
`Settings.load` raises a fixed public error outside the original exception handler,
so malformed JSON is not retained through an exception context/cause. This does not
make traceback-local-variable dumps safe; never enable them for configuration input.

`Settings.from_mapping(mapping)` and `Settings.load(path)` validate the same schema.
Settings are immutable snapshots. `identity` is SHA-256 of canonical JSON: object key
order is ignored, but values, array order, decimal spelling and the arming flag count.
`as_mapping()` returns a fresh copy. Repr and status omit the full configuration,
account identity and credentials; status includes only the config digest and safe flags.
This is a configuration identity, not a release/source attestation.

## Explicit read-only integration boundary

`personal_trading.integration.inspect_paper_account(settings, *, state_directory,
acknowledgement, environment)` is a library-only one-shot operation. **Do not call it
against a real account without separate authorization and privately provisioned paper
credentials.** Local tests replace only HTTP responses with labeled synthetic account
fixtures and deny actual sockets. They are not evidence of account connectivity.

The caller must provide:

- A completely validated configuration with `armed: true`.
- `acknowledgement` exactly equal to that configuration's `identity`.
- One stable, absolute, private, service-owned state directory outside the application
  release, on a local Linux filesystem, with trusted ancestors.
- An explicitly supplied environment mapping containing runtime-only `ALPACA_API_KEY`
  and `ALPACA_API_SECRET`. The CLI never reads these. No other credential names,
  OAuth fallback, profile discovery, dotenv or key-file lookup is used.

The operation validates arming/acknowledgement **before** state or credential access,
then acquires local ownership and checks durable config identity. It requires the
locally qualified versions **LumiBot 4.6.64 and alpaca-py 0.42.0**, failing closed on
other version strings. Version strings do not attest source contents; release digests
and the pinned commit must be verified by the separate artifact/deployment process.

It creates the real SDK `TradingClient(..., paper=True, url_override=allowed_origin)`.
Its constrained Requests session permits only `GET` of the exact paper `/v2/account`
URL, with redirects forbidden and connection/read timeouts. Proxy/netrc discovery is
disabled (`trust_env=False`). The SDK still owns authentication, response models and
its read-retry handling. This transport restriction is not order execution logic.

The returned real account UUID must exactly match the declaration, status must be
`ACTIVE`, and `trading_blocked`, `account_blocked`, and `trade_suspended_by_user`
must all be present as exact JSON `false` in the raw HTTP response, before SDK model
coercion. Numbers, strings, null and missing flags fail closed; the modeled account
is checked again afterward. Only then are real
`AlpacaData`, `Alpaca(..., trading_client=client, connect_stream=False,
start_orders_thread=False)` and `Trader(backtest=False, debug=False, quiet_logs=True)`
constructed. No strategy is supplied or added; `Trader.run_all` is never called.
The broker/trader are **not returned**, and the SDK session is closed.

A successful result says `PAPER_ACCOUNT_VERIFIED`, `framework_constructed: true`,
`execution: DISARMED`, `orders_enabled: false`, and `reconciled: false`. It checks no
positions, orders, balances, quotes or entitlements. No cancellation, liquidation,
monitoring or broker-state writes occur. This is not permission to activate trading.

Use this probe in a **dedicated process**, not embedded alongside other workloads:
construction temporarily disables process-wide logging, redirects stdout/stderr to
a null sink and forces framework telemetry off. It restores those controls afterward.
Third-party error text is intentionally discarded in favor of a fixed error with no
chained diagnostic text. Never enable traceback-local-variable dumps, HTTP debug
capture, external exception/credential inspection, or secret-bearing telemetry.

## Local ownership and durable identity

`exclusive_executor(directory)` uses Linux `flock(LOCK_EX | LOCK_NB)` on a persistent
`executor.lock` inode. It refuses unsafe final directory/file ownership/permissions,
symlink lock files and hardlinked lock files. Kernel ownership releases on close or
process death; the file intentionally remains. Every cooperating caller must use the
**same directory**. Do not rename/delete a lock directory/file while processes can run.
This is not a distributed lock, a scheduler, or protection from a process that ignores
it, another host, an untrusted ancestor, or a malicious same-UID operator.

`bound_executor(directory, config_identity)` also binds `identity.json` to schema 1
and the config digest. Initial creation uses a private temporary file, fsync, atomic
replace and directory fsync. Subsequent invocations require exact canonical metadata.
Records are opened nonblocking and checked with `fstat` for a private, service-owned,
single-link regular file before reading; a FIFO is refused without waiting for a
writer, and failed identity checks release executor ownership.
Changes to account, scope, risk, policy or arming cannot silently rebind that directory.
Missing-on-first-use metadata records **declared configuration only**, before the
account read; it is not proof that any broker check passed. Corruption/mismatch stops
inspection and requires operator review. There is no automatic reset/migration path.

These two small files are justified by preventing overlapping invocations and accidental
reuse of an operator's stable state location across incompatible declarations. They are
**not** an order journal, idempotency store, fill ledger, restart recovery algorithm,
reconciliation mechanism or P&L ledger. Do not replay account state from them. A code
rollback must inspect current broker state in a separately implemented/approved path;
it cannot undo trades. Stop-new-risk is not cancel-all or abandonment of monitoring;
none of those operations are currently implemented here.

## Pinned source findings and local qualification

Inspected source: selected tag `v4.6.64`, commit
`951732e501bccfbffc3718c9e6bd27e51f07fef0`.

- `lumibot/brokers/alpaca.py`: actual config is `API_KEY`, `API_SECRET`, `PAPER`;
  the adapter's own SDK constructor uses `paper=self.is_paper`. The application
  maps runtime variable names explicitly; it never passes an unvalidated config through.
- `AlpacaData` explicitly warns that `ENDPOINT` is ignored. Merely putting a paper URL
  in that framework config is not a safeguard. The injected real SDK client plus the
  fixed transport enforce the selected origin in this probe.
- `Alpaca` accepts `trading_client`, `data_source`, `connect_stream` and
  `start_orders_thread`; injected clients use its polling lifecycle. Both worker flags
  must explicitly be false. Broker construction otherwise can start telemetry even
  with those flags false; the overlay explicitly disables it for inspection.
- `Trader()` has no constructor `run` side effect. The actual execution method is
  `run_all(...)`, not the old example's `run()`. No strategy is constructed because
  strategy construction/hooks would expand the startup side-effect surface.
- SDK 0.42.0 supports `url_override`; the integration uses its private `_session` only
  for the constrained read transport/cleanup. This coupling is intentional, version
  gated and must be requalified before any SDK upgrade.

Run `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s personal_tests -v`.
All new pure logic tests use unittest/stdlib. Real-framework fixture tests explicitly
skip when alpaca-py is absent; an offline-only green result is not integration proof.
For integration qualification use an isolated environment with `alpaca-py==0.42.0`,
`pytz==2025.2`, `python-dateutil==2.9.0.post0`, `termcolor==3.1.0`,
`numpy>=1.20.0,<2.5.0` (matching the pinned framework's constraint), and their resolved
transitive dependencies. This minimal probe environment is **not** a complete LumiBot
production installation or a release lockfile. The upstream setup has much broader
requirements. Deployment must separately lock/build/verify them for the actual target.

Actual local real-class/HTTP-fixture qualification used Python 3.12; dependency-free
configuration/CLI checks also ran on Python 3.14. No target architecture or full
framework suite is certified. No real credentials were loaded, no authenticated
network/broker call occurred, and no orders were submitted.
