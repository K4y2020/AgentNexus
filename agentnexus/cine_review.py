"""Read a bound Cine report and bounded media chunks on its owning host."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from html.parser import HTMLParser
from pathlib import Path

from agentnexus.seedance.cine_contracts import _inside, current_ledger
from agentnexus.workspace_fs import WorkspaceReaderError

CHUNK_BYTES = 1024 * 1024
PRODUCTION_JSON_LIMIT = 8 * CHUNK_BYTES


class _ReportData(HTMLParser):
    """Extract JSON from older reports without executing their HTML/scripts."""

    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        self.active = tag == "script" and dict(attrs).get("id") == "review-data"

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def _snapshot(revision):
    path = _inside(revision, revision / "report/report.html")
    with path.open("rb") as handle:
        raw = handle.read(8 * CHUNK_BYTES + 1)
    if len(raw) > 8 * CHUNK_BYTES:
        raise ValueError("CINE_REVIEW_TOO_LARGE")
    html_token = hashlib.sha256(raw).hexdigest()
    snapshot_path = revision / "report/review.json"
    if snapshot_path.exists():
        data, token = _json(_inside(revision, snapshot_path))
        if data.get("reportSha256") == html_token:
            return data, token
    parser = _ReportData()
    parser.feed(raw.decode("utf-8"))
    return json.loads("".join(parser.parts)), html_token


def _json(path: Path):
    with path.open("rb") as handle:
        raw = handle.read(8 * CHUNK_BYTES + 1)
    if len(raw) > 8 * CHUNK_BYTES:
        raise ValueError("CINE_REVIEW_TOO_LARGE")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _production_dirs(workspace: Path) -> list[Path]:
    root = workspace.resolve()
    work = (root / "work").resolve()
    if not work.is_relative_to(root):
        raise ValueError("CINE_WORK_PATH_ESCAPE")
    if not work.is_dir():
        return []
    candidates = []
    direct = work / "production.json"
    if direct.is_file():
        candidates.append(work)
    for child in work.iterdir():
        if child.is_dir() and (child / "production.json").is_file():
            candidates.append(child)
    return sorted(candidates, key=lambda path: path.name.casefold())


def _text_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _text_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _text_values(child)


def _production_payload(workspace: Path, package: Path) -> dict:
    manifest, manifest_token = _json(_inside(package, package / "production.json"))
    artifacts = {}
    hash_status = []
    for stage, entry in (manifest.get("artifacts") or {}).items():
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            hash_status.append(
                {"stage": stage, "match": False, "reason": "invalid manifest entry"}
            )
            continue
        path = _inside(package, package / entry["path"])
        try:
            data, _ = _json(path)
            artifacts[stage] = data
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            hash_status.append({
                "stage": stage,
                "match": actual == entry.get("sha256"),
            })
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            hash_status.append({"stage": stage, "match": False, "reason": str(exc)})

    validation = None
    validation_path = package / ".cine-validation" / "latest.json"
    if validation_path.is_file():
        try:
            validation, _ = _json(_inside(package, validation_path))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            validation = {"status": "invalid"}

    blockers = []
    if not validation:
        blockers.append("尚未找到 production finalize 校验报告")
    elif validation.get("production_authorized") is not True:
        blockers.append("生产包尚未获得生成授权")
    for reason in (validation or {}).get("unverified", []):
        blockers.append(f"未验证：{reason}")
    undefined = sum(
        value.casefold().count("subject undefined") for value in _text_values(artifacts)
    )
    if undefined:
        blockers.append(f"H3 提示词包含 {undefined} 处 Subject undefined")
    source_material = artifacts.get("source_material") or {}
    if source_material.get("review_status_note") == "exported_unverified":
        blockers.append("原片来源材料仍是 unverified，只能作为改编参考")

    mapping = artifacts.get("mapping") or []
    return {
        "id": package.relative_to(workspace).as_posix(),
        "name": package.name,
        "mode": manifest.get("mode"),
        "scope": manifest.get("scope"),
        "targetSeconds": manifest.get("target_seconds"),
        "stages": sorted((manifest.get("artifacts") or {}).keys()),
        "hashStatus": hash_status,
        "hashesMatch": bool(hash_status) and all(row["match"] for row in hash_status),
        "mappingCount": len(mapping) if isinstance(mapping, list) else 0,
        "validation": {
            "status": validation.get("status") if validation else "missing",
            "runId": validation.get("run_id") if validation else None,
            "productionAuthorized": (
                validation.get("production_authorized") if validation else False
            ),
            "unverified": validation.get("unverified", []) if validation else [],
            "stageStatuses": {
                row.get("stage"): row.get("status")
                for row in (validation or {}).get("stages", [])
                if isinstance(row, dict) and row.get("stage")
            },
        },
        "blockers": blockers,
        "manifestToken": manifest_token,
        "artifacts": artifacts,
    }


def _production_packages(workspace: Path) -> list[dict]:
    packages = []
    for package in _production_dirs(workspace):
        try:
            packages.append(_production_payload(workspace, package))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return packages


def _script_packages(
    workspace: Path, source_id: str | None = None, revision_id: str | None = None
) -> list[dict]:
    outputs = (workspace / "outputs").resolve()
    if not outputs.is_dir():
        return []
    packages = []
    script_candidates = sorted(
        list(outputs.glob("*-script.json")) + list(outputs.glob("script.json")),
        key=lambda p: p.name.casefold(),
    )
    for script_file in script_candidates:
        try:
            raw = script_file.read_text(encoding="utf-8")
            sdata = json.loads(raw)
            episodes = sdata.get("episodes") or []
            ep = episodes[0] if episodes else {}
            title = sdata.get("title") or script_file.stem.replace("-script", "")
            target_sec = ep.get("targetSeconds")
            scenes = ep.get("scenes") or []
            manifest_token = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            packages.append({
                "id": script_file.relative_to(workspace).as_posix(),
                "name": f"剧本 · {title}",
                "mode": "改编剧本",
                "scope": f"{len(scenes)} 场戏",
                "targetSeconds": target_sec,
                "stages": ["script"],
                "hashStatus": [{"stage": "script", "match": True}],
                "hashesMatch": True,
                "mappingCount": 0,
                "validation": {
                    "status": "draft",
                    "productionAuthorized": False,
                    "unverified": [],
                    "stageStatuses": {"script": "ready"},
                },
                "manifestToken": manifest_token,
                "artifacts": {
                    "script": sdata,
                    "source_material": {
                        "source_id": source_id or "",
                        "revision_id": revision_id or "",
                    },
                },
            })
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return packages


def read_review(workspace: Path, session_id: str, params: dict) -> dict:
    """No arbitrary path parameter: assets must belong to the current source revision."""
    try:
        ledger = current_ledger(workspace, session_id)
        revision = ledger.parent
        project = revision.parent.parent
        source, _ = _json(_inside(project, project / "source.json"))
        data, token = _snapshot(revision)
        data["sourceName"] = Path(source["path"]).name
        if data["revisionId"] != revision.name or data["sourceId"] != source["source_id"]:
            raise ValueError("CINE_REVIEW_IDENTITY_MISMATCH")
        if params.get("token") and params["token"] != token:
            raise ValueError("CINE_REVIEW_STALE: refresh the report view")
        asset = params.get("asset")
        if asset is None:
            production_packages = _production_packages(workspace.resolve()) + _script_packages(
                workspace.resolve(), data.get("sourceId"), data.get("revisionId")
            )
            selected_production = None
            requested_production = params.get("production")
            if requested_production:
                selected_production = next(
                    (item for item in production_packages if item["id"] == requested_production),
                    None,
                )
                if selected_production is None:
                    raise ValueError("CINE_PRODUCTION_NOT_FOUND")
            return {
                "status": "ready",
                "token": token,
                "project": project.relative_to(workspace.resolve()).as_posix(),
                "data": data,
                "productionPackages": [
                    {key: value for key, value in item.items() if key != "artifacts"}
                    for item in production_packages
                ],
                "production": selected_production,
            }
        if asset == "video":
            # Source selection is explicit in source.json. Server access is owner-only.
            path = Path(source["path"]).resolve(strict=True)
            if path.suffix.lower() not in {".mp4", ".mov", ".webm", ".m4v"}:
                raise ValueError("CINE_MEDIA_TYPE_UNSUPPORTED")
            if source.get("size_bytes") and path.stat().st_size != source["size_bytes"]:
                raise ValueError("CINE_SOURCE_CHANGED: reindex the selected source")
        else:
            evidence, _ = _json(_inside(revision, revision / "evidence_index.json"))
            matches = [
                row
                for row in evidence
                if row.get("evidence_id") == asset
                and row.get("source_id") == source["source_id"]
                and row.get("revision_id") == revision.name
                and row.get("extraction_status") == "ok"
            ]
            if len(matches) != 1 or not any(row["id"] == asset for row in data["images"]):
                raise ValueError("CINE_EVIDENCE_NOT_IN_REPORT")
            path = _inside(revision, revision / matches[0]["relative_path"].replace("\\", "/"))
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                raise ValueError("CINE_EVIDENCE_TYPE_UNSUPPORTED")
        stat = path.stat()
        etag = f"{token}:{stat.st_size}:{stat.st_mtime_ns}"
        result = {
            "size": stat.st_size,
            "etag": etag,
            "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        }
        if "offset" in params:
            offset, length = params["offset"], params.get("length", CHUNK_BYTES)
            if (
                type(offset) is not int
                or type(length) is not int
                or offset < 0
                or offset >= stat.st_size
                or not 0 < length <= CHUNK_BYTES
            ):
                raise ValueError("CINE_MEDIA_RANGE_INVALID")
            if params.get("etag") != etag:
                raise ValueError("CINE_MEDIA_CHANGED")
            with path.open("rb") as handle:
                handle.seek(offset)
                result["content"] = base64.b64encode(handle.read(length)).decode("ascii")
        return result
    except FileNotFoundError as exc:
        if params.get("asset") is None:
            return {
                "status": "not_ready",
                "message": "当前 Topic 尚无可用拉片报告，请让 Cine 刷新报告。",
            }
        raise WorkspaceReaderError(404, "not_found", "Cine media is missing") from exc
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("CINE_REVIEW_INVALID: refresh the bound report") from exc
