import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.ai import AIError, AIResult, Usage
from app.config import Settings
from app.main import create_app
from app.schemas import CardExtraction, Phone, QuerySpec


def empty_spec(**kw) -> QuerySpec:
    base = dict(explanation="", industries=[], keywords=[], title_keywords=[], exclude_title_keywords=[],
                names=[], companies=[], cities=[], provinces=[], countries=[], tags=[], require_fields=[],
                return_fields=[])
    base.update(kw)
    return QuerySpec(**base)


def extraction(**kw) -> CardExtraction:
    base = dict(name="", name_native="", title="", title_native="", department="", company="",
                company_native="", phones=[], emails=[], website="", address="", address_native="", city="",
                province_state="", country="", postal_code="", wechat="", whatsapp="", linkedin="",
                other_contacts=[], raw_text="")
    base.update(kw)
    return CardExtraction(**base)


class FakeAI:
    """Stands in for Claude: returns canned results and records calls."""

    def __init__(self):
        self.next_extraction = extraction(
            name="Li Wei", name_native="李伟", title="General Manager", title_native="总经理",
            company="Canton Home Furniture Co.", company_native="广州家居家具有限公司",
            phones=[Phone(type="mobile", number="+86 138 0013 8000")], emails=["liwei@cantonhome.cn"],
            city="Guangzhou", province_state="Guangdong", country="China", raw_text="李伟 总经理",
        )
        self.next_spec = empty_spec(explanation="All contacts")
        self.extract_error: str | None = None
        self.calls: list[tuple] = []

    def extract_card(self, images):
        self.calls.append(("extract", len(images)))
        if self.extract_error:
            raise AIError(self.extract_error)
        return AIResult(self.next_extraction, Usage("claude-sonnet-5-5", 2000, 800))

    def parse_query(self, question, industries, tags):
        self.calls.append(("query", question, tuple(industries), tuple(tags)))
        return AIResult(self.next_spec, Usage("claude-haiku-5-5", 1800, 300))


def jpeg_bytes(w=800, h=500, color=(240, 240, 240)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def ai():
    return FakeAI()


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", admin_username="admin", admin_password="admin-pass-123")


@pytest.fixture
def app(settings, ai):
    return create_app(settings, ai)


class Client(TestClient):
    """TestClient that always sends the CSRF header and uses HTTPS (secure cookies)."""

    def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", None) or {})
        headers.setdefault("X-Recall", "1")
        return super().request(method, url, headers=headers, **kw)


def make_client(app) -> Client:
    return Client(app, base_url="https://testserver")


def login(app, username, password) -> Client:
    c = make_client(app)
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def admin(app):
    return login(app, "admin", "admin-pass-123")


@pytest.fixture
def make_user(app, admin):
    def _make(username, password="password-123", is_admin=False):
        r = admin.post("/api/admin/users", json={"username": username, "password": password, "is_admin": is_admin})
        assert r.status_code == 201, r.text
        return login(app, username, password), r.json()
    return _make


def sample_contact(**kw) -> dict:
    base = {
        "name": "Li Wei", "name_native": "李伟", "title": "General Manager", "title_native": "总经理",
        "company": "Canton Home Furniture Co.", "phones": [{"type": "mobile", "number": "+86 138 0013 8000"}],
        "emails": ["liwei@cantonhome.cn"], "city": "Guangzhou", "country": "China",
        "industry": "Furniture", "description": "Sofas and dining chairs", "tags": ["canton-fair"],
    }
    base.update(kw)
    return base
