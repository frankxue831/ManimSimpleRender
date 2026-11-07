"""Read-only local host and boot evidence for conservative render recovery.

This is private local evidence, not authentication or an externally shareable
machine identifier. Recovery assumes a trusted host, no cloned machine IDs or VM
snapshot migration, and no malicious local writers. A matching fingerprint with
a different boot UUID is useful only within those assumptions. Missing evidence
must never be replaced with a hostname, PID, wall clock or caller-provided value.
"""

import hashlib
import os
import plistlib
import re
import selectors
import stat
from subprocess import DEVNULL, PIPE, Popen as _Popen, TimeoutExpired
import sys
import time
import uuid

_PROBE_TIMEOUT = 2.0
_CLEANUP_TIMEOUT = 0.2
_MAX_PROBE_BYTES = 64 * 1024
_MAX_FILE_BYTES = 128
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_HASH = re.compile(r"[0-9a-f]{64}")
_MACHINE_ID = re.compile(r"[0-9a-fA-F]{32}")
_IOREG = ("/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice", "-a")
_SYSCTL = ("/usr/sbin/sysctl", "-n", "kern.bootsessionuuid")
_DOMAIN = b"scene-production.local-execution-identity.v1\0"


def _canonical_uuid(value):
    if type(value) is not str or not _UUID.fullmatch(value):
        raise ValueError("Unavailable local identity")
    parsed = uuid.UUID(value)
    if parsed.int in (0, (1 << 128) - 1):
        raise ValueError("Placeholder local identity")
    return str(parsed)


def validate_identity(value):
    """Validate only the exact, canonical persisted schema; do not probe."""
    if type(value) is not dict or set(value) != {"version", "platform", "host", "boot"}:
        return False
    if type(value["version"]) is not int or value["version"] != 1:
        return False
    if type(value["platform"]) is not str or value["platform"] not in ("darwin", "linux"):
        return False
    if type(value["host"]) is not str or not _HASH.fullmatch(value["host"]):
        return False
    try:
        return _canonical_uuid(value["boot"]) == value["boot"]
    except (TypeError, ValueError):
        return False


class _UniqueKeysDict(dict):
    """Reject ambiguous plist dictionaries instead of accepting the last key."""

    def __setitem__(self, key, value):
        if key in self:
            raise ValueError("Ambiguous local identity")
        super().__setitem__(key, value)


def _read_probe(argv):
    # Keep a private imported alias: render tests/interceptors patch Popen on the
    # shared subprocess module, but must not turn identity probes into renders.
    process = _Popen(argv, stdin=DEVNULL, stdout=PIPE, stderr=DEVNULL, shell=False)
    deadline = time.monotonic() + _PROBE_TIMEOUT
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("Local identity probe timed out")
                chunk = os.read(
                    process.stdout.fileno(), min(4096, _MAX_PROBE_BYTES + 1 - len(output))
                )
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > _MAX_PROBE_BYTES:
                    raise ValueError("Oversized local identity probe")
        if process.wait(timeout=max(0.0, deadline - time.monotonic())) != 0:
            raise ValueError("Local identity probe failed")
        return bytes(output)
    finally:
        try:
            if process.poll() is None:
                # Only clean up this helper's own fixed read-only subprocess.
                process.kill()
                try:
                    process.wait(timeout=_CLEANUP_TIMEOUT)
                except TimeoutExpired:
                    pass
        finally:
            if process.stdout is not None:
                process.stdout.close()


def _read_fixed_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Unsupported local identity file")
        output = bytearray()
        while True:
            chunk = os.read(descriptor, _MAX_FILE_BYTES + 1 - len(output))
            if not chunk:
                return bytes(output)
            output.extend(chunk)
            if len(output) > _MAX_FILE_BYTES:
                raise ValueError("Oversized local identity file")
    finally:
        os.close(descriptor)


def _line(data):
    # Each fixed source emits one ASCII value, optionally followed by a newline.
    return data.decode("ascii").removesuffix("\n")


def local_execution_identity():
    """Return a fresh identity, or None for any unsupported/uncertain evidence.

    Uses fixed local files or absolute read-only commands, never a shell, network
    request or configuration change. Probe output, waits and cleanup are bounded.
    Raw host UUIDs and exception details are neither returned nor logged.
    """
    try:
        platform = sys.platform
        if platform == "darwin":
            entries = plistlib.loads(_read_probe(_IOREG), dict_type=_UniqueKeysDict)
            if type(entries) is not list or len(entries) != 1 or not isinstance(entries[0], dict):
                return None
            host = _canonical_uuid(entries[0].get("IOPlatformUUID"))
            boot = _canonical_uuid(_line(_read_probe(_SYSCTL)))
        elif platform == "linux":
            machine = _line(_read_fixed_file("/etc/machine-id"))
            if not _MACHINE_ID.fullmatch(machine):
                return None
            host = _canonical_uuid(str(uuid.UUID(hex=machine)))
            boot = _canonical_uuid(_line(_read_fixed_file("/proc/sys/kernel/random/boot_id")))
        else:
            return None
        fingerprint = hashlib.sha256(
            _DOMAIN + platform.encode("ascii") + b"\0" + host.encode("ascii")
        ).hexdigest()
        result = {"version": 1, "platform": platform, "host": fingerprint, "boot": boot}
        return result if validate_identity(result) else None
    except Exception:
        # Unavailable identity disables recovery; it is not a reason to guess or
        # reveal command output, raw UUIDs, paths, or private exception messages.
        return None
