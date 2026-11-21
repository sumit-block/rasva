"""Phase I3 "Change outfit": request building, roles, text fallback, plain-language
validation, and a full run through the queue with I1's fake image editor."""

from __future__ import annotations

import logging
import sqlite3
import struct
import zlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from rasva.services import outfit
from tests.conftest import make_client, wait_for

EDITOR = "tests.fake_image_editor:FakeImageEditor"
EDITOR_ID = "test-image-editor"
PID = "prj_1"


def png(color: tuple[int, int, int] = (200, 30, 30), size: int = 4) -> bytes:
    row = b"\x00" + bytes(color) * size
    raw = zlib.compress(row * size)

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def asset(aid: str, *, project: str = PID, kind: str = "reference", media: str = "image/png"):
    return {"id": aid, "project_id": project, "kind": kind, "media_type": media}


PERSON, GARMENT = asset("ast_person"), asset("ast_garment")


# ---------------------------------------------------------------- builder


def test_photo_request_orders_references_and_keeps_the_person():
    p = outfit.plan(project_id=PID, person=PERSON, garment=GARMENT)
    assert p.references == [
        {"asset_id": "ast_person", "role": "person"},
        {"asset_id": "ast_garment", "role": "garment"},
    ]
    assert p.prompt.startswith("Dress the person in image 1 in the clothing shown in image 2.")
    for kept in ("face", "identity", "hair", "skin tone", "body shape", "hands", "pose"):
        assert kept in p.prompt
    assert "background, lighting and framing" in p.prompt
    assert p.workflow == {
        "name": "outfit",
        "instruction_version": outfit.INSTRUCTION_VERSION,
        "garment_source": "photo",
        "garment_type": "auto",
        "keep_background": True,
        "person_asset_id": "ast_person",
        "garment_asset_id": "ast_garment",
    }
    assert p.inputs() == {"prompt": p.prompt, "references": p.references}


def test_text_fallback_uses_only_the_person_photo():
    p = outfit.plan(
        project_id=PID, person=PERSON, description="  a maroon silk saree\nwith a gold border. "
    )
    assert p.references == [{"asset_id": "ast_person", "role": "person"}]
    assert "image 2" not in p.prompt
    assert "to: a maroon silk saree with a gold border." in p.prompt
    assert p.workflow["garment_source"] == "text"
    assert p.workflow["garment_asset_id"] is None


def test_photo_and_text_records_both_and_photo_leads():
    p = outfit.plan(project_id=PID, person=PERSON, garment=GARMENT, description="full sleeves")
    assert p.workflow["garment_source"] == "photo_and_text"
    assert "shown in image 2" in p.prompt
    assert p.prompt.index("image 2") < p.prompt.index("Garment details: full sleeves.")


@pytest.mark.parametrize(
    ("kind", "named", "leave"),
    [
        ("top", "top (the upper-body garment)", "Leave the lower-body clothing as it is."),
        ("bottom", "lower-body garment", "Leave the upper-body clothing as it is."),
        ("full", "whole outfit", None),
    ],
)
def test_garment_type_changes_the_wording(kind, named, leave):
    p = outfit.plan(project_id=PID, person=PERSON, garment=GARMENT, garment_type=kind)
    assert f"in the {named} shown in image 2" in p.prompt
    if leave:
        assert leave in p.prompt
    assert p.workflow["garment_type"] == kind


def test_background_can_change():
    p = outfit.plan(project_id=PID, person=PERSON, garment=GARMENT, keep_background=False)
    assert outfit.KEEP_SCENE not in p.prompt
    assert outfit.KEEP_PERSON in p.prompt
    assert p.workflow["keep_background"] is False


