"""Email alert delivery via Resend.

Resend free tier: 3,000 emails/month, no credit card needed.
Sign up at https://resend.com → get API key → add to .env

Template supports EN, HI (Hindi), SW (Swahili) — same as the old WhatsApp service.
"""

import logging
from dataclasses import dataclass

import httpx

from app.config import Settings

log = logging.getLogger(__name__)

_LEVEL_LABELS = {
    "normal":  {"en": "Normal",  "hi": "सामान्य",  "sw": "Kawaida"},
    "watch":   {"en": "Watch",   "hi": "सावधान",   "sw": "Angalia"},
    "warning": {"en": "Warning", "hi": "चेतावनी",  "sw": "Onyo"},
    "severe":  {"en": "Severe",  "hi": "गंभीर",    "sw": "Kali"},
}

_LEVEL_COLORS = {
    "normal":  "#22c55e",
    "watch":   "#eab308",
    "warning": "#f97316",
    "severe":  "#ef4444",
}


def _level_label(level: str, language: str) -> str:
    lang = language[:2].lower()
    return _LEVEL_LABELS.get(level, {}).get(lang, level.capitalize())


@dataclass(frozen=True)
class EmailResult:
    message_id: str
    response: dict


def render_alert_text(
    name: str,
    zone_name: str,
    score: float,
    level: str,
    language: str,
) -> str:
    """Plain-text body for logs and fallback."""
    label = _level_label(level, language)
    if language.startswith("hi"):
        return (
            f"नमस्ते {name},\n\n"
            f"{zone_name} में फसल तनाव का स्तर {label} है (जोखिम स्कोर {score:.0f}/100)।\n\n"
            "पानी बचाएँ और स्थानीय कृषि मार्गदर्शन देखें।\n\n"
            "— Canopy Early Warning System"
        )
    if language.startswith("sw"):
        return (
            f"Habari {name},\n\n"
            f"Hatari ya msongo wa mazao katika {zone_name} ni {label} (alama {score:.0f}/100).\n\n"
            "Hifadhi maji na angalia mwongozo wa kilimo wa mtaa.\n\n"
            "— Canopy Early Warning System"
        )
    return (
        f"Hello {name},\n\n"
        f"Crop stress risk in {zone_name} is {label} (score {score:.0f}/100).\n\n"
        "Conserve water where possible and check local agricultural guidance.\n\n"
        "— Canopy Early Warning System"
    )


def render_alert_html(
    name: str,
    zone_name: str,
    score: float,
    level: str,
    language: str,
) -> str:
    """Rich HTML email body."""
    label = _level_label(level, language)
    color = _LEVEL_COLORS.get(level, "#6b7280")

    if language.startswith("hi"):
        intro = f"नमस्ते <strong>{name}</strong>,"
        body = (
            f"<strong>{zone_name}</strong> में फसल तनाव का स्तर "
            f"<span style='color:{color};font-weight:bold'>{label}</span> है।"
        )
        advice = "पानी बचाएँ और स्थानीय कृषि मार्गदर्शन देखें।"
    elif language.startswith("sw"):
        intro = f"Habari <strong>{name}</strong>,"
        body = (
            f"Hatari ya msongo wa mazao katika <strong>{zone_name}</strong> ni "
            f"<span style='color:{color};font-weight:bold'>{label}</span>."
        )
        advice = "Hifadhi maji na angalia mwongozo wa kilimo wa mtaa."
    else:
        intro = f"Hello <strong>{name}</strong>,"
        body = (
            f"Crop stress risk in <strong>{zone_name}</strong> is "
            f"<span style='color:{color};font-weight:bold'>{label}</span>."
        )
        advice = "Conserve water where possible and check local agricultural guidance."

    bar_width = int(score)

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f172a;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f172a;padding:32px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#1e293b;border-radius:12px;overflow:hidden;max-width:600px;">

        <!-- Header -->
        <tr>
          <td style="background:#166534;padding:24px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <span style="color:#4ade80;font-size:22px;font-weight:700;letter-spacing:2px;">🌿 CANOPY</span><br>
                  <span style="color:#86efac;font-size:12px;letter-spacing:1px;">CROP RISK MONITOR</span>
                </td>
                <td align="right">
                  <span style="background:{color};color:#fff;padding:6px 14px;border-radius:20px;font-size:13px;font-weight:700;letter-spacing:1px;">{label.upper()}</span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:32px;">
            <p style="color:#e2e8f0;font-size:16px;margin:0 0 16px;">{intro}</p>
            <p style="color:#cbd5e1;font-size:15px;margin:0 0 24px;">{body}</p>

            <!-- Risk score bar -->
            <div style="background:#0f172a;border-radius:8px;padding:20px;margin-bottom:24px;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="color:#94a3b8;font-size:12px;letter-spacing:1px;">RISK SCORE</td>
                  <td align="right" style="color:{color};font-size:28px;font-weight:700;">{score:.0f}<span style="font-size:14px;color:#64748b;">/100</span></td>
                </tr>
              </table>
              <div style="background:#334155;border-radius:4px;height:8px;margin-top:12px;">
                <div style="background:{color};width:{bar_width}%;height:8px;border-radius:4px;"></div>
              </div>
            </div>

            <p style="color:#94a3b8;font-size:14px;margin:0 0 24px;">⚠️ {advice}</p>

            <hr style="border:none;border-top:1px solid #334155;margin:24px 0;">
            <p style="color:#475569;font-size:12px;margin:0;">
              This alert was generated automatically by Canopy's satellite-based crop stress detection system.<br>
              Zone: <strong style="color:#64748b;">{zone_name}</strong>
            </p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#0f172a;padding:16px 32px;text-align:center;">
            <span style="color:#334155;font-size:11px;">Canopy Early Warning System · Powered by satellite data</span>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_email_alert(
    settings: Settings,
    to_email: str,
    name: str,
    zone_name: str,
    score: float,
    level: str,
    language: str,
) -> EmailResult:
    """Send a crop risk alert email via Resend."""
    label = _level_label(level, language)
    subject = f"[Canopy] {label} crop stress alert — {zone_name}"

    payload = {
        "from": settings.resend_from_email,
        "to": [to_email],
        "subject": subject,
        "html": render_alert_html(name, zone_name, score, level, language),
        "text": render_alert_text(name, zone_name, score, level, language),
    }

    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }

    log.debug("Sending email alert to %s", to_email)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.resend.com/emails",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        body = response.json()

    msg_id = body.get("id", "unknown")
    log.info("Email alert sent to %s (id=%s)", to_email, msg_id)
    return EmailResult(message_id=msg_id, response=body)