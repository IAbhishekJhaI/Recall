"""Pydantic models shared by the API, the AI calls and the storage layer."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

IMAGE_ID_RE = re.compile(r"^[0-9a-f]{32}$")

PhoneType = Literal["mobile", "office", "fax", "other"]


class Phone(BaseModel):
    type: PhoneType = Field(description="mobile, office (landline/tel), fax, or other")
    number: str = Field(description="Number exactly as printed, including country code if shown")


# --------------------------------------------------------------------------
# What Claude extracts from the card. Only printed information; no inference.
# Every field is required so the structured-output schema is simple; the model
# returns "" / [] for anything not on the card.
# --------------------------------------------------------------------------
class CardCheck(BaseModel):
    """Cheap pre-check: is this upload really a business card?"""

    is_business_card: bool = Field(description="True only if the image(s) show a business/visiting card")


class CardExtraction(BaseModel):
    is_business_card: bool = Field(description="True if the image(s) show a business card; if False every other field must be empty")
    name: str = Field(description="Person's name in Latin script as printed (e.g. 'Li Wei' or 'John Smith'); '' if only a native-script name is printed")
    name_native: str = Field(description="Person's name in native script (e.g. Chinese characters) if printed, else ''")
    title: str = Field(description="Job title in English as printed, else ''")
    title_native: str = Field(description="Job title in native script as printed (e.g. 总经理), else ''")
    department: str = Field(description="Department as printed, else ''")
    company: str = Field(description="Company name in English/Latin script as printed, else ''")
    company_native: str = Field(description="Company name in native script as printed, else ''")
    phones: list[Phone] = Field(description="All phone/fax numbers on the card")
    emails: list[str] = Field(description="All email addresses on the card")
    website: str = Field(description="Website as printed, else ''")
    address: str = Field(description="Full postal address in English/Latin script as printed, else ''")
    address_native: str = Field(description="Full postal address in native script as printed, else ''")
    city: str = Field(description="City of the address, normalised to its common English name (广州 -> Guangzhou), else ''")
    province_state: str = Field(description="Province/state, normalised to English (广东 -> Guangdong), else ''")
    country: str = Field(description="Country in English. Use it only if printed or unambiguous from the address/city/phone country code; else ''")
    postal_code: str = Field(description="Postal/ZIP code as printed, else ''")
    wechat: str = Field(description="WeChat ID as printed, else ''")
    whatsapp: str = Field(description="WhatsApp number/ID as printed, else ''")
    linkedin: str = Field(description="LinkedIn URL or handle as printed, else ''")
    other_contacts: list[str] = Field(description="Any other contact handles as 'Label: value' (QQ, Skype, Line, Telegram, Instagram...)")
    raw_text: str = Field(description="All text visible on the card(s), line by line")


SHORT = 300
LONG = 4000


def _clip(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


class ContactIn(BaseModel):
    """A contact as submitted by the user (extracted fields + user-entered fields)."""

    name: str = ""
    name_native: str = ""
    title: str = ""
    title_native: str = ""
    department: str = ""
    company: str = ""
    company_native: str = ""
    phones: list[Phone] = Field(default_factory=list, max_length=20)
    emails: list[str] = Field(default_factory=list, max_length=20)
    website: str = ""
    address: str = ""
    address_native: str = ""
    city: str = ""
    province_state: str = ""
    country: str = ""
    postal_code: str = ""
    wechat: str = ""
    whatsapp: str = ""
    linkedin: str = ""
    other_contacts: list[str] = Field(default_factory=list, max_length=20)
    raw_text: str = ""
    # entered by the user
    industry: str = ""
    description: str = ""
    notes: str = ""
    tags: list[str] = Field(default_factory=list, max_length=30)
    front_image: str | None = None
    back_image: str | None = None

    @field_validator(
        "name", "name_native", "title", "title_native", "department", "company",
        "company_native", "website", "city", "province_state", "country",
        "postal_code", "wechat", "whatsapp", "linkedin", "industry",
    )
    @classmethod
    def _short(cls, v: str) -> str:
        return _clip(v, SHORT)

    @field_validator("address", "address_native", "description", "notes", "raw_text")
    @classmethod
    def _long(cls, v: str) -> str:
        return _clip(v, LONG)

    @field_validator("emails", "other_contacts")
    @classmethod
    def _str_list(cls, v: list[str]) -> list[str]:
        return [_clip(x, SHORT) for x in v if x and x.strip()]

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        seen: dict[str, str] = {}
        for t in v:
            t = _clip(t, 60)
            if t and t.lower() not in seen:
                seen[t.lower()] = t
        return list(seen.values())

    @field_validator("phones")
    @classmethod
    def _phones(cls, v: list[Phone]) -> list[Phone]:
        return [Phone(type=p.type, number=_clip(p.number, 60)) for p in v if p.number.strip()]

    @field_validator("front_image", "back_image")
    @classmethod
    def _image_id(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        if not IMAGE_ID_RE.match(v):
            raise ValueError("invalid image id")
        return v


class Contact(ContactIn):
    id: str
    created_at: str
    updated_at: str


# --------------------------------------------------------------------------
# Natural-language query -> structured filter (produced by Claude Haiku).
# --------------------------------------------------------------------------
FieldName = Literal[
    "name", "name_native", "title", "title_native", "department", "company",
    "company_native", "phones", "emails", "website", "address", "address_native",
    "city", "province_state", "country", "postal_code", "wechat", "whatsapp",
    "linkedin", "other_contacts", "industry", "description", "notes", "tags",
    "created_at",
]
RequirableField = Literal["emails", "phones", "wechat", "whatsapp", "website", "address", "linkedin"]

DEFAULT_RETURN_FIELDS: list[str] = ["name", "title", "company", "phones", "emails", "city", "industry"]


class QuerySpec(BaseModel):
    is_contact_search: bool = Field(description="True only if the message asks to find/list/filter the user's saved contacts; if False every other field must be empty")
    industries: list[str] = Field(description="Industry values to match (prefer values from the user's existing industries list)")
    keywords: list[str] = Field(description="Topic keywords/synonyms (English and native-language) matched anywhere in the contact; OR-ed with industries")
    title_keywords: list[str] = Field(description="Job-title words/synonyms; contact matches if its title contains any of them")
    exclude_title_keywords: list[str] = Field(description="Title words that disqualify a match (e.g. Vice, Deputy, Assistant, 副, 助理 when asking for the top person)")
    names: list[str] = Field(description="Person names to match")
    companies: list[str] = Field(description="Company names to match")
    cities: list[str] = Field(description="Cities, with English and native spellings")
    provinces: list[str] = Field(description="Provinces/states, with English and native spellings")
    countries: list[str] = Field(description="Countries, with English and native spellings")
    tags: list[str] = Field(description="Tags to match (from the user's existing tags list)")
    require_fields: list[RequirableField] = Field(description="Only include contacts that have these fields filled in")
    return_fields: list[FieldName] = Field(description="Columns to show, most relevant first; [] for the default set")


MAX_QUESTION_CHARS = 200


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class NewUser(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    password: str = Field(min_length=8, max_length=256)
    is_admin: bool = False


class UserUpdate(BaseModel):
    disabled: bool | None = None
    is_admin: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


class DuplicateCheck(BaseModel):
    emails: list[str] = Field(default_factory=list, max_length=20)
    phones: list[str] = Field(default_factory=list, max_length=20)
    exclude_id: str | None = None


class ExportRequest(BaseModel):
    fmt: Literal["csv", "xlsx"] = "csv"
    ids: list[str] | None = Field(default=None, max_length=5000)
    fields: list[FieldName] | None = None
