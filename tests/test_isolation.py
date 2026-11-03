"""Privacy: one user's cards must never be visible to, or changeable by, anyone else."""

import pytest

from .conftest import jpeg_bytes, sample_contact


@pytest.fixture
def alice_bob(make_user):
    alice, alice_info = make_user("alice")
    bob, bob_info = make_user("bob")
    return alice, bob


def _scan_and_save(client, **kw):
    r = client.post("/api/cards/extract", files={"front": ("f.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 200, r.text
    started = r.json()
    body = sample_contact(front_image=started["front_image"], **kw)
    r = client.post("/api/contacts", json=body)
    assert r.status_code == 201, r.text
    return started, r.json()


def test_other_user_cannot_read_update_delete(alice_bob):
    alice, bob = alice_bob
    started, contact = _scan_and_save(alice)
    cid, img = contact["id"], contact["front_image"]

    assert bob.get(f"/api/contacts/{cid}").status_code == 404
    assert bob.put(f"/api/contacts/{cid}", json=sample_contact(name="hacked")).status_code == 404
    assert bob.delete(f"/api/contacts/{cid}").status_code == 404
    assert bob.get(f"/api/images/{img}").status_code == 404
    assert bob.get(f"/api/cards/extract/{started['job_id']}").status_code == 404

    # Alice's data is untouched and still visible to her.
    r = alice.get(f"/api/contacts/{cid}")
    assert r.status_code == 200 and r.json()["name"] == "Li Wei"
    assert alice.get(f"/api/images/{img}").status_code == 200
    assert alice.get(f"/api/cards/extract/{started['job_id']}").status_code == 200


def test_lists_search_query_export_are_scoped(alice_bob, ai):
    alice, bob = alice_bob
    _scan_and_save(alice)
    assert alice.get("/api/contacts").json() != []

    assert bob.get("/api/contacts").json() == []
    assert bob.get("/api/contacts?q=Li").json() == []
    meta = bob.get("/api/meta").json()
    assert meta == {"industries": [], "tags": [], "count": 0}

    r = bob.post("/api/query", json={"question": "all furniture suppliers"})
    assert r.status_code == 200 and r.json()["contacts"] == []
    # Haiku only ever sees the asking user's own industries/tags.
    assert ai.calls[-1][2:] == ((), ())

    dupes = bob.post("/api/contacts/duplicates", json={"emails": ["liwei@cantonhome.cn"], "phones": []})
    assert dupes.json() == []

    alice_id = alice.get("/api/contacts").json()[0]["id"]
    csv = bob.post("/api/export", json={"fmt": "csv", "ids": [alice_id]}).content.decode("utf-8-sig")
    assert "Li Wei" not in csv
    csv_all = bob.post("/api/export", json={"fmt": "csv"}).content.decode("utf-8-sig")
    assert "Li Wei" not in csv_all


def test_cannot_attach_another_users_image(alice_bob):
    alice, bob = alice_bob
    _, contact = _scan_and_save(alice)
    r = bob.post("/api/contacts", json=sample_contact(front_image=contact["front_image"]))
    assert r.status_code == 400


def test_admin_cannot_see_users_cards(alice_bob, admin):
    alice, _ = alice_bob
    _, contact = _scan_and_save(alice)
    assert admin.get(f"/api/contacts/{contact['id']}").status_code == 404
    assert admin.get(f"/api/images/{contact['front_image']}").status_code == 404
    assert admin.get("/api/contacts").json() == []
    users = admin.get("/api/admin/users").json()
    assert all(set(u) == {"id", "username", "is_admin", "disabled", "created_at", "ai_cost_this_month"} for u in users)


def test_disabled_user_loses_access_immediately(make_user, admin):
    carol, info = make_user("carol")
    assert carol.get("/api/contacts").status_code == 200
    assert admin.patch(f"/api/admin/users/{info['id']}", json={"disabled": True}).status_code == 200
    assert carol.get("/api/contacts").status_code == 401


def test_deleting_user_removes_their_data(make_user, admin, settings):
    dave, info = make_user("dave")
    _scan_and_save(dave)
    user_dir = settings.users_dir / info["id"]
    assert user_dir.exists()
    assert admin.delete(f"/api/admin/users/{info['id']}").status_code == 200
    assert not user_dir.exists()
    assert dave.get("/api/contacts").status_code == 401


def test_unauthenticated_requests_rejected(app):
    from .conftest import make_client
    anon = make_client(app)
    for method, path in [("get", "/api/contacts"), ("get", "/api/meta"), ("post", "/api/query"),
                         ("post", "/api/export"), ("get", "/api/images/" + "a" * 32),
                         ("get", "/api/admin/users")]:
        kwargs = {"json": {"question": "x"}} if path == "/api/query" else {}
        assert getattr(anon, method)(path, **kwargs).status_code == 401, path


def test_non_admin_cannot_use_admin_routes(alice_bob):
    alice, _ = alice_bob
    assert alice.get("/api/admin/users").status_code == 404
    assert alice.post("/api/admin/users", json={"username": "eve", "password": "password-123"}).status_code == 404
    assert alice.get("/api/admin/usage").status_code == 404
