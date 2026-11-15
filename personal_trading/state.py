"""Single-host advisory ownership/config binding, NOT an order/fill journal."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile


def _private(info, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError("unsafe state permissions")
    if not directory and info.st_nlink != 1:
        raise OSError("unsafe state links")


@contextmanager
def exclusive_executor(directory):
    """Linux/local disk only. Keep the lock inode; unlinking can split ownership."""
    descriptor = None
    try:
        directory = Path(directory)
        if not directory.is_absolute():
            raise OSError("state directory must be absolute")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private(directory.lstat(), directory=True)
        descriptor = os.open(directory / "executor.lock",
                             os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        _private(os.fstat(descriptor))
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        raise RuntimeError("local executor state unavailable or already owned") from None
    try:
        yield directory
    finally:
        os.close(descriptor)


@contextmanager
def bound_executor(directory, config_identity):
    """Bind one state location to exact declared config, before any broker access.

    This records NO broker observations and is deliberately never auto-migrated.
    Malformed, mismatched or partially written state requires operator review.
    """
    if type(config_identity) is not str or not re.fullmatch(r"[0-9a-f]{64}", config_identity):
        raise RuntimeError("invalid configuration identity")
    expected = {"schema": 1, "config_identity": config_identity}
    if Path(directory).resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise RuntimeError("state directory must be outside the application release")
    with exclusive_executor(directory) as root:
        pending = None
        try:
            path = root / "identity.json"
            try:
                # Reject special files without blocking on a FIFO before fstat.
                descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                descriptor = None
            if descriptor is not None:
                with os.fdopen(descriptor, encoding="utf-8") as handle:
                    _private(os.fstat(handle.fileno()))
                    text = handle.read(4097)
                # App-owned canonical bytes, not operator-editable recovery input.
                # Exact comparison also rejects duplicate keys and bool/int schema ambiguity.
                if text != json.dumps(expected, sort_keys=True):
                    raise ValueError("identity mismatch")
            else:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root,
                                                 prefix=".identity-", delete=False) as handle:
                    pending = handle.name
                    json.dump(expected, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(pending, path)
                pending = None
                directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except (OSError, ValueError, UnicodeError):
            raise RuntimeError("state identity unavailable or mismatched; operator review required") from None
        finally:
            if pending is not None:
                os.unlink(pending)
        yield root
