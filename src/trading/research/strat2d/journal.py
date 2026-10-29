"""Immutable atomic transactions for model, cursor, simulation and event evidence."""

import hashlib
import json
import os
import re
import uuid
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def logical_hash(value):
    digest = hashlib.sha256()
    for part in json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(
        value
    ):
        digest.update(part.encode())
    return digest.hexdigest()


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        for part in json.JSONEncoder(
            sort_keys=True, separators=(",", ":"), allow_nan=False
        ).iterencode(value):
            handle.write(part)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


class Journal:
    """The directory rename is the commit; there is no mutable checkpoint pointer.

    One caller must hold the output OS lock. Committed transactions are never
    rewritten. A killed staging directory is preserved but ignored on restart.
    """

    def __init__(self, output, manifest, initial, guard=lambda: None):
        self.output, self.guard = Path(output), guard
        self.identity = logical_hash(manifest)
        self.output.mkdir(parents=True, exist_ok=True)
        frozen = self.output / "manifest.json"
        if frozen.exists():
            try:
                existing = json.loads(frozen.read_text("utf-8"))
            except (OSError, ValueError):
                raise ValueError("STRAT-2D manifest corrupt") from None
            if existing != manifest:
                raise ValueError("STRAT-2D manifest/config/code/environment mismatch")
        elif any(self.output.glob("[0-9]*")):
            raise ValueError("STRAT-2D manifest missing beside committed evidence")
        else:
            temporary = self.output / f".manifest-{uuid.uuid4().hex}"
            write_new(temporary, manifest)
            os.rename(temporary, frozen)
            sync_directory(self.output)
        self.generation, self.previous, self.state = -1, None, initial
        names = sorted(p.name for p in self.output.iterdir() if re.fullmatch(r"\d{8}", p.name))
        if len(names) > 10000:
            raise ValueError("Transaction metadata budget exceeded")
        for number, name in enumerate(names):
            self.guard()
            if name != f"{number:08d}":
                raise ValueError("STRAT-2D missing transaction in receipt chain")
            directory = self.output / name
            if directory.is_symlink() or directory.is_junction():
                raise ValueError("STRAT-2D transaction alias forbidden")
            try:
                receipt = json.loads((directory / "receipt.json").read_text("utf-8"))
                if (
                    receipt["identity"] != self.identity
                    or receipt["generation"] != number
                    or receipt["previous"] != self.previous
                    or set(receipt["files"]) != {"state.json", "events.jsonl"}
                ):
                    raise ValueError
                for filename, expected in receipt["files"].items():
                    if file_hash(directory / filename) != expected:
                        raise ValueError
                state = json.loads((directory / "state.json").read_text("utf-8"))
                if logical_hash(state) != receipt["logical_state_hash"]:
                    raise ValueError
            except (OSError, ValueError, KeyError, TypeError):
                raise ValueError(
                    "STRAT-2D committed transaction corrupt; evidence preserved"
                ) from None
            self.state, self.generation = state, number
            self.previous = file_hash(directory / "receipt.json")

    def commit(self, state, events, fault=None):
        self.guard()
        generation = self.generation + 1
        if generation >= 10000:
            raise ValueError("Transaction budget exceeded")
        staging = self.output / f".pending-{uuid.uuid4().hex}"
        staging.mkdir()
        write_new(staging / "state.json", state)
        with (staging / "events.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
            for event in events:
                handle.write(canonical(event) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        receipt = {
            "version": 1,
            "identity": self.identity,
            "generation": generation,
            "previous": self.previous,
            "logical_state_hash": logical_hash(state),
            "files": {name: file_hash(staging / name) for name in ("events.jsonl", "state.json")},
        }
        write_new(staging / "receipt.json", receipt)
        sync_directory(staging)
        if fault:
            fault("before_commit", generation)
        os.rename(staging, self.output / f"{generation:08d}")
        sync_directory(self.output)
        if fault:
            fault("after_commit", generation)
        self.state, self.generation = state, generation
        self.previous = file_hash(self.output / f"{generation:08d}" / "receipt.json")


def committed_files(output):
    """Bounded file hashing; run logs and uncommitted staging are deliberately excluded."""
    output = Path(output)
    result = {"manifest.json": file_hash(output / "manifest.json")}
    for number in range(10000):
        directory = output / f"{number:08d}"
        if not directory.exists():
            break
        for name in ("receipt.json", "state.json", "events.jsonl"):
            result[f"{number:08d}/{name}"] = file_hash(directory / name)
    return result
