"""Safely package and re-verify a completed prospective capture pair.

This utility does not create market data. It only bundles a previously VALID
capture after verifying its manifest, provenance, hashes, individual datasets,
and the common accepted UTC window again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .prospective_book_ticker_capture import (
    DEFAULT_DURATION_SECONDS,
    ProspectiveBookTickerCaptureError,
    validate_paired_manifests,
)
from .prospective_capture_pair import SCHEMA_VERSION as PAIR_SCHEMA_VERSION

_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_RUN_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_BUNDLE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_PAIR_MANIFEST_BYTES = 2 * 1024 * 1024


class CaptureBundleError(ValueError):
    """The capture bundle could not be independently accepted."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_file(root: Path, raw: Any, field: str) -> Path:
    if type(raw) is not str or not raw.strip():
        raise CaptureBundleError(f"{field}_PATH_INVALID")
    normalized = raw.replace("\\", "/")
    posix = PurePosixPath(normalized)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise CaptureBundleError(f"{field}_PATH_UNSAFE")
    candidate = root.joinpath(*posix.parts)
    current = root
    for part in posix.parts:
        current = current / part
        if current.is_symlink():
            raise CaptureBundleError(f"{field}_SYMLINK_NOT_ALLOWED")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise CaptureBundleError(f"{field}_PATH_OUTSIDE_OR_MISSING") from exc
    if not resolved.is_file():
        raise CaptureBundleError(f"{field}_FILE_REQUIRED")
    return resolved


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"NONFINITE_JSON_CONSTANT:{token}")),
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise CaptureBundleError(f"{label}_UNREADABLE") from exc
    if not isinstance(value, Mapping):
        raise CaptureBundleError(f"{label}_MUST_BE_OBJECT")
    return dict(value)


def _validate_source_provenance(pair: Mapping[str, Any]) -> None:
    keys = ("git_head_sha", "origin_main_sha", "live_main_sha", "branch", "source_status_entries")
    records = [
        pair.get("source_provenance"),
        pair.get("source_provenance_at_launch"),
        pair.get("source_provenance_at_finish"),
    ]
    if any(not isinstance(record, Mapping) for record in records):
        raise CaptureBundleError("SOURCE_PROVENANCE_SNAPSHOTS_MISSING")
    first = records[0]
    for record in records:
        if record.get("status") != "VALID" or record.get("branch") != "main":
            raise CaptureBundleError("SOURCE_PROVENANCE_NOT_VALID")
        for sha_key in ("git_head_sha", "origin_main_sha", "live_main_sha"):
            if type(record.get(sha_key)) is not str or not _SHA_RE.fullmatch(record[sha_key]):
                raise CaptureBundleError("SOURCE_PROVENANCE_SHA_INVALID")
        if record.get("source_status_entries") != []:
            raise CaptureBundleError("SOURCE_WORKTREE_NOT_CLEAN")

    # Capture start and launch must prove that this frozen source was the exact
    # main revision before streaming began.
    if not (first["git_head_sha"] == first["origin_main_sha"] == first["live_main_sha"]):
        raise CaptureBundleError("SOURCE_PROVENANCE_REVISION_MISMATCH")
    launch = records[1]
    if any(launch.get(key) != first.get(key) for key in keys):
        raise CaptureBundleError("SOURCE_PROVENANCE_CHANGED_BEFORE_CAPTURE_LAUNCH")

    # A year-long capture pins the actual local code SHA, not a promise that
    # nobody can merge unrelated changes to remote main for twelve months.
    # The local HEAD/worktree must remain frozen. Local origin/main and live
    # origin/main at finish are retained as context and may legitimately advance.
    finish = records[2]
    if finish.get("git_head_sha") != first.get("git_head_sha"):
        raise CaptureBundleError("CAPTURE_CODE_REVISION_CHANGED")

def _list_bundle_files(root: Path, expected_files: set[str]) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise CaptureBundleError("SYMLINK_NOT_ALLOWED_IN_CAPTURE_TREE")
        relative = path.relative_to(root).as_posix()
        if path.is_file():
            if path.name.endswith(".tmp"):
                raise CaptureBundleError("TEMPORARY_FILE_IN_CAPTURE_TREE")
            if relative not in expected_files:
                # Evidence folders can contain private operator notes or other
                # unrelated artifacts. Refuse them rather than leaking them.
                raise CaptureBundleError("UNEXPECTED_CAPTURE_FILE")
            files.append(path)
    actual_files = {path.relative_to(root).as_posix() for path in files}
    if actual_files != expected_files:
        raise CaptureBundleError("REQUIRED_CAPTURE_FILE_MISSING")
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _write_deterministic_zip(files: Sequence[Path], root: Path, target: Path) -> None:
    # Fixed ZIP timestamps make the package reproducible for identical file bytes.
    with target.open("xb") as raw_stream:
        with zipfile.ZipFile(
            raw_stream, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for path in files:
                relative_name = path.relative_to(root).as_posix()
                info = zipfile.ZipInfo(relative_name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())


def create_capture_bundle(
    run_dir: Path,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Re-validate a VALID pair and create a ZIP plus a SHA-256 sidecar."""
    raw_root = Path(run_dir)
    if raw_root.is_symlink():
        raise CaptureBundleError("CAPTURE_DIRECTORY_SYMLINK_NOT_ALLOWED")
    try:
        root = raw_root.resolve(strict=True)
    except OSError as exc:
        raise CaptureBundleError("CAPTURE_DIRECTORY_NOT_FOUND") from exc
    if not root.is_dir():
        raise CaptureBundleError("CAPTURE_DIRECTORY_REQUIRED")

    pair_path = root / "pair-manifest.json"
    if pair_path.is_symlink() or not pair_path.is_file():
        raise CaptureBundleError("PAIR_MANIFEST_MISSING")
    pair = _load_json(pair_path, "PAIR_MANIFEST")
    if type(pair.get("schema_version")) is not int or pair.get("schema_version") != PAIR_SCHEMA_VERSION:
        raise CaptureBundleError("PAIR_SCHEMA_INVALID")
    if pair.get("status") != "VALID":
        raise CaptureBundleError("PAIR_STATUS_NOT_VALID")

    configuration = pair.get("configuration")
    if not isinstance(configuration, Mapping):
        raise CaptureBundleError("PAIR_CONFIGURATION_INVALID")
    if type(configuration.get("schema_version")) is not int or configuration.get("schema_version") != PAIR_SCHEMA_VERSION:
        raise CaptureBundleError("PAIR_CONFIGURATION_SCHEMA_INVALID")
    if configuration.get("capture_kind") != "PROSPECTIVE_BINANCE_SPOT_KLINE_AND_BOOK_TICKER_PAIR":
        raise CaptureBundleError("PAIR_CAPTURE_KIND_INVALID")
    if configuration.get("symbols") != ["BTCUSDT", "ETHUSDT"]:
        raise CaptureBundleError("PAIR_SYMBOLS_INVALID")
    minimum_overlap = configuration.get("minimum_overlap_seconds")
    if type(minimum_overlap) is not int or minimum_overlap < DEFAULT_DURATION_SECONDS:
        raise CaptureBundleError("PAIR_MINIMUM_OVERLAP_INVALID")
    if configuration.get("trading_enabled") is not False:
        raise CaptureBundleError("TRADING_MUST_REMAIN_DISABLED")
    if pair.get("configuration_sha256") != hashlib.sha256(_canonical_json(configuration)).hexdigest():
        raise CaptureBundleError("PAIR_CONFIGURATION_HASH_MISMATCH")
    if pair.get("source_provenance") != configuration.get("source_provenance"):
        raise CaptureBundleError("PAIR_SOURCE_PROVENANCE_MISMATCH")
    _validate_source_provenance(pair)

    kline_path = _safe_relative_file(root, pair.get("kline_manifest"), "KLINE_MANIFEST")
    bookticker_path = _safe_relative_file(root, pair.get("bookticker_manifest"), "BOOKTICKER_MANIFEST")
    logs = pair.get("logs")
    if not isinstance(logs, Mapping):
        raise CaptureBundleError("PAIR_LOG_INDEX_INVALID")
    for key in ("kline", "bookticker"):
        expected = f"logs/{key}.log"
        if logs.get(key) != expected:
            raise CaptureBundleError("PAIR_LOG_PATH_INVALID")
        _safe_relative_file(root, logs.get(key), f"{key.upper()}_LOG")

    stored_validation = pair.get("validation")
    if not isinstance(stored_validation, Mapping) or stored_validation.get("status") != "VALID":
        raise CaptureBundleError("PAIR_VALIDATION_NOT_VALID")

    try:
        recomputed = validate_paired_manifests(
            kline_manifest_path=kline_path,
            bookticker_manifest_path=bookticker_path,
            minimum_overlap_seconds=minimum_overlap,
        )
    except (OSError, ValueError, ProspectiveBookTickerCaptureError) as exc:
        raise CaptureBundleError(f"INDEPENDENT_PAIR_VALIDATION_FAILED:{type(exc).__name__}:{exc}") from exc
    if recomputed.get("status") != "VALID":
        raise CaptureBundleError("INDEPENDENT_PAIR_VALIDATION_NOT_VALID")
    for key in (
        "status",
        "overlap_started_at",
        "overlap_ended_at",
        "overlap_seconds",
        "minimum_overlap_seconds",
        "common_complete_utc_days",
    ):
        if stored_validation.get(key) != recomputed.get(key):
            raise CaptureBundleError(f"STORED_PAIR_VALIDATION_MISMATCH:{key}")

    expected_files = {
        "pair-manifest.json",
        pair.get("kline_manifest"),
        pair.get("bookticker_manifest"),
        "logs/kline.log",
        "logs/bookticker.log",
    }
    kline_relative_dir = kline_path.parent.relative_to(root).as_posix()
    bookticker_relative_dir = bookticker_path.parent.relative_to(root).as_posix()
    for symbol in ("BTCUSDT", "ETHUSDT"):
        expected_files.add(f"{kline_relative_dir}/{symbol}.jsonl")
        expected_files.add(f"{bookticker_relative_dir}/{symbol}-bookTicker.jsonl")
    files = _list_bundle_files(root, expected_files)

    raw_output = Path(output_path) if output_path is not None else root.parent / f"{root.name}-evidence.zip"
    if raw_output.is_symlink():
        raise CaptureBundleError("BUNDLE_OUTPUT_SYMLINK_NOT_ALLOWED")
    destination = raw_output.resolve(strict=False)
    sidecar = Path(str(destination) + ".sha256")
    if destination == root or root in destination.parents:
        raise CaptureBundleError("BUNDLE_OUTPUT_MUST_BE_OUTSIDE_CAPTURE_DIRECTORY")
    if destination.suffix.lower() != ".zip":
        raise CaptureBundleError("BUNDLE_OUTPUT_MUST_BE_ZIP")
    if not destination.parent.is_dir():
        raise CaptureBundleError("BUNDLE_OUTPUT_PARENT_MISSING")
    if destination.exists() or sidecar.exists():
        raise CaptureBundleError("BUNDLE_OUTPUT_EXISTS_REFUSING_OVERWRITE")
    temporary = destination.with_name(destination.name + ".tmp")
    sidecar_temporary = sidecar.with_name(sidecar.name + ".tmp")
    if temporary.exists() or sidecar_temporary.exists():
        raise CaptureBundleError("BUNDLE_TEMPORARY_OUTPUT_EXISTS")

    archive_created = False
    sidecar_created = False
    try:
        _write_deterministic_zip(files, root, temporary)
        with temporary.open("rb") as source, destination.open("xb") as sink:
            archive_created = True
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                sink.write(chunk)
            sink.flush()
            os.fsync(sink.fileno())
        temporary.unlink()
        archive_sha256 = _sha256_file(destination)
        with sidecar_temporary.open("x", encoding="ascii", newline="\n") as stream:
            stream.write(f"{archive_sha256}  {destination.name}\n")
            stream.flush()
            os.fsync(stream.fileno())
        with sidecar_temporary.open("rb") as source, sidecar.open("xb") as sink:
            sidecar_created = True
            for chunk in iter(lambda: source.read(4096), b""):
                sink.write(chunk)
            sink.flush()
            os.fsync(sink.fileno())
        sidecar_temporary.unlink()
    except Exception:
        for cleanup in (temporary, sidecar_temporary):
            try:
                cleanup.unlink(missing_ok=True)
            except OSError:
                pass
        if archive_created:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        if sidecar_created:
            try:
                sidecar.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    return {
        "status": "VALID",
        "run_id": pair.get("run_id"),
        "archive": str(destination),
        "sha256_file": str(sidecar),
        "sha256": archive_sha256,
        "size_bytes": destination.stat().st_size,
        "files_included": len(files),
        "minimum_overlap_seconds": minimum_overlap,
        "overlap_seconds": recomputed.get("overlap_seconds"),
        "common_complete_utc_days": recomputed.get("common_complete_utc_days"),
        "trading_enabled": False,
    }



def _safe_bundle_manifest_name(raw: Any, top_dir: str, label: str) -> str:
    if type(raw) is not str or not raw or "\\" in raw:
        raise CaptureBundleError(f"{label}_PATH_UNSAFE")
    path = PurePosixPath(raw)
    parts = path.parts
    if (
        path.is_absolute()
        or path.as_posix() != raw
        or len(parts) != 3
        or parts[0] != top_dir
        or parts[2] != "manifest.json"
        or not _RUN_COMPONENT_RE.fullmatch(parts[1])
    ):
        raise CaptureBundleError(f"{label}_PATH_UNSAFE")
    return raw


def _expected_bundle_files_from_manifest(raw: bytes) -> set[str]:
    try:
        pair = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"NONFINITE_JSON_CONSTANT:{token}")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CaptureBundleError("PAIR_MANIFEST_UNREADABLE_IN_BUNDLE") from exc
    if not isinstance(pair, Mapping):
        raise CaptureBundleError("PAIR_MANIFEST_MUST_BE_OBJECT_IN_BUNDLE")
    kline_manifest = _safe_bundle_manifest_name(pair.get("kline_manifest"), "kline", "KLINE_MANIFEST")
    bookticker_manifest = _safe_bundle_manifest_name(
        pair.get("bookticker_manifest"), "bookticker", "BOOKTICKER_MANIFEST"
    )
    logs = pair.get("logs")
    if not isinstance(logs, Mapping) or logs.get("kline") != "logs/kline.log" or logs.get("bookticker") != "logs/bookticker.log":
        raise CaptureBundleError("PAIR_LOG_PATH_INVALID")
    kline_dir = PurePosixPath(kline_manifest).parent.as_posix()
    bookticker_dir = PurePosixPath(bookticker_manifest).parent.as_posix()
    expected = {
        "pair-manifest.json",
        kline_manifest,
        bookticker_manifest,
        "logs/kline.log",
        "logs/bookticker.log",
    }
    for symbol in ("BTCUSDT", "ETHUSDT"):
        expected.add(f"{kline_dir}/{symbol}.jsonl")
        expected.add(f"{bookticker_dir}/{symbol}-bookTicker.jsonl")
    return expected


def verify_capture_bundle(
    archive_path: Path,
    *,
    sidecar_path: Path | None = None,
) -> dict[str, Any]:
    """Verify a received ZIP and SHA-256 sidecar, then revalidate its capture."""
    raw_archive = Path(archive_path)
    if raw_archive.is_symlink():
        raise CaptureBundleError("BUNDLE_ARCHIVE_SYMLINK_NOT_ALLOWED")
    try:
        archive_path_resolved = raw_archive.resolve(strict=True)
    except OSError as exc:
        raise CaptureBundleError("BUNDLE_ARCHIVE_NOT_FOUND") from exc
    if not archive_path_resolved.is_file():
        raise CaptureBundleError("BUNDLE_ARCHIVE_FILE_REQUIRED")

    raw_sidecar = Path(sidecar_path) if sidecar_path is not None else Path(str(archive_path_resolved) + ".sha256")
    if raw_sidecar.is_symlink():
        raise CaptureBundleError("BUNDLE_SIDECAR_SYMLINK_NOT_ALLOWED")
    try:
        sidecar_resolved = raw_sidecar.resolve(strict=True)
        sidecar_text = sidecar_resolved.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise CaptureBundleError("BUNDLE_SHA256_SIDECAR_UNREADABLE") from exc
    if not sidecar_resolved.is_file():
        raise CaptureBundleError("BUNDLE_SHA256_SIDECAR_REQUIRED")
    sidecar_match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)\n", sidecar_text)
    if sidecar_match is None or sidecar_match.group(2) != archive_path_resolved.name:
        raise CaptureBundleError("BUNDLE_SHA256_SIDECAR_FORMAT_INVALID")
    expected_sha256 = sidecar_match.group(1)
    actual_sha256 = _sha256_file(archive_path_resolved)
    if actual_sha256 != expected_sha256:
        raise CaptureBundleError("BUNDLE_SHA256_MISMATCH")

    try:
        with zipfile.ZipFile(archive_path_resolved, mode="r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise CaptureBundleError("DUPLICATE_BUNDLE_MEMBER")
            for info in infos:
                name = info.filename
                if (
                    not name
                    or "\\" in name
                    or PurePosixPath(name).is_absolute()
                    or PurePosixPath(name).as_posix() != name
                    or any(part in {"", ".", ".."} for part in PurePosixPath(name).parts)
                ):
                    raise CaptureBundleError("ZIP_MEMBER_PATH_UNSAFE")
                if info.is_dir() or info.create_system != 3:
                    raise CaptureBundleError("ZIP_MEMBER_NOT_REGULAR_FILE")
                unix_mode = info.external_attr >> 16
                if stat.S_IFMT(unix_mode) != stat.S_IFREG:
                    raise CaptureBundleError("ZIP_MEMBER_NOT_REGULAR_FILE")
                if info.flag_bits & 0x1:
                    raise CaptureBundleError("ENCRYPTED_ZIP_MEMBER_NOT_ALLOWED")
                if type(info.file_size) is not int or info.file_size < 0:
                    raise CaptureBundleError("ZIP_MEMBER_SIZE_INVALID")
            pair_infos = [info for info in infos if info.filename == "pair-manifest.json"]
            if len(pair_infos) != 1:
                raise CaptureBundleError("PAIR_MANIFEST_MISSING_IN_BUNDLE")
            if pair_infos[0].file_size > MAX_PAIR_MANIFEST_BYTES:
                raise CaptureBundleError("PAIR_MANIFEST_SIZE_LIMIT_EXCEEDED")
            expected_files = _expected_bundle_files_from_manifest(archive.read(pair_infos[0]))
            actual_files = set(names)
            if actual_files - expected_files:
                raise CaptureBundleError("UNEXPECTED_CAPTURE_FILE_IN_BUNDLE")
            if expected_files - actual_files:
                raise CaptureBundleError("REQUIRED_CAPTURE_FILE_MISSING_IN_BUNDLE")
            if len(infos) != len(expected_files):
                raise CaptureBundleError("BUNDLE_MEMBER_COUNT_INVALID")
            total_size = sum(info.file_size for info in infos)
            if total_size > MAX_BUNDLE_UNCOMPRESSED_BYTES:
                raise CaptureBundleError("BUNDLE_UNCOMPRESSED_SIZE_LIMIT_EXCEEDED")

            with tempfile.TemporaryDirectory(prefix="bot-cripto-capture-verify-") as temporary_dir:
                temporary_root = Path(temporary_dir)
                extraction_root = temporary_root / "capture"
                extraction_root.mkdir()
                for info in infos:
                    destination = extraction_root.joinpath(*PurePosixPath(info.filename).parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, mode="r") as source, destination.open("xb") as sink:
                        while True:
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            sink.write(chunk)
                        sink.flush()
                        os.fsync(sink.fileno())
                revalidated = create_capture_bundle(
                    extraction_root,
                    output_path=temporary_root / "verification.zip",
                )
                if revalidated.get("status") != "VALID":
                    raise CaptureBundleError("RECEIVED_BUNDLE_REVALIDATION_NOT_VALID")
                return {
                    "status": "VALID",
                    "archive": str(archive_path_resolved),
                    "sha256_file": str(sidecar_resolved),
                    "sha256": actual_sha256,
                    "files_verified": len(infos),
                    "uncompressed_size_bytes": total_size,
                    "run_id": revalidated.get("run_id"),
                    "minimum_overlap_seconds": revalidated.get("minimum_overlap_seconds"),
                    "overlap_seconds": revalidated.get("overlap_seconds"),
                    "common_complete_utc_days": revalidated.get("common_complete_utc_days"),
                    "trading_enabled": False,
                }
    except CaptureBundleError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, ValueError) as exc:
        raise CaptureBundleError(f"BUNDLE_ARCHIVE_INVALID:{type(exc).__name__}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify and package an accepted prospective Kline + bookTicker capture; no trading."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="re-validate a pair and create a ZIP plus SHA-256 sidecar")
    create.add_argument("--run-dir", type=Path, required=True)
    create.add_argument("--output", type=Path, default=None)
    verify = subparsers.add_parser("verify", help="verify a received ZIP/sidecar and independently revalidate the capture")
    verify.add_argument("--archive", type=Path, required=True)
    verify.add_argument("--sidecar", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_capture_bundle(args.run_dir, output_path=args.output)
        else:
            result = verify_capture_bundle(args.archive, sidecar_path=args.sidecar)
    except (CaptureBundleError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": f"{type(exc).__name__}:{exc}"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result.get("status") == "VALID" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CaptureBundleError", "create_capture_bundle", "verify_capture_bundle", "main"]