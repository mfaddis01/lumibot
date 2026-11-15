"""Build source archives offline. This is not a wheel or installed runtime."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import zlib

REPOSITORY = "mfaddis01/lumibot"
# Source-verifier resource policy only; these are not runtime qualification limits.
MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_MEMBERS = 4096
REQUIRED_FILES = frozenset({"lumibot/__init__.py", "personal_trading/__init__.py",
                            "personal_deploy/source_bundle.py", "setup.py", "LICENSE", "README.md"})


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


ROOT_FILES = frozenset({"LICENSE", "README.md", "setup.py", "setup.cfg", "pyproject.toml",
                        "MANIFEST.in", "requirements.txt", "requirements_agent_evals.txt", "uv.lock"})
BLOCKED_PARTS = frozenset({"runtime", "state", "data", "cache", "logs", "artifacts", "reports",
                           "__pycache__", "secrets", "venv", "dist", "build"})


def allowed(name):
    if (not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
            or any(part in {"", "."} for part in name.split("/"))):
        return False
    path = Path(name)
    if path.as_posix() != name or path.is_absolute() or any(part.startswith(".") or part.lower() in BLOCKED_PARTS for part in path.parts):
        return False
    if name in ROOT_FILES:
        return True
    if path.parts[0] in {"lumibot", "personal_trading", "personal_deploy"} and path.suffix == ".py":
        return True
    return (name == "lumibot/resources/conf.yaml"
            or (name.startswith("lumibot/components/agents/skills/") and path.suffix in {".md", ".yaml"})
            or (name.startswith("lumibot/example_strategies/fixtures/") and path.suffix == ".json"))


def build_bundle(source, commit, output, *, preview=False):
    source, output = Path(source), Path(output)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("a full lowercase 40-character source commit is required")
    try:
        origin = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=source, stderr=subprocess.PIPE, text=True).strip()
        if origin not in {f"https://github.com/{REPOSITORY}.git", f"https://github.com/{REPOSITORY}",
                          f"git@github.com:{REPOSITORY}.git"}:
            raise ValueError("source origin is not the selected personal fork")
        actual = subprocess.check_output(["git", "rev-parse", "--verify", f"{commit}^{{commit}}"], cwd=source, stderr=subprocess.PIPE, text=True).strip()
        if actual != commit:
            raise ValueError("source commit identity mismatch")
    except subprocess.CalledProcessError:
        raise ValueError("source repository or commit is unavailable") from None
    entries = subprocess.check_output(
        ["git", "ls-tree", "-rz", commit], cwd=source
    ).split(b"\0")
    modes = {}
    for entry in entries:
        if entry:
            metadata, name = entry.split(b"\t", 1)
            modes[name.decode("utf-8")] = metadata.split()[0].decode("ascii")
    names = list(modes)
    if preview:
        import os
        names = [name for name in ROOT_FILES if (source / name).is_file()]
        for namespace in ("lumibot", "personal_trading", "personal_deploy"):
            if (source / namespace).is_symlink():
                raise ValueError("symlink source directories are forbidden")
            for directory, dirs, leafs in os.walk(source / namespace, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in BLOCKED_PARTS]
                if any((Path(directory) / d).is_symlink() for d in dirs):
                    raise ValueError("symlink source directories are forbidden")
                names.extend((Path(directory) / leaf).relative_to(source).as_posix() for leaf in leafs)
        if any((source / name).is_symlink() or not (source / name).is_file() for name in names if allowed(name)):
            raise ValueError("source payload must contain only regular files")
        files = {name: (source / name).read_bytes() for name in names if allowed(name)}
    else:
        if any(modes[name] not in {"100644", "100755"} for name in names if allowed(name)):
            raise ValueError("source payload must contain only regular git blobs")
        files = {
            name: subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=source)
            for name in names if allowed(name)
        }
    if not REQUIRED_FILES.issubset(files):
        raise ValueError("source payload is missing required framework, overlay, or license inputs")
    records = [
        {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    manifest = {
        "schema_version": 1,
        "artifact_kind": "source-bundle",
        "dependency_resolution": "not-performed",
        "target": {"os": None, "architecture": None, "python_abi": None},
        "reviewed_upstream_base": {"repository": "Lumiwealth/lumibot", "tag": "v4.6.64",
                                   "commit": "951732e501bccfbffc3718c9e6bd27e51f07fef0"},
        "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "repository": REPOSITORY,
        "source_commit": commit,
        "source_mode": "working-tree-preview" if preview else "committed",
        "release_eligible": False,
        "runtime_installed": False,
        "target_compatibility": "unverified",
        "payload_sha256": hashlib.sha256(_canonical(records)).hexdigest(),
        "files": records,
    }
    files["SOURCE-MANIFEST.json"] = _canonical(manifest) + b"\n"
    with output.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name, data in sorted(files.items()):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o644
                    archive.addfile(info, io.BytesIO(data))
    return {"artifact": output.name, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "repository": REPOSITORY, "source_commit": commit, "source_mode": manifest["source_mode"],
            "payload_sha256": manifest["payload_sha256"], "file_count": len(records)}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate source manifest key")
        result[key] = value
    return result


def verify_bundle(artifact, expected_sha256, expected_commit, *, allow_preview=False):
    with Path(artifact).open("rb") as handle:
        data = handle.read(MAX_COMPRESSED_BYTES + 1)
    if len(data) > MAX_COMPRESSED_BYTES:
        raise ValueError("compressed source archive exceeds limit")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("artifact digest mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise ValueError("a full lowercase source commit is required")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as compressed:
            expanded = compressed.read(MAX_EXPANDED_BYTES + 1)
    except (EOFError, gzip.BadGzipFile, zlib.error):
        raise ValueError("source gzip is malformed") from None
    if len(expanded) > MAX_EXPANDED_BYTES:
        raise ValueError("expanded source archive exceeds limit")
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        files = {}
        for index, member in enumerate(archive, start=1):
            if index > MAX_MEMBERS:
                raise ValueError("source archive member count exceeds limit")
            if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                raise ValueError("source archive member exceeds size limit")
            if (not member.isfile() or member.name in files
                    or (member.name != "SOURCE-MANIFEST.json" and not allowed(member.name))):
                raise ValueError("unsafe or duplicate archive member")
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError("archive member is not readable")
            files[member.name] = handle.read(member.size)
    try:
        manifest = json.loads(files.pop("SOURCE-MANIFEST.json"), object_pairs_hook=_unique_object)
    except (KeyError, ValueError, RecursionError):
        raise ValueError("source manifest missing or malformed") from None
    if not isinstance(manifest, dict) or not REQUIRED_FILES.issubset(files):
        raise ValueError("manifest must describe the required framework and overlay sources")
    if set(manifest) != {
            "schema_version", "artifact_kind", "dependency_resolution", "target",
            "reviewed_upstream_base", "builder_sha256", "repository", "source_commit",
            "source_mode", "release_eligible", "runtime_installed", "target_compatibility",
            "payload_sha256", "files"}:
        raise ValueError("source manifest fields are missing or unsupported")
    if manifest.get("target") != {"os": None, "architecture": None, "python_abi": None}:
        raise ValueError("source target must remain exactly unqualified")
    if manifest.get("reviewed_upstream_base") != {
            "repository": "Lumiwealth/lumibot", "tag": "v4.6.64",
            "commit": "951732e501bccfbffc3718c9e6bd27e51f07fef0"}:
        raise ValueError("unsupported reviewed upstream reference metadata")
    if (not isinstance(manifest.get("builder_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", manifest["builder_sha256"])):
        raise ValueError("source manifest requires a lowercase builder digest")
    records = [{"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
               for name, content in sorted(files.items())]
    if (not isinstance(manifest.get("files"), list)
            or any(not isinstance(record, dict) or type(record.get("size")) is not int
                   or record["size"] < 0 for record in manifest["files"])):
        raise ValueError("source file records require nonnegative integer sizes")
    if (manifest.get("files") != records
            or manifest.get("payload_sha256") != hashlib.sha256(_canonical(records)).hexdigest()):
        raise ValueError("payload differs from source manifest")
    if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
            or manifest.get("artifact_kind") != "source-bundle"
            or manifest.get("runtime_installed") is not False
            or manifest.get("release_eligible") is not False
            or manifest.get("target_compatibility") != "unverified"
            or manifest.get("dependency_resolution") != "not-performed"):
        raise ValueError("unsupported source schema or false runtime qualification")
    if manifest.get("repository") != REPOSITORY or manifest.get("source_commit") != expected_commit:
        raise ValueError("artifact source identity mismatch")
    modes = {"committed", "working-tree-preview"} if allow_preview else {"committed"}
    if not isinstance(manifest.get("source_mode"), str) or manifest["source_mode"] not in modes:
        raise ValueError("preview artifacts require explicit preview verification")
    return manifest


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="source-bundle", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--source", type=Path, default=Path("."))
    build.add_argument("--commit", required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--preview", action="store_true", help="Non-release working-tree snapshot")
    verify = commands.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--sha256", required=True, help="Expected digest from trusted external handoff")
    verify.add_argument("--commit", required=True)
    verify.add_argument("--allow-preview", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            report = build_bundle(args.source, args.commit, args.output, preview=args.preview)
        else:
            manifest = verify_bundle(args.artifact, args.sha256, args.commit, allow_preview=args.allow_preview)
            report = {key: manifest[key] for key in ("repository", "source_commit", "source_mode", "payload_sha256", "target_compatibility")}
            report["sha256"] = args.sha256
    except ValueError as exc:
        parser.error(str(exc))
    except (OSError, subprocess.SubprocessError, tarfile.TarError, UnicodeError, KeyError, TypeError):
        parser.error("bundle operation failed; check inputs without exposing local paths")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