def test_description_control_characters_are_removed():
    p = outfit.plan(project_id=PID, person=PERSON, description="red\x00 kurta​")
    assert "red kurta" in p.prompt
    assert "\x00" not in p.prompt


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"person": None, "garment": GARMENT}, "Choose a photo of the person first."),
        ({"person": PERSON}, "Add a photo of the garment, or describe it in words."),
        ({"person": PERSON, "description": "   "}, "Add a photo of the garment"),
        ({"person": PERSON, "description": "ab"}, "Describe the garment in a few more words."),
        ({"person": PERSON, "description": "x" * 301}, "under 300 characters"),
        ({"person": PERSON, "garment": PERSON}, "Choose a different photo for the garment"),
        (
            {"person": asset("ast_other", project="prj_2"), "garment": GARMENT},
            "The person photo is not in this project.",
        ),
        (
            {"person": PERSON, "garment": asset("ast_g", project="prj_2")},
            "The garment photo is not in this project.",
        ),
        (
            {"person": asset("ast_a", media="audio/wav"), "garment": GARMENT},
            "The person photo must be a PNG, JPEG or WebP photo added to this project.",
        ),
        (
            {"person": asset("ast_t", kind="import"), "garment": GARMENT},
            "The person photo must be a PNG, JPEG or WebP photo added to this project.",
        ),
        (
            {"person": asset("ast_o", kind="output"), "description": "a kurta"},
            "The person photo must be a PNG, JPEG or WebP photo added to this project.",
        ),
        (
            {"person": PERSON, "garment": GARMENT, "garment_type": "hat"},
            "Choose what to change: the top, the bottom, or the whole outfit.",
        ),
    ],
)
def test_validation_messages_are_plain(kwargs, message):
    with pytest.raises(outfit.OutfitError, match=message.replace("(", r"\(")):
        outfit.plan(project_id=PID, **kwargs)


# ---------------------------------------------------------------- API


@pytest.fixture
def client(settings) -> Iterator[TestClient]:
    with make_client(settings, extra_engines=(EDITOR,)) as c:
        yield c


@pytest.fixture
def project(client: TestClient) -> dict:
    r = client.post("/api/v1/projects", json={"name": "Looks", "module": "image"})
    assert r.status_code == 201, r.text
    return r.json()


def upload(client: TestClient, project_id: str, name: str = "p.png", role: str = "person"):
    r = client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": (name, png(), "image/png")},
        data={"kind": "reference", "role": role},
    )
    assert r.status_code == 201, r.text
    return r.json()


def change(client: TestClient, project_id: str, **body):
    return client.post(f"/api/v1/projects/{project_id}/outfit", json=body)


def test_garment_photo_job_runs_and_records_the_source(client, project):
    person = upload(client, project["id"])
    garment = upload(client, project["id"], "g.png", "garment")
    r = change(
        client, project["id"], engine_id=EDITOR_ID, person_asset_id=person["id"],
        garment_asset_id=garment["id"], garment_type="full", seed=7,
    )  # fmt: skip
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["task"] == "image.edit"
    inputs = job["inputs"]
    assert [x["role"] for x in inputs["references"]] == ["person", "garment"]
    assert [x["asset_id"] for x in inputs["references"]] == [person["id"], garment["id"]]
    assert inputs["seed"] == 7
    assert inputs["workflow"]["garment_source"] == "photo"
    assert inputs["workflow"]["garment_type"] == "full"

    done = wait_for(client, job["id"], {"completed", "failed"})
    assert done["state"] == "completed", done["error_message"]
    out = client.get(f"/api/v1/assets/{done['outputs'][0]}").json()
    assert out["metadata"]["source_asset_ids"] == [person["id"], garment["id"]]
    assert out["metadata"]["seed"] == 7 and out["metadata"]["synthetic"] is True


def test_text_fallback_job(client, project):
    person = upload(client, project["id"])
    r = change(
        client, project["id"], engine_id=EDITOR_ID, person_asset_id=person["id"],
        garment_description="a white cotton kurta with full sleeves",
    )  # fmt: skip
    assert r.status_code == 201, r.text
    inputs = r.json()["inputs"]
    assert [x["role"] for x in inputs["references"]] == ["person"]
    assert inputs["workflow"]["garment_source"] == "text"
    assert "white cotton kurta" in inputs["prompt"]
    assert isinstance(inputs["seed"], int)


def test_picks_an_installed_editor_by_default(client, project):
    person = upload(client, project["id"])
    r = change(client, project["id"], person_asset_id=person["id"], garment_description="a shirt")
    assert r.status_code == 201, r.text
    assert r.json()["engine_id"] == EDITOR_ID


def test_roles_come_from_the_request_not_the_upload(client, project):
    # Uploaded with the "wrong" stored roles: the outfit flow's order and roles win.
    person = upload(client, project["id"], role="garment")
    garment = upload(client, project["id"], "g.png", role="person")
    r = change(
        client, project["id"], engine_id=EDITOR_ID, person_asset_id=person["id"],
        garment_asset_id=garment["id"],
    )  # fmt: skip
    refs = r.json()["inputs"]["references"]
    assert [(x["asset_id"], x["role"]) for x in refs] == [
        (person["id"], "person"),
        (garment["id"], "garment"),
    ]


