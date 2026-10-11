"""Durable identities and receipts for Cine's opt-in generation submissions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from .client import SeedanceError
from .production_gate import ProductionRejected, inside


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("invalid generation receipt")
        return value
    except (OSError, ValueError) as exc:
        raise ProductionRejected("CINE_GENERATION_RECEIPT_INVALID", {"path": str(path)}) from exc


class GenerationRequest:
    """Keep one identity across lost responses; fail closed on changed inputs."""

    def __init__(
        self,
        directory: Path,
        request_id: str,
        project_id: str,
        intent: dict[str, Any],
        context: dict[str, Any],
    ):
        if not isinstance(request_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id
        ):
            raise ProductionRejected("CINE_GENERATION_REQUEST_ID_INVALID")
        self.directory = directory
        self.request_id = request_id
        self.project_id = project_id
        self.identity = _hash(
            {"project": project_id, "session": intent.get("session_id"), "request": request_id}
        )
        self.command_id = "cine-generation-" + self.identity
        self.path = directory / (self.identity + ".json")
        self.intent = intent
        self.context = context
        self.record: dict[str, Any] = {}
        self.lock = FileLock(str(self.path) + ".lock")

    def acquire(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.lock.acquire(timeout=0)
        except Timeout as exc:
            raise ProductionRejected(
                "CINE_GENERATION_REQUEST_IN_PROGRESS", self.metadata()
            ) from exc
        try:
            with FileLock(str(self.directory / "registry.lock"), timeout=0):
                if self.path.exists():
                    self.record = _read(self.path)
                    self.validate_record()
                    if (
                        self.record.get("intent") != self.intent
                        or self.record.get("context") != self.context
                    ):
                        raise ProductionRejected(
                            "CINE_GENERATION_REQUEST_CONFLICT", self.metadata()
                        )
                    return
                self.check_pending()
                self.record = {
                    "version": 1,
                    "generation_request_id": self.request_id,
                    "project_id": self.project_id,
                    "command_id": self.command_id,
                    "intent": self.intent,
                    "context": self.context,
                    "state": "prepared",
                }
                self.save()
        except Timeout as exc:
            self.close()
            raise ProductionRejected(
                "CINE_GENERATION_REQUEST_IN_PROGRESS", self.metadata()
            ) from exc
        except BaseException:
            self.close()
            raise

    def validate_record(self) -> None:
        record = self.record
        invalid = (
            record.get("generation_request_id") != self.request_id
            or record.get("project_id") != self.project_id
            or record.get("command_id") != self.command_id
            or record.get("state")
            not in {"prepared", "submitting", "unknown", "submitted", "rejected"}
        )
        command = record.get("command")
        if command is not None:
            invalid = invalid or not isinstance(command, dict)
            if isinstance(command, dict):
                invalid = invalid or any(
                    (
                        command.get("type") != "generation.submit",
                        command.get("commandId") != self.command_id,
                        command.get("idempotencyKey") != self.command_id,
                        command.get("nodeId") != self.intent.get("node_id"),
                        command.get("kind") != self.intent.get("generation_kind"),
                        command.get("expectedContinuationJobId")
                        != self.intent.get("expected_continuation_job_id"),
                    )
                )
        if invalid:
            raise ProductionRejected("CINE_GENERATION_RECEIPT_INVALID", self.metadata())

    def check_pending(self) -> None:
        for path in self.directory.glob("*.json"):
            if path == self.path:
                continue
            pending = _read(path)
            if pending.get("project_id") != self.project_id or pending.get("state") not in {
                "submitting",
                "unknown",
            }:
                continue
            previous = pending.get("intent") or {}
            same_node = (
                bool(self.intent.get("node_id"))
                and previous.get("node_id") == self.intent["node_id"]
            )
            same_artifact = all(
                previous.get(key) == self.intent.get(key)
                for key in (
                    "production_dir",
                    "production_stage",
                    "production_pointer",
                    "generation_kind",
                )
            )
            same_target = same_node or same_artifact
            if same_target:
                raise ProductionRejected(
                    "CINE_GENERATION_OUTCOME_UNKNOWN",
                    {
                        "generation_request_id": pending.get("generation_request_id"),
                        "command_id": pending.get("command_id"),
                        "receipt_path": str(path),
                        "next_step": (
                            "Reconcile or retry the original generation_request_id; "
                            "do not create a new request for this target."
                        ),
                    },
                )

    def metadata(self) -> dict[str, Any]:
        return {
            "generation_request_id": self.request_id,
            "command_id": self.command_id,
            "request_state": self.record.get("state", "prepared"),
            "receipt_path": str(self.path),
        }

    def save(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.directory, delete=False
        ) as stream:
            temp = Path(stream.name)
            json.dump(self.record, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def saved_result(self) -> dict[str, Any] | None:
        receipt = self.record.get("receipt")
        if self.record.get("state") in {"submitted", "rejected"} and isinstance(receipt, dict):
            return {**receipt, **self.metadata(), "replayed": True}
        return None

    async def current_revision(self, client, expected_prompt):
        context, snapshot, data = await generation_context(client, self.project_id, self.intent)
        if context != self.context or (
            expected_prompt is not None and data.get("prompt") != expected_prompt
        ):
            raise ProductionRejected("CINE_GENERATION_REQUEST_CONFLICT", self.metadata())
        return snapshot.get("revision")

    async def submit(
        self, client, command: dict[str, Any], result_fields: dict[str, Any]
    ) -> dict[str, Any]:
        # V3 atomically replays (projectId, idempotencyKey) before checking revisions.
        replayed = self.record.get("state") in {"submitting", "unknown"}
        frozen = self.record.get("command")
        if frozen is not None:
            command = frozen
        else:
            command = {**command, "commandId": self.command_id, "idempotencyKey": self.command_id}
            self.record["command"] = command
            self.record["result_fields"] = result_fields
        try:
            with FileLock(str(self.directory / "registry.lock"), timeout=0):
                self.check_pending()
                self.record["state"] = "submitting"
                self.save()
        except Timeout as exc:
            raise ProductionRejected(
                "CINE_GENERATION_REQUEST_IN_PROGRESS", self.metadata()
            ) from exc
        try:
            response = await client.submit_command(self.project_id, command)
            job = (response.get("response") or {}).get("job") or response.get("job") or {}
            if (
                response.get("accepted") is not True
                or not isinstance(job, dict)
                or not job.get("id")
            ):
                if response.get("accepted") is False and not replayed:
                    result = {
                        "status": "rejected",
                        "outcome": "failed",
                        "error_code": "CINE_GENERATION_SUBMISSION_REJECTED",
                        "error": str(response),
                    }
                    self.record.update(state="rejected", receipt=result)
                    self.save()
                    return {**result, **self.metadata()}
                raise ValueError("Submission did not return an accepted job identity")
            if job.get("projectId", self.project_id) != self.project_id:
                raise ValueError("Submission returned a different project")
        except Exception as exc:  # noqa: BLE001 - persist an unknown outcome after any submit failure
            if (
                isinstance(exc, SeedanceError)
                and exc.status_code in {400, 401, 403, 404, 409, 422}
                and not replayed
            ):
                result = {
                    "status": "rejected",
                    "outcome": "failed",
                    "error_code": exc.code,
                    "error": str(exc),
                    "next_step": (
                        "Fix the stated input error before authorizing a new generation request."
                    ),
                }
                self.record.update(state="rejected", receipt=result)
                self.save()
                return {**result, **self.metadata()}
            self.record["state"] = "unknown"
            self.record["last_error"] = str(exc)
            self.save()
            return {
                "status": "unknown",
                "outcome": "unknown",
                "error_code": "CINE_GENERATION_OUTCOME_UNKNOWN",
                "error": str(exc),
                **self.metadata(),
                "next_step": (
                    "Retry submit_generation with this generation_request_id and the same inputs. "
                    "The saved V3 command and idempotency key will be replayed; "
                    "never change the request ID to recover a missing response."
                ),
            }
        result = {
            **self.record.get("result_fields", result_fields),
            "status": "submitted",
            "outcome": "succeeded",
            "action": "submit_generation",
            "verified": True,
            "job": job,
            "poll_after_seconds": 20,
            "replayed": replayed,
        }
        self.record.update(state="submitted", receipt=result)
        self.save()
        return {**result, **self.metadata()}

    def close(self) -> None:
        self.lock.release()


async def open_generation_request(
    server, session_id, client, *, request_id, project_id, production_dir, intent, proof
):
    if intent.get("prompt") != proof["prompt"]:
        raise ProductionRejected("CINE_PROMPT_MISMATCH")
    response = await server.get(f"/v1/sessions/{session_id}", timeout=10)
    response.raise_for_status()
    session = response.json()
    if (
        session.get("id") != session_id
        or (session.get("labels") or {}).get("seedance.project_id") != project_id
    ):
        raise ProductionRejected("CINE_PROJECT_BINDING_MISMATCH")
    root = Path(session["workspace"]).resolve(strict=True)
    production = inside(root, production_dir)
    directory = (root / ".cine-validation" / "generation-requests").resolve()
    backend = getattr(client, "base_url", None)
    intent = {
        **intent,
        "session_id": session_id,
        "production_dir": str(production),
        "input_hashes": proof.get("input_hashes", {}),
        "backend": backend if isinstance(backend, str) else None,
    }
    if not directory.is_relative_to(root):
        raise ProductionRejected("CINE_PRODUCTION_PATH_ESCAPE")
    if request_id is None:
        if directory.exists():
            check = GenerationRequest(directory, "untracked-check", project_id, intent, {})
            try:
                with FileLock(str(directory / "registry.lock"), timeout=0):
                    check.check_pending()
            except Timeout as exc:
                raise ProductionRejected("CINE_GENERATION_REQUEST_IN_PROGRESS") from exc
        return None
    context, _snapshot, data = await generation_context(client, project_id, intent)
    request = GenerationRequest(directory, request_id, project_id, intent, context)
    request.acquire()
    if intent.get("node_id") and request.record.get("command"):
        expected = request.record["command"].get("expectedPrompt")
        if expected is not None and data.get("prompt") != expected:
            request.close()
            raise ProductionRejected("CINE_GENERATION_REQUEST_CONFLICT", request.metadata())
    return request


async def generation_context(client, project_id, intent):
    context: dict[str, Any] = {}
    snapshot, data = {}, {}
    if intent.get("node_id"):
        snapshot = await client.get_snapshot(project_id)
        node = next(
            (row for row in snapshot.get("nodes", []) if row.get("id") == intent["node_id"]), None
        )
        if node is None:
            raise ProductionRejected("CINE_GENERATION_NODE_MISSING")
        data = node.get("data") or {}
        context = {
            key: data.get(key)
            for key in (
                "model",
                "aspectRatio",
                "size",
                "quality",
                "count",
                "style",
                "durationSec",
                "referenceMode",
                "provider",
                "lastFrameContinuation",
                "turnaroundSourceNodeId",
            )
        }
        if intent.get("model"):
            context["model"] = intent["model"]
        if intent.get("aspect_ratio"):
            context["aspectRatio"] = intent["aspect_ratio"]
        if intent.get("duration_seconds") is not None:
            context["durationSec"] = intent["duration_seconds"]
        context["references"] = await client.get_node_references(project_id, intent["node_id"])
        if intent.get("expected_continuation_job_id") is not None:
            context["continuation"], _ = await validate_continuation(
                client,
                project_id,
                intent["node_id"],
                intent["expected_continuation_job_id"],
                snapshot=snapshot,
                references=context["references"],
            )
        # Optimized prompts retain the canonical source; unrelated canvas edits remain conflicts.
        context["prompt"] = intent["prompt"]
    return context, snapshot, data


async def validate_continuation(
    client, project_id, node_id, expected_job_id, *, snapshot=None, references=None
):
    """Pin a continuation to the exact successful upstream job selected by the plan."""
    if not isinstance(expected_job_id, str) or not expected_job_id.strip() or not node_id:
        raise ProductionRejected("CINE_CONTINUATION_SOURCE_MISMATCH")
    snapshot = snapshot if snapshot is not None else await client.get_snapshot(project_id)
    nodes = snapshot.get("nodes", [])
    slots = [
        node
        for node in nodes
        if node.get("type") == "image"
        and (node.get("data") or {}).get("continuationTargetNodeId") == node_id
    ]
    if len(slots) != 1:
        raise ProductionRejected("CINE_CONTINUATION_SOURCE_MISMATCH")
    slot = slots[0]
    data = slot.get("data") or {}
    source_id = data.get("continuationSourceNodeId")
    output = data.get("continuationSourceOutputRef")
    source = next((node for node in nodes if node.get("id") == source_id), None)
    job = await client.get_job(expected_job_id)
    references = (
        references
        if references is not None
        else await client.get_node_references(project_id, node_id)
    )
    frame = next(
        (
            ref
            for ref in references
            if ref.get("id") == "node:" + slot["id"] and ref.get("kind") == "image"
        ),
        None,
    )
    expected_frame = data.get("activeOutputRef")
    if isinstance(expected_frame, str) and expected_frame.startswith("local://"):
        expected_frame = "/v3/storage/" + expected_frame[8:]
    if (
        data.get("continuationSourceJobId") != expected_job_id
        or not source
        or (source.get("data") or {}).get("activeOutputRef") != output
        or job.get("id") != expected_job_id
        or job.get("projectId") != project_id
        or job.get("nodeId") != source_id
        or job.get("kind") != "video"
        or job.get("status") != "succeeded"
        or not isinstance(output, str)
        or not output
        or output not in (job.get("outputRefs") or [job.get("outputRef")])
        or not frame
        or not expected_frame
        or frame.get("url") != expected_frame
        or not any(
            edge.get("kind") == "sequence"
            and edge.get("from") == source_id
            and edge.get("to") == node_id
            for edge in snapshot.get("edges", [])
        )
    ):
        raise ProductionRejected(
            "CINE_CONTINUATION_SOURCE_MISMATCH",
            {
                "expected_continuation_job_id": expected_job_id,
                "slot_id": slot.get("id"),
                "next_step": (
                    "Wait for or repair the last frame of the plan's exact preceding job; "
                    "do not use an older or replacement job's frame."
                ),
            },
        )
    return {
        "slot_id": slot["id"],
        "source_job_id": expected_job_id,
        "source_output_ref": output,
        "frame_url": frame["url"],
    }, snapshot.get("revision")
