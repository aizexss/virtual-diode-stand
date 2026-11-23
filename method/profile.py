"""Strict public-profile validation and canonical JSON input/output support.

Validation checks the declared evidence model, not the real-world truth of its
binding, ordering, coverage or lower-bound premises.
"""

import hashlib
import json
import os
from pathlib import Path
import tempfile


PROFILE_SCHEMA = "floor-explanation/v1"
PROFILE_FIELDS = {
    "schema", "run", "domain", "consumer", "barrier", "closure", "recovery",
    "records", "coverage", "gaps", "provenance",
}


class ProfileIssue(Exception):
    """A reproducible schema or declared-model inconsistency."""

    def __init__(self, code, path):
        super().__init__(f"{code} at {path or '/'}")
        self.diagnostic = {"code": code, "path": path}


def _object(value, keys, path):
    if type(value) is not dict:
        raise ProfileIssue("TYPE", path)
    if set(value) != set(keys):
        raise ProfileIssue("SCHEMA", path)


def _text(value, path):
    if type(value) is not str or not value:
        raise ProfileIssue("TYPE", path)


def _integer(value, path):
    if type(value) is not int:
        raise ProfileIssue("TYPE", path)
    if value < 0:
        raise ProfileIssue("RANGE", path)


def _witness(value, path):
    if value is not None and type(value) is not str:
        raise ProfileIssue("TYPE", path)


def _kind(value, path):
    _text(value, path)
    if value not in ("P", "Q"):
        raise ProfileIssue("SCHEMA", path)


def _validate_structure(profile):
    _object(profile, PROFILE_FIELDS, "")
    for key in ("run", "consumer", "provenance"):
        _text(profile[key], f"/{key}")
    if type(profile["schema"]) is not str:
        raise ProfileIssue("TYPE", "/schema")
    if profile["schema"] != PROFILE_SCHEMA:
        raise ProfileIssue("SCHEMA", "/schema")
    if profile["domain"] is not None:
        _text(profile["domain"], "/domain")

    for name, fields in (("barrier", {"id", "order", "floor"}),
                         ("closure", {"id", "order"}),
                         ("recovery", {"id", "order", "consumer", "witness"})):
        endpoint = profile[name]
        if endpoint is None:
            continue
        _object(endpoint, fields, f"/{name}")
        _text(endpoint["id"], f"/{name}/id")
        _integer(endpoint["order"], f"/{name}/order")
        if name == "barrier":
            _integer(endpoint["floor"], "/barrier/floor")
        if name == "recovery":
            _text(endpoint["consumer"], "/recovery/consumer")
            _witness(endpoint["witness"], "/recovery/witness")

    if type(profile["records"]) is not list:
        raise ProfileIssue("TYPE", "/records")
    for index, record in enumerate(profile["records"]):
        path = f"/records/{index}"
        _object(record, {"id", "order", "kind", "candidates", "binding"}, path)
        _text(record["id"], path + "/id")
        _integer(record["order"], path + "/order")
        _kind(record["kind"], path + "/kind")
        _text(record["binding"], path + "/binding")
        candidates = record["candidates"]
        if type(candidates) is not list:
            raise ProfileIssue("TYPE", path + "/candidates")
        for candidate_index, version in enumerate(candidates):
            _integer(version, path + f"/candidates/{candidate_index}")
        if not candidates or any(a >= b for a, b in zip(candidates, candidates[1:])):
            raise ProfileIssue("CANDIDATES", path + "/candidates")

    _object(profile["coverage"], {"P", "Q"}, "/coverage")
    for kind in ("P", "Q"):
        coverage = profile["coverage"][kind]
        path = f"/coverage/{kind}"
        _object(coverage, {"complete", "witness"}, path)
        if type(coverage["complete"]) is not bool:
            raise ProfileIssue("TYPE", path + "/complete")
        _witness(coverage["witness"], path + "/witness")
    if type(profile["gaps"]) is not list:
        raise ProfileIssue("TYPE", "/gaps")
    for index, gap in enumerate(profile["gaps"]):
        path = f"/gaps/{index}"
        _object(gap, {"kind", "lower", "witness"}, path)
        _kind(gap["kind"], path + "/kind")
        _integer(gap["lower"], path + "/lower")
        _witness(gap["witness"], path + "/witness")


