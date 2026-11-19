# TradeLocker demo integration — development tracking

This feature branch tracks development, not an operational release. It does not authorize or enable trading. The stable application branch and deployment remain separate; the owner merges reviewed PRs.

## Scope

- TradeLocker Public API through its official Python client and a LumiBot adapter.
- Demo-only, exact configured XAUUSD.R instrument, explicit account binding.
- One experimental strategy and a bounded monitored demo trial; no profitability claim.

## Six deliverables

1. Trading adapter and full runtime qualification — in development.
2. Contract sizing, economic valuation, session and permission semantics — research in progress.
3. Supervised demo execution test — blocked on implementation, qualified inputs and exact test approval.
4. Gold-strategy research, explicit rules and implementation — research in progress; no strategy selected.
5. Fail-closed safeguards, private journal and restart reconciliation — in development.
6. Signal-only evaluation and bounded demo trial — planning; execution blocked on earlier deliverables and agreed limits.

Independent research, execution/safety, runtime and operations preparation run in parallel. Strategy implementation follows a usable research specification; real orders follow working safeguards and explicit test/trial scope. A successful read-only check does not establish executing or deployed operation.

## Documentation and evidence rules

- development/: plans, research, proposals, task state and engineering handoffs.
- docs/: verified working operating behavior, with deployment status stated accurately.
- Local-only records hold machine-specific worker handles, private account observations and test receipts. They are not public repository fixtures.
- No credentials, personal account data, owner email, installed environments, cache or local host paths belong in this PR.
- Source changes receive scoped tests/review before promotion. Merge, release, deployment and strategy activation are distinct events.

## Current PR contents

Development tracking only. No adapter code or executable trading capability has been added to this branch yet. Subsequent reviewed changes will be added as their actual evidence becomes available. GitHub Actions is disabled; this is not a green-CI claim. Repository Issues is disabled, so this draft PR checklist supplies remote tracking without changing settings.
