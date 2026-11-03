"""Recall - business-card scanner and contact search (FastAPI app)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import export as exporter
from .accounts import AccountStore, User
from .ai import AIError, AIService, ClaudeAI, ImageError, Usage, prepare_image
from .auth import (
    COOKIE_NAME,
    clear_session_cookie,
    client_ip,
    current_user,
    require_admin,
    set_session_cookie,
    user_store,
)
from .config import Settings, estimate_cost
from .jobs import JobManager
from .schemas import (
    ContactIn,
    DuplicateCheck,
    ExportRequest,
    LoginRequest,
    NewUser,
    PasswordChange,
    QueryRequest,
    UserUpdate,
)
from .store import UserStore, delete_user_data

log = logging.getLogger("recall")
STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None, ai: AIService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.users_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="Recall", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.accounts = AccountStore(settings.accounts_db, settings.session_days)
    app.state.ai = ai or ClaudeAI(settings)
    app.state.jobs = JobManager()

    accounts: AccountStore = app.state.accounts
    if accounts.count_users() == 0 and settings.admin_username and settings.admin_password:
        accounts.create_user(settings.admin_username, settings.admin_password, is_admin=True)
        log.info("Created initial admin user %r", settings.admin_username)

    # ---- middleware --------------------------------------------------------
    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        # CSRF defence: state-changing API calls must carry a custom header,
        # which browsers will not send cross-origin without a CORS preflight.
        if path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("x-recall") != "1":
                return JSONResponse({"detail": "Missing request header"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def record_usage(user: User, kind: str, usage: Usage) -> None:
        cost = estimate_cost(settings.model_prices, usage.model, usage.input_tokens, usage.output_tokens,
                             usage.cache_read_tokens, usage.cache_write_tokens)
        accounts.record_usage(user.id, kind, usage.model, usage.input_tokens, usage.output_tokens,
                              usage.cache_read_tokens, usage.cache_write_tokens, cost)

    # ---- auth --------------------------------------------------------------
    @app.post("/api/login")
    def login(body: LoginRequest, request: Request, response: Response):
        ip = client_ip(request)
        if accounts.login_blocked(body.username, ip):
            raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
        user = accounts.authenticate(body.username, body.password)
        if user is None:
            accounts.record_login_failure(body.username, ip)
            raise HTTPException(401, "Wrong username or password")
        accounts.clear_login_failures(body.username)
        token = accounts.create_session(user.id)
        set_session_cookie(response, token, settings)
        return user.public()

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        accounts.delete_session(request.cookies.get(COOKIE_NAME))
        clear_session_cookie(response, settings)
        return {"ok": True}

    @app.get("/api/me")
    def me(user: User = Depends(current_user)):
        return user.public()

    @app.post("/api/me/password")
    def change_password(body: PasswordChange, user: User = Depends(current_user)):
        if not accounts.check_password(user.id, body.current_password):
            raise HTTPException(400, "Current password is wrong")
        accounts.update_user(user.id, password=body.new_password)  # signs out every device
        response = JSONResponse({"ok": True})
        set_session_cookie(response, accounts.create_session(user.id), settings)  # keep this one signed in
        return response

    @app.get("/api/me/usage")
    def my_usage(user: User = Depends(current_user)):
        return accounts.usage_summary(user.id)

    # ---- card reading ------------------------------------------------------
    def run_extraction(job_id: str, user: User, images: list[bytes]) -> None:
        try:
            result = app.state.ai.extract_card(images)
            record_usage(user, "extract", result.usage)
            app.state.jobs.finish(job_id, result.value.model_dump())
        except AIError as e:
            app.state.jobs.fail(job_id, str(e))
        except Exception:  # pragma: no cover - unexpected
            log.exception("Card extraction crashed")
            app.state.jobs.fail(job_id, "Something went wrong while reading the card.")

    @app.post("/api/cards/extract")
    async def extract(
        background: BackgroundTasks,
        front: UploadFile = File(...),
        back: UploadFile | None = File(None),
        user: User = Depends(current_user),
        store: UserStore = Depends(user_store),
    ):
        max_bytes = settings.max_upload_mb * 1024 * 1024
        images: list[bytes] = []
        ids: list[str] = []
        for upload in (front, back):
            if upload is None or not upload.filename:
                continue
            raw = await upload.read(max_bytes + 1)
            try:
                jpeg = prepare_image(raw, max_bytes)
            except ImageError as e:
                raise HTTPException(400, str(e))
            images.append(jpeg)
            ids.append(store.save_image(jpeg))
        store.cleanup_orphan_images()
        job = app.state.jobs.create(user.id)
        background.add_task(run_extraction, job.id, user, images)
        return {"job_id": job.id, "front_image": ids[0], "back_image": ids[1] if len(ids) > 1 else None}

    @app.get("/api/cards/extract/{job_id}")
    def extract_status(job_id: str, user: User = Depends(current_user)):
        job = app.state.jobs.get(job_id, user.id)
        if job is None:
            raise HTTPException(404, "Not found")
        return {"status": job.status, "result": job.result, "error": job.error}

    # ---- contacts ----------------------------------------------------------
    @app.get("/api/meta")
    def meta(store: UserStore = Depends(user_store)):
        return {"industries": store.distinct_industries(), "tags": store.distinct_tags(), "count": store.count()}

    @app.get("/api/contacts")
    def list_contacts(q: str = "", store: UserStore = Depends(user_store)):
        return [c.model_dump() for c in store.list(q[:200])]

    @app.post("/api/contacts", status_code=201)
    def create_contact(body: ContactIn, store: UserStore = Depends(user_store)):
        try:
            return store.create(body).model_dump()
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/contacts/{contact_id}")
    def get_contact(contact_id: str, store: UserStore = Depends(user_store)):
        c = store.get(contact_id)
        if c is None:
            raise HTTPException(404, "Not found")
        return c.model_dump()

    @app.put("/api/contacts/{contact_id}")
    def update_contact(contact_id: str, body: ContactIn, store: UserStore = Depends(user_store)):
        try:
            c = store.update(contact_id, body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if c is None:
            raise HTTPException(404, "Not found")
        return c.model_dump()

    @app.delete("/api/contacts/{contact_id}")
    def delete_contact(contact_id: str, store: UserStore = Depends(user_store)):
        if not store.delete(contact_id):
            raise HTTPException(404, "Not found")
        return {"ok": True}

    @app.post("/api/contacts/duplicates")
    def duplicates(body: DuplicateCheck, store: UserStore = Depends(user_store)):
        found = store.find_duplicates(body.emails, body.phones, body.exclude_id)
        return [{"id": c.id, "name": c.name or c.name_native, "company": c.company or c.company_native} for c in found]

    @app.get("/api/images/{image_id}")
    def image(image_id: str, store: UserStore = Depends(user_store)):
        path = store.image_path(image_id)
        if path is None:
            raise HTTPException(404, "Not found")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    # ---- natural-language query -------------------------------------------
    @app.post("/api/query")
    def query(body: QueryRequest, user: User = Depends(current_user), store: UserStore = Depends(user_store)):
        try:
            result = app.state.ai.parse_query(body.question, store.distinct_industries(), store.distinct_tags())
        except AIError as e:
            raise HTTPException(502, str(e))
        record_usage(user, "query", result.usage)
        spec = result.value
        contacts = store.search(spec)
        return {
            "explanation": spec.explanation,
            "fields": store.return_fields(spec),
            "spec": spec.model_dump(),
            "contacts": [c.model_dump() for c in contacts],
        }

    # ---- export ------------------------------------------------------------
    @app.post("/api/export")
    def export(body: ExportRequest, store: UserStore = Depends(user_store)):
        contacts = store.get_many(body.ids) if body.ids is not None else store.list()
        fields = list(dict.fromkeys(body.fields)) if body.fields else None
        if body.fmt == "xlsx":
            data = exporter.to_xlsx(contacts, fields)
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            data = exporter.to_csv(contacts, fields)
            media = "text/csv; charset=utf-8"
        return Response(data, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="recall-contacts.{body.fmt}"'})

    # ---- admin: account management only (no access to anyone's cards) -------
    @app.get("/api/admin/users")
    def admin_list_users(_: User = Depends(require_admin)):
        costs = accounts.usage_by_user()
        return [{**u.public(), "ai_cost_this_month": costs.get(u.id, 0.0)} for u in accounts.list_users()]

    @app.post("/api/admin/users", status_code=201)
    def admin_create_user(body: NewUser, _: User = Depends(require_admin)):
        try:
            return accounts.create_user(body.username, body.password, body.is_admin).public()
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.patch("/api/admin/users/{user_id}")
    def admin_update_user(user_id: str, body: UserUpdate, admin: User = Depends(require_admin)):
        if user_id == admin.id and (body.disabled or body.is_admin is False):
            raise HTTPException(400, "You can't disable or demote your own account")
        u = accounts.update_user(user_id, disabled=body.disabled, is_admin=body.is_admin, password=body.password)
        if u is None:
            raise HTTPException(404, "Not found")
        return u.public()

    @app.delete("/api/admin/users/{user_id}")
    def admin_delete_user(user_id: str, admin: User = Depends(require_admin)):
        if user_id == admin.id:
            raise HTTPException(400, "You can't delete your own account")
        if accounts.get_user(user_id) is None:
            raise HTTPException(404, "Not found")
        accounts.delete_user(user_id)
        delete_user_data(settings.users_dir, user_id)
        return {"ok": True}

    @app.get("/api/admin/usage")
    def admin_usage(_: User = Depends(require_admin)):
        return accounts.usage_summary()

    # ---- frontend ----------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def get_app() -> FastAPI:  # uvicorn --factory app.main:get_app
    logging.basicConfig(level=logging.INFO)
    return create_app()
