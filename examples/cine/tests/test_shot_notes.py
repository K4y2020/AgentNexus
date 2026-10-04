"""Per-shot location reading, batching, exclusions and resumable cache."""

import copy
import json
import sys
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import shot_notes as notes

GATEWAY = ("http://gateway.test/v1", "key", "vision")
LOCATIONS = [
    {"id": "L1", "name": "停车库", "setting": "车位标线、承重柱"},
    {"id": "L2", "name": "检修车间", "setting": "工作台、升降架"},
]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    def create(count=26, teaser_end=None):
        root = tmp_path / "projects" / "film"
        write(root / "project.json", {"current_revision": "r1"})
        write(root / "source.json", {"source_id": "src", "start_pts": 10})
        shots = [
            {
                "source_id": "src",
                "revision_id": "r1",
                "shot_id": f"shot-{i}",
                "interval": {
                    "in_pts": 10 + i * 2,
                    "out_pts": 12 + i * 2,
                    "time_base_num": 1,
                    "time_base_den": 2,
                },
            }
            for i in range(count)
        ]
        write(root / "revisions/r1/source_shots.json", shots)
        plan = (
            {"opening_teaser": {"status": "found", "end_seconds": teaser_end}}
            if teaser_end
            else {}
        )
        write(root / "revisions/r1/story_plan.json", plan)
        return root

    monkeypatch.setattr(notes, "_frame", lambda *_args, **_kwargs: b"jpeg")
    return create


def reply(batch, place="L1", new=None):
    return {
        "new": new or [],
        "people": [],
        "shots": [
            {
                "n": shot["index"],
                "place": place,
                "what": "空车位旁停着汽车",
                "people": [],
                "crowd": False,
            }
            for shot, _ in batch
        ],
    }


def test_batches_carry_known_locations_and_keep_the_ledger(monkeypatch, project):
    root = project()
    ledger = root / "revisions/r1/source_shots.json"
    before = ledger.read_bytes()
    asked, frames = [], []

    def ask(known, batch, gateway, **_kwargs):
        assert gateway == GATEWAY
        asked.append((copy.deepcopy(known), [s["index"] for s, _ in batch]))
        return reply(batch, new=[LOCATIONS[0]] if not known else [])

    monkeypatch.setattr(notes, "_ask", ask)
    monkeypatch.setattr(notes, "_frame", lambda _m, t, _p, **_kwargs: frames.append(t) or b"jpeg")
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)

    assert asked == [([], list(range(24))), ([LOCATIONS[0]], [24, 25])]
    assert frames[:4] == [0.3, 0.7, 1.3, 1.7]
    assert result["status"] == "read"
    assert result["locations"][0]["spans"] == [{"start": 0, "end": 26, "shots": list(range(26))}]
    assert result["locations"][0]["seconds"] == 26
    assert ledger.read_bytes() == before


def test_later_batch_repeated_location_keeps_the_first_definition(monkeypatch, project):
    root = project(48)

    def ask(known, batch, _gateway, **_kwargs):
        location = (
            {"id": "L1", "name": "同一车库", "setting": "承重柱和车位"} if known else LOCATIONS[0]
        )
        return reply(batch, new=[location])

    monkeypatch.setattr(notes, "_ask", ask)
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert result["status"] == "read" and not result["errors"]
    assert not result["unread_shots"] and all(shot["place"] == "L1" for shot in result["shots"])
    assert result["locations"][0]["name"] == LOCATIONS[0]["name"]
    assert result["locations"][0]["setting"] == LOCATIONS[0]["setting"]
    assert result["locations"][0]["seconds"] == 48


def test_insert_unknown_and_teaser_break_spans_and_do_not_count(monkeypatch, project):
    root = project(6, teaser_end=1)

    def ask(_known, batch, _gateway, **_kwargs):
        result = reply(batch, new=LOCATIONS)
        for item, place in zip(
            result["shots"], ["L2", "L1", "insert", "L1", "?", "L1"], strict=True
        ):
            item["place"] = place
        return result

    monkeypatch.setattr(notes, "_ask", ask)
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert [loc["id"] for loc in result["locations"]] == ["L1"]
    assert result["shots"][0]["teaser"] is True
    assert result["locations"][0]["seconds"] == 3
    assert [s["shots"] for s in result["locations"][0]["spans"]] == [[1], [3], [5]]


def test_a_shot_crossing_the_teaser_boundary_counts_only_body_time(monkeypatch, project):
    root = project(2, teaser_end=0.6)
    monkeypatch.setattr(
        notes, "_ask", lambda _k, batch, _g, **_kwargs: reply(batch, new=[LOCATIONS[0]])
    )
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert result["locations"][0]["spans"][0]["start"] == 0.6
    assert result["locations"][0]["seconds"] == 1.4


