import os
import time
import uuid

import pytest

from app.schemas import ContactIn, Phone
from app.store import UserStore, delete_user_data

from .conftest import jpeg_bytes


@pytest.fixture
def store(tmp_path):
    s = UserStore(tmp_path, uuid.uuid4().hex)
    yield s
    s.close()


def test_crud_roundtrip(store):
    c = store.create(ContactIn(name="A", phones=[Phone(type="mobile", number="123456789")], tags=["x", "X", "y"]))
    assert c.tags == ["x", "y"]
    got = store.get(c.id)
    assert got.phones[0].number == "123456789"
    updated = store.update(c.id, ContactIn(name="B", industry="Furniture"))
    assert updated.name == "B" and updated.industry == "Furniture"
    assert store.distinct_industries() == ["Furniture"]
    assert store.delete(c.id) is True
    assert store.get(c.id) is None
    assert store.delete(c.id) is False


def test_list_filter(store):
    store.create(ContactIn(name="Li Wei", company="广州家具"))
    store.create(ContactIn(name="Anna", company="Milano"))
    assert [c.name for c in store.list("家具")] == ["Li Wei"]
    assert [c.name for c in store.list("ANNA milano")] == ["Anna"]
    assert len(store.list()) == 2


def test_duplicates_by_email_and_phone(store):
    a = store.create(ContactIn(name="A", emails=["Li@X.cn"], phones=[Phone(type="mobile", number="+86 138-0013-8000")]))
    assert [d.id for d in store.find_duplicates(["li@x.cn"], [])] == [a.id]
    assert [d.id for d in store.find_duplicates([], ["13800138000"])] == [a.id]
    assert store.find_duplicates(["li@x.cn"], [], exclude_id=a.id) == []
    assert store.find_duplicates(["other@x.cn"], ["999"]) == []


def test_images_attach_and_cleanup(store):
    img = store.save_image(jpeg_bytes())
    old = store.save_image(jpeg_bytes())
    c = store.create(ContactIn(name="A", front_image=img))
    assert store.image_path(img) is not None
    # orphan older than a day is removed, attached image kept
    past = time.time() - 2 * 86400
    os.utime(store.image_path(old), (past, past))
    os.utime(store.image_path(img), (past, past))
    assert store.cleanup_orphan_images() == 1
    assert store.image_path(img) is not None and store.image_path(old) is None
    store.delete(c.id)
    assert store.image_path(img) is None


def test_missing_image_rejected(store):
    with pytest.raises(ValueError):
        store.create(ContactIn(name="A", front_image=uuid.uuid4().hex))


def test_invalid_ids(tmp_path, store):
    with pytest.raises(ValueError):
        UserStore(tmp_path, "../etc")
    with pytest.raises(ValueError):
        UserStore(tmp_path, "abc")
    assert store.image_path("../../auth") is None
    assert store.get("'; DROP TABLE contacts; --") is None
    with pytest.raises(ValueError):
        ContactIn(front_image="../x")


def test_delete_user_data(tmp_path):
    uid = uuid.uuid4().hex
    s = UserStore(tmp_path, uid)
    s.create(ContactIn(name="A"))
    s.close()
    delete_user_data(tmp_path, uid)
    assert not (tmp_path / uid).exists()
