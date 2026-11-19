# TradeLocker demo integration — development status

This feature is not an operational release. The owner merges reviewed changes; merge, deployment and trading activation remain separate.

## Implemented and exercised offline

- Official-SDK-backed, exact-account read adapter for demo, GATESFX and XAUUSD.R.
- Explicit local SDK packaging adjustments with unchanged upstream code, hash-gated inputs and installed-runtime dependency checks.
- Separate 1-minute and 5-minute signal-only replay experiments. Repeated eligible same-day opportunities; no forced orders, stock calendar or fixed daytime window.
- Synthetic supervised execution foundation, private journal/single-writer controls and bounded reconciliation.
- Separate owner-terminal empirical routing probe. It can send one approved real demo open and one exact linked close after separate digest-bound acknowledgements; it is not a strategy runner.

The combined offline suite passed 141 tests with zero failures/skips. Three code-review scopes and two security-review scopes cover the candidate and repairs. This is not CI, authenticated execution, workspace-wide verification or profitability evidence.

## Remaining boundaries

No real probe order has been submitted. Credentials/session are intentionally not retained. The probe requires a foreground owner terminal, account selection, explicit API-native quantity and two acknowledgements. It discloses an unprotected interval and no dollar-loss cap; failures may require manual reconciliation. It must not become an autonomous executor.

Actual economics, costs, calendar/permission ambiguities, protection and freshness remain unqualified. Use verified overnight gold sessions, not stock hours; uninterrupted 24x7 is not assumed. Replay is not connected to a production strategy lifecycle. No backtest, forward results, deployment or funded/live use is established.

## Organization

Only explicitly selected source/tests are included. Private receipts, account observations, environments and wheels are excluded. Development plans are separate from verified operating documentation. Actions and Issues remain disabled; this draft PR is the remote checklist. No settings or infrastructure changes are included.
