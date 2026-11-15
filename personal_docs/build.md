# Personal source-build foundation

This is a **source bundle**, not a wheel, an installed runtime, a deployment, or permission to trade. Repository Actions remain on owner hold. This feature branch removes all seven inherited workflows described in the audit below and tests that only `personal-checks.yml` remains. The cleanup must be reviewed and merged into the default branch before Actions are enabled; this change does not enable them.

## Checks

From the fork checkout, using Python stdlib and Git only:

```sh
python -m unittest discover -s personal_tests
python -m unittest discover -s personal_ci_tests -v
```

The basic stdlib path does not install dependencies or require broker keys. When optional pinned SDK/framework dependencies are installed, the application suite also exercises real constructors against synthetic account fixtures with sockets blocked; that is not a real account check. The CI suite builds isolated synthetic Git repositories under the configured temporary directory; its commits/remotes are fixture-only. It does not modify the real checkout's Git index, branches, or remotes. A fixture `setup.py` raises if executed.

`.github/workflows/personal-checks.yml` runs these commands and makes a committed source bundle on path-scoped main PRs and pushes to main/the foundation branch. Both event filters include **all `.github/workflows/**` changes**, so even a new non-personal workflow-only change schedules the workflow inventory guard when the branch filter matches and Actions are enabled. The guard still permits only `personal-checks.yml`; broad trigger coverage is not permission to add or enable workflows. It has only `contents: read`, no stored secrets, no persisted checkout credentials, no dependency cache, no deployment/publishing steps, no artifact upload, no private-repository access, no schedule, and no promotion or trading activation. Its temporary bundle disappears with the runner. Branch/path filtering means a skipped check must not be configured as an unconditional required check for unrelated changes without reviewing GitHub's skipped-check behavior.

The runner is Ubuntu 24.04 with a Python 3.12 interpreter selected by setup-python. That is a test environment, **not the Oracle target**. Action/interpreter downloads need network access; test/build logic itself is offline. The Python patch and runner image are not hermetically pinned.

### Verified official action pins

Resolved read-only from the primary repositories with `git ls-remote`, including the versioned tags, and checked their `action.yml` at the resolved SHA:

| Official repository | Versioned tag | Immutable commit |
| --- | --- | --- |
| actions/checkout | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| actions/setup-python | v6.3.0 | `ece7cb06caefa5fff74198d8649806c4678c61a1` |

Both action definitions specify Node 24. These pins identify the reviewed upstream action source; this is not an audit of every bundled action dependency. Re-resolve/review changes deliberately rather than replacing pins with moving tags. Primary evidence can be reproduced with `git ls-remote https://github.com/actions/checkout.git refs/tags/v7.0.1` and the corresponding setup-python v6.3.0 query.

## Building and verifying

Only after the owner commits the overlay, select that **full lowercase 40-character commit**. The builder checks the selected fork's origin and that the object is a commit; it never fetches, commits, stages, publishes, runs setup.py, or installs dependencies.

```sh
COMMIT="$(git rev-parse HEAD)"
python personal_deploy/source_bundle.py build \
  --source . --commit "$COMMIT" --output "$TMPDIR/personal-source.tar.gz"
```

The JSON result reports the artifact basename, SHA-256 of the full compressed archive, full commit, fixed repository, source mode, file count, and payload digest. Save the artifact digest in a trusted external handoff; a digest supplied beside an untrusted artifact is not authentication. Verify without extracting or executing archive members:

```sh
python personal_deploy/source_bundle.py verify \
  --artifact "$TMPDIR/personal-source.tar.gz" \
  --sha256 '<trusted-full-artifact-sha256>' --commit "$COMMIT"
```

The source bundle contains actual framework Python source from this fork's selected Git commit, the personal Python overlay, and the builder source. Committed mode reads Git blobs, ignoring uncommitted changes. It requires framework/overlay entry files, setup.py, license, README, and builder; it fails rather than silently shipping only upstream when the overlay is absent at that commit. The builder does not install the PyPI lumibot distribution as a substitute.

A local implementation smoke test before commit may use `--preview`. It snapshots allowlisted workspace files and labels them `working-tree-preview`; `source_commit` then means the **base/reference commit**, not a claim those bytes are committed. Preview verification requires `--allow-preview`; CI never enables either flag. A preview must never be promoted or relabeled as a committed build. Workspaces must not be edited concurrently when making a release input; previews are not atomic repository snapshots.

### Allowlist, hashes, and exclusions

The explicit policy in `source_bundle.py` permits:

