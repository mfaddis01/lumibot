"""Source-only dependency candidate helpers (never import the application)."""
import ast

# Minimum glibc 2.36 policy (e.g. Debian bookworm); not a native image claim.
TARGET = {
    "implementation_name": "cpython", "implementation_version": "3.12.3",
    "os_name": "posix", "platform_machine": "x86_64",
    "platform_python_implementation": "CPython", "platform_release": "",
    "platform_system": "Linux", "platform_version": "",
    "python_full_version": "3.12.3", "python_version": "3.12", "sys_platform": "linux",
}
PLATFORMS = [f"manylinux_2_{minor}_x86_64" for minor in range(36, 16, -1)] + [
    "manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64",
]


def close_graph(report, roots, environment):
    """Independently evaluate target markers and propagate extras to a fixed point."""
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.specifiers import SpecifierSet
    from pip._vendor.packaging.utils import canonicalize_name
    selected = {canonicalize_name(item["metadata"]["name"]): item["metadata"]
                for item in report["install"]}
    errors = []
    if len(selected) != len(report["install"]):
        errors.append("duplicate distribution names")
    requirements = [Requirement(root) for root in roots]
    def active(req, extras):
        if req.marker and any(key in str(req.marker) for key in ("platform_release", "platform_version")):
            raise ValueError("Kernel marker cannot be qualified for unspecified image")
        return not req.marker or any(req.marker.evaluate(dict(environment, extra=extra))
                                     for extra in {""} | set(extras))
    requirements = [req for req in requirements if active(req, set())]
    extras = {}
    reachable = set()
    for req in requirements:
        name = canonicalize_name(req.name)
        reachable.add(name)
        extras.setdefault(name, set()).update(req.extras)
    while True:
        before = (len(reachable), sum(map(len, extras.values())))
        for name in list(reachable):
            for text in selected.get(name, {}).get("requires_dist", []):
                req = Requirement(text)
                if active(req, extras.get(name, set())):
                    target = canonicalize_name(req.name)
                    reachable.add(target)
                    extras.setdefault(target, set()).update(req.extras)
        if before == (len(reachable), sum(map(len, extras.values()))):
            break
    def satisfied(req):
        name = canonicalize_name(req.name)
        return (not req.url and name in selected
                and req.specifier.contains(selected[name]["version"], prereleases=True))
    for req in requirements:
        if not satisfied(req):
            errors.append("unsatisfied root: " + str(req))
    edges = []
    for name in sorted(reachable):
        if name not in selected:
            errors.append("missing: " + name)
            continue
        metadata = selected[name]
        if not SpecifierSet(metadata.get("requires_python", "")).contains(environment["python_full_version"]):
            errors.append("Requires-Python: " + name)
        for text in metadata.get("requires_dist", []):
            req = Requirement(text)
            if active(req, extras.get(name, set())):
                ok = satisfied(req)
                edges.append({"from": name, "requirement": text,
                              "to": canonicalize_name(req.name), "satisfied": ok})
                if not ok:
                    errors.append("unsatisfied edge: " + name + " -> " + text)
    unreachable = sorted(set(selected) - reachable)
    errors.extend("unreachable: " + name for name in unreachable)
    return {"reachable": sorted(reachable), "active_edges": edges,
            "enabled_extras": {name: sorted(value) for name, value in sorted(extras.items()) if value},
            "unreachable": unreachable, "errors": errors}


def verify_artifact(item, release):
    """Bind a selected published wheel to registry hash/URL and allowed target tags."""
    from urllib.parse import unquote, urlparse
    from pip._vendor.packaging.tags import cpython_tags, compatible_tags
    from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
    info = item["download_info"]
    filename = unquote(urlparse(info["url"]).path.rsplit("/", 1)[-1])
    name, version, _, wheel_tags = parse_wheel_filename(filename)
    allowed = set(cpython_tags((3, 12), abis=["cp312"], platforms=PLATFORMS)) | set(
        compatible_tags((3, 12), interpreter="cp312", platforms=PLATFORMS))
    if not wheel_tags & allowed:
        raise ValueError("Incompatible target wheel: " + filename)
    if name != canonicalize_name(item["metadata"]["name"]) or str(version) != item["metadata"]["version"]:
        raise ValueError("Wheel identity mismatch: " + filename)
    matches = [artifact for artifact in release["urls"] if artifact["filename"] == filename]
    if len(matches) != 1:
        raise ValueError("Selected wheel not uniquely published: " + filename)
    artifact = matches[0]
    digest = info["archive_info"]["hashes"]["sha256"]
    if (artifact["digests"]["sha256"] != digest or artifact["url"] != info["url"]
            or artifact["yanked"] or artifact["packagetype"] != "bdist_wheel"
            or urlparse(info["url"]).scheme != "https"
            or urlparse(info["url"]).hostname != "files.pythonhosted.org"):
        raise ValueError("Published artifact provenance mismatch: " + filename)
    return {"name": name, "version": str(version), "filename": filename,
            "sha256": digest, "url": info["url"], "size": artifact["size"],
            "source": "published-pypi-wheel", "hash_verified": True,
            "yanked": False}


def render_lock(report, closure, additional_hashes=None):
    """Render exact distributions plus extras; reject failed graph verification."""
    import re
    from pip._vendor.packaging.utils import canonicalize_name
    from pip._vendor.packaging.version import Version
    if closure["errors"]:
        raise ValueError("Cannot emit an invalid dependency graph")
    lines = [
        "# Candidate only: Linux/amd64 CPython 3.12, glibc >=2.36; not Docker/runtime qualification.",
        "# Generated from setup.py AST + reviewed overlay; regenerate deliberately, never pip freeze.",
        "# ibapi exception: published sdist hash AND separately self-built universal wheel hash.",
        "# No published ibapi wheel exists in the recorded release metadata; see dependency provenance.",
        "# For binary-only replay, supply the evidence ibapi wheel via --find-links.",
        "# For public-sdist builds, use pinned offline build tools; never invent a published wheel hash.",
    ]
    for item in sorted(report["install"], key=lambda item: canonicalize_name(item["metadata"]["name"])):
        metadata = item["metadata"]
        name = canonicalize_name(metadata["name"])
        version = str(Version(metadata["version"]))
        hashes = set((additional_hashes or {}).get(name, []))
        hashes.add(item["download_info"]["archive_info"]["hashes"]["sha256"])
        if not all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in hashes):
            raise ValueError("Invalid SHA-256: " + name)
        extras = closure["enabled_extras"].get(name, [])
        suffix = "[" + ",".join(sorted(extras)) + "]" if extras else ""
        lines.append(name + suffix + "==" + version + " " + " ".join(
            "--hash=sha256:" + digest for digest in sorted(hashes)))
    return "\n".join(lines) + "\n"


def extract_roots(source):
    """Read the one literal install_requires list; reject dynamic declarations."""
    values = [keyword.value for node in ast.walk(ast.parse(source))
              if isinstance(node, ast.Call)
              and ((isinstance(node.func, ast.Attribute) and node.func.attr == "setup")
                   or (isinstance(node.func, ast.Name) and node.func.id == "setup"))
              for keyword in node.keywords if keyword.arg == "install_requires"]
    if len(values) != 1:
        raise ValueError("Expected one install_requires declaration")
    try:
        roots = ast.literal_eval(values[0])
    except (ValueError, TypeError) as exc:
        raise ValueError("install_requires must be literal") from exc
    if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
        raise ValueError("install_requires must be a list of strings")
    return roots
