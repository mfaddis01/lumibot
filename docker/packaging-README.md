# DISARMED image packaging — source preparation, not a built release

## What is pinned

`Dockerfile` uses the official `library/python` **single-platform linux/amd64
manifest** `sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4`.
Its config ID is separately `sha256:313cf8f190a1abab00472c1f0c950c10486c22114bcd8aef2a8198fb587a6d18`;
config `PYTHON_VERSION` is **3.12.16**, not the resolver host's 3.12.3.
`packaging-base.json` records discovery-index identity and the independent marker
replay: all 54 mandatory roots, 264 selected/reachable distributions and 557 active
edges/extras remain identical at 3.12.16, with no closure errors. This is metadata
and marker evidence, not native installation, libc measurement, or `pip check`.
The Debian bookworm upstream recipe is glibc-based; build code asserts actual
Linux/x86_64, Python 3.12.16 and glibc >=2.36 before any fetch or build action.
Registry retrieval retained only index/manifest/config metadata, **not rootfs layers**.

The dependency candidate is unchanged, SHA-256
`ac137679962c2a4e724ba8ce7c18bd026407a60ad9824e669e1a9984667b7bea`.
`packaging-artifacts.json` retains the exact public URL, filename, size and hash of
263 application wheels, the **real public ibapi sdist**, and three separately
pinned build-tool wheels. No scratch/evidence wheel is needed to build this image.
The sole network-enabled Docker `RUN` downloads these public archives with a
proxy-free credential-free stdlib client, rejects redirects, and checks exact
length/hash before publishing bytes. It performs no setup/import/install.

Offline builder code installs `dependency-build-tools.txt` into `/build-tools`,
separate from `/opt/venv`. It builds ibapi from its hash-verified sdist and the
framework from an isolated disposable source copy; `setup.py` build side effects
cannot alter preserved input bytes. Both wheel builds use `--no-index --no-deps
--no-build-isolation`, pinned tools, and the committed source epoch. The original
candidate's historical self-built ibapi wheel hash is **not** treated as a
published artifact or promised to match a new build: a separate generated local
install lock records the wheel actually produced by this build, its public-sdist
origin, and its actual hash. That wheel record is consistency evidence, not
independent trust/approval. The original candidate lock remains preserved.

## Clean committed context, not a source-store archive

`packaging-inputs.json` is a reviewed **exact path + SHA-256** allowlist of the
framework source/resources, personal overlay and root build/version inputs.
New/changed framework or overlay paths require explicit policy review. The
exporter separately names every permitted packaging/lock/provenance/test/doc
input. It reads immutable **Git blob OIDs**, not working-tree file contents; it
never uses `git archive` filters, recursive checkout copying, HOME, Git remotes,
or basename-only secret filtering. Symlinks/submodules/special input modes are
refused. Hidden source paths are not allowed. Excluded tracked files are not read.
Ignored synthetic private files are omitted; nonignored untracked files and
tracked dirt refuse export before destination creation. The executing exporter
must equal its committed blob. Output must be new and outside the checkout.

The canonical `packaging-inventory.json` binds every exported file, Git source
commit/blob/mode, exact framework build inputs and personal version. Its SHA-256
must be retained outside the context and passed as `CONTEXT_SHA256`; omitted or
mismatched values refuse the build. Inventory verification rejects changed,
missing, duplicate/noncanonical, linked, or extra files. `Dockerfile.dockerignore`
is defense in depth **only for this exported layout**, not permission to build
from a live checkout. Do not use the old 64 MiB source bundle as an image context:
it does not bind `personal_release/VERSION`, Dockerfile or dependency inputs.
The source store and its meaning remain unchanged.

All framework source (including the tracked ThetaTerminal resource) is retained.
`setup.py` produces the framework wheel but deliberately excludes
`personal_trading`; the builder explicitly installs that overlay into venv
site-packages and all reviewed non-Python framework resources into their runtime
package paths. The runtime working directory is **not** a source checkout.
Original source, VERSION, Dockerfile, both dependency locks, provenance, recipe,
artifact policy and input inventory remain under `/opt/personal-trading/inputs`.
Generated wheel hashes, resource/overlay inventory, observed Python/libc,
`pip check`, import-origin checks and offline synthetic CLI results are recorded
in `/opt/personal-trading/build-provenance.json` **only if a future real build
executes and passes them**. No such build record is claimed to exist today.
No downloaded archives, build-tools venv, or caches are copied into the runtime.

