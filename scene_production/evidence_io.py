"""Bounded local evidence I/O. Direct children only; not a media sandbox."""

import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from . import pairs
from .reinspection import _finite

CHUNK = 65536


class Budget:
    def __init__(self, seconds, cancel=None):
        self.started = time.monotonic()
        self.deadline = self.started + seconds
        self.cancel = cancel

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise InterruptedError("evidence export cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("evidence operation deadline exceeded")
        return remaining


def strict_json(raw):
    return json.loads(raw, object_pairs_hook=pairs, parse_float=_finite, parse_constant=_finite)


def identity(path, limit, budget, expected=None, collect=False):
    budget.check()
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("expected a regular, non-symlink file")
    path = path.resolve(strict=True)
    size = path.stat().st_size
    if not 0 <= size <= limit:
        raise ValueError("input exceeds byte limit")
    if expected is not None and (
        type(expected) is not str or re.fullmatch(r"[0-9a-f]{64}", expected) is None
    ):
        raise ValueError("invalid recorded SHA-256")
    sha, seen, chunks = hashlib.sha256(), 0, []
    with path.open("rb") as handle:
        while True:
            budget.check()
            chunk = handle.read(min(CHUNK, limit - seen + 1))
            if not chunk:
                break
            seen += len(chunk)
            if seen > limit:
                raise ValueError("input exceeds byte limit during read")
            sha.update(chunk)
            if collect:
                chunks.append(chunk)
    budget.check()
    if seen != size or path.stat().st_size != size:
        raise ValueError("input changed during read")
    if expected is not None and sha.hexdigest() != expected:
        raise ValueError("recorded artifact SHA-256 mismatch")
    entry = {"path": str(path), "size_bytes": size, "sha256": sha.hexdigest()}
    return (entry, b"".join(chunks)) if collect else entry


def capture(command, budget, timeout, *, limit=2 * 1024 * 1024, sink=None, diagnostics=None):
    """Finite pipe chunks/queue; cap before accumulation or sink delivery.

    Cleanup allows 1 second for terminate, then 1 for kill/reap, plus two
    0.2-second reader joins. Only the directly owned child is supervised.
    Buffered read overshoot is <= 7 * CHUNK plus OS pipe buffers, not stored
    in the result. No decoder RSS or kernel I/O latency guarantee is made.
    """
    deadline = time.monotonic() + min(timeout, budget.check())
    process = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    messages, stop = queue.Queue(maxsize=4), threading.Event()

    def read_pipe(pipe, name):
        try:
            while not stop.is_set():
                chunk = os.read(pipe.fileno(), CHUNK)
                while not stop.is_set():
                    try:
                        messages.put((name, chunk), timeout=0.05)
                        break
                    except queue.Full:
                        pass
                if not chunk:
                    break
        except OSError:
            pass

    readers = [
        threading.Thread(target=read_pipe, args=(pipe, name), daemon=True)
        for pipe, name in ((process.stdout, "out"), (process.stderr, "err"))
    ]
    out, err, sizes, ended = [], [], {"out": 0, "err": 0}, set()
    try:
        for thread in readers:
            thread.start()
        while len(ended) < 2:
            budget.check()
            if time.monotonic() >= deadline:
                raise TimeoutError("media tool deadline exceeded")
            try:
                name, chunk = messages.get(timeout=0.05)
            except queue.Empty:
                continue
            if not chunk:
                ended.add(name)
                continue
            sizes[name] += len(chunk)
            if sizes[name] > (limit if name == "out" else 65536):
                raise ValueError("media tool capture exceeds byte limit")
            if name == "out" and sink is not None:
                sink(chunk)
            else:
                (out if name == "out" else err).append(chunk)
        process.wait(timeout=min(budget.check(), max(0.001, deadline - time.monotonic())))
        if process.returncode:
            raise RuntimeError(
                "media tool failed: " + b"".join(err).decode("utf-8", "replace")[:2000]
            )
        return b"".join(out)
    finally:
        stop.set()
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        for thread in readers:
            if thread.ident is not None:
                thread.join(timeout=0.2)
        process.stdout.close()
        process.stderr.close()
        if diagnostics is not None:
            diagnostics.event(
                "owned_child_exit",
                exit_observed=process.poll() is not None,
                returncode=process.returncode,
            )


class Storage:
    """A pre-write aggregate cap; every generated byte passes through this writer."""

    def __init__(self, root, reserved, budget):
        self.root, self.reserved, self.budget = root, reserved, budget
        self.written = 0

    def write(self, relative, producer, limit):
        self.budget.check()
        path = self.root / relative
        store = self
        with path.open("xb") as handle:

            class Writer:
                count = 0

                def write(self, data):
                    store.budget.check()
                    if self.count + len(data) > limit or store.written + len(data) > store.reserved:
                        raise ValueError("output exceeds reserved storage")
                    self.count += len(data)
                    store.written += len(data)
                    written = handle.write(data)
                    if written != len(data):
                        raise OSError("short output write")
                    return written

                def flush(self):
                    handle.flush()

            producer(Writer())
        self.budget.check()
        return identity(path, limit, self.budget)

    def json(self, relative, value):
        payload = (json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode()
        return self.write(relative, lambda f: f.write(payload), 2 * 1024 * 1024)
