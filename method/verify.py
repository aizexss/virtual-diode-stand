"""Public, independent checker for floor-explanation/v1 artifacts.

Only Python's standard library is imported. The checker validates public input
constraints itself and checks the supplied constructive evidence directly. It
does not call the producer, its parser/validator, or a shared verdict function.
Trust in binding, order, coverage and assurance witnesses remains a premise.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile


PROFILE_KEYS = {"schema", "run", "consumer", "domain", "barrier", "closure", "recovery", "records", "coverage", "gaps", "provenance"}
ARTIFACT_KEYS = {"schema", "profile_sha256", "verdict", "scope", "context", "payload"}


def _integer(value):
    return type(value) is int


def _nonempty(value):
    return isinstance(value, str) and len(value) > 0


def _inspect(profile):
    """Reproduce schema failures and unresolved premises, independently.

    Malformed child containers are not traversed. I certificates need only name
    nonempty, actually reproduced constraints; unresolved certificates must list
    every applicable missing premise in an otherwise structurally valid input.
    """
    issues, missing = [], []

    def add(code, path):
        issue = {"code": code, "path": path}
        if issue not in issues:
            issues.append(issue)

    def absent(code, path):
        missing.append({"code": code, "path": path})

    def shape(value, keys, path):
        if not isinstance(value, dict):
            add("TYPE", path)
            return False
        if set(value) != set(keys):
            add("SCHEMA", path)
            return False
        return True

    def identity(value, path):
        if not isinstance(value, str):
            add("TYPE", path)
            return False
        if not value:
            add("TYPE", path)
            return False
        return True

    def number(value, path):
        if not _integer(value):
            add("TYPE", path)
            return False
        if value < 0:
            add("RANGE", path)
            return False
        return True

    def witness(value, path):
        if value is not None and not isinstance(value, str):
            add("TYPE", path)
            return False
        return True

    if not shape(profile, PROFILE_KEYS, ""):
        return issues, missing
    if not isinstance(profile["schema"], str):
        add("TYPE", "/schema")
    elif profile["schema"] != "floor-explanation/v1":
        add("SCHEMA", "/schema")
    for key in ("run", "consumer", "provenance"):
        identity(profile[key], "/" + key)
    if profile["domain"] is None:
        absent("DOMAIN", "/domain")
    else:
        identity(profile["domain"], "/domain")

    known_orders = {}
    seen_ids = set()

    def unique(value, path):
        if identity(value, path):
            if value in seen_ids:
                add("ID_DUPLICATE", path)
            seen_ids.add(value)

    for key, fields in (("barrier", {"id", "order", "floor"}), ("closure", {"id", "order"})):
        value = profile[key]
        path = "/" + key
        if value is None:
            absent(key.upper(), path)
        elif shape(value, fields, path):
            unique(value["id"], path + "/id")
            if number(value["order"], path + "/order"):
                known_orders[key] = value["order"]
            if key == "barrier":
                number(value["floor"], "/barrier/floor")
    if "barrier" in known_orders and "closure" in known_orders and known_orders["barrier"] >= known_orders["closure"]:
        add("ORDER", "/closure/order")

    recovery = profile["recovery"]
    if recovery is not None and shape(recovery, {"id", "order", "consumer", "witness"}, "/recovery"):
        unique(recovery["id"], "/recovery/id")
        order_ok = number(recovery["order"], "/recovery/order")
        identity(recovery["consumer"], "/recovery/consumer")
        if recovery["consumer"] != profile["consumer"]:
            add("RECOVERY_CONTEXT", "/recovery/consumer")
        if order_ok and "barrier" in known_orders and recovery["order"] >= known_orders["barrier"]:
            add("RECOVERY_CONTEXT", "/recovery/order")
        if witness(recovery["witness"], "/recovery/witness") and not _nonempty(recovery["witness"]):
            absent("RECOVERY_WITNESS", "/recovery/witness")

    records = profile["records"]
    if not isinstance(records, list):
        add("TYPE", "/records")
    else:
        previous = None
        for index, row in enumerate(records):
            path = "/records/" + str(index)
            if not shape(row, {"id", "order", "kind", "candidates", "binding"}, path):
                continue
            unique(row["id"], path + "/id")
            identity(row["binding"], path + "/binding")
            if number(row["order"], path + "/order"):
                order = row["order"]
                if (previous is not None and order <= previous) or ("barrier" in known_orders and order <= known_orders["barrier"]) or ("closure" in known_orders and order >= known_orders["closure"]):
                    add("ORDER", path + "/order")
                previous = order
            if not _nonempty(row["kind"]):
                add("TYPE", path + "/kind")
            elif row["kind"] not in ("P", "Q"):
                add("SCHEMA", path + "/kind")
            candidates = row["candidates"]
            if not isinstance(candidates, list):
                add("TYPE", path + "/candidates")
            else:
                clean = True
                for item_index, value in enumerate(candidates):
                    if not number(value, path + "/candidates/" + str(item_index)):
                        clean = False
                if not candidates or (clean and any(left >= right for left, right in zip(candidates, candidates[1:]))):
                    add("CANDIDATES", path + "/candidates")

    coverage = profile["coverage"]
    coverage_ok = shape(coverage, {"P", "Q"}, "/coverage")
    family_complete = {}
    if coverage_ok:
        for kind in ("P", "Q"):
            entry, path = coverage[kind], "/coverage/" + kind
            if not shape(entry, {"complete", "witness"}, path):
                continue
            if type(entry["complete"]) is not bool:
                add("TYPE", path + "/complete")
            else:
                family_complete[kind] = entry["complete"]
            if witness(entry["witness"], path + "/witness") and entry["complete"] is True and not _nonempty(entry["witness"]):
                absent("COVERAGE_WITNESS", path + "/witness")

    gaps = profile["gaps"]
    counts = {"P": 0, "Q": 0}
    if not isinstance(gaps, list):
        add("TYPE", "/gaps")
    else:
        for index, gap in enumerate(gaps):
            path = "/gaps/" + str(index)
            if not shape(gap, {"kind", "lower", "witness"}, path):
                continue
            kind = gap["kind"]
            if not _nonempty(kind):
                add("TYPE", path + "/kind")
            elif kind not in counts:
                add("SCHEMA", path + "/kind")
            else:
                counts[kind] += 1
                if counts[kind] > 1:
                    add("COVERAGE_GAP", path + "/kind")
            lower_ok = number(gap["lower"], path + "/lower")
            if witness(gap["witness"], path + "/witness") and lower_ok and gap["lower"] > 0 and not _nonempty(gap["witness"]):
                absent("GAP_WITNESS", path + "/witness")
        for kind, complete in family_complete.items():
            if counts[kind] != (0 if complete else 1):
                add("COVERAGE_GAP", "/coverage/" + kind)
    return issues, missing


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _same(left, right):
    """JSON equality retains the distinction between booleans and integers."""
    try:
        return _canonical(left) == _canonical(right)
    except (ValueError, TypeError, OverflowError):
        return False


def verify_explanation(profile, artifact):
    """Return {valid: bool, errors: list}; never accepts a claimed message alone."""
    errors = []

    def fail(message):
        errors.append(message)

    try:
        profile_digest = hashlib.sha256(_canonical(profile)).hexdigest()
    except (ValueError, TypeError, OverflowError):
        return {"valid": False, "errors": ["Profile is not a finite JSON value"]}
    issues, missing = _inspect(profile)
    if not isinstance(artifact, dict) or set(artifact) != ARTIFACT_KEYS:
        return {"valid": False, "errors": ["Artifact root must have exactly the frozen keys"]}
    if artifact["schema"] != "floor-explanation-artifact/v1":
        fail("Wrong artifact schema")
    if artifact["profile_sha256"] != profile_digest:
        fail("Public profile digest does not match")
    payload = artifact["payload"]

    def exact_shape(value, keys, label):
        if not isinstance(value, dict) or set(value) != set(keys):
            fail(label + " has wrong keys or type")
            return False
        return True

    def diagnostics(value, expected, exact, label):
        if not isinstance(value, list) or not value:
            fail(label + " must be a nonempty diagnostic list")
            return
        got = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {"code", "path"} or not isinstance(item["code"], str) or not isinstance(item["path"], str):
                fail(label + " contains malformed diagnostics")
                return
            pair = (item["code"], item["path"])
            if pair in got:
                fail(label + " contains duplicate diagnostics")
            got.append(pair)
        actual = {(item["code"], item["path"]) for item in expected}
        if not set(got).issubset(actual) or (exact and set(got) != actual):
            fail(label + " is not independently reproduced")

    if issues:
        if (artifact["verdict"], artifact["scope"], artifact["context"]) != ("I", "INCONSISTENT", "UNRESOLVED"):
            fail("Inconsistent input requires I/INCONSISTENT/UNRESOLVED")
        if exact_shape(payload, {"issues"}, "I payload"):
            diagnostics(payload["issues"], issues, False, "I issues")
        return {"valid": not errors, "errors": errors}
    if missing:
        if (artifact["verdict"], artifact["scope"], artifact["context"]) != ("U", "UNRESOLVED", "UNRESOLVED"):
            fail("Incomplete premises require U/UNRESOLVED/UNRESOLVED")
        if exact_shape(payload, {"missing"}, "Unresolved payload"):
            diagnostics(payload["missing"], missing, True, "Missing premises")
        return {"valid": not errors, "errors": errors}

    if artifact["scope"] != "EXACT":
        fail("Qualified closed input requires EXACT scope")
    context = "POST_RECOVERY" if profile["recovery"] is not None else "POST_BARRIER"
    if artifact["context"] != context:
        fail("Claimed recovery context does not match public premises")
    floor = profile["barrier"]["floor"]
    verdict = artifact["verdict"]
    if verdict == "C":
        if exact_shape(payload, {"barrier", "closure", "coverage", "gap_bounds", "record_minima"}, "C payload"):
            for key, public_key in (("barrier", "barrier"), ("closure", "closure"), ("coverage", "coverage"), ("gap_bounds", "gaps")):
                if not _same(payload[key], profile[public_key]):
                    fail("C does not preserve public " + public_key)
            expected = [{"id": row["id"], "lower": row["candidates"][0]} for row in profile["records"]]
            if not _same(payload["record_minima"], expected):
                fail("C record minima omit or misstate an observed record")
            for row in profile["records"]:
                if any(value < floor for value in row["candidates"]):
                    fail("C has an allowed below-floor visible version")
            for gap in profile["gaps"]:
                if gap["lower"] < floor:
                    fail("C has an allowed below-floor hidden version")
    elif verdict == "V":
        if exact_shape(payload, {"barrier_id", "record_id"}, "V payload"):
            if payload["barrier_id"] != profile["barrier"]["id"]:
                fail("V names a different barrier")
            rows = [row for row in profile["records"] if row["id"] == payload["record_id"]]
            if len(rows) != 1 or any(value >= floor for value in rows[0]["candidates"]):
                fail("V does not identify a forced-bad observed record")
    elif verdict == "U":
        if exact_shape(payload, {"safe", "unsafe"}, "U payload"):
            allowed_hidden = {gap["kind"]: gap["lower"] for gap in profile["gaps"]}

            def check_completion(history, label):
                if not exact_shape(history, {"visible", "hidden"}, label):
                    return None
                visible, hidden = history["visible"], history["hidden"]
                if not isinstance(visible, list) or len(visible) != len(profile["records"]) or not isinstance(hidden, list):
                    fail(label + " has incomplete projection or malformed hidden events")
                    return None
                safe = True
                for observed, assignment in zip(profile["records"], visible):
                    if not exact_shape(assignment, {"id", "version"}, label + " visible assignment"):
                        return None
                    version = assignment["version"]
                    if assignment["id"] != observed["id"] or not _integer(version) or version not in observed["candidates"]:
                        fail(label + " visible assignment violates observed membership/order")
                        return None
                    safe = safe and version >= floor
                preceding_slot = 0
                for event in hidden:
                    if not exact_shape(event, {"kind", "slot", "version"}, label + " hidden event"):
                        return None
                    kind, slot, version = event["kind"], event["slot"], event["version"]
                    if not isinstance(kind, str) or kind not in allowed_hidden or not _integer(slot) or not 0 <= slot <= len(visible) or slot < preceding_slot or not _integer(version) or version < allowed_hidden.get(kind, 0):
                        fail(label + " hidden event violates family, slot, order or version premise")
                        return None
                    preceding_slot = slot
                    safe = safe and version >= floor
                return safe

            if check_completion(payload["safe"], "safe completion") is not True:
                fail("Safe completion does not establish the floor predicate")
            if check_completion(payload["unsafe"], "unsafe completion") is not False:
                fail("Unsafe completion does not refute the floor predicate")
    else:
        fail("Qualified input requires a C, V or constructive U artifact")
    return {"valid": not errors, "errors": errors}


def _strict_read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("nonfinite JSON constant: " + value)

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("nonfinite JSON number: " + value)
        return result

    with open(path, "r", encoding="utf-8") as handle:
        result = json.load(handle, object_pairs_hook=pairs, parse_constant=nonfinite, parse_float=finite_float)
    # The frozen wire is canonical UTF-8 JSON: lone surrogate escapes cannot
    # encode to that wire and must fail before any diagnosis is emitted.
    _canonical(result)
    return result


def _write_atomic(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, prefix="." + target.name + ".", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _output_aliases_input(output, inputs):
    target = Path(output)
    resolved_target = os.path.normcase(str(target.resolve()))
    for source in map(Path, inputs):
        if resolved_target == os.path.normcase(str(source.resolve())):
            return True
        if target.exists() and source.exists() and target.samefile(source):
            return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if _output_aliases_input(args.output, (args.profile, args.artifact)):
            print("Output must not alias the public profile or supplied artifact", file=sys.stderr)
            return 2
        profile = _strict_read(args.profile)
        artifact = _strict_read(args.artifact)
    except (ValueError, OSError) as exc:
        print("JSON input rejected: " + str(exc), file=sys.stderr)
        return 2
    result = verify_explanation(profile, artifact)
    _write_atomic(args.output, result)
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
