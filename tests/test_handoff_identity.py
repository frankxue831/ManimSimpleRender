"""Local execution identity is bounded, private evidence and fails closed."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scene_production.handoff import execution_identity as identity

HOST = "12345678-1234-4234-8234-123456789abc"
BOOT = "87654321-4321-4321-8321-cba987654321"
IOREG = ("/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice", "-a")
SYSCTL = ("/usr/sbin/sysctl", "-n", "kern.bootsessionuuid")


class ProbeProcess:
    """Pipe-backed process double: only external command execution is replaced."""

    def __init__(self, output, returncode=0):
        read_fd, write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        try:
            os.write(write_fd, output)
        finally:
            os.close(write_fd)
        self.returncode = None
        self.exit_status = returncode
        self.killed = False

    def wait(self, timeout):
        self.returncode = self.exit_status
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class ExecutionIdentityTests(unittest.TestCase):
    @contextmanager
    def mac_probes(self, host=None, boot=None, returncode=0):
        if os.name != "posix":
            self.skipTest("Identity probe fixtures require POSIX pipe selectors")
        outputs = {
            IOREG: plistlib.dumps([{"IOPlatformUUID": HOST.upper()}]) if host is None else host,
            SYSCTL: (BOOT.upper() + "\n").encode() if boot is None else boot,
        }
        children = []

        def start(argv, **options):
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
            self.assertEqual(options["stderr"], subprocess.DEVNULL)
            self.assertEqual(options["stdout"], subprocess.PIPE)
            self.assertIs(options["shell"], False)
            child = ProbeProcess(outputs[tuple(argv)], returncode)
            children.append(child)
            return child

        with (
            patch.object(identity.sys, "platform", "darwin"),
            patch.object(identity, "_Popen", side_effect=start),
        ):
            try:
                yield children
            finally:
                for child in children:
                    child.stdout.close()

    @contextmanager
    def linux_files(self, machine=None, boot=None):
        if os.name != "posix":
            self.skipTest("Identity file fixtures require POSIX descriptor flags")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            machine_path, boot_path = root / "machine", root / "boot"
            machine_path.write_bytes(
                (HOST.replace("-", "") + "\n").encode() if machine is None else machine
            )
            boot_path.write_bytes((BOOT + "\n").encode() if boot is None else boot)
            targets = {
                "/etc/machine-id": machine_path,
                "/proc/sys/kernel/random/boot_id": boot_path,
            }
            real_open = os.open

            def redirected_open(path, flags):
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                self.assertTrue(flags & os.O_NONBLOCK)
                return real_open(targets[path], flags)

            with (
                patch.object(identity.sys, "platform", "linux"),
                patch.object(identity.os, "open", side_effect=redirected_open),
            ):
                yield

    def assert_identity(self, result, platform):
        self.assertIsNotNone(result)
        self.assertTrue(identity.validate_identity(result))
        self.assertEqual(set(result), {"version", "platform", "host", "boot"})
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["platform"], platform)
        self.assertEqual(result["boot"], BOOT)
        self.assertEqual(len(result["host"]), 64)
        self.assertNotIn(HOST, json.dumps(result).lower())
        self.assertNotIn(HOST.replace("-", ""), json.dumps(result).lower())
        self.assertNotEqual(result["host"], hashlib.sha256(HOST.encode()).hexdigest())

    def test_mac_fixed_read_only_probes_return_stable_private_identity(self):
        with self.mac_probes():
            first = identity.local_execution_identity()
            second = identity.local_execution_identity()
        self.assert_identity(first, "darwin")
        self.assertEqual(first, second)

    def test_linux_fixed_files_return_stable_private_identity(self):
        with self.linux_files():
            first = identity.local_execution_identity()
            second = identity.local_execution_identity()
        self.assert_identity(first, "linux")
        self.assertEqual(first, second)

    def test_host_fingerprint_is_platform_separated_and_case_normalized(self):
        with self.mac_probes():
            mac = identity.local_execution_identity()
        with self.linux_files():
            linux = identity.local_execution_identity()
        with self.linux_files(machine=(HOST.replace("-", "").upper() + "\n").encode()):
            upper = identity.local_execution_identity()
        self.assertNotEqual(mac["host"], linux["host"])
        self.assertEqual(linux, upper)

    def test_boot_changes_do_not_change_host_fingerprint(self):
        with self.mac_probes():
            before = identity.local_execution_identity()
        with self.mac_probes(boot=b"99999999-9999-4999-8999-999999999999\n"):
            after = identity.local_execution_identity()
        self.assertEqual(before["host"], after["host"])
        self.assertNotEqual(before["boot"], after["boot"])

    def test_unsupported_platforms_do_not_probe(self):
        for platform in ("win32", "freebsd14", "", None):
            with (
                self.subTest(platform=platform),
                patch.object(identity.sys, "platform", platform),
                patch.object(identity, "_Popen", side_effect=AssertionError),
                patch.object(identity.os, "open", side_effect=AssertionError),
            ):
                self.assertIsNone(identity.local_execution_identity())

    def test_mac_missing_malformed_or_ambiguous_host_fails_closed(self):
        values = [
            b"not a plist",
            b"",
            plistlib.dumps({"IOPlatformUUID": HOST}),
            plistlib.dumps([]),
            plistlib.dumps([{}, {"IOPlatformUUID": HOST}]),
            plistlib.dumps([{}]),
            plistlib.dumps([{"IOPlatformUUID": 3}]),
            plistlib.dumps([{"IOPlatformUUID": HOST.replace("-", "")}]),
            plistlib.dumps([{"IOPlatformUUID": "00000000-0000-0000-0000-000000000000"}]),
            plistlib.dumps([{"IOPlatformUUID": "ffffffff-ffff-ffff-ffff-ffffffffffff"}]),
            (
                f'<plist version="1.0"><array><dict><key>IOPlatformUUID</key><string>{HOST}'
                f"</string><key>IOPlatformUUID</key><string>{HOST}</string></dict></array></plist>"
            ).encode(),
        ]
        for value in values:
            with self.subTest(value=value), self.mac_probes(host=value):
                self.assertIsNone(identity.local_execution_identity())

    def test_malformed_boot_fails_closed_on_each_platform(self):
        values = [
            b"",
            b"unavailable",
            BOOT.replace("-", "").encode(),
            (BOOT + "\n" + BOOT).encode(),
            b"\xff",
            b"00000000-0000-0000-0000-000000000000\n",
            b"ffffffff-ffff-ffff-ffff-ffffffffffff\n",
        ]
        for value in values:
            with self.subTest(value=value, platform="darwin"), self.mac_probes(boot=value):
                self.assertIsNone(identity.local_execution_identity())
            with self.subTest(value=value, platform="linux"), self.linux_files(boot=value):
                self.assertIsNone(identity.local_execution_identity())

    def test_linux_malformed_placeholder_or_oversized_machine_id_fails_closed(self):
        for value in (b"", b"g" * 32, b"0" * 32, b"f" * 32, HOST.encode(), b"a" * 10000):
            with self.subTest(value=value[:40]), self.linux_files(machine=value):
                self.assertIsNone(identity.local_execution_identity())

    def test_linux_oversized_boot_fails_closed(self):
        with self.linux_files(boot=(BOOT + "\n" + " " * 1000).encode()):
            self.assertIsNone(identity.local_execution_identity())

    @unittest.skipUnless(os.name == "posix", "Requires POSIX directory descriptors")
    def test_linux_nonregular_source_is_rejected_and_descriptor_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            descriptor = os.open(tmp, os.O_RDONLY)
            with (
                patch.object(identity.sys, "platform", "linux"),
                patch.object(identity.os, "open", return_value=descriptor),
            ):
                self.assertIsNone(identity.local_execution_identity())
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    @unittest.skipUnless(os.name == "posix", "Requires POSIX descriptor flags")
    def test_linux_read_error_is_rejected_and_descriptor_is_closed(self):
        with tempfile.TemporaryFile() as source:
            descriptor = os.dup(source.fileno())
            with (
                patch.object(identity.sys, "platform", "linux"),
                patch.object(identity.os, "open", return_value=descriptor),
                patch.object(identity.os, "read", side_effect=PermissionError("private")),
            ):
                self.assertIsNone(identity.local_execution_identity())
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_probe_and_file_errors_fail_closed_without_exposing_error(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        for error in (PermissionError("private"), OSError("private"), RuntimeError("private")):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(identity.sys, "platform", "darwin"),
                patch.object(identity, "_Popen", side_effect=error),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                self.assertIsNone(identity.local_execution_identity())
            with (
                self.subTest(error=type(error).__name__),
                patch.object(identity.sys, "platform", "linux"),
                patch.object(identity.os, "open", side_effect=error),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                self.assertIsNone(identity.local_execution_identity())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_nonzero_probe_status_fails_closed(self):
        with self.mac_probes(returncode=1):
            self.assertIsNone(identity.local_execution_identity())

    def test_probe_output_overflow_fails_closed_and_cleans_own_child(self):
        with (
            self.mac_probes() as children,
            patch.object(identity, "_MAX_PROBE_BYTES", 16),
        ):
            self.assertIsNone(identity.local_execution_identity())
            self.assertTrue(children[0].stdout.closed)
            self.assertTrue(children[0].killed)

    def test_boot_probe_output_overflow_also_fails_closed(self):
        with self.mac_probes(boot=b"x" * 4097), patch.object(identity, "_MAX_PROBE_BYTES", 4096):
            self.assertIsNone(identity.local_execution_identity())

    def test_probe_timeout_fails_closed_and_cleans_own_child(self):
        with (
            self.mac_probes() as children,
            patch.object(identity.selectors.DefaultSelector, "select", return_value=[]),
        ):
            self.assertIsNone(identity.local_execution_identity())
            self.assertTrue(children[0].stdout.closed)
        self.assertEqual(len(children), 1)
        self.assertTrue(children[0].killed)

    def test_expired_deadline_fails_closed_before_waiting(self):
        with (
            self.mac_probes() as children,
            patch.object(identity.time, "monotonic", side_effect=[10, 13]),
        ):
            self.assertIsNone(identity.local_execution_identity())
            self.assertTrue(children[0].stdout.closed)
            self.assertTrue(children[0].killed)

    def test_wait_timeout_and_failed_cleanup_stay_bounded_and_fail_closed(self):
        with (
            self.mac_probes() as children,
            patch.object(
                ProbeProcess, "wait", side_effect=subprocess.TimeoutExpired("private", 0.1)
            ),
        ):
            self.assertIsNone(identity.local_execution_identity())
            self.assertTrue(children[0].stdout.closed)
            self.assertTrue(children[0].killed)

    def test_cleanup_permission_error_does_not_leak_or_leave_pipe_open(self):
        with (
            self.mac_probes() as children,
            patch.object(identity.selectors.DefaultSelector, "select", return_value=[]),
            patch.object(ProbeProcess, "kill", side_effect=PermissionError("private")),
        ):
            self.assertIsNone(identity.local_execution_identity())
            self.assertTrue(children[0].stdout.closed)

    def test_global_render_popen_patches_do_not_intercept_identity_probes(self):
        with self.mac_probes(), patch("subprocess.Popen", side_effect=AssertionError):
            result = identity.local_execution_identity()
        self.assert_identity(result, "darwin")

    def test_validation_requires_exact_schema_and_canonical_values(self):
        valid = {"version": 1, "platform": "linux", "host": "abcde123" * 8, "boot": BOOT}
        self.assertTrue(identity.validate_identity(valid))
        invalid = [None, [], "identity", {}, {**valid, "extra": True}]
        for key in valid:
            invalid.append({k: v for k, v in valid.items() if k != key})
        for key, values in {
            "version": (True, "1", 1.0, 0, 2),
            "platform": (None, "Darwin", "win32", []),
            "host": (None, 1, "z" * 64, "A" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n"),
            "boot": (
                None,
                1,
                BOOT.upper(),
                BOOT + "\n",
                BOOT.replace("-", ""),
                "00000000-0000-0000-0000-000000000000",
                "ffffffff-ffff-ffff-ffff-ffffffffffff",
            ),
        }.items():
            invalid.extend({**valid, key: value} for value in values)
        for value in invalid:
            with self.subTest(value=value):
                self.assertIs(identity.validate_identity(value), False)


if __name__ == "__main__":
    unittest.main()