def test_failed_batch_is_partial_and_next_run_reads_only_that_batch(monkeypatch, project):
    root = project()
    calls = []

    def ask(known, batch, _gateway, **_kwargs):
        calls.append([s["index"] for s, _ in batch])
        if batch[0][0]["index"] == 24:
            raise OSError("gateway unreachable")
        return reply(batch, new=[LOCATIONS[0]] if not known else [])

    monkeypatch.setattr(notes, "_ask", ask)
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert result["status"] == "partial" and result["unread_shots"] == [24, 25]
    assert "gateway unreachable" in result["errors"][0]
    assert all(s["place"] == "?" for s in result["shots"][24:])
    monkeypatch.setattr(
        notes,
        "_ask",
        lambda _known, batch, _g, **_kwargs: (
            calls.append([s["index"] for s, _ in batch]) or reply(batch)
        ),
    )
    recovered = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert calls[-1] == [24, 25]
    assert recovered["status"] == "read" and recovered["errors"] == []
    assert recovered["locations"][0]["seconds"] == 26


def test_complete_result_is_reused_without_extracting_or_requesting(monkeypatch, project):
    root = project(2)
    monkeypatch.setattr(
        notes, "_ask", lambda _k, batch, _g, **_kwargs: reply(batch, new=[LOCATIONS[0]])
    )
    first = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)

    def unexpected(*_args, **_kwargs):
        pytest.fail("complete result should be reused")

    monkeypatch.setattr(notes, "_frame", unexpected)
    monkeypatch.setattr(notes, "_ask", unexpected)
    assert notes.read_shot_notes("film.mp4", root, gateway=GATEWAY) == first


@pytest.mark.parametrize("change", ["model", "ledger", "teaser", "schema"])
def test_changed_inputs_are_not_reused(monkeypatch, project, change):
    root = project(2)
    calls = []
    monkeypatch.setattr(
        notes,
        "_ask",
        lambda _k, batch, _g, **_kwargs: calls.append(True) or reply(batch, new=[LOCATIONS[0]]),
    )
    notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    gateway = GATEWAY
    if change == "model":
        gateway = (*GATEWAY[:2], "new-vision")
    elif change == "ledger":
        ledger = root / "revisions/r1/source_shots.json"
        ledger.write_text(ledger.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    elif change == "teaser":
        write(
            root / "revisions/r1/story_plan.json",
            {"opening_teaser": {"status": "found", "end_seconds": 1}},
        )
    else:
        path = root / "revisions/r1/shot_notes.json"
        cache = json.loads(path.read_text(encoding="utf-8"))
        write(path, {**cache, "schema_version": 1})
    notes.read_shot_notes("film.mp4", root, gateway=gateway)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"new": [], "shots": [{"n": 0, "place": "L99", "what": "交谈"}]},
        {"new": [], "shots": []},
    ],
)
def test_bad_or_missing_model_rows_are_partial(monkeypatch, project, bad):
    root = project(2)
    monkeypatch.setattr(notes, "_ask", lambda *_a, **_kwargs: bad)
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert result["status"] == "partial" and result["errors"]


def test_missing_configuration_is_saved_as_unavailable(project):
    root = project(2)
    result = notes.read_shot_notes("film.mp4", root, gateway=(None, None, None))
    assert result["status"] == "unavailable" and not result["locations"]
    assert (root / "revisions/r1/shot_notes.json").is_file()


def test_people_ids_and_first_appearance_survive_across_batches(monkeypatch, project):
    root = project(26)
    first = {"id": "P1", "look": "短发男子，灰色外套", "name_caption": None}
    seen = []

    def ask(_known, batch, _gateway, *, people):
        seen.append(copy.deepcopy(people))
        result = reply(batch, new=[LOCATIONS[0]])
        result["people"] = [
            {**first, "look": "相同男子，换了蓝外套", "name_caption": "乘客"} if people else first
        ]
        for shot in result["shots"]:
            shot["people"] = ["P1"]
        return result

    monkeypatch.setattr(notes, "_ask", ask)
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert result["schema_version"] == 3 and result["status"] == "read"
    assert seen == [[], [first]]
    assert result["people"] == [{**first, "name_caption": "乘客"}]
    assert all(s["people"] == ["P1"] for s in result["shots"])


