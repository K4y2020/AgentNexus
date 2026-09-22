import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from agentnexus.cine_review import CHUNK_BYTES, read_review
from agentnexus.host.connect import HostProcess
from agentnexus.server.auth import LEVEL_OWNER
from agentnexus.server.routes.cine_review import register_cine_review_routes
from agentnexus.workspace_fs import WorkspaceReader


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    project = tmp_path / "projects/film"
    revision = project / "revisions/r1"
    write(
        tmp_path / ".cine/sessions/session1.json",
        {
            "session_id": "session1",
            "project_path": str(project),
        },
    )
    write(project / "project.json", {"current_revision": "r1"})
    write(project / "source.json", {"source_id": "source1", "path": str(tmp_path / "source.mp4")})
    write(revision / "source_shots.json", [])
    write(
        revision / "evidence_index.json",
        [
            {
                "evidence_id": "image1",
                "source_id": "source1",
                "revision_id": "r1",
                "relative_path": "evidence/image.png",
                "extraction_status": "ok",
            }
        ],
    )
    image = revision / "evidence/image.png"
    image.parent.mkdir()
    image.write_bytes(b"image-bytes")
    (tmp_path / "source.mp4").write_bytes(b"0123456789" * 200000)
    data = {"sourceId": "source1", "revisionId": "r1", "images": [{"id": "image1"}], "cues": []}
    report = revision / "report/report.html"
    report.parent.mkdir()
    report.write_text(
        '<script id="review-data" type="application/json">' + json.dumps(data) + "</script>",
        encoding="utf-8",
    )
    return tmp_path, project, revision, data


def test_host_dispatch_reads_only_bound_report_and_current_token(project):
    root, _, revision, data = project
    reader = WorkspaceReader(root)
    result = HostProcess._dispatch_fs_op(reader, "cine_review", "session1", {})
    assert result["project"] == "projects/film"
    assert read_review(root, "session2", {})["status"] == "not_ready"
    with pytest.raises(ValueError, match="BINDING_REQUIRED"):
        read_review(root, "../session1", {})
    with pytest.raises(ValueError, match="STALE"):
        read_review(root, "session1", {"asset": "video", "token": "old"})
    data["revisionId"] = "wrong"
    html = '<script id="review-data">' + json.dumps(data) + "</script>"
    (revision / "report/report.html").write_text(html, encoding="utf-8")
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        read_review(root, "session1", {})


def test_topic_workspace_accepts_runner_session_via_canonical_topic_binding(tmp_path):
    """A server session may differ from the canonical Bot Topic id."""
    root = tmp_path / "topics/topic1"
    project = root / "projects/film"
    revision = project / "revisions/r1"
    write(
        root / ".cine/sessions/topic1.json",
        {"session_id": "topic1", "project_path": str(project)},
    )
    write(project / "project.json", {"current_revision": "r1"})
    write(project / "source.json", {"source_id": "source1", "path": "inputs/source.mp4"})
    write(revision / "source_shots.json", [])
    write(revision / "evidence_index.json", [])
    data = {"sourceId": "source1", "revisionId": "r1", "images": [], "cues": []}
    report = revision / "report/report.html"
    report.parent.mkdir(parents=True)
    report.write_text(
        '<script id="review-data" type="application/json">' + json.dumps(data) + "</script>",
        encoding="utf-8",
    )

    result = read_review(root, "server-session", {})

    assert result["status"] == "ready"
    assert result["project"] == "projects/film"


