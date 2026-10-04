import os
import time
import requests
from collections import defaultdict
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from google import genai
from google.genai import types
from twilio.twiml.messaging_response import MessagingResponse

from prompts import SYSTEM_PROMPT

load_dotenv()


def get_secret(key_name, default=""):
    val = os.environ.get(key_name, "").strip().strip('"').strip("'")
    return val if val else default


GEMINI_API_KEY       = get_secret("GEMINI_API_KEY")
TWILIO_ACCOUNT_SID   = get_secret("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN    = get_secret("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = get_secret("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]

# Maximum conversation turns to keep per user (prevents unbounded memory growth)
MAX_HISTORY_TURNS = 10

# Initialize Clients
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
twilio_client  = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    from twilio.rest import Client as TwilioClient
    twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

app = FastAPI(title="LifeLens AI — Inbound WhatsApp Webhook")

# ── Per-user conversation history ─────────────────────────────────────────────
# Structure: { "whatsapp:+91XXXXXXXXXX": [ {"role": "user"|"model", "parts": [...]} ] }
# Each entry's "parts" is a list of google.genai types.Part objects.
user_histories: dict[str, list[dict]] = defaultdict(list)


def _trim_history(from_number: str):
    """Keep only the last MAX_HISTORY_TURNS turns (user+model pairs)."""
    history = user_histories[from_number]
    # Each pair = 2 entries (user + model); keep last N pairs
    max_entries = MAX_HISTORY_TURNS * 2
    if len(history) > max_entries:
        user_histories[from_number] = history[-max_entries:]


def _build_contents(from_number: str) -> list[types.Content]:
    """Convert stored history into a list of types.Content for Gemini."""
    return [
        types.Content(role=entry["role"], parts=entry["parts"])
        for entry in user_histories[from_number]
    ]


def ask_gemini(from_number: str, new_parts: list, max_retries: int = 2) -> str:
    """
    Append new_parts as a user turn, run Gemini with the full conversation
    history for this WhatsApp user, store the model reply, and return its text.
    """
    if not gemini_client:
        return "⚠️ Gemini API key missing or invalid on server."

    # Add the new user turn to history
    user_histories[from_number].append({"role": "user", "parts": new_parts})
    _trim_history(from_number)

    contents = _build_contents(from_number)

    last_err = ""
    for model in FALLBACK_MODELS:
        for attempt in range(max_retries):
            try:
                res = gemini_client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
                )
                if res and res.text:
                    # Store the model reply in history so next turn has full context
                    user_histories[from_number].append({
                        "role": "model",
                        "parts": [types.Part.from_text(text=res.text)],
                    })
                    _trim_history(from_number)
                    return res.text
            except Exception as err:
                last_err = str(err)
                if "404" in last_err or "NOT_FOUND" in last_err:
                    break  # try next model immediately
                if (
                    "503" in last_err or "UNAVAILABLE" in last_err
                    or "429" in last_err or "RESOURCE_EXHAUSTED" in last_err
                ) and attempt < max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break

    # Remove the user turn we added if Gemini failed (avoid broken state)
    if user_histories[from_number] and user_histories[from_number][-1]["role"] == "user":
        user_histories[from_number].pop()

    if "503" in last_err or "UNAVAILABLE" in last_err or "RESOURCE_EXHAUSTED" in last_err:
        return "⏳ The AI model is temporarily busy. Please wait a moment and try again."
    if "API_KEY" in last_err or "401" in last_err:
        return "🔑 Invalid API key configuration."
    return "⚠️ Gemini error: " + last_err[:250]


# ── Health check ───────────────────────────────────────────────────────────────
@app.get("/")
def health_check():
    return {"status": "ok", "service": "LifeLens AI WhatsApp Webhook Server"}


# ── Inbound WhatsApp webhook ───────────────────────────────────────────────────
@app.post("/webhook")
async def twilio_whatsapp_webhook(request: Request):
    """
    Twilio WhatsApp Webhook Handler.

    Maintains per-user conversation history so follow-up text questions
    after an image upload correctly remember the document context.

    Flow:
      • Image message  → download media → add image + analysis prompt to
                         user history → Gemini Vision → store reply → send back
      • Text message   → add text to user history → Gemini (full history)
                         → store reply → send back
      • "reset" / "new"→ clear history for this user
    """
    form_data   = await request.form()
    from_number = form_data.get("From", "")
    body_text   = form_data.get("Body", "").strip()
    num_media   = int(form_data.get("NumMedia", "0"))

    resp = MessagingResponse()

    # ── Reset command ──────────────────────────────────────────────────────────
    if body_text.lower() in {"reset", "new", "clear", "start over", "restart"}:
        user_histories.pop(from_number, None)
        resp.message(
            "🔄 *LifeLens AI* — Session cleared!\n\n"
            "Send me a new photo of any document (timetable, bill, ticket, lab report) "
            "to start fresh."
        )
        return Response(content=str(resp), media_type="application/xml")

    # ── Case 1: Inbound Image / Document ──────────────────────────────────────
    if num_media > 0:
        media_url          = form_data.get("MediaUrl0", "")
        media_content_type = form_data.get("MediaContentType0", "")

        if not media_content_type.startswith("image/"):
            resp.message(
                "⚠️ LifeLens AI currently processes image documents (JPG, PNG). "
                "Please upload a clear photo of your document."
            )
            return Response(content=str(resp), media_type="application/xml")

        # Download Twilio media with Basic Auth
        try:
            auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None
            dl   = requests.get(media_url, auth=auth, timeout=15)
            if dl.status_code != 200:
                resp.message(
                    f"⚠️ Failed to download your image (Status {dl.status_code}). "
                    "Please try re-sending."
                )
                return Response(content=str(resp), media_type="application/xml")
            image_bytes = dl.content
        except Exception as err:
            resp.message(f"⚠️ Error downloading image: {str(err)[:100]}")
            return Response(content=str(resp), media_type="application/xml")

        # Build the new user parts: image + analysis prompt
        analysis_prompt = (
            body_text if body_text else
            "Analyze this document photo. Identify the Category (College / Everyday / "
            "Work / Travel / Medical), Priority (HIGH / MEDIUM / LOW), key facts, "
            "dates and deadlines, required step-by-step actions, and any warnings or "
            "missing information."
        )
        new_parts = [
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            types.Part.from_text(text=analysis_prompt),
        ]

        # Clear old history for this user before a new document session
        user_histories[from_number] = []

        ai_reply = ask_gemini(from_number, new_parts)

    # ── Case 2: Text-only message ──────────────────────────────────────────────
    else:
        if not body_text:
            resp.message(
                "👋 Hi! I'm *LifeLens AI*.\n\n"
                "Send me a photo of any document — timetable, bill, ticket, "
                "lab report — and I'll extract key facts and an action plan!\n\n"
                "_After sending an image, you can ask me follow-up questions like "
                "\"Who is the class coordinator?\" and I'll remember the document._"
            )
            return Response(content=str(resp), media_type="application/xml")

        # Check if there's any history for context
        if not user_histories[from_number]:
            # No prior context — answer as a general query but nudge user
            hint = (
                "\n\n_(Tip: Send me a photo of a document first, then ask follow-up "
                "questions — I'll remember it for the whole conversation!)_"
            )
            new_parts = [types.Part.from_text(text=body_text)]
            ai_reply  = ask_gemini(from_number, new_parts)
            ai_reply  = ai_reply + hint
        else:
            # Has prior context → pure follow-up question in conversation
            new_parts = [types.Part.from_text(text=body_text)]
            ai_reply  = ask_gemini(from_number, new_parts)

    # ── Respect Twilio 1600-character limit ────────────────────────────────────
    if len(ai_reply) > 1500:
        ai_reply = ai_reply[:1450] + "\n\n...(truncated — send 'more' for continuation)"

    resp.message(ai_reply)
    return Response(content=str(resp), media_type="application/xml")
