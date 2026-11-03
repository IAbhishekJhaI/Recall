"""Claude API calls: reading business cards and interpreting search questions."""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

import anthropic
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings
from .schemas import CardExtraction, QuerySpec

log = logging.getLogger("recall.ai")

T = TypeVar("T")

MAX_LONG_EDGE = 1568  # enough detail for small print, keeps image tokens ~1.5k
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AIError(Exception):
    """A user-presentable failure from the AI step."""


class ImageError(ValueError):
    pass


@dataclass
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class AIResult(Generic[T]):
    value: T
    usage: Usage


class AIService(Protocol):
    def extract_card(self, images: list[bytes]) -> AIResult[CardExtraction]: ...
    def parse_query(self, question: str, industries: list[str], tags: list[str]) -> AIResult[QuerySpec]: ...


def prepare_image(raw: bytes, max_bytes: int) -> bytes:
    """Validate an uploaded photo, fix its rotation, downscale it and re-encode as JPEG.
    Re-encoding also strips EXIF metadata (e.g. GPS location) from what we store."""
    if len(raw) > max_bytes:
        raise ImageError("Image is too large")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.verify()
        with Image.open(io.BytesIO(raw)) as img:
            if img.width * img.height > 60_000_000:
                raise ImageError("Image dimensions are too large")
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ImageError("File is not a readable image") from e


EXTRACT_SYSTEM = """You read photos of business cards and transcribe them into structured fields.

Rules:
- Record only information that is printed on the card. Never guess or infer a person's industry, role, seniority or anything else that is not written.
- Many cards are bilingual (e.g. Chinese on one side, English on the other). Put Latin-script/English text in the main fields (name, title, company, address) and native-script text in the *_native fields. If only a native-script version exists, leave the English field empty rather than translating it - except city, province_state and country, which are always given as their common English names (广州 -> Guangzhou, 广东省 -> Guangdong, 中国 -> China).
- Copy phone numbers, emails, websites and IDs exactly; do not reformat them. Classify each phone as mobile, office, fax or other based on its label (手机/M/Mob = mobile, 电话/T/Tel = office, 传真/F = fax).
- Use an empty string or empty list for anything not on the card.
- raw_text: all visible text, line by line, so nothing is lost."""


QUERY_SYSTEM = """You translate a user's question about their own business-card contacts into a search filter.

The contact database has these fields: name, name_native, title, title_native, department, company, company_native, phones, emails, website, address, address_native, city, province_state, country, postal_code, wechat, whatsapp, linkedin, other_contacts, industry (entered by the user), description (entered by the user: what the contact supplies/does, how they met), notes, tags, created_at.
Cards are often Chinese/English bilingual; city/province/country are stored in English, but addresses may be in Chinese.

How matching works (all values are case-insensitive substring matches):
- A contact must match EVERY non-empty group below. Within a group, matching ANY value is enough.
- "industries" and "keywords" together form one topic group: the contact matches if its industry contains any listed industry OR any keyword appears anywhere in the contact (company, description, notes, raw card text...).
- title_keywords match title/title_native; exclude_title_keywords removes contacts whose title contains any of those words.
- cities/provinces/countries match the structured field or the address text.

Guidelines:
- For a topic like "furniture suppliers": put matching values from the user's existing industries list in "industries" (choose generously among related ones), and add keywords with English and Chinese synonyms and related product words (furniture, sofa, chair, table, cabinet, 家具, 沙发...). Don't add generic words like "supplier" or "company" as keywords - they match too much.
- For roles like "CEOs": include common title variants and native-language equivalents (CEO, Chief Executive, President, Managing Director, General Manager, Founder, Owner, 总经理, 首席执行官, 总裁, 董事长, 创始人) and exclude deputies/assistants (Vice, Deputy, Assistant, VP, 副, 助理). Only use exclusions when the question asks for the top person.
- For places, include English and native spellings (Guangzhou, Canton, 广州).
- Leave groups empty when the question doesn't constrain them. A request for all contacts has every filter empty.
- return_fields: the columns the user asked for ("mails" -> emails, "numbers" -> phones), plus name and company for context. Use [] when they ask for "details" or don't specify.
- require_fields: use when the user only wants contacts that have that information (e.g. "emails of ..." -> ["emails"]).
- explanation: one short, plain sentence describing what will be shown."""


