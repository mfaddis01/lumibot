"""Explicit dedicated-process runtime setup; never discovers ambient secrets."""
import atexit
import hashlib
from importlib import metadata, util
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
_RUNTIME_STATE = None
SAFE_ENV = {
    'PATH': '/usr/bin:/bin',
    'LANG': 'C.UTF-8',
    'KMP_INIT_AT_FORK': 'FALSE',

    'LUMIBOT_DISABLE_DOTENV': '1',
    'LUMIBOT_DISABLE_DOTENV_LOCAL': '1',
    'LUMIBOT_LAZY_CREDENTIALS': '1',
    'LUMIBOT_LOG_LEVEL': 'ERROR',
    'LUMIBOT_TELEMETRY': '0',
    'LOG_ERRORS_TO_CSV': 'false',
    'LUMIBOT_CONNECT_STREAM': '0',
    'LUMIBOT_START_ORDERS_THREAD': '0',
}


def configure():
    """Call before importing LumiBot, only in a fresh dedicated process.

    Intentionally clears inherited environment WITHOUT fetching its values.
    Never use this helper inside an existing application/strategy worker.
    """
    if 'lumibot.credentials' in sys.modules or 'lumibot.tools.lumibot_logger' in sys.modules:
        raise RuntimeError('Framework already initialized; start a new dedicated process')
    # Disposable state outside the checkout; caller chooses TMPDIR before launch.
    global _RUNTIME_STATE
    if _RUNTIME_STATE is None:
        _RUNTIME_STATE = tempfile.TemporaryDirectory(prefix='tradelocker-runtime-')
        atexit.register(_RUNTIME_STATE.cleanup)
        state = Path(_RUNTIME_STATE.name)
        SAFE_ENV.update(HOME=str(state / 'home'), LUMIBOT_CACHE_FOLDER=str(state / 'cache'),
                        MPLCONFIGDIR=str(state / 'matplotlib'), TMPDIR=tempfile.gettempdir())
    os.environ.clear()
    os.environ.update(SAFE_ENV)
    sys.dont_write_bytecode = True
    # Never prefer a checkout over the installed distribution. The personal
    # package is already loaded; removing checkout roots does not alter its path.
    installed = Path(metadata.distribution('lumibot').locate_file('lumibot')).resolve()
    sys.path[:] = [entry for entry in sys.path
                   if not (Path(entry or os.curdir) / 'lumibot').is_dir()
                   or (Path(entry or os.curdir) / 'lumibot').resolve() == installed]
    verify_framework()


def verify_framework():
    distribution = metadata.distribution('lumibot')
    if distribution.version != '4.6.64+source8dbcf8f.1':
        raise RuntimeError('Unqualified installed framework identity')
    installed = Path(distribution.locate_file('lumibot')).resolve()
    spec = util.find_spec('lumibot')
    if spec is None or Path(spec.origin).resolve().parent != installed:
        raise RuntimeError('Framework source shadowing; start an isolated process')
    manifest = json.loads((ROOT / 'framework-source.json').read_text())
    for relative, digest in manifest['files'].items():
        if hashlib.sha256(Path(distribution.locate_file(relative)).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Framework source changed; requalify candidate')


def require_safe_runtime():
    if dict(os.environ) != SAFE_ENV:
        raise RuntimeError('Call runtime.configure() in a dedicated process first')