## After reviewed bytes are committed by the parent

The current implementation worktree is intentionally dirty/untracked; export
**must fail now**. There is no `--preview`, dirt bypass, or fabricated committed
context. The parent alone owns Git staging/commit/publication. An explicit future
build, once Docker tooling/daemon use is separately approved, can use:

```sh
# Run the exporter from the reviewed committed checkout. Use a NEW output path.
env -i PATH=/usr/bin:/bin HOME=/nonexistent PYTHONDONTWRITEBYTECODE=1 \
  python3 docker/packaging_context.py --repo "$PWD" --output "$CONTEXT"
# Retain the printed SHA-256 independently as CONTEXT_SHA256. Then verify:
env -i PATH=/usr/bin:/bin HOME=/nonexistent PYTHONDONTWRITEBYTECODE=1 \
  python3 "$CONTEXT/docker/packaging_build.py" verify \
  --root "$CONTEXT" --digest "$CONTEXT_SHA256"
# FUTURE AUTHORIZED BUILDER ONLY; not executed for this handoff:
docker build --platform linux/amd64 --file "$CONTEXT/docker/Dockerfile" \
  --build-arg CONTEXT_SHA256="$CONTEXT_SHA256" \
  --tag personal-trading:offline-candidate "$CONTEXT"
```

Use an explicitly approved credential-free builder/daemon/CLI configuration, not
an inherited host Docker context, proxy, credential helper or private build cache.
Do not globally pass `--network=none` to this future build: the one public archive
fetch needs its network; all setup/build/install steps have Dockerfile
`RUN --network=none`. Do not inject build secrets, SSH mounts, private indexes,
extra contexts, external caches, rootfs downloads outside the approved builder,
or credentials. No push/publication or host installation is authorized here.

Context contents and dependency selection are reproducible; source epoch is
bound and used by wheel construction. **Bit-identical OCI output is not proven**:
BuildKit/engine version, image/layer timestamps and actual repeated native builds
remain a future qualification gate. This is not a signed provenance system.

## Runtime contract and remaining gates

The image defaults to `python -m personal_trading --help`; Compose explicitly
chooses offline one-shot `readiness --config /etc/personal-trading/config.json`.
UID:GID is `10001:10001`, workdir `/opt/personal-trading`, HOME `/tmp`, user-site
imports disabled, and `LUMIBOT_DISABLE_DOTENV=1` is explicit. The runtime venv is
created without pip; the final stage also removes the base's global pip and
ensurepip. There is no runtime installation, broker credential, entrypoint
arming logic, strategy execution, restart loop or trader healthcheck. `run` must
still return **3**, including with a synthetically armed config.

The separate Compose contract supplies network-none, read-only rootfs, bounded
tmpfs/resources/logging, dropped capabilities/no-new-privileges and no host
home/Docker socket. Image defaults alone do not enforce these daemon controls.
No state/credentials are embedded. Retaining ThetaTerminal.jar is not Java or
ThetaData runtime qualification; optional extras/services remain unqualified.

**BLOCKED: no actual Docker build was run; Docker CLI/daemon are unavailable and
installation/startup is not approved.** Full native 264-graph install, builder
`pip check`, full-framework/native imports/system-library compatibility, actual
image manifest/config identity, final nonroot/read-only/offline container
readiness, Compose-native validation, UID mapping and resource enforcement remain
unperformed. A future build is designed to fail closed on missing libraries or
other incompatibilities; static tests do not supply those libraries or evidence.
The offline unit fixture checks only the explicitly installed personal overlay,
not a full native framework installation or an image. Retain these distinct
claims in any release descriptor. No live account/order calls are permitted.

## Offline regression commands

```sh
python3.12 -m unittest discover -s docker/packaging_tests -v
python3.12 -m unittest discover -s docker/dependency_tests -v
python3.12 -m unittest discover -s personal_tests -v
python3.12 -m unittest discover -s personal_ci_tests -v
```

Handoff evidence lives outside tracked source at
`review/docker-implementation/packaging-evidence/`: individual RED/GREEN logs,
registry metadata/hash proof, prerequisite six-file checks, patch marker replay,
network-denied final regression logs, exact commands and file hashes. Fixture Git
writes are confined to synthetic task-local temporary repositories; actual source
Git state is never written by this workstream.
