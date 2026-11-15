# Personal application source versioning

## Scope and present status

Actions remains disabled. This is source implementation awaiting independent
review and owner merge, **not** permission to enable Actions, execute a workflow,
create a tag/release, deploy, or trade. No workflow/settings/account changes were
made during implementation. The owner must separately authorize activation.

The personal application has its own version in `personal_release/VERSION`,
changelog in `personal_release/CHANGELOG.md`, manifest in
`.release-please-manifest.json`, and tags such as `personal-app-v0.1.0`.
Upstream LumiBot stays at v4.6.64: `setup.py`, `lumibot/__init__.py`, upstream
changelogs, `uv.lock` and unprefixed upstream tags are not release targets.
This does not create or publish a new LumiBot/PyPI package.

A personal source release is **not runtime qualification**. There is no qualified
VM, service, broker connection or paper-trading runtime implied by a source tag.
There is no deployment, no package publishing, no artifact upload, no orders,
no automatic merge and no infrastructure provisioning in these workflows.
GitHub's default source archives may accompany a GitHub release; they are not a
qualified build artifact. Existing source-bundle verification and all separate
owner deployment/trading gates still apply.

## Initial stable bootstrap (not an already published release)

The explicit **exclusive** initial boundary is merged personal foundation main
`a250d6b74fac9462da36636953295378d7156899`. `bootstrap-sha` stops initial
history collection **before** that commit is added: inherited upstream history
and the foundation commit itself are not first-release changelog entries. This
boundary is never a `last-release-sha` override; later releases use their own
release commits. Do not move it or seed an upstream version into the manifest.

`VERSION` starts at `0.1.0`, the changelog says **Unreleased**, and the initial
manifest is `{}`. These mean “bootstrap target, no personal release yet,” not
that 0.1.0 has shipped. Do not seed `{\".\": \"0.1.0\"}` before the first release
PR: Release Please would treat that as an existing version and bump again.
`initial-version: 0.1.0` selects the first stable, non-draft, non-prerelease
personal source release once a releasable commit exists after the boundary.

“Stable” here means SemVer without a prerelease suffix and GitHub's prerelease
flag off; 0.x remains initial development, not a production-readiness claim.
No alpha/beta runtime or release execution has been demonstrated. A prerelease
channel is not configured. Prerelease tags or a 1.0 compatibility guarantee need
a separately reviewed policy change. `initial-version` is only used without a
prior personal release, not an instruction to reset subsequent releases.

The root `.` package intentionally considers conventional commits across the
repository, while the `simple` strategy updates only the two personal metadata
files and the manifest. Personal tags include the `personal-app` component and
`-v` separator so upstream `v4.6.64` cannot become a personal release baseline.
After the bootstrap, an upstream-sync PR is still a personal change: review its
squash message and compatibility rather than auto-importing upstream releases.

## Conventional squash PR titles and normal release flow

Use squash merging so the final main commit message carries the reviewed PR
title. Review/edit the squash message on GitHub rather than assuming repository
merge-message defaults; branch commits alone do not establish a release type.
Recommended title for this implementation: `feat: add personal app source versioning`.
The first releasable commit after the boundary yields **0.1.0**, even if its
message is a feature or breaking change; subsequent changes follow this policy:

| Squash title/message | Example after 0.1.0 |
| --- | --- |
| `fix: correct personal validation` | patch: 0.1.1 |
| `feat: add personal reporting` | minor: 0.2.0 |
| `feat!: replace personal configuration contract` | minor while pre-1.0: 0.2.0 |
| `docs: explain an operator check`, `chore: tidy comments` | no release alone |

`BREAKING CHANGE:` in the commit body also signals a breaking change. At/after
1.0.0 breaking changes bump major; features bump minor and fixes bump patch.
The largest releasable change since the preceding personal release wins.
`Release-As` commit footers and release-PR overrides can force versions; they
are not the normal path and require deliberate owner review. Do not use them
as an automatic bootstrap or to silently introduce prereleases.

After activation is separately approved:

1. A human-approved conventional squash PR reaches `main` in the personal fork.
2. A push to `main` runs Release Please using the committed configuration through
   the GitHub API (no checkout or repository shell execution in the write job).
   It opens/updates a release PR with only `personal_release/VERSION`,
   `personal_release/CHANGELOG.md`, and `.release-please-manifest.json` updates.
3. The owner reviews the version, changelog, exact diff and applicable checks.
   The bot does not approve or merge the release PR. Preserve its generated
   title/body and `autorelease: pending` state; don't remove its state labels.
   A manifest version in an open release PR is a release candidate, not proof
   a tag or GitHub release exists.
4. The owner merges the release PR. The resulting owner-origin main push lets
   Release Please create the prefixed tag and GitHub release for that commit.
   It does not deploy anything or change the upstream package version.
5. The owner independently verifies the tag target, GitHub release and metadata.
   Never infer success from a merged release PR or a green static test alone.

The release workflow has no paths filter: every main push must be considered,
including the generated release PR merge. It has no tag, PR, manual, scheduled,
`pull_request_target` or `workflow_run` trigger. The write job is restricted to
`mfaddis01/lumibot`, the `push` event and `refs/heads/main`. Runs are serialized
without canceling an in-flight release operation.

## Token, workflow permissions and event recursion

Use only the built-in `GITHUB_TOKEN` (`github.token`); **no PAT** is provisioned,
requested or required by this implementation. The release workflow starts with
`permissions: {}` and grants the one API-only job exactly the three write
permissions documented by the pinned action: `contents` for branch/tag/release
writes, `pull-requests` for release PRs, and `issues` for issue/PR label state.
All unspecified permissions are disabled. There is no `id-token`, Actions-write,
checks-write, packages-write or deployment permission. These are repository-wide
GitHub capabilities, not a technical guarantee that a token can only write the
personal paths; the pinned source, no checkout/shell, tests and review bound the
intended behavior. PR write access could approve PRs in principle, but this
workflow has no approval or merge step.

The owner must separately review Actions policy and the repository setting
**Allow GitHub Actions to create and approve pull requests** before activation.
YAML cannot grant access when repository/organization policy disallows it. Do
not broaden global/default workflow permissions just to make this workflow work;
retain explicit per-job grants. Do not revive inherited publishing workflows.

The pinned action README's blanket warning about bot PR checks is older than
current GitHub documentation. Current GitHub docs say GITHUB_TOKEN-created PR
`opened`, `synchronize` and `reopened` events produce **approval-required** runs;
a user with write access must choose **Approve workflows to run**. Other PR
activity types do not create runs. `workflow_dispatch` and `repository_dispatch`
are recursion exceptions too, but neither is configured here. Token-generated
push/tag/release events do not automatically cascade into other workflows.
Do not assume release-created workflows, automatic PR CI, or deployment will
run. Verify the actual check state; if checks are absent/pending under current
policy, stop and have the owner approve supported runs or perform/review offline
checks against the exact release PR source. Do not bypass required checks,
switch to `pull_request_target`, or provision a PAT to avoid this limitation.
A real owner merge is distinct from a bot-token push and is the intended release
processing trigger. Hosted behavior has not been exercised here.

## Offline guards and activation gates

`personal-checks.yml` is unchanged. The additional read-only
`personal-release-checks.yml` covers root release config/manifest and
`personal_release/**`-only PRs and pushes, which the existing path filters do not
cover. Its two triggers also cover workflow, versioning-doc and CI-test changes.
It uses the existing reviewed checkout/setup-python immutable pins, disables
checkout credential persistence, and runs only stdlib policy/version tests.
No write permission or secret expression exists in that checks workflow.

The workflow allowlist now contains exactly `personal-checks.yml`,
`personal-release-checks.yml` and `personal-release.yml`. All other `.yml`/`.yaml`
workflows, including retired upstream publishing automation, remain forbidden.
New workflows must not be added without updating the reviewed guards. Release
workflow tests are exact text allowlists (not complete YAML/schema validation):
extra triggers, permissions, aliases, jobs, action sources or shell steps fail.
Metadata tests accept both bootstrap and generated release candidates rather
than permanently freezing the initial manifest/version.

