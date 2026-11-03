import io

from openpyxl import load_workbook
from PIL import Image

from .conftest import empty_spec, jpeg_bytes, sample_contact


def test_extract_flow(make_user, ai):
    user, _ = make_user("alice")
    r = user.post("/api/cards/extract", files={
        "front": ("f.jpg", jpeg_bytes(3000, 1800), "image/jpeg"),
        "back": ("b.jpg", jpeg_bytes(), "image/jpeg"),
    })
    assert r.status_code == 200, r.text
    started = r.json()
    assert started["front_image"] and started["back_image"]
    # TestClient runs background tasks before returning, so the job is done.
    job = user.get(f"/api/cards/extract/{started['job_id']}").json()
    assert job["status"] == "done"
    assert job["result"]["name_native"] == "李伟"
    assert job["result"]["city"] == "Guangzhou"
    assert ai.calls[:2] == [("check", 2), ("extract", 2)]
    # stored image is downscaled JPEG
    img = Image.open(io.BytesIO(user.get(f"/api/images/{started['front_image']}").content))
    assert max(img.size) <= 1568 and img.format == "JPEG"
    # usage was recorded
    usage = user.get("/api/me/usage").json()
    assert usage["by_kind"]["extract"]["calls"] == 1
    assert usage["by_kind"]["card_check"]["calls"] == 1
    assert 0.01 < usage["total_cost_usd"] < 0.02


def test_extract_error_reported(make_user, ai):
    user, _ = make_user("alice")
    ai.extract_error = "The AI declined to process this card."
    started = user.post("/api/cards/extract", files={"front": ("f.jpg", jpeg_bytes(), "image/jpeg")}).json()
    job = user.get(f"/api/cards/extract/{started['job_id']}").json()
    assert job == {"status": "error", "result": None, "error": "The AI declined to process this card."}


def test_rejects_non_image(make_user):
    user, _ = make_user("alice")
    r = user.post("/api/cards/extract", files={"front": ("f.jpg", b"not an image", "image/jpeg")})
    assert r.status_code == 400


def test_query_endpoint(make_user, ai):
    user, _ = make_user("alice")
    user.post("/api/contacts", json=sample_contact())
    user.post("/api/contacts", json=sample_contact(name="Anna", industry="Lighting", description="LED", city="Milan",
                                                  emails=["a@x.it"], phones=[], tags=[]))
    ai.next_spec = empty_spec(industries=["Furniture"], return_fields=["name", "emails"])
    r = user.post("/api/query", json={"question": "furniture suppliers"})
    assert r.status_code == 200
    data = r.json()
    assert [c["name"] for c in data["contacts"]] == ["Li Wei"]
    assert data["fields"] == ["name", "emails"]
    assert data["explanation"] == "Contacts: industry Furniture"
    assert "spec" not in data
    # Haiku was given the user's industries and tags for context
    assert ai.calls[-1][2] == ("Furniture", "Lighting") or set(ai.calls[-1][2]) == {"Furniture", "Lighting"}
    assert ai.calls[-1][3] == ("canton-fair",)


def test_export_csv_and_xlsx(make_user):
    user, _ = make_user("alice")
    c = user.post("/api/contacts", json=sample_contact(notes="=HYPERLINK(\"evil\")")).json()
    csv_text = user.post("/api/export", json={"fmt": "csv"}).content.decode("utf-8-sig")
    assert "Li Wei" in csv_text and "李伟" in csv_text
    assert "'=HYPERLINK" in csv_text  # formula injection neutralised
    r = user.post("/api/export", json={"fmt": "xlsx", "ids": [c["id"]], "fields": ["name", "emails"]})
    wb = load_workbook(io.BytesIO(r.content))
    rows = list(wb.active.values)
    assert rows == [("Name", "Emails"), ("Li Wei", "liwei@cantonhome.cn")]


def test_update_and_meta(make_user):
    user, _ = make_user("alice")
    c = user.post("/api/contacts", json=sample_contact()).json()
    r = user.put(f"/api/contacts/{c['id']}", json=sample_contact(industry="Home furnishings", tags=["vip"]))
    assert r.status_code == 200 and r.json()["industry"] == "Home furnishings"
    meta = user.get("/api/meta").json()
    assert meta["industries"] == ["Home furnishings"] and meta["tags"] == ["vip"] and meta["count"] == 1


def test_validation(make_user):
    user, _ = make_user("alice")
    assert user.post("/api/contacts", json={"phones": [{"type": "pager", "number": "1"}]}).status_code == 422
    assert user.post("/api/query", json={"question": ""}).status_code == 422
