"""Construct premise-explicit recovery-floor explanations from public evidence.

This producer neither authenticates public premises nor implements a transport.
Exactness concerns only the factorized model specified in SPEC.md.
"""

import argparse
import copy
from pathlib import Path
import sys

if __package__:
    from .profile import atomic_write_json, load_json, profile_digest, validate_profile
else:
    from profile import atomic_write_json, load_json, profile_digest, validate_profile


ARTIFACT_SCHEMA = "floor-explanation-artifact/v1"


def explain(profile):
    """Return an artifact without changing the supplied valid JSON value."""
    digest = profile_digest(profile)
    validation = validate_profile(profile)
    artifact = {
        "schema": ARTIFACT_SCHEMA, "profile_sha256": digest,
        "verdict": "I", "scope": "INCONSISTENT", "context": "UNRESOLVED",
        "payload": {"issues": validation["issues"]},
    }
    if validation["issues"]:
        return artifact
    if validation["missing"]:
        artifact.update(verdict="U", scope="UNRESOLVED",
                        payload={"missing": validation["missing"]})
        return artifact

    artifact["scope"] = "EXACT"
    artifact["context"] = "POST_RECOVERY" if profile["recovery"] is not None else "POST_BARRIER"
    records = profile["records"]
    floor = profile["barrier"]["floor"]
    # Candidates are validated sorted/nonempty, so endpoints give exact bounds.
    forced_bad = next((record for record in records if record["candidates"][-1] < floor), None)
    if forced_bad is not None:
        artifact.update(verdict="V", payload={"barrier_id": profile["barrier"]["id"],
                                              "record_id": forced_bad["id"]})
        return artifact

    ambiguous_record = next((record for record in records if record["candidates"][0] < floor), None)
    unsafe_gap = next((gap for gap in profile["gaps"] if gap["lower"] < floor), None)
    if ambiguous_record is None and unsafe_gap is None:
        artifact.update(verdict="C", payload={
            "barrier": copy.deepcopy(profile["barrier"]),
            "closure": copy.deepcopy(profile["closure"]),
            "coverage": copy.deepcopy(profile["coverage"]),
            "gap_bounds": copy.deepcopy(profile["gaps"]),
            "record_minima": [{"id": record["id"], "lower": record["candidates"][0]}
                              for record in records],
        })
        return artifact

    # No forced-bad record exists: each visible set contains a safe candidate.
    safe = {"visible": [{"id": record["id"],
                         "version": next(version for version in record["candidates"] if version >= floor)}
                        for record in records], "hidden": []}
    unsafe = copy.deepcopy(safe)
    if ambiguous_record is not None:
        for assignment in unsafe["visible"]:
            if assignment["id"] == ambiguous_record["id"]:
                assignment["version"] = ambiguous_record["candidates"][0]
                break
    else:
        unsafe["hidden"].append({"kind": unsafe_gap["kind"], "slot": 0,
                                 "version": unsafe_gap["lower"]})
    artifact.update(verdict="U", payload={"safe": safe, "unsafe": unsafe})
    return artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Public profile JSON input")
    parser.add_argument("--output", required=True, help="Explanation JSON output")
    args = parser.parse_args(argv)
    try:
        profile_path = Path(args.profile)
        output_path = Path(args.output)
        if (profile_path.resolve() == output_path.resolve()
                or (profile_path.exists() and output_path.exists()
                    and profile_path.samefile(output_path))):
            raise ValueError("Output must not alias the public profile input")
        profile = load_json(args.profile)
        artifact = explain(profile)
        atomic_write_json(args.output, artifact)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError) as error:
        print(f"Explanation not written: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