Run offline, without installing LumiBot or using credentials:

```sh
python -m unittest discover -s personal_ci_tests -p 'test_*policy.py'
python -m unittest discover -s personal_ci_tests -p test_versioning.py
python -m unittest discover -s personal_ci_tests
python -m unittest discover -s personal_tests
```

Before activation the owner must obtain independent review, merge the reviewed
implementation, approve the exact Actions/PR-permission policy and authorize
activation. Enabling Actions does not replay old pushes; a subsequent authorized
main push is needed. No manual dispatch is added to bypass that gate. Initial
runtime deployment and paper-trading qualification remain separate approvals.

## Primary-source qualification and pins

Qualified from public official sources on 2026-11-14. The latest official action
release returned by GitHub was v5.0.0, whose tag resolved to full commit
`45996ed1f6d02564a971a2fa1b5860e934307cf7`; the workflow uses
`googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7`.
The commit pin is immutable; the release API reported the release itself as
`immutable: false`, so this does not claim GitHub's immutable-release feature.
The action declares Node 24 and ships its own `dist/index.js`. Its lockfile pins
Release Please **17.6.0**. The latest standalone core release was 17.11.2, but
that is not the engine bundled by the selected action. Configuration/schema
and behavior were qualified against the bundled 17.6.0 source commit
`712fcf01effd08d7b0e7b1fd3861f2cb388bc8d1`, not guessed from newer docs.
Node 24 action execution on GitHub has not been tested by these offline guards.

The committed guards use no release-tool dependency. In separate, untracked review
tooling, the bundled 17.6.0 engine and the pinned action's production dependency
lock were exercised under Node 24 with synthetic SCM records and network access
forbidden. Scenarios covered the exclusive boundary, ignored upstream tags,
initial 0.1.0, patch/feature/breaking bumps, docs-only no-release behavior, the
three-file update scope, and merged-PR prefixed release candidates. This is local
engine evidence, not execution of the GitHub action or a real tag/release.

Official sources:

- Action latest release: https://api.github.com/repos/googleapis/release-please-action/releases/latest
- Action tag resolution: https://api.github.com/repos/googleapis/release-please-action/git/ref/tags/v5.0.0
- Pinned action README/permissions: https://github.com/googleapis/release-please-action/blob/45996ed1f6d02564a971a2fa1b5860e934307cf7/README.md
- Pinned input contract/Node runtime: https://github.com/googleapis/release-please-action/blob/45996ed1f6d02564a971a2fa1b5860e934307cf7/action.yml
- Pinned bundled engine: https://github.com/googleapis/release-please-action/blob/45996ed1f6d02564a971a2fa1b5860e934307cf7/package-lock.json
- Core latest release: https://api.github.com/repos/googleapis/release-please/releases/latest
- Manifest/config/bootstrap semantics: https://github.com/googleapis/release-please/blob/712fcf01effd08d7b0e7b1fd3861f2cb388bc8d1/docs/manifest-releaser.md
- Exact schema: https://github.com/googleapis/release-please/blob/712fcf01effd08d7b0e7b1fd3861f2cb388bc8d1/schemas/config.json
- Simple version/changelog updater: https://github.com/googleapis/release-please/blob/712fcf01effd08d7b0e7b1fd3861f2cb388bc8d1/src/strategies/simple.ts
- Exclusive bootstrap/history/tag filtering: https://github.com/googleapis/release-please/blob/712fcf01effd08d7b0e7b1fd3861f2cb388bc8d1/src/manifest.ts
- Initial version and release PR generation: https://github.com/googleapis/release-please/blob/712fcf01effd08d7b0e7b1fd3861f2cb388bc8d1/src/strategies/base.ts
- Pre-1.0 bump policy: https://github.com/googleapis/release-please/blob/712fcf01effd08d7b0e7b1fd3861f2cb388bc8d1/src/versioning-strategies/default.ts
- Current token recursion behavior: https://docs.github.com/en/actions/concepts/security/github_token
- PR creation permission setting: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository
