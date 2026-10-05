"""Preparation is an administrative operation; service reads only mounted build/unlocked data."""

import csv
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .models import Instrument
from .util import EngineError, atomic_json, config_root, file_hash, fingerprint

FIELDS = ["open", "high", "low", "close", "volume_base", "volume_quote", "trades"]
TRACKS = {
    "modern": {"split": "2024-10-05T00:00:00Z", "end": "2026-10-05T00:00:00Z"},
    "xmr": {"split": "2022-02-20T00:00:00Z", "end": "2024-02-20T03:00:00Z"},
}
XMR_TERMINAL = "2024-02-20T02:00:00Z"


def prepare(source: Path, destination: Path):
    """Stream partition without evaluating prices; refuse to overwrite any prepared dataset."""
    if destination.exists() and any(destination.glob("build/*/manifest.json")):
        raise EngineError(
            "DATA_EXISTS", "Use a new data directory; prepared datasets are immutable"
        )
    files = sorted(source.glob("*_WITHGAP.csv"))
    if not files:
        raise EngineError("NO_DATA", "No source CSV files found")
    manifests = {}
    snapshot_path = config_root() / "revolut-pairs-eea.json"
    snapshot = (
        json.loads(snapshot_path.read_text(encoding="utf-8"))
        if snapshot_path.exists()
        else {"pairs": {}}
    )
    for track, bounds in TRACKS.items():
        manifests[track] = {"schema_version": "1.0", "track": track, "bounds": bounds, "assets": {}}
    for path in files:
        asset = path.name.removesuffix("_WITHGAP.csv")
        if asset == "HYPEUSDT":
            continue
        if not asset.isalnum() or not asset.endswith("USDT"):
            raise EngineError("BAD_SYMBOL", path.name)
        for track, bounds in TRACKS.items():
            if track == "xmr" and asset != "XMRUSDT":
                continue
            if track == "modern" and asset == "XMRUSDT":
                continue
            handles = []
            counts = {"build": 0, "sealed": 0}
            try:
                writers = {}
                for period in counts:
                    target = destination / period / track / f"{asset}.csv"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    handle = target.open("w", newline="", encoding="utf-8")
                    handles.append(handle)
                    writers[period] = csv.writer(handle)
                    writers[period].writerow(["open_time_utc", *FIELDS])
                with path.open(newline="", encoding="utf-8-sig") as handle:
                    reader = csv.DictReader(handle)
                    if reader.fieldnames != ["open_time_utc", *FIELDS]:
                        raise EngineError("BAD_COLUMNS", path.name)
                    previous = None
                    for row in reader:
                        ts = pd.Timestamp(row["open_time_utc"])
                        if (
                            ts.tzinfo is None
                            or ts != ts.floor("h")
                            or (previous is not None and ts <= previous)
                        ):
                            raise EngineError(
                                "BAD_TIME",
                                f"{asset}: timestamps must be unique ascending UTC hours",
                            )
                        ts = ts.tz_convert("UTC")
                        previous = ts
                        if ts >= pd.Timestamp(bounds["end"]):
                            continue
                        period = "build" if ts < pd.Timestamp(bounds["split"]) else "sealed"
                        writers[period].writerow([ts.isoformat(), *[row[f] for f in FIELDS]])
                        counts[period] += 1
            finally:
                for handle in handles:
                    handle.close()
            pair_name = asset.removesuffix("USDT") + "/USDC"
            pair = snapshot["pairs"].get(pair_name)
            instrument = Instrument()
            constraints_source = "research proxy; not verified venue instrument constraints"
            if pair is not None and pair.get("status") == "active":
                instrument = Instrument(
                    quantity_step=float(pair["base_step"]),
                    min_quantity=float(pair["min_order_size"]),
                    max_quantity=float(pair["max_order_size"]),
                )
                constraints_source = f"Revolut EEA {pair_name} base-quantity limits as of {snapshot['checked_date']}; USDT notional limits are research proxies; no USDC conversion inferred"
            manifests[track]["assets"][asset] = {
                "source_sha256": file_hash(path),
                "terminal_open": XMR_TERMINAL if asset == "XMRUSDT" else None,
                "terminal_assumption": "assumed last-observed-open liquidation"
                if asset == "XMRUSDT"
                else None,
                "instrument": instrument.model_dump(),
                "constraints_source": constraints_source,
                "partitions": {
                    period: {
                        "rows": counts[period],
                        "sha256": file_hash(destination / period / track / f"{asset}.csv"),
                    }
                    for period in counts
                },
            }
    for track, manifest in manifests.items():
        manifest["dataset_id"] = fingerprint(manifest)
        for period in ("build", "sealed"):
            # Build manifest contains no sealed prices, only hash/availability metadata.
            atomic_json(destination / period / track / "manifest.json", manifest)
    (destination / "unlocked").mkdir(parents=True, exist_ok=True)
    return {track: manifest["dataset_id"] for track, manifest in manifests.items()}


def unlock(source: Path, destination: Path, track: str, acknowledge: bool):
    if not acknowledge:
        raise EngineError(
            "ACK_REQUIRED", "Unlock requires explicit --acknowledge-unseen-data-is-spent"
        )
    manifest = json.loads((source / track / "manifest.json").read_text(encoding="utf-8"))
    target = destination / track
    if target.exists():
        raise EngineError("ALREADY_UNLOCKED", "This track is already unlocked", 409)
    import shutil

    shutil.copytree(source / track, target)
    atomic_json(
        target / "unlock.json", {"dataset_id": manifest["dataset_id"], "unseen_data_spent": True}
    )