class ClaudeAI:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            kwargs = {"api_key": self.settings.anthropic_api_key} if self.settings.anthropic_api_key else {}
            try:
                self._client = anthropic.Anthropic(max_retries=3, timeout=120, **kwargs)
            except Exception as e:  # missing credentials etc.
                raise AIError("The AI service is not configured (missing ANTHROPIC_API_KEY).") from e
        return self._client

    @staticmethod
    def _usage(model: str, response) -> Usage:
        u = response.usage
        return Usage(
            model=model,
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )

    @staticmethod
    def _check(response, what: str) -> None:
        if response.stop_reason == "refusal":
            raise AIError(f"The AI declined to process this {what}.")
        if response.stop_reason == "max_tokens":
            raise AIError(f"The AI response for this {what} was cut off.")
        if getattr(response, "parsed_output", None) is None:
            raise AIError(f"The AI returned an unreadable result for this {what}.")

    def extract_card(self, images: list[bytes]) -> AIResult[CardExtraction]:
        content: list[dict] = []
        labels = ["Front of the card:", "Back of the card:"]
        for label, img in zip(labels, images):
            content.append({"type": "text", "text": label})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.standard_b64encode(img).decode("ascii"),
            }})
        content.append({"type": "text", "text": "Transcribe this business card into the structured fields."})

        model = self.settings.extract_model
        kwargs = dict(
            model=model,
            max_tokens=8000,
            system=EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_format=CardExtraction,
            output_config={"effort": "low"},
        )
        try:
            if self.settings.extract_fallbacks:
                try:
                    # On a safety-classifier decline, the API retries on a fallback model in the same call.
                    response = self.client.messages.parse(
                        **kwargs,
                        extra_headers={"anthropic-beta": FALLBACK_BETA},
                        extra_body={"fallbacks": "default"},
                    )
                except anthropic.BadRequestError as e:
                    if "fallback" not in str(e).lower():
                        raise
                    log.warning("Server-side fallbacks rejected; retrying without them: %s", e)
                    response = self.client.messages.parse(**kwargs)
            else:
                response = self.client.messages.parse(**kwargs)
        except anthropic.APIConnectionError as e:
            raise AIError("Could not reach the AI service. Please try again.") from e
        except anthropic.RateLimitError as e:
            raise AIError("The AI service is busy. Please try again in a minute.") from e
        except anthropic.APIStatusError as e:
            log.error("Card extraction failed: %s", e)
            raise AIError("The AI service returned an error while reading the card.") from e

        self._check(response, "card")
        return AIResult(value=response.parsed_output, usage=self._usage(getattr(response, "model", model) or model, response))

    def parse_query(self, question: str, industries: list[str], tags: list[str]) -> AIResult[QuerySpec]:
        user_text = (
            f"Industries in use: {industries if industries else '(none yet)'}\n"
            f"Tags in use: {tags if tags else '(none yet)'}\n\n"
            f"Question: {question}"
        )
        model = self.settings.query_model
        try:
            response = self.client.messages.parse(
                model=model,
                max_tokens=4000,
                system=QUERY_SYSTEM,
                messages=[{"role": "user", "content": user_text}],
                output_format=QuerySpec,
                output_config={"effort": "low"},
            )
        except anthropic.APIConnectionError as e:
            raise AIError("Could not reach the AI service. Please try again.") from e
        except anthropic.RateLimitError as e:
            raise AIError("The AI service is busy. Please try again in a minute.") from e
        except anthropic.APIStatusError as e:
            log.error("Query parsing failed: %s", e)
            raise AIError("The AI service returned an error while reading your question.") from e

        self._check(response, "question")
        return AIResult(value=response.parsed_output, usage=self._usage(model, response))