def test_retry_keeps_the_workflow_record(client, project, settings):
    person = upload(client, project["id"])
    r = change(
        client, project["id"], engine_id=EDITOR_ID, person_asset_id=person["id"],
        garment_description="a green kurta", seed=5,
    )  # fmt: skip
    job = wait_for(client, r.json()["id"], {"completed", "failed"})
    with sqlite3.connect(settings.paths.db_file) as db:  # make it retryable
        db.execute("UPDATE jobs SET state = 'failed' WHERE id = ?", (job["id"],))
    retry = client.post(f"/api/v1/jobs/{job['id']}/retry")
    assert retry.status_code == 201, retry.text
    inputs = retry.json()["inputs"]
    assert inputs["workflow"] == job["inputs"]["workflow"]
    assert inputs["workflow"]["garment_source"] == "text"
    assert inputs["prompt"] == job["inputs"]["prompt"] and inputs["seed"] == 5


def test_assets_from_another_project_are_refused(client, project):
    other = client.post("/api/v1/projects", json={"name": "Other", "module": "image"}).json()
    theirs = upload(client, other["id"])
    mine = upload(client, project["id"])
    for body in (
        {"person_asset_id": theirs["id"], "garment_description": "a shirt"},
        {"person_asset_id": mine["id"], "garment_asset_id": theirs["id"]},
        {"person_asset_id": mine["id"], "garment_asset_id": "ast_missing"},
    ):
        r = change(client, project["id"], engine_id=EDITOR_ID, **body)
        assert r.status_code == 422, r.text
        assert "is not in this project" in r.json()["error"]["message"]


def test_plain_errors(client, project):
    person = upload(client, project["id"])
    r = change(client, project["id"], engine_id=EDITOR_ID, person_asset_id=person["id"])
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "Add a photo of the garment, or describe it in words."
    r = change(client, project["id"], engine_id="sd15", person_asset_id=person["id"])
    assert r.json()["error"]["message"] == "Add a photo of the garment, or describe it in words."
    r = change(
        client, project["id"], engine_id="sd15", person_asset_id=person["id"],
        garment_description="a shirt",
    )  # fmt: skip
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "That engine cannot change outfits."
    r = change(
        client, project["id"], engine_id=EDITOR_ID, person_asset_id=person["id"],
        garment_description="a shirt", preset="balanced",
    )  # fmt: skip
    assert r.json()["error"]["code"] == "invalid_preset"  # the test editor has no presets
    r = client.post("/api/v1/projects/prj_nope/outfit", json={"person_asset_id": person["id"]})
    assert r.status_code == 404


def test_no_editing_engine_gives_a_plain_message(settings):
    with make_client(settings) as c:
        p = c.post("/api/v1/projects", json={"name": "Looks", "module": "image"}).json()
        person = upload(c, p["id"])
        r = change(c, p["id"], person_asset_id=person["id"], garment_description="a red shirt")
    assert r.status_code == 422
    assert r.json()["error"] == {
        "code": "no_engine",
        "message": "Changing outfits needs an image editing engine. Install one from Models first.",
    }


def test_description_stays_out_of_logs(client, project, caplog):
    caplog.set_level(logging.DEBUG)
    person = upload(client, project["id"])
    secret = "a very particular zebra-striped wedding sherwani"
    r = change(
        client, project["id"], engine_id=EDITOR_ID, person_asset_id=person["id"],
        garment_description=secret,
    )  # fmt: skip
    wait_for(client, r.json()["id"], {"completed", "failed"})
    assert "zebra-striped" not in caplog.text


def test_outfit_jobs_are_image_versions(client, project):
    """I3 + I4: outfit results join the image's versions, and "again" adds to them."""
    person = upload(client, project["id"])
    garment = upload(client, project["id"], "g.png", "garment")
    body = dict(engine_id=EDITOR_ID, person_asset_id=person["id"], garment_asset_id=garment["id"])
    first = change(client, project["id"], seed=1, **body).json()
    assert wait_for(client, first["id"], {"completed", "failed"})["state"] == "completed"
    groups = client.get(f"/api/v1/projects/{project['id']}/image-groups").json()
    assert [[v["id"] for v in g["versions"]] for g in groups] == [[first["id"]]]

    again = client.post(f"/api/v1/jobs/{first['id']}/again", json={"seed": "same"})
    assert again.status_code == 201, again.text
    assert again.json()["inputs"]["workflow"]["name"] == "outfit"
    wait_for(client, again.json()["id"], {"completed", "failed"})
    groups = client.get(f"/api/v1/projects/{project['id']}/image-groups").json()
    assert len(groups) == 1 and len(groups[0]["versions"]) == 2