def _validate_relations(profile):
    seen_ids = set()
    endpoints = [(f"/{name}", profile[name]) for name in ("barrier", "closure", "recovery")]
    identities = endpoints + [(f"/records/{i}", record)
                              for i, record in enumerate(profile["records"])]
    for path, record in identities:
        if record is None:
            continue
        if record["id"] in seen_ids:
            raise ProfileIssue("ID_DUPLICATE", path + "/id")
        seen_ids.add(record["id"])

    barrier, closure, recovery = (profile[name] for name in ("barrier", "closure", "recovery"))
    if barrier is not None and closure is not None and barrier["order"] >= closure["order"]:
        raise ProfileIssue("ORDER", "/closure/order")
    previous = None
    for index, record in enumerate(profile["records"]):
        order = record["order"]
        if ((previous is not None and order <= previous)
                or (barrier is not None and order <= barrier["order"])
                or (closure is not None and order >= closure["order"])):
            raise ProfileIssue("ORDER", f"/records/{index}/order")
        previous = order
    if recovery is not None:
        if recovery["consumer"] != profile["consumer"]:
            raise ProfileIssue("RECOVERY_CONTEXT", "/recovery/consumer")
        if barrier is not None and recovery["order"] >= barrier["order"]:
            raise ProfileIssue("RECOVERY_CONTEXT", "/recovery/order")

    gap_kinds = set()
    for index, gap in enumerate(profile["gaps"]):
        if gap["kind"] in gap_kinds:
            raise ProfileIssue("COVERAGE_GAP", f"/gaps/{index}/kind")
        gap_kinds.add(gap["kind"])
    for kind in ("P", "Q"):
        if profile["coverage"][kind]["complete"] == (kind in gap_kinds):
            raise ProfileIssue("COVERAGE_GAP", f"/coverage/{kind}")


def validate_profile(profile):
    """Return first reproducible inconsistency or the exact missing-premise set.

    Structural inconsistency takes precedence over unresolved evidence. A single
    diagnostic suffices for I; all applicable missing premises are listed for U.
    """
    try:
        _validate_structure(profile)
        _validate_relations(profile)
    except ProfileIssue as error:
        return {"issues": [error.diagnostic], "missing": []}

    missing = []
    for field, code in (("domain", "DOMAIN"), ("barrier", "BARRIER"), ("closure", "CLOSURE")):
        if profile[field] is None:
            missing.append({"code": code, "path": f"/{field}"})
    recovery = profile["recovery"]
    if recovery is not None and not recovery["witness"]:
        missing.append({"code": "RECOVERY_WITNESS", "path": "/recovery/witness"})
    for kind in ("P", "Q"):
        coverage = profile["coverage"][kind]
        if coverage["complete"] and not coverage["witness"]:
            missing.append({"code": "COVERAGE_WITNESS", "path": f"/coverage/{kind}/witness"})
    for index, gap in enumerate(profile["gaps"]):
        if gap["lower"] > 0 and not gap["witness"]:
            missing.append({"code": "GAP_WITNESS", "path": f"/gaps/{index}/witness"})
    return {"issues": [], "missing": sorted(missing, key=lambda item: (item["path"], item["code"]))}


def canonical_bytes(value):
    """Encode the frozen profile digest representation; reject nonfinite values."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def profile_digest(profile):
    return hashlib.sha256(canonical_bytes(profile)).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Nonfinite JSON constant: {value}")


def load_json(path):
    """Parse JSON without duplicate keys or nonfinite numbers."""
    with Path(path).open(encoding="utf-8") as source:
        value = json.load(source, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    canonical_bytes(value)  # Also rejects exponent overflow to Infinity and invalid UTF-8 strings.
    return value


def atomic_write_json(path, value):
    """Replace output only after a complete, flushed serialization succeeds."""
    target = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=target.parent, prefix=f".{target.name}.",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