- Python files under `lumibot/`, `personal_trading/`, and `personal_deploy/`;
- exact framework resource `lumibot/resources/conf.yaml`, framework agent skill Markdown/YAML, and example fixture JSON;
- exact root files `LICENSE`, `README.md`, `setup.py`, `setup.cfg`, `pyproject.toml`, `MANIFEST.in`, `requirements.txt`, `requirements_agent_evals.txt`, and `uv.lock`, when present.

Hidden paths, `.git`, `.env`, `.secrets`, runtime/state/data/cache/log/report/artifact/build directories, nonallowlisted files, and links are excluded or rejected. Preview traversal does not descend into hidden/runtime directories. The optional ThetaTerminal JAR is intentionally excluded, as are arbitrary native binaries, local configuration JSON, test trees, workflow files, and runtime data. This bundle is not a general-purpose upstream source distribution and is not qualified for ThetaData. Review/extend the allowlist with regression tests when a chosen runtime needs another resource.

`SOURCE-MANIFEST.json` contains canonical per-file names/sizes/SHA-256s; `payload_sha256` hashes the sorted canonical JSON record list, excluding the manifest to avoid self-reference. The archive's external hash also covers the manifest. Sorted members, zero timestamps/owners, fixed file modes, and a filename-free zero-time gzip header produce deterministic bytes for the same inputs/toolchain. Cross-Python/zlib compressed-byte identity is not promised. No source checkout path, host name, credential values, or wall-clock timestamp is generated into the manifest. The hash of the actually executing builder is recorded separately from the bundled builder's file digest.

The verifier checks external digest, fixed fork/full commit, safe unique regular member names, per-file records, payload hash, supported manifest claims, and explicit preview status. It does not extract files. The recorded `reviewed_upstream_base` is the selected design reference (`Lumiwealth/lumibot`, v4.6.64, `951732e501bccfbffc3718c9e6bd27e51f07fef0`), not a remote attestation or an ancestry/compatibility proof. Local origin configuration is an identity sanity check, not cryptographic proof that GitHub served a commit. Owner review must bind the actual fork commit to the approved source. A file allowlist is not a content secret scanner: secrets embedded in an otherwise allowed source file require public-source review and must never be committed.

### Strict source verification policy

The supported manifest has exactly these mandatory fields: `schema_version`, `artifact_kind`, `dependency_resolution`, `target`, `reviewed_upstream_base`, `builder_sha256`, `repository`, `source_commit`, `source_mode`, `release_eligible`, `runtime_installed`, `target_compatibility`, `payload_sha256`, and `files`. Unknown or missing fields and duplicate JSON keys at any object depth are rejected. Version must be the exact JSON integer `1`, not `true` or `1.0`. The builder digest must be a lowercase 64-character SHA-256; it records the executing builder independently of the bundled source and is not an attestation by itself.

The reviewed upstream metadata must exactly match the documented repository/tag/commit reference. The target object must have exactly `os`, `architecture`, and `python_abi`, all JSON null. Runtime/release flags must be the exact JSON boolean false; compatibility remains `unverified` and dependency resolution `not-performed`. File records must exactly match the sorted payload, with no extra record fields and exact nonnegative integer sizes (not booleans or floats). Neither strict parsing nor a matching digest grants runtime eligibility. Dot and empty path components are refused before path indexing, including the standalone `.` member; verification never extracts or executes payload files.

The verifier also applies fixed source-input resource bounds:

| Input | Maximum |
| --- | --- |
| Compressed archive | 32 MiB |
| Entire expanded gzip stream | 128 MiB |
| Each logical tar member, including the manifest | 16 MiB |
| Logical tar members, including the manifest | 4,096 |

Compressed and expanded reads request at most their bound plus one sentinel byte. Expansion is bounded **before tar parsing**, including tar headers/padding, trailing data, and concatenated gzip streams; members are size/count-checked before reading their declared content. Small deterministic fixtures lower the constants in tests to exercise exact boundaries and one-byte/member-over refusals without large allocations. Truncated/invalid gzip receives a controlled CLI refusal rather than a traceback. These are verifier input limits, not a process-memory sandbox or runtime/Oracle qualification; Python objects/parser overhead is additional. The builder's existing canonical source format and committed/preview semantics are unchanged. Larger source inputs require an explicit policy review rather than relabeling them as a runtime release.

## Runtime/target compatibility gate — not satisfied

Every manifest states `runtime_installed: false`, `release_eligible: false`, `dependency_resolution: not-performed`, `target_compatibility: unverified`, and unknown OS/architecture/Python ABI. Passing source checks does not change these claims. **No Oracle instance exists yet.** Fresh provisioning requires a separate, exact-plan approval; architecture, image, and shape remain undecided. This artifact is source/staging only, not Oracle-validated.