@pytest.mark.parametrize("caption", [4, [], {}])
def test_name_caption_must_be_text_or_null(monkeypatch, project, caption):
    root = project(1)
    result = reply([({"index": 0}, [])], new=[LOCATIONS[0]])
    result["people"] = [{"id": "P1", "look": "短发男子", "name_caption": caption}]
    result["shots"][0]["people"] = ["P1"]
    monkeypatch.setattr(notes, "_ask", lambda *_a, **_k: result)
    found = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert found["status"] == "partial" and found["unread_shots"] == [0]
    assert "invalid person" in found["errors"][0]


def test_damaged_people_catalog_cannot_be_reused(monkeypatch, project):
    root = project(1)
    calls = []
    monkeypatch.setattr(
        notes, "_ask", lambda _k, b, _g, **_kw: calls.append(1) or reply(b, new=[LOCATIONS[0]])
    )
    notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    path = root / "revisions/r1/shot_notes.json"
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached["people"] = [{"id": "P1", "look": 7, "name_caption": None}]
    write(path, cached)
    notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert calls == [1, 1]


def test_unused_empty_location_placeholder_does_not_discard_valid_shots(monkeypatch, project):
    root = project(1)
    result = reply(
        [({"index": 0}, [])], new=[LOCATIONS[0], {"id": "L2", "name": None, "setting": None}]
    )
    monkeypatch.setattr(notes, "_ask", lambda *_a, **_k: result)
    found = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert found["status"] == "read" and len(found["locations"]) == 1


@pytest.mark.parametrize("content", ['{"new":[],"people":[],"shots":[]}', "invalid JSON"])
def test_request_records_actual_gateway_usage_even_when_model_json_is_invalid(
    monkeypatch, content
):
    usage = {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500}
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(
        {
            "choices": [{"message": {"content": content}}],
            "usage": usage,
        }
    ).encode("utf-8")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: response)
    if content == "invalid JSON":
        with pytest.raises(notes.ReplyError) as error:
            notes._ask([], [], GATEWAY, people=[])
        stats = error.value.request_stats
    else:
        stats = notes._ask([], [], GATEWAY, people=[])["_request_stats"]
    assert stats["usage"] == usage
    assert stats["output_characters"] == len(content)
    assert stats["seconds"] >= 0 and stats["parse_seconds"] >= 0


def test_partial_resume_retains_failed_request_measurements_and_complete_cache(
    monkeypatch, project
):
    root = project(2)
    stats = {"usage": {"completion_tokens": 900}, "seconds": 1.5, "parse_seconds": 0.01}

    def bad(*_a, **_k):
        raise notes.ReplyError("invalid JSON", stats)

    monkeypatch.setattr(notes, "_ask", bad)
    first = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert first["attempts"][0]["batches"][0]["request"] == stats
    monkeypatch.setattr(notes, "_ask", lambda _k, b, _g, **_kw: reply(b, new=[LOCATIONS[0]]))
    second = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert second["status"] == "read" and len(second["attempts"]) == 2
    assert second["attempts"][0] == first["attempts"][0]
    monkeypatch.setattr(
        notes, "_ask", lambda *_a, **_k: pytest.fail("complete cache must be reused")
    )
    assert notes.read_shot_notes("film.mp4", root, gateway=GATEWAY) == second


def test_failed_shot_request_reuses_frames_on_resume(monkeypatch, project, tmp_path):
    root = project(2)
    media = tmp_path / "film.mp4"
    media.write_bytes(b"video")
    extracted = []
    monkeypatch.setattr(notes, "_frame", lambda *_a: extracted.append(True) or b"jpeg")
    monkeypatch.setattr(
        notes, "_ask", lambda *_a, **_k: (_ for _ in ()).throw(OSError("retry later"))
    )
    cache = tmp_path / "frames"
    assert (
        notes.read_shot_notes(media, root, gateway=GATEWAY, frame_cache=cache)["status"]
        == "partial"
    )
    assert len(extracted) == 4

    monkeypatch.setattr(
        notes, "_ask", lambda _k, batch, _g, **_kw: reply(batch, new=[LOCATIONS[0]])
    )
    assert (
        notes.read_shot_notes(media, root, gateway=GATEWAY, frame_cache=cache)["status"] == "read"
    )
    assert len(extracted) == 4


def test_group_shot_keeps_known_people_and_unnumbered_crowd(monkeypatch, project):
    root = project(26)
    person = {"id": "P1", "look": "戴紫色帽子的游客", "name_caption": None}

    def ask(_known, batch, _gateway, *, people):
        result = reply(batch, new=[LOCATIONS[0]])
        result["people"] = [] if people else [person]
        for shot in result["shots"]:
            shot.update(people=["P1"], crowd=True, what="P1 与远处游客一起等车")
        return result

    monkeypatch.setattr(notes, "_ask", ask)
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert result["status"] == "read"
    assert all(s["people"] == ["P1"] and s["crowd"] for s in result["shots"])
    monkeypatch.setattr(notes, "_ask", lambda *_a, **_k: pytest.fail("reuse crowd evidence"))
    assert notes.read_shot_notes("film.mp4", root, gateway=GATEWAY) == result


