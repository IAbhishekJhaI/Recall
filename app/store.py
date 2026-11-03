"""Per-user contact storage.

Every user has their own directory ``<data_dir>/users/<user_id>/`` holding a
private SQLite database and their card images. A UserStore can only ever open
the directory of the user id it was constructed with, and that id always comes
from the authenticated session (see app.auth). There is deliberately no API in
this module that lists or opens other users' stores.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .schemas import (
    DEFAULT_RETURN_FIELDS,
    IMAGE_ID_RE,
    Contact,
    ContactIn,
    QuerySpec,
)

USER_ID_RE = re.compile(r"^[0-9a-f]{32}$")
CONTACT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

TEXT_FIELDS = [
    "name", "name_native", "title", "title_native", "department", "company", "company_native",
    "website", "address", "address_native", "city", "province_state", "country", "postal_code",
    "wechat", "whatsapp", "linkedin", "raw_text", "industry", "description", "notes",
]
JSON_FIELDS = ["phones", "emails", "other_contacts", "tags"]
IMAGE_FIELDS = ["front_image", "back_image"]
ALL_FIELDS = TEXT_FIELDS + JSON_FIELDS + IMAGE_FIELDS

# Fields concatenated into the lowercase search blob.
SEARCH_FIELDS = [
    "name", "name_native", "title", "title_native", "department", "company", "company_native",
    "website", "address", "address_native", "city", "province_state", "country", "wechat",
    "whatsapp", "linkedin", "raw_text", "industry", "description", "notes",
]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    {", ".join(f"{f} TEXT NOT NULL DEFAULT ''" for f in TEXT_FIELDS)},
    {", ".join(f"{f} TEXT NOT NULL DEFAULT '[]'" for f in JSON_FIELDS)},
    front_image TEXT,
    back_image TEXT,
    search_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS contacts_created ON contacts(created_at);
"""