def validate_prices(frame: pd.DataFrame) -> pd.DataFrame:
    try:
        index = pd.DatetimeIndex(pd.to_datetime(frame.pop("open_time_utc"), utc=True))
        frame = frame[FIELDS].apply(pd.to_numeric, errors="raise")
    except (KeyError, ValueError) as error:
        raise EngineError("BAD_DATA", str(error)) from error
    if (
        index.has_duplicates
        or not index.is_monotonic_increasing
        or not index.equals(index.floor("h"))
    ):
        raise EngineError("BAD_DATA", "Timestamps must be ascending unique hourly opens")
    values = frame.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise EngineError("BAD_DATA", "Nonfinite or nonpositive prices")
    if (frame[["volume_base", "volume_quote", "trades"]] < 0).any().any():
        raise EngineError("BAD_DATA", "Negative activity")
    if (
        (frame.high < frame[["open", "close", "low"]].max(axis=1))
        | (frame.low > frame[["open", "close", "high"]].min(axis=1))
    ).any():
        raise EngineError("BAD_DATA", "Invalid OHLC ordering")
    if (frame.trades != np.floor(frame.trades)).any():
        raise EngineError("BAD_DATA", "Trade counts must be integral")
    frame.index = index
    return frame.astype(float)


def hourly_grid(frame: pd.DataFrame, index: pd.DatetimeIndex, terminal_open=None):
    grid = frame.reindex(index).copy()
    observed = grid.close.notna()
    observed_positions = np.where(observed, np.arange(len(grid)), -1)
    last_position = np.maximum.accumulate(observed_positions)
    age = np.arange(len(grid)) - last_position
    # A later observed row must not decide whether earlier missing bars get filled.
    # Only an explicitly known cessation boundary can terminate synthetic continuity.
    within_history = (index >= frame.index.min()) if len(frame) else np.zeros(len(index), bool)
    if terminal_open is not None:
        within_history &= index <= terminal_open
    synthetic = ~observed & within_history & (last_position >= 0) & (age <= 4)
    previous = grid.close.ffill()
    for field in ("open", "high", "low", "close"):
        grid.loc[synthetic, field] = previous.loc[synthetic]
    grid["observed"] = observed
    grid["synthetic"] = synthetic
    grid["stale_age"] = np.where(last_position >= 0, age, -1)
    grid["valuation"] = previous
    return grid


class DatasetStore:
    def __init__(self, root: Path):
        self.root = root

    def manifest(self, track: str, period: str):
        if track not in TRACKS or period not in {"build", "test"}:
            raise EngineError("BAD_PERIOD", "Unknown track/period")
        base = self.root / ("build" if period == "build" else "unlocked") / track
        if period == "test" and not (base / "unlock.json").exists():
            raise EngineError(
                "SEALED", "User must explicitly unlock this track outside the API", 403
            )
        if not (base / "manifest.json").exists():
            raise EngineError("DATA_NOT_PREPARED", "Prepare the dataset first", 409)
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        original = dict(manifest)
        dataset_id = original.pop("dataset_id")
        if fingerprint(original) != dataset_id:
            raise EngineError("DATA_CHANGED", "Manifest fingerprint mismatch", 409)
        if period == "test":
            receipt = json.loads((base / "unlock.json").read_text(encoding="utf-8"))
            if (
                receipt.get("dataset_id") != dataset_id
                or receipt.get("unseen_data_spent") is not True
            ):
                raise EngineError("SEALED", "Invalid unlock receipt", 403)
        return base, manifest

    @lru_cache(maxsize=64)
    def _read(self, filename: str, digest: str, size: int, mtime: int):
        path = Path(filename)
        if file_hash(path) != digest:
            raise EngineError("DATA_CHANGED", "CSV fingerprint mismatch", 409)
        return validate_prices(pd.read_csv(path))

    def load(self, track: str, period: str, assets: list[str], start=None, end=None):
        base, manifest = self.manifest(track, period)
        split = pd.Timestamp(manifest["bounds"]["split"])
        boundary = pd.Timestamp(manifest["bounds"]["end"]) if period == "test" else split
        lower = split if period == "test" else None
        start = pd.Timestamp(start).tz_convert("UTC") if start else lower
        end = pd.Timestamp(end).tz_convert("UTC") if end else boundary
        if end > boundary or (lower is not None and start < lower):
            raise EngineError("SEALED_RANGE", "Request exceeds permitted period", 403)
        frames, instruments, terminal = {}, {}, {}
        for asset in assets:
            if asset not in manifest["assets"]:
                raise EngineError("ASSET_UNAVAILABLE", f"{asset} is unavailable in {track}")
            metadata = manifest["assets"][asset]
            path = base / f"{asset}.csv"
            stat = path.stat()
            partition = "sealed" if period == "test" else "build"
            frame = self._read(
                str(path),
                metadata["partitions"][partition]["sha256"],
                stat.st_size,
                stat.st_mtime_ns,
            )
            # For test warmup read only this track's permitted build data.
            if period == "test":
                warm, _, _ = self.load(track, "build", [asset])[:3]
                frame = pd.concat([warm[asset], frame])
            frames[asset] = frame.loc[frame.index < end].copy()
            instruments[asset] = Instrument.model_validate(metadata["instrument"])
            if metadata["terminal_open"] is not None:
                terminal[asset] = pd.Timestamp(metadata["terminal_open"])
        if start is None:
            available = [f.index.min() for f in frames.values() if len(f)]
            if not available:
                raise EngineError("NO_DATA", "No observations in requested period")
            start = min(available)
        if start >= end:
            raise EngineError("NO_DATA", "Empty evaluation interval")
        return frames, instruments, terminal, start, end, manifest