def test_new_json_and_old_writer_cannot_leave_stale_report(project):
    root, _, revision, data = project
    report = revision / "report/report.html"
    data["reportSha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
    data["marker"] = "new snapshot"
    write(revision / "report/review.json", data)
    assert read_review(root, "session1", {})["data"]["marker"] == "new snapshot"
    data["marker"] = "legacy refresh"
    report.write_text(
        '<script id="review-data">' + json.dumps(data) + "</script>", encoding="utf-8"
    )
    assert read_review(root, "session1", {})["data"]["marker"] == "legacy refresh"


def test_bound_production_packages_are_topic_scoped_and_selectable(project):
    root, _, _, _ = project
    package = root / "work/production-test"
    outline = package / "outline.json"
    outline.parent.mkdir(parents=True)
    outline.write_text(json.dumps({"characters": ["C01"], "scenes": ["S01"]}), encoding="utf-8")
    outline_hash = hashlib.sha256(outline.read_bytes()).hexdigest()
    write(
        package / "production.json",
        {
            "schema_version": 1,
            "mode": "adaptation",
            "target_seconds": 30,
            "artifacts": {"outline": {"path": "outline.json", "sha256": outline_hash}},
        },
    )
    write(
        package / ".cine-validation/latest.json",
        {
            "status": "native_validated",
            "production_authorized": False,
            "unverified": ["semantic_quality"],
            "stages": [{"stage": "outline", "status": "passed"}],
        },
    )
    report = read_review(root, "session1", {})
    assert [item["id"] for item in report["productionPackages"]] == ["work/production-test"]
    assert report["production"] is None
    selected = read_review(root, "session1", {"production": "work/production-test"})
    assert selected["production"]["artifacts"]["outline"]["characters"] == ["C01"]
    assert "生产包尚未获得生成授权" in selected["production"]["blockers"]
    with pytest.raises(ValueError, match="PRODUCTION_NOT_FOUND"):
        read_review(root, "session1", {"production": "work/other"})


def test_rejects_escaped_evidence_and_limits_chunks(project):
    root, _, revision, _ = project
    result = read_review(root, "session1", {})
    params = {"asset": "video", "token": result["token"]}
    info = read_review(root, "session1", params)
    chunk = read_review(
        root, "session1", {**params, "etag": info["etag"], "offset": 5, "length": 7}
    )
    assert base64.b64decode(chunk["content"]) == b"5678901"
    with pytest.raises(ValueError, match="RANGE_INVALID"):
        read_review(root, "session1", {**params, "offset": 0, "length": CHUNK_BYTES + 1})
    with pytest.raises(ValueError, match="NOT_IN_REPORT"):
        read_review(root, "session1", {"asset": "missing"})
    (root / "outside.png").write_bytes(b"private")
    write(
        revision / "evidence_index.json",
        [
            {
                "evidence_id": "image1",
                "source_id": "source1",
                "revision_id": "r1",
                "relative_path": "../../../../outside.png",
                "extraction_status": "ok",
            }
        ],
    )
    with pytest.raises(ValueError, match="PATH_ESCAPE"):
        read_review(root, "session1", {"asset": "image1"})


def test_media_range_auth_and_disconnect_contract(project):
    root, _, _, _ = project
    app, router = FastAPI(), APIRouter()
    calls = []

    async def authorize(session, request, level):
        assert level == LEVEL_OWNER
        if request.headers.get("x-owner") != "yes":
            raise HTTPException(403)
        return SimpleNamespace(workspace=str(root))

    async def read_host(session, conv, op, params):
        calls.append(params)
        return read_review(root, session, params)

    register_cine_review_routes(router, authorize, read_host)
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/sessions/session1/cine-review").status_code == 403
    assert not calls
    headers = {"x-owner": "yes"}
    result = client.get("/sessions/session1/cine-review", headers=headers).json()
    path = f"/sessions/session1/cine-review/assets/video?token={result['token']}"
    response = client.get(path, headers={**headers, "range": "bytes=5-11"})
    assert response.status_code == 206 and response.content == b"5678901"
    assert response.headers["content-range"] == "bytes 5-11/2000000"
    assert client.get(path, headers={**headers, "range": "bytes=-3"}).content == b"789"
    assert client.get(path, headers={**headers, "range": "bytes=2000000-"}).status_code == 416
    assert client.get(path, headers={**headers, "range": "bytes=0-1,5-8"}).status_code == 416
    response = client.head(path, headers=headers)
    assert response.status_code == 200 and response.content == b""
    assert response.headers["content-length"] == "2000000"
    assert client.get(path, headers=headers).content == (root / "source.mp4").read_bytes()