@pytest.mark.parametrize("bad", [None, "true", 1, []])
def test_crowd_requires_a_real_boolean(monkeypatch, project, bad):
    root = project(1)
    result = reply([({"index": 0}, [])], new=[LOCATIONS[0]])
    result["shots"][0]["crowd"] = bad
    monkeypatch.setattr(notes, "_ask", lambda *_a, **_k: result)
    found = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert found["status"] == "partial"
    assert "crowd must be a boolean" in found["errors"][0]


def test_old_schema_and_missing_crowd_cache_are_reread(monkeypatch, project):
    root = project(1)
    calls = []
    monkeypatch.setattr(
        notes, "_ask", lambda _k, b, _g, **_kw: calls.append(1) or reply(b, new=[LOCATIONS[0]])
    )
    notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    path = root / "revisions/r1/shot_notes.json"
    for version in (2, 3):
        old = json.loads(path.read_text(encoding="utf-8"))
        old["schema_version"] = version
        old["shots"][0].pop("crowd")
        write(path, old)
        assert notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)["schema_version"] == 3
    assert len(calls) == 3


def test_one_bad_location_costs_only_its_shots_and_a_retry_recovers_them(monkeypatch, project):
    root = project(4)
    calls = []

    def ask(_known, batch, _gateway, *, hint=None, **_kwargs):
        calls.append(([s["index"] for s, _ in batch], hint))
        result = reply(batch, new=[LOCATIONS[0]])
        if hint is None:
            # The new location has no setting, and two shots use it.
            result["new"].append({"id": "L2", "name": "车间"})
            result["shots"][2]["place"] = "L2"
            result["shots"][3]["place"] = "L2"
        else:
            result["new"] = [LOCATIONS[1]]
            for item in result["shots"]:
                item["place"] = "L2"
        return result

    monkeypatch.setattr(notes, "_ask", ask)
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert calls[0] == ([0, 1, 2, 3], None)
    assert calls[1][0] == [2, 3] and "L2" in calls[1][1]
    assert result["status"] == "read" and not result["unread_shots"] and not result["errors"]
    assert [s["place"] for s in result["shots"]] == ["L1", "L1", "L2", "L2"]


def test_a_reply_that_stays_malformed_is_retried_twice_then_left_partial(monkeypatch, project):
    root = project(2)
    calls = []

    def ask(_known, _batch, _gateway, *, hint=None, **_kwargs):
        calls.append(hint)
        return {"new": [], "people": [], "shots": [{"n": 0, "place": "L9", "what": "交谈"}]}

    monkeypatch.setattr(notes, "_ask", ask)
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert len(calls) == 1 + notes.RETRIES and calls[0] is None and all(calls[1:])
    assert result["status"] == "partial" and result["unread_shots"] == [0, 1]
    assert "unknown location L9" in result["errors"][0]


def test_an_unreachable_gateway_is_not_retried_in_the_same_run(monkeypatch, project):
    root = project(2)
    calls = []

    def ask(*_args, **_kwargs):
        calls.append(1)
        raise OSError("gateway unreachable")

    monkeypatch.setattr(notes, "_ask", ask)
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert calls == [1] and result["status"] == "partial"


def test_shot_language_is_kept_and_an_unknown_value_stays_empty(monkeypatch, project):
    root = project(3)

    def ask(_known, batch, _gateway, **_kwargs):
        result = reply(batch, new=[LOCATIONS[0]])
        for item, size, camera in zip(
            result["shots"],
            ["wide", "close", "huge"],
            ["Static Shot", "Push In", "Spin"],
            strict=True,
        ):
            item.update(size=size, camera=camera)
        return result

    monkeypatch.setattr(notes, "_ask", ask)
    monkeypatch.setattr(notes, "_black_intervals", lambda _media: [(1.9, 2.1)])
    result = notes.read_shot_notes("film.mp4", root, gateway=GATEWAY)
    assert [(s["size"], s["camera"]) for s in result["shots"]] == [
        ("wide", "Static Shot"),
        ("close", "Push In"),
        (None, None),
    ]
    # The third shot starts at 2 s, beside the black stretch: a dip to black.
    assert [s["transition"] for s in result["shots"]] == ["cut", "cut", "fade"]
