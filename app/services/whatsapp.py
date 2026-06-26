"""WhatsApp Cloud API delivery.

Template parameter order (must match your Meta Business template):
  {{1}} = farmer name
  {{2}} = zone name
  {{3}} = risk score  (0-100)
  {{4}} = risk level  (normal / watch / warning / severe)
"""

import logging
from dataclasses import dataclass

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

_LEVEL_LABELS = {
    "normal":  {"en": "Normal",  "hi": "सामान्य",    "sw": "Kawaida"},
    "watch":   {"en": "Watch",   "hi": "सावधान",     "sw": "Angalia"},
    "warning": {"en": "Warning", "hi": "चेतावनी",    "sw": "Onyo"},
    "severe":  {"en": "Severe",  "hi": "गंभीर",      "sw": "Kali"},
}


def _level_label(level: str, language: str) -> str:
    lang = language[:2].lower()
    return _LEVEL_LABELS.get(level, {}).get(lang, level.capitalize())


@dataclass(frozen=True)
class WhatsAppResult:
    message_id: str
    response: dict


def render_alert_message(
    name: str,
    zone_name: str,
    score: float,
    level: str,
    language: str,
) -> str:
    """Plain-text fallback for logs, previews, and non-template sessions."""
    label = _level_label(level, language)
    if language.startswith("hi"):
        return (
            f"नमस्ते {name}, {zone_name} में फसल तनाव का स्तर {label} है "
            f"(जोखिम स्कोर {score:.0f}/100)। "
            "पानी बचाएँ और स्थानीय कृषि मार्गदर्शन देखें।"
        )
    if language.startswith("sw"):
        return (
            f"Habari {name}, hatari ya msongo wa mazao katika {zone_name} ni {label} "
            f"(alama {score:.0f}/100). "
            "Hifadhi maji na angalia mwongozo wa kilimo wa mtaa."
        )
    return (
        f"Hello {name}, crop stress risk in {zone_name} is {label} "
        f"(score {score:.0f}/100). "
        "Conserve water where possible and check local agricultural guidance."
    )


async def send_template_alert(
    settings: Settings,
    phone_e164: str,
    name: str,
    zone_name: str,
    score: float,
    level: str,
    language: str,
) -> WhatsAppResult:
    """Send a WhatsApp template message via the Meta Cloud API."""
    url = (
        f"https://graph.facebook.com/{settings.whatsapp_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    label = _level_label(level, language)
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_e164.lstrip("+"),
        "type": "template",
        "template": {
            "name": settings.whatsapp_template_name,
            "language": {"code": language or settings.whatsapp_template_language},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": name},
                        {"type": "text", "text": zone_name},
                        {"type": "text", "text": f"{score:.0f}"},
                        {"type": "text", "text": label},
                    ],
                }
            ],
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    log.debug("Sending WhatsApp alert to %s", phone_e164)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
    msg_id = body["messages"][0]["id"]
    log.debug("WhatsApp message sent: %s", msg_id)
    return WhatsAppResult(message_id=msg_id, response=body)
