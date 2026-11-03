"""QuerySpec -> SQL against a fixture set of contacts (no AI involved)."""

import uuid

import pytest

from app.schemas import ContactIn, Phone
from app.store import UserStore

from .conftest import empty_spec

CONTACTS = [
    dict(name="Li Wei", name_native="李伟", title="General Manager", title_native="总经理", company="Canton Home Furniture",
         city="Guangzhou", country="China", industry="Furniture", emails=["liwei@ch.cn"], description="Sofas, chairs"),
    dict(name="Zhang Min", title_native="副总经理", title="Deputy General Manager", company="Pearl Sofa Factory",
         city="Guangzhou", country="China", industry="Home furnishings", emails=["zm@pearl.cn"]),
    dict(name="Chen Jie", title="CEO", company="Bright LED Lighting", city="Guangzhou", country="China",
         industry="Lighting", emails=["chen@bright.cn"]),
    dict(name="Wang Fang", title="CEO", company="Shenzhen Electronics", city="Shenzhen", country="China",
         industry="Electronics", emails=["wf@sze.cn"]),
    dict(name="Huang Lei", title="Chief Executive Officer", company="Oak & Co", address="88 Tianhe Rd, Guangzhou",
         industry="", description="Solid wood dining tables", emails=[]),
    dict(name="Anna Rossi", title="Sales Manager", company="Milano Arredamento", city="Milan", country="Italy",
         industry="Furniture", emails=["anna@milano.it"], phones=[Phone(type="office", number="+39 02 1234")]),
    dict(name="Liu Yang", title_native="首席执行官", company_native="广州木艺家具", address_native="广州市天河区",
         industry="家具", emails=["ly@muyi.cn"]),
]


@pytest.fixture
def store(tmp_path):
    s = UserStore(tmp_path, uuid.uuid4().hex)
    for c in CONTACTS:
        s.create(ContactIn(**c))
    yield s
    s.close()


def names(results):
    return sorted(c.name or c.name_native for c in results)


def test_furniture_suppliers_topic_or(store):
    spec = empty_spec(industries=["Furniture", "Home furnishings", "家具"],
                      keywords=["furniture", "sofa", "dining table", "家具"])
    assert names(store.search(spec)) == ["Anna Rossi", "Huang Lei", "Li Wei", "Liu Yang", "Zhang Min"]


def test_ceo_emails_in_guangzhou(store):
    spec = empty_spec(
        title_keywords=["CEO", "Chief Executive", "General Manager", "总经理", "首席执行官"],
        exclude_title_keywords=["Deputy", "Vice", "Assistant", "副", "助理"],
        cities=["Guangzhou", "广州"], require_fields=["emails"], return_fields=["name", "company", "emails"],
    )
    # Zhang Min (deputy, 副总经理) excluded; Huang Lei excluded (no email); Wang Fang is in Shenzhen.
    assert names(store.search(spec)) == ["Chen Jie", "Li Wei", "Liu Yang"]
    assert store.return_fields(spec) == ["name", "company", "emails"]


def test_city_matches_address_text(store):
    spec = empty_spec(cities=["Guangzhou"], title_keywords=["Chief Executive"])
    assert names(store.search(spec)) == ["Huang Lei"]


def test_empty_spec_returns_all(store):
    assert len(store.search(empty_spec())) == len(CONTACTS)


def test_default_return_fields(store):
    assert store.return_fields(empty_spec())[0] == "name"
    assert store.return_fields(empty_spec(return_fields=["emails"])) == ["name", "emails"]


def test_values_are_parameterised(store):
    evil = "x') OR 1=1; DROP TABLE contacts; --"
    spec = empty_spec(keywords=[evil], cities=[evil], require_fields=["emails"])
    sql, params = store.build_query(spec)
    assert evil.lower() in params
    assert "DROP" not in sql
    assert store.search(spec) == []
    assert store.count() == len(CONTACTS)


def test_require_phone(store):
    assert names(store.search(empty_spec(require_fields=["phones"]))) == ["Anna Rossi"]


def test_case_insensitive_and_country(store):
    assert names(store.search(empty_spec(countries=["italy"]))) == ["Anna Rossi"]
