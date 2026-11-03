from .conftest import login, make_client


def test_login_logout(app):
    c = login(app, "admin", "admin-pass-123")
    assert c.get("/api/me").json()["username"] == "admin"
    cookie = c.cookies.get("recall_session")
    assert cookie
    assert c.post("/api/logout").status_code == 200
    # old token no longer works even if replayed
    replay = make_client(app)
    replay.cookies.set("recall_session", cookie)
    assert replay.get("/api/me").status_code == 401


def test_cookie_flags(app):
    c = make_client(app)
    r = c.post("/api/login", json={"username": "admin", "password": "admin-pass-123"})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "secure" in sc and "samesite=lax" in sc


def test_wrong_password_and_rate_limit(app):
    c = make_client(app)
    for _ in range(10):
        assert c.post("/api/login", json={"username": "admin", "password": "nope"}).status_code == 401
    r = c.post("/api/login", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 429


def test_csrf_header_required(app):
    c = login(app, "admin", "admin-pass-123")
    r = c.post("/api/contacts", json={"name": "x"}, headers={"X-Recall": ""})
    assert r.status_code == 403


def test_change_password_revokes_other_sessions(make_user, app):
    phone, _ = make_user("erin", "password-123")
    laptop = login(app, "erin", "password-123")
    r = phone.post("/api/me/password", json={"current_password": "password-123", "new_password": "new-password-456"})
    assert r.status_code == 200
    assert phone.get("/api/me").status_code == 200
    assert laptop.get("/api/me").status_code == 401
    login(app, "erin", "new-password-456")


def test_admin_reset_password_and_self_protection(make_user, admin, app):
    user, info = make_user("frank")
    me = admin.get("/api/me").json()
    assert admin.patch(f"/api/admin/users/{info['id']}", json={"password": "reset-pass-789"}).status_code == 200
    assert user.get("/api/me").status_code == 401
    login(app, "frank", "reset-pass-789")
    assert admin.patch(f"/api/admin/users/{me['id']}", json={"disabled": True}).status_code == 400
    assert admin.delete(f"/api/admin/users/{me['id']}").status_code == 400


def test_duplicate_username(admin):
    assert admin.post("/api/admin/users", json={"username": "gina", "password": "password-123"}).status_code == 201
    assert admin.post("/api/admin/users", json={"username": "GINA", "password": "password-123"}).status_code == 409


def test_security_headers(app):
    r = make_client(app).get("/")
    assert r.status_code == 200
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