MAX_TERMS = 25
MAX_TERM_LEN = 100
RESULT_LIMIT = 1000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _terms(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        v = _norm(v)[:MAX_TERM_LEN]
        if v and v not in out:
            out.append(v)
        if len(out) >= MAX_TERMS:
            break
    return out


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


class UserStore:
    def __init__(self, users_dir: Path, user_id: str):
        if not USER_ID_RE.match(user_id or ""):
            raise ValueError("invalid user id")
        self.user_id = user_id
        self.root = (Path(users_dir) / user_id).resolve()
        if self.root.parent != Path(users_dir).resolve():
            raise ValueError("invalid user directory")
        self.images_dir = self.root / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.root / "recall.db", timeout=10, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "UserStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _search_text(data: dict) -> str:
        parts = [str(data.get(f, "")) for f in SEARCH_FIELDS]
        parts += data.get("emails", [])
        parts += [p["number"] if isinstance(p, dict) else p.number for p in data.get("phones", [])]
        parts += data.get("other_contacts", [])
        parts += data.get("tags", [])
        return "\n".join(p for p in parts if p).lower()

    @staticmethod
    def _row_to_contact(row: sqlite3.Row) -> Contact:
        data = {k: row[k] for k in row.keys() if k != "search_text"}
        for f in JSON_FIELDS:
            data[f] = json.loads(data[f] or "[]")
        return Contact(**data)

    def _row_values(self, c: ContactIn) -> dict:
        data = c.model_dump()
        for img in IMAGE_FIELDS:
            if data[img] and not self.image_path(data[img]):
                raise ValueError(f"{img} not found")
        values = {f: data[f] for f in TEXT_FIELDS}
        for f in JSON_FIELDS:
            values[f] = json.dumps(data[f], ensure_ascii=False)
        for f in IMAGE_FIELDS:
            values[f] = data[f]
        values["search_text"] = self._search_text(data)
        return values

    # ---- CRUD ------------------------------------------------------------
    def create(self, c: ContactIn) -> Contact:
        values = self._row_values(c)
        now = _now_iso()
        values.update(id=uuid.uuid4().hex, created_at=now, updated_at=now)
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self.conn:
            self.conn.execute(f"INSERT INTO contacts ({cols}) VALUES ({marks})", tuple(values.values()))
        got = self.get(values["id"])
        assert got is not None
        return got

    def get(self, contact_id: str) -> Contact | None:
        if not CONTACT_ID_RE.match(contact_id or ""):
            return None
        row = self.conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
        return self._row_to_contact(row) if row else None

    def update(self, contact_id: str, c: ContactIn) -> Contact | None:
        old = self.get(contact_id)
        if old is None:
            return None
        values = self._row_values(c)
        values["updated_at"] = _now_iso()
        sets = ", ".join(f"{k} = ?" for k in values)
        with self.conn:
            self.conn.execute(f"UPDATE contacts SET {sets} WHERE id = ?", (*values.values(), contact_id))
        for img in IMAGE_FIELDS:
            old_img = getattr(old, img)
            if old_img and old_img not in (values["front_image"], values["back_image"]):
                self._delete_image(old_img)
        return self.get(contact_id)

    def delete(self, contact_id: str) -> bool:
        c = self.get(contact_id)
        if c is None:
            return False
        with self.conn:
            self.conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        for img in (c.front_image, c.back_image):
            if img:
                self._delete_image(img)
        return True

    def list(self, q: str = "", limit: int = RESULT_LIMIT) -> list[Contact]:
        terms = _norm(q).split()
        sql = "SELECT * FROM contacts"
        params: list = []
        if terms:
            sql += " WHERE " + " AND ".join("instr(search_text, ?) > 0" for _ in terms[:MAX_TERMS])
            params += terms[:MAX_TERMS]
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [self._row_to_contact(r) for r in self.conn.execute(sql, params)]

    def get_many(self, ids: list[str]) -> list[Contact]:
        ids = [i for i in ids if CONTACT_ID_RE.match(i or "")][:5000]
        out: list[Contact] = []
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start:chunk_start + 500]
            marks = ",".join("?" for _ in chunk)
            out += [self._row_to_contact(r) for r in
                    self.conn.execute(f"SELECT * FROM contacts WHERE id IN ({marks})", chunk)]
        order = {cid: i for i, cid in enumerate(ids)}
        return sorted(out, key=lambda c: order.get(c.id, 0))

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]

    def distinct_industries(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT industry, COUNT(*) n FROM contacts WHERE industry != '' GROUP BY lower(industry) ORDER BY n DESC LIMIT 200"
        )
        return [r["industry"] for r in rows]

    def distinct_tags(self) -> list[str]:
        seen: dict[str, str] = {}
        for (tags,) in self.conn.execute("SELECT tags FROM contacts WHERE tags != '[]'"):
            for t in json.loads(tags):
                seen.setdefault(t.lower(), t)
        return sorted(seen.values(), key=str.lower)[:300]

    def find_duplicates(self, emails: list[str], phones: list[str], exclude_id: str | None = None) -> list[Contact]:
        emails_n = {_norm(e) for e in emails if _norm(e)}
        phones_n = {_digits(p)[-9:] for p in phones if len(_digits(p)) >= 6}
        if not emails_n and not phones_n:
            return []
        dupes = []
        for row in self.conn.execute("SELECT * FROM contacts"):
            c = self._row_to_contact(row)
            if c.id == exclude_id:
                continue
            if emails_n & {_norm(e) for e in c.emails} or phones_n & {_digits(p.number)[-9:] for p in c.phones if len(_digits(p.number)) >= 6}:
                dupes.append(c)
        return dupes

    # ---- structured search (from QuerySpec) -------------------------------
    @staticmethod
    def build_query(spec: QuerySpec) -> tuple[str, list]:
        """Turn a QuerySpec into parameterised SQL. Column names are fixed here;
        only the values come from the spec, and they are always bound parameters."""
        where: list[str] = []
        params: list = []

        def any_of(columns: list[str], values: list[str]) -> str | None:
            vals = _terms(values)
            if not vals:
                return None
            expr = "lower(" + " || ' ' || ".join(columns) + ")"
            clauses = []
            for v in vals:
                clauses.append(f"instr({expr}, ?) > 0")
                params.append(v)
            return "(" + " OR ".join(clauses) + ")"

        # Topic: industry match OR keyword anywhere.
        topic_parts = [p for p in (
            any_of(["industry"], spec.industries),
            any_of(["search_text"], spec.keywords),
        ) if p]
        if topic_parts:
            where.append("(" + " OR ".join(topic_parts) + ")")

        groups = [
            (["title", "title_native"], spec.title_keywords),
            (["name", "name_native"], spec.names),
            (["company", "company_native"], spec.companies),
            (["city", "address", "address_native"], spec.cities),
            (["province_state", "address", "address_native"], spec.provinces),
            (["country", "address", "address_native"], spec.countries),
            (["tags"], spec.tags),
        ]
        for cols, vals in groups:
            clause = any_of(cols, vals)
            if clause:
                where.append(clause)

        excl = any_of(["title", "title_native"], spec.exclude_title_keywords)
        if excl:
            where.append("NOT " + excl)

        for f in spec.require_fields:
            if f not in ALL_FIELDS:  # defence in depth; the schema already restricts this
                continue
            if f in JSON_FIELDS:
                where.append(f"{f} != '[]'")
            else:
                where.append(f"{f} != ''")

        sql = "SELECT * FROM contacts"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY created_at DESC LIMIT {RESULT_LIMIT}"
        return sql, params

    def search(self, spec: QuerySpec) -> list[Contact]:
        sql, params = self.build_query(spec)
        return [self._row_to_contact(r) for r in self.conn.execute(sql, params)]

    @staticmethod
    def return_fields(spec: QuerySpec) -> list[str]:
        fields = [f for f in dict.fromkeys(spec.return_fields)]
        if not fields:
            return list(DEFAULT_RETURN_FIELDS)
        if "name" not in fields and "name_native" not in fields:
            fields.insert(0, "name")
        return fields

    # ---- images ----------------------------------------------------------
    def save_image(self, jpeg_bytes: bytes) -> str:
        image_id = uuid.uuid4().hex
        (self.images_dir / f"{image_id}.jpg").write_bytes(jpeg_bytes)
        return image_id

    def image_path(self, image_id: str) -> Path | None:
        if not IMAGE_ID_RE.match(image_id or ""):
            return None
        p = (self.images_dir / f"{image_id}.jpg").resolve()
        if p.parent != self.images_dir.resolve() or not p.is_file():
            return None
        return p

    def _delete_image(self, image_id: str) -> None:
        p = self.image_path(image_id)
        if p:
            p.unlink(missing_ok=True)

    def cleanup_orphan_images(self, older_than_s: int = 24 * 3600) -> int:
        """Delete uploaded images never attached to a saved contact (abandoned scans)."""
        used = set()
        for front, back in self.conn.execute("SELECT front_image, back_image FROM contacts"):
            used.update(x for x in (front, back) if x)
        cutoff = time.time() - older_than_s
        removed = 0
        for p in self.images_dir.glob("*.jpg"):
            if p.stem not in used and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                removed += 1
        return removed


def delete_user_data(users_dir: Path, user_id: str) -> None:
    """Remove a user's entire data directory (used when an admin deletes an account)."""
    if not USER_ID_RE.match(user_id or ""):
        raise ValueError("invalid user id")
    root = (Path(users_dir) / user_id).resolve()
    if root.parent != Path(users_dir).resolve():
        raise ValueError("invalid user directory")
    if root.exists():
        shutil.rmtree(root)
