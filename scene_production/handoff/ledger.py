"""Local bookkeeping, not an authentication or permission authority.

Callers must verify provenance and enforce runtime permission policies themselves.
No operation executes tasks, interprets restrictions, or retries failed work.
"""

from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid


class LedgerError(ValueError):
    """A requested transition violates a ledger invariant."""


def _id():
    return uuid.uuid4().hex


def _text(value):
    return isinstance(value, str) and bool(value.strip())


class Ledger:
    def __init__(self, directory, *, clock=time.time):
        self.directory = Path(directory).absolute()
        path = self.directory / "ledger.sqlite3"
        if any(p.is_symlink() for p in (self.directory, path)):
            raise LedgerError("Symlink workflow paths are not supported")
        self.clock = clock
        self.db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
        self.db.execute("PRAGMA busy_timeout=5000")
        if self.db.execute("PRAGMA user_version").fetchone()[0] != 1:
            self.db.close()
            raise LedgerError("Unsupported ledger schema")

    @classmethod
    def create(cls, directory, *, clock=time.time):
        path = Path(directory).absolute()
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
        db = sqlite3.connect(path / "ledger.sqlite3")
        try:
            db.execute(
                "CREATE TABLE records (kind TEXT, id TEXT, body TEXT, " "PRIMARY KEY(kind,id))"
            )
            db.execute("PRAGMA user_version=1")
            db.commit()
        finally:
            db.close()
        (path / "ledger.sqlite3").chmod(0o600)
        return cls(path, clock=clock)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def _transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _get(self, kind, key):
        row = self.db.execute(
            "SELECT body FROM records WHERE kind=? AND id=?", (kind, key)
        ).fetchone()
        if row is None:
            raise LedgerError(f"Unknown {kind}: {key}")
        return json.loads(row[0])

    def _all(self, kind):
        return [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT body FROM records WHERE kind=? ORDER BY rowid", (kind,)
            )
        ]

    def _put(self, kind, key, value, *, new=False):
        sql = "INSERT" if new else "INSERT OR REPLACE"
        try:
            self.db.execute(
                f"{sql} INTO records VALUES (?,?,?)",
                (kind, key, json.dumps(value, allow_nan=False)),
            )
        except sqlite3.IntegrityError as exc:
            raise LedgerError(f"Duplicate {kind}: {key}") from exc

    def _event(self, task, event_type, attempt=None):
        event = dict(
            event_id=_id(),
            owner=task["owner"],
            type=event_type,
            task_id=task["task_id"],
            authorization_id=task["authorization_id"],
            attempt_id=attempt,
            ready_generation=task.get("ready_generation", 0),
            acknowledged=False,
            created_at=self.clock(),
            authorization_revision=self._get("auth", task["authorization_id"])["revision"],
            acknowledged_at=None,
        )
        self._put("event", event["event_id"], event, new=True)

    def add_authorization(self, data):
        if not isinstance(data, dict):
            raise LedgerError("Authorization must be an object")
        if data.get("source_kind") != "user_message" or not isinstance(
            data.get("question_text"), str
        ):
            raise LedgerError("User-message provenance and question_text required")
        required = ("authorization_id", "source_ref", "original_text")
        if any(not _text(data.get(k)) for k in required):
            raise LedgerError("Original user source and text are required")
        for key in ("allowed_actions", "allowed_resources", "restrictions"):
            if not isinstance(data.get(key), list) or any(not _text(v) for v in data[key]):
                raise LedgerError(f"{key} must be a list of strings")
        auth = {
            k: data[k] for k in required + ("allowed_actions", "allowed_resources", "restrictions")
        }
        auth.update(
            source_kind="user_message",
            question_text=data["question_text"],
            revision=1,
            status="active",
            revocation_source_ref=None,
        )
        with self._transaction():
            self._put("auth", auth["authorization_id"], auth, new=True)
        return auth

    def get_authorization(self, authorization_id):
        return self._get("auth", authorization_id)

    def revoke_authorization(self, auth_id, source_ref):
        if not _text(source_ref):
            raise LedgerError("Revocation source required")
        with self._transaction():
            auth = self._get("auth", auth_id)
            if auth["status"] != "revoked":
                auth.update(
                    status="revoked",
                    revision=auth["revision"] + 1,
                    revocation_source_ref=source_ref,
                )
                self._put("auth", auth_id, auth)
                for task in self._all("task"):
                    if task["authorization_id"] == auth_id:
                        self._event(task, "authorization_revoked")
                self._notify_ready()
        return auth

    def create_task(self, data):
        if not isinstance(data, dict):
            raise LedgerError("Task must be an object")
        keys = ("task_id", "title", "owner", "action", "resource", "authorization_id")
        if any(not _text(data.get(k)) for k in keys):
            raise LedgerError("Task fields must be nonempty strings")
        if data.get("kind") not in ("manual", "render"):
            raise LedgerError("Invalid task kind")
        deps = data.get("depends_on", [])
        if not isinstance(deps, list) or any(not _text(d) for d in deps):
            raise LedgerError("Dependencies must be task IDs")
        if not isinstance(data.get("payload", {}), dict):
            raise LedgerError("Payload must be an object")
        task = {k: data[k] for k in keys}
        task.update(
            kind=data["kind"],
            depends_on=list(dict.fromkeys(deps)),
            payload=data.get("payload", {}),
            status="pending",
            fence=0,
        )
        with self._transaction():
            self._get("auth", task["authorization_id"])
            for dep in deps:
                self._get("task", dep)
            self._put("task", task["task_id"], task, new=True)
            self._event(task, "created")
            self._notify_ready()
        return self.get_task(task["task_id"])

    def _block(self, task):
        if task["status"] != "pending":
            return task["status"]
        auth = self._get("auth", task["authorization_id"])
        if auth["status"] != "active":
            return "authorization_revoked"
        if (
            task["action"] not in auth["allowed_actions"]
            or task["resource"] not in auth["allowed_resources"]
        ):
            return "out_of_scope"
        for dep in task["depends_on"]:
            parent = self._get("task", dep)
            parent_auth = self._get("auth", parent["authorization_id"])
            if parent["status"] != "succeeded" or parent_auth["status"] != "active":
                return "dependency_blocked"
        if task["kind"] == "render" and any(
            t["task_id"] != task["task_id"]
            and t["kind"] == "render"
            and t["status"] in ("running", "unknown")
            for t in self._all("task")
        ):
            return "render_slot_held"
        return None

    def get_handoff(self, task_id):
        with self._transaction():
            task = self.get_task(task_id)
            return dict(
                task=task,
                attempts=self.list_attempts(task_id),
                resolutions=self.list_resolutions(task_id),
                authorization=self.get_authorization(task["authorization_id"]),
                predecessors=[
                    dict(
                        task=self.get_task(d),
                        attempts=self.list_attempts(d),
                        resolutions=self.list_resolutions(d),
                    )
                    for d in task["depends_on"]
                ],
            )

    def get_task(self, task_id):
        task = self._get("task", task_id)
        reason = self._block(task)
        return dict(task, eligible=reason is None, blocked_reason=reason)

    def list_tasks(self, owner=None, eligible_only=False):
        if owner is not None and not _text(owner):
            raise LedgerError("Owner must be a nonempty string")
        if not isinstance(eligible_only, bool):
            raise LedgerError("Eligibility filter must be boolean")
        # A consistent current-state scan does not depend on inbox ACK state.
        # It is advisory: claim still rechecks eligibility transactionally.
        with self._transaction():
            tasks = [
                self.get_task(t["task_id"])
                for t in self._all("task")
                if owner is None or t["owner"] == owner
            ]
        return [t for t in tasks if not eligible_only or t["eligible"]]

    def get_attempt(self, attempt_id):
        return self._get("attempt", attempt_id)

    def list_attempts(self, task_id=None):
        return [a for a in self._all("attempt") if task_id is None or a["task_id"] == task_id]

    def _duration(self, seconds):
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or seconds <= 0
        ):
            raise LedgerError("Lease duration must be finite and positive")

    def claim(self, task_id, worker_id, authorization_revision, lease_seconds=60):
        return self._claim(task_id, worker_id, authorization_revision, lease_seconds)

    def claim_local_render(self, task_id, worker_id, authorization_revision, lease_seconds=60):
        """Only the fixed local runner may use this execution lifecycle contract.

        Every launch MUST first commit begin_execution. Direct/external claims
        have no such guard and are deliberately ineligible for slot recovery.
        """
        from .execution_identity import local_execution_identity

        guard = dict(version=1, state="not_started", identity=local_execution_identity())
        return self._claim(task_id, worker_id, authorization_revision, lease_seconds, guard)

    def _claim(self, task_id, worker_id, authorization_revision, lease_seconds, guard=None):
        self._duration(lease_seconds)
        if type(authorization_revision) is not int or authorization_revision < 1:
            raise LedgerError("Revision must be a positive integer")
        if not _text(worker_id):
            raise LedgerError("Worker required")
        with self._transaction():
            task = self._get("task", task_id)
            if guard is not None and (task["kind"], task["action"]) != ("render", "render"):
                raise LedgerError("Local execution guard requires a render task")
            auth = self._get("auth", task["authorization_id"])
            if self._block(task) or auth["revision"] != authorization_revision:
                raise LedgerError("Task is not eligible or authorization is stale")
            task.update(status="running", fence=task["fence"] + 1)
            attempt = dict(
                attempt_id=_id(),
                task_id=task_id,
                worker_id=worker_id,
                fence=task["fence"],
                lease_until=self.clock() + lease_seconds,
                payload=task["payload"],
                status="running",
                authorization_id=auth["authorization_id"],
                authorization_revision=auth["revision"],
            )
            if guard is not None:
                attempt["execution_guard"] = guard
            self._put("attempt", attempt["attempt_id"], attempt, new=True)
            self._put("task", task_id, task)
            self._event(task, "claimed", attempt["attempt_id"])
            self._notify_ready()
        return attempt

    def begin_execution(self, attempt_id, fence):
        """Irreversible spawn intent, committed BEFORE the local runner spawns."""
        from .execution_identity import local_execution_identity, validate_identity

        with self._transaction():
            attempt, task = self._active(attempt_id, fence)
            guard = attempt.get("execution_guard")
            if (
                task["kind"] != "render"
                or not isinstance(guard, dict)
                or set(guard) != {"version", "state", "identity"}
                or type(guard.get("version")) is not int
                or guard["version"] != 1
                or guard.get("state") != "not_started"
                or self._get("auth", task["authorization_id"])["status"] != "active"
            ):
                raise LedgerError("Local execution cannot start")
            # Capture the execution machine/boot at this gate, not just claim
            # time. A replayed old-boot guard must not label a new local launch.
            current_identity = local_execution_identity()
            if (
                validate_identity(guard["identity"])
                and validate_identity(current_identity)
                and guard["identity"] != current_identity
            ):
                raise LedgerError("Execution machine or boot changed after claim")
            guard["identity"] = current_identity
            self._active(attempt_id, fence)
            guard["state"] = "spawn_intent"
            self._put("attempt", attempt_id, attempt)
        return attempt

    def _active(self, attempt_id, fence):
        if type(fence) is not int or fence < 1:
            raise LedgerError("Fence must be a positive integer")
        attempt = self._get("attempt", attempt_id)
        task = self._get("task", attempt["task_id"])
        if (
            attempt["status"] != "running"
            or task["fence"] != fence
            or attempt["fence"] != fence
            or attempt["lease_until"] <= self.clock()
        ):
            raise LedgerError("Stale, expired, or terminal attempt")
        return attempt, task

    def heartbeat(self, attempt_id, fence, lease_seconds=60):
        self._duration(lease_seconds)
        with self._transaction():
            attempt, task = self._active(attempt_id, fence)
            auth = self._get("auth", task["authorization_id"])
            if auth["status"] != "active":
                raise LedgerError("Authorization revoked")
            attempt["lease_until"] = self.clock() + lease_seconds
            self._put("attempt", attempt_id, attempt)
        return attempt

    def finish(self, attempt_id, fence, status, result):
        if status not in ("succeeded", "failed", "unknown") or not isinstance(result, dict):
            raise LedgerError("Invalid completion")
        with self._transaction():
            attempt, task = self._active(attempt_id, fence)
            auth = self._get("auth", task["authorization_id"])
            if auth["status"] != "active" and status == "succeeded":
                raise LedgerError("Revoked authorization cannot complete successfully")
            attempt.update(status=status, result=result)
            task.update(status=status)
            self._put("attempt", attempt_id, attempt)
            self._put("task", task["task_id"], task)
            self._event(task, status, attempt_id)
            self._notify_ready(attempt_id)
        return attempt

    def _notify_ready(self, attempt_id=None):
        # Ready is advisory; each blocked-to-eligible transition gets its own event.
        for task in self._all("task"):
            eligible = self._block(task) is None
            previously_eligible = task.get("last_eligible", False)
            if eligible != previously_eligible:
                task["last_eligible"] = eligible
                if eligible:
                    task["ready_generation"] = task.get("ready_generation", 0) + 1
                self._put("task", task["task_id"], task)
                if eligible:
                    self._event(task, "ready", attempt_id)

    def reconcile(self):
        expired = []
        with self._transaction():
            for attempt in self._all("attempt"):
                if attempt["status"] == "running" and attempt["lease_until"] <= self.clock():
                    task = self._get("task", attempt["task_id"])
                    attempt["status"] = "unknown"
                    task.update(status="unknown", fence=task["fence"] + 1)
                    self._put("attempt", attempt["attempt_id"], attempt)
                    self._put("task", task["task_id"], task)
                    self._event(task, "unknown", attempt["attempt_id"])
                    expired.append(attempt)
            self._notify_ready()
        return expired

    def get_resolution(self, resolution_id):
        return self._get("resolution", resolution_id)

    def list_resolutions(self, task_id=None):
        return [r for r in self._all("resolution") if task_id is None or r["task_id"] == task_id]

    def resolve_unknown(self, data):
        """Audit abandonment, never adopt success or authorize another attempt.

        Text supplied by an operator is context, NOT proof of stopped execution.
        Only an unused fenced launch gate or a verified local host reboot can
        release a render slot. Other unknowns deliberately remain blocked.
        """
        from .execution_identity import local_execution_identity, validate_identity

        text_fields = (
            "resolution_id",
            "task_id",
            "attempt_id",
            "operator",
            "source_ref",
            "reason",
        )
        fields = set(text_fields) | {"expected_fence", "decision", "evidence_refs"}
        if (
            not isinstance(data, dict)
            or set(data) != fields
            or any(not _text(data.get(k)) for k in text_fields)
            or type(data["expected_fence"]) is not int
            or data["expected_fence"] < 1
            or data["decision"] != "abandon"
            or not isinstance(data["evidence_refs"], list)
            or not data["evidence_refs"]
            or any(not _text(v) for v in data["evidence_refs"])
        ):
            raise LedgerError("Complete audit context and abandonment decision required")
        with self._transaction():
            prior = next(
                (r for r in self._all("resolution") if r["resolution_id"] == data["resolution_id"]),
                None,
            )
            if prior is not None:
                if prior["request"] != data:
                    raise LedgerError("Resolution ID already used with different context")
                return prior
            task = self._get("task", data["task_id"])
            attempt = self._get("attempt", data["attempt_id"])
            if (
                task["kind"] != "render"
                or task["status"] != "unknown"
                or attempt["status"] != "unknown"
                or attempt["task_id"] != task["task_id"]
                or task["fence"] != data["expected_fence"]
            ):
                raise LedgerError("Unknown render and current task fence required")
            guard = attempt.get("execution_guard")
            if (
                not isinstance(guard, dict)
                or set(guard) != {"version", "state", "identity"}
                or type(guard.get("version")) is not int
                or guard["version"] != 1
            ):
                raise LedgerError("No supported execution guard")
            # Kernel identity is independently read while holding the same CAS
            # transaction, never taken from the operator's request or evidence.
            current_identity = (
                local_execution_identity() if guard.get("state") == "spawn_intent" else None
            )
            if guard.get("state") == "not_started":
                proof = {"kind": "fenced_before_spawn_intent"}
            elif (
                guard.get("state") == "spawn_intent"
                and validate_identity(guard.get("identity"))
                and validate_identity(current_identity)
                and guard["identity"]["platform"] == current_identity["platform"]
                and guard["identity"]["host"] == current_identity["host"]
                and guard["identity"]["boot"] != current_identity["boot"]
            ):
                proof = dict(
                    kind="same_host_reboot",
                    previous_identity=guard["identity"],
                    current_identity=current_identity,
                )
            else:
                raise LedgerError("Stopped local execution cannot be established")
            resolution = dict(
                resolution_id=data["resolution_id"],
                task_id=task["task_id"],
                attempt_id=attempt["attempt_id"],
                request=data,
                proof=proof,
                created_at=self.clock(),
                previous_task_status=task["status"],
                previous_fence=task["fence"],
                resulting_fence=task["fence"] + 1,
                authorization=self._get("auth", task["authorization_id"]),
            )
            task.update(
                status="abandoned",
                fence=task["fence"] + 1,
                resolution_id=data["resolution_id"],
            )
            self._put("resolution", data["resolution_id"], resolution, new=True)
            self._put("task", task["task_id"], task)
            self._event(task, "abandoned", attempt["attempt_id"])
            self._notify_ready()
        return resolution

    def pending_events(self, owner):
        return [e for e in self._all("event") if e["owner"] == owner and not e["acknowledged"]]

    def acknowledge(self, event_id, owner):
        with self._transaction():
            event = self._get("event", event_id)
            if event["owner"] != owner:
                raise LedgerError("Recipient mismatch")
            if not event["acknowledged"]:
                event["acknowledged_at"] = self.clock()
            event["acknowledged"] = True
            self._put("event", event_id, event)
        return event