Before anyone constructs or approves a runtime artifact, obtain the actual target OS/version, CPU architecture, libc, Python version/ABI, and approved resource/installation constraints. Then, separately approved and isolated:

1. Confirm the selected fork commit, executing builder digest, bundle digest, and actual source inputs; keep personal state/config outside the release.
2. Review upstream setup.py/build side effects. At the selected base it deletes `dist/` on import and has a custom build hook; this source packer deliberately never executes it.
3. Resolve/lock dependencies for that actual platform with recorded package hashes and build inputs. Included setup.py/requirements/uv.lock are source inputs, **not evidence of a solved or target-compatible environment**.
4. Build/install the selected fork's framework and overlay into a target-compatible isolated environment, run `pip check`, and prove imports come from that artifact rather than a preinstalled package or the checkout. Validate needed resources/native wheels on the real architecture; an x86 runner does not qualify ARM.
5. Run application tests and an approved disarmed/offline startup check. No broker credentials, paper orders, automatic service activation, release promotion, or cloud changes are authorized by this document.

Local Python 3.14 source/unit execution is also not evidence of a supported framework/dependency runtime. No runtime dependency install, framework import, Oracle check, deployment, or package publication was performed here.

## Inherited workflow audit — owner action before Actions enablement

Static audit covers **all seven inherited files** at the selected base. None was changed by this worker. Their current moving action tags are separate from the new pinned personal workflow. Remove/disable all inherited workflows from the fork's active/default branch before enabling repository Actions; do not rely on missing secrets or upstream branch names to prevent execution.

### Publishing/write hazards (highest priority)

| File | Triggers and consequential authority | Required owner disposition |
| --- | --- | --- |
| `.github/workflows/release.yml` | Push tags `v*.*.*`; manual qualification/recovery. Global `contents: write`, `id-token: write`, `actions: read`. Builds/installs upstream, uses provider test secrets; publish job uses `PYPI_API_TOKEN`, PyPI publish action and GitHub release creation; successful publication creates/commits/pushes the next `version/*` branch from dev. Optional environment approval comment is not an enforced gate. | Remove/disable before any enablement. Never reuse for personal releases. Even a manual qualification run inherits excessive permissions. |
| `.github/workflows/docs.yml` | dev PR/push with doc paths. Global `contents: write`, `pages: write`, `id-token: write`. Push path commits/pushes generated docs, deploys GitHub Pages, and sends IndexNow notifications to external search services. | Remove/disable before enablement. Not a personal docs/build check. |

### Other unwanted triggers, credential/dependency surface, and cost

| File | Static findings | Required owner disposition |
| --- | --- | --- |
| `.github/workflows/cicd.yaml` | Tag pushes `v*.*.*`/`ci-*`, dev path-scoped PRs, manual dispatch. Read-only contents but provider/broker test secret references, environments, dependency installation, six unit and four backtest shards. No publishing job in this file. | Remove/disable; not the secret-free personal test command. |
| `.github/workflows/metamask.yml` | Path-scoped PRs; pushes on dev/`version/**`; manual dispatch. Read-only token and persist-credentials false, but installs `.[metamask]`, framework/extras and test tooling across two Python versions. | Remove/disable; unrelated broker/runtime and installation surface. |
| `.github/workflows/agent-evals.yml` | Manual dispatch; read-only contents. Installs upstream dev dependencies, verifies native real-model qualification receipts, uploads JSON evidence. The workflow command is `verify`, not proof it makes inference calls; transitive script behavior was not qualified for this personal use. | Remove/disable; unnecessary upstream qualification/artifact surface. |
| `.github/workflows/public-hygiene.yml` | Every PR; pushes on dev/main/`version/**`; manual dispatch. Read-only contents, full-history checkout, changed-line checker, moving action tags and persisted checkout defaults. No publish step. | Remove/disable or separately adapt/re-pin after reviewing scanner coverage. Not covered by the new workflow. |
| `.github/workflows/star-history-health.yml` | Scheduled every six hours plus manual dispatch; empty token permissions; curl request to upstream's Lumiwealth star-history service. | Remove/disable; unnecessary scheduled external traffic/cost. |

This is a source/configuration audit, not evidence that any inherited workflow ran, that its secrets exist, or that external publishing would succeed. Repository-wide disablement was supplied as a prerequisite and was not changed or re-enabled. Keep the private infra repository on its own hold pending budget and permissions review; the public workflow has no token or connection to it.
