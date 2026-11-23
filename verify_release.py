"""Offline verification of the public v1.0.0 research artifact."""

from hashlib import sha256
from pathlib import Path, PurePosixPath
from subprocess import CalledProcessError, TimeoutExpired, run
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile
import json
import sys


CORE = "DataDiode_Research_Testbed.zip"
ADDENDUM = "Reproduction_Addendum.zip"
GODIODE = "GoDiode_F04_Offline_Evidence.zip"
EXPECTED = {CORE, ADDENDUM, GODIODE}


def check(condition, detail):
    if not condition:
        raise ValueError(detail)


def unpack(archive_path, target):
    with ZipFile(archive_path) as archive:
        names = archive.namelist()
        check(len(names) == len(set(names)), f"{archive_path.name}: duplicate paths")
        for name in names:
            path = PurePosixPath(name)
            check(
                path.parts and not path.is_absolute() and ".." not in path.parts
                and "\\" not in name and ":" not in name,
                f"{archive_path.name}: invalid path",
            )
        check(archive.testzip() is None, f"{archive_path.name}: ZIP integrity failure")
        archive.extractall(target)


def run_python(folder, script, *arguments):
    run([sys.executable, "-B", script, *map(str, arguments)], cwd=folder, check=True, timeout=240)


def compare_tree(actual, expected, count):
    reproduced = sorted(p.relative_to(actual) for p in actual.rglob("*") if p.is_file())
    saved = sorted(p.relative_to(expected) for p in expected.rglob("*") if p.is_file())
    check(reproduced == saved and len(saved) == count, f"{expected.name}: output inventory mismatch")
    for name in saved:
        check((actual / name).read_bytes() == (expected / name).read_bytes(), f"{name}: output changed")
    print(f"PASS: {expected.name} ({count} files)", flush=True)


def verify(bundle_dir):
    manifest = json.loads((bundle_dir / "ARTIFACT_MANIFEST.json").read_text(encoding="utf-8"))
    members = manifest["archives"]
    check({member["file"] for member in members} == EXPECTED and len(members) == len(EXPECTED), "invalid manifest")
    for member in members:
        content = (bundle_dir / member["file"]).read_bytes()
        check(len(content) == member["bytes"], f"{member['file']}: size mismatch")
        check(sha256(content).hexdigest() == member["sha256"], f"{member['file']}: SHA-256 mismatch")
    print("PASS: archive hashes", flush=True)

    with TemporaryDirectory(prefix="verify-", dir=bundle_dir) as directory:
        work = Path(directory)
        core = bundle_dir / CORE
        unpack(bundle_dir / ADDENDUM, work)
        addendum = work / "Reproduction_Addendum"

        replay = addendum / "replay_below_floor"
        report = work / "replay"
        run_python(replay, "verify_control.py", "--archive", core, "--run", replay / "executed_run01",
                   "--output", report)
        compare_tree(report, replay / "profile_readback", 23)

        ambiguity = addendum / "ambiguous_read"
        report = work / "ambiguity"
        run_python(ambiguity, "verify.py", "--archive", core, "--run", ambiguity / "executed_run01",
                   "--output", report)
        compare_tree(report, ambiguity / "readback", 22)

        contribution = addendum / "contribution_audit"
        report = work / "retained"
        run_python(contribution, "audit_retained_controls.py", "--archive", core, "--output", report)
        compare_tree(report, contribution / "retained_controls", 2)
        report = work / "projected"
        run_python(contribution, "audit_projection.py", "--source", ambiguity, "--output", report)
        compare_tree(report, contribution / "projected_read", 33)

        thresholds = addendum / "threshold_analysis"
        report = work / "thresholds.json"
        run_python(thresholds, "verify_thresholds.py", "--testbed", core, "--addendum", addendum,
                   "--output", report)
        check(report.read_bytes() == (thresholds / "RESULTS.json").read_bytes(), "threshold output changed")
        print("PASS: threshold analysis", flush=True)

        unpack(bundle_dir / GODIODE, work)
        run_python(work / "GoDiode_F04_Offline_Evidence", "verify_archived.py")


if __name__ == "__main__":
    try:
        verify(Path(__file__).resolve().parent)
    except (OSError, ValueError, KeyError, BadZipFile, CalledProcessError, TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: v1.0.0 offline evidence checks.")
