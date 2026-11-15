# Dependency candidate — Linux/amd64 CPython 3.12

`requirements.linux-amd64.txt` is a full **source-graph candidate**, not an installed environment or a qualified Docker image. It contains all 54 mandatory `setup.py` roots and their 264-package resolved graph, including enabled upstream extras. It does not add optional LumiBot extras such as `metamask`, `browser`, or `thetadata`. The reviewed overlay keeps `alpaca-py==0.42.0` and compatible `websockets==15.0.1`.

## Target and evidence

Selection policy: Linux x86_64, CPython `cp312` and compatible `abi3`/universal wheels, **glibc >=2.36**. The real resolver ran with Python 3.12.3, pip 24.0, explicit manylinux tags through 2.36 and an explicit Linux/x86_64 target marker environment. Kernel release/version markers were unspecified and are rejected by the graph checker. Target image digest, actual interpreter patch, system libraries and native execution still require image qualification.

Workspace evidence (outside tracked source): `review/docker-implementation/dependency-evidence/`, with handoff `review/docker-implementation/dependency-handoff.json`. Relative to the application worktree these are under `../../review/docker-implementation/`.

- `setup-roots.txt`, `overlay-constraints.txt`, `source.json`: exact AST-read source inputs and provenance. Project `setup.py` was **not executed**.
- `pip-resolver-{command,result,report}.json`, `pip-resolver.log`: real public-PyPI resolution, dependencies enabled.
- `closure-verification.json`, `selected-distributions.json`, `pypi-selected-metadata.json`: fixed-point extras, 557 active edges, artifact tags/URLs/public SHA-256 verification.
- `pip-hash-replay-{command,result,report}.json`, `pip-hash-replay.log`: actual `--require-hashes` replay of this exact requirements file, dependencies enabled. All 264 names/versions/selected hashes match.
- `tdd-*-red.log`, `tdd-*-green.log`: four incremental generator test cycles; these test generator behavior, not application/native compatibility.

`dependency_candidate.py` provides AST extraction, independent fixed-point graph verification, target/published-wheel verification and deterministic lock rendering. It imports neither LumiBot nor broker SDKs. It deliberately uses pip **24.0**'s vendored packaging implementation; pin tooling from `dependency-build-tools.txt` in a **new disposable venv**, never mutate a shared environment. The source generator is a library; the executed orchestration scripts, their real commands and logs are retained in evidence. Regeneration scripts there contain the exact workspace paths and must be reviewed before reuse elsewhere.

Run offline generator tests using the separately pinned tooling interpreter:

```sh
PYTHONDONTWRITEBYTECODE=1 /path/to/disposable-venv/bin/python \
  -m unittest discover -s docker/dependency_tests -v
```

Build/resolver subprocesses must use an empty HOME, an explicit environment allowlist and scratch-local TMPDIR. The experiment monitored only its own scratch subtree and stayed below its 680,000,000-byte stop threshold. The complete graph was not installed to avoid exceeding the bounded scratch budget.

## ibapi is an explicit source-build exception

Required `ibapi==9.81.1.post1` has only a published sdist in the recorded PyPI release metadata:

- Published `ibapi-9.81.1.post1.tar.gz`, SHA-256 `49f6678bf4cced996920f32ad4b48e6897749ac30ba14a661082285f4ec09cd6`.
- Freshly built `ibapi-9.81.1.post1-py3-none-any.whl`, SHA-256 `7cf006ac0a3de22a258d6d38ce3e3da51c718a70e05300e5d354d9d0481774a8`. **Self-built, not published.** Retained in dependency evidence, not tracked source or an ARM wheelhouse.

Both hashes appear on its requirement. The other 263 requirement hashes identify selected published amd64/universal wheels. The ibapi source/setup/imported version helper were read first; only this third-party sdist was built. The build used fresh pinned Python3.12.3/pip24.0/setuptools84.1.0/wheel0.45.1 tooling, fixed `SOURCE_DATE_EPOCH=1700000000`, `--no-index --no-deps --no-build-isolation`, a sanitized disposable environment and an inherited kernel seccomp network-denial filter. The wheel declares `Root-Is-Purelib: true`, `Tag: py3-none-any`, no dependency edges and no compiled libraries. This is not a general filesystem sandbox or a production artifact attestation.

Binary-only **cross-resolution** replay requires `--find-links /path/to/dependency-evidence` to supply that self-built wheel. Installing this file using only PyPI and `--only-binary=:all:` cannot work for ibapi. An image packager must deliberately either supply/rebuild and verify the reviewed universal wheel, or allow **only ibapi** from the hash-pinned published sdist with separately pinned offline build tooling. Do not remove the sdist hash, silently permit arbitrary sdists, or pretend a self-built hash is a published PyPI wheel hash. The separately pinned build setuptools is 84.1.0; the application graph independently selects setuptools 84.2.1. Keep these environments/stages separate.

No Docker daemon was installed or used. No full-graph native install, installed `pip check`, native-extension import, image build/run, application setup/build, broker/account access or production qualification was performed. Dependency vulnerability/license review and runtime/system-library/resource qualification remain separate gates. No dependency constraint blocker remains in this candidate.
