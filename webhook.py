import os
import io
import time
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from google import genai
from google.genai import types
from twilio.rest import Client as TwilioClient
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

# Initialize Clients
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else None

app = FastAPI(title="LifeLens AI — Inbound WhatsApp Webhook")


def ask_gemini_vision(image_bytes, user_text=None, max_retries=2):
    if not gemini_client:
        return "⚠️ Gemini API key missing or invalid on server."

    parts = [types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")]
    prompt = (
        user_text.strip() if user_text and user_text.strip() else
        "Analyze this document photo. Identify the Category (College / Everyday / Work / Travel / Medical), "
        "Priority (HIGH / MEDIUM / LOW), key facts, dates and deadlines, required step-by-step actions, "
        "and any warnings or missing information."
    )
    parts.append(types.Part.from_text(text=prompt))
    contents = [types.Content(role="user", parts=parts)]

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
                    return res.text
            except Exception as err:
                last_err = str(err)
                if "404" in last_err or "NOT_FOUND" in last_err:
                    break
                if ("503" in last_err or "UNAVAILABLE" in last_err or "429" in last_err or "RESOURCE_EXHAUSTED" in last_err) and attempt < max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break

    if "503" in last_err or "UNAVAILABLE" in last_err or "RESOURCE_EXHAUSTED" in last_err:
        return "⏳ The AI model is temporarily busy. Please wait a moment and try again."
    if "API_KEY" in last_err or "401" in last_err:
        return "🔑 Invalid API key configuration."
    return "⚠️ Gemini Vision error: " + last_err[:250]


def ask_gemini_text(user_text, max_retries=2):
    if not gemini_client:
        return "⚠️ Gemini API key missing or invalid on server."

    contents = [types.Content(role="user", parts=[types.Part.from_text(text=user_text)])]

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
                    return res.text
            except Exception as err:
                last_err = str(err)
                if "404" in last_err or "NOT_FOUND" in last_err:
                    break
                if ("503" in last_err or "UNAVAILABLE" in last_err or "429" in last_err or "RESOURCE_EXHAUSTED" in last_err) and attempt < max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break

    if "503" in last_err or "UNAVAILABLE" in last_err or "RESOURCE_EXHAUSTED" in last_err:
        return "⏳ The AI model is temporarily busy. Please wait a moment and try again."
    return "⚠️ Gemini error: " + last_err[:250]


@app.get("/")
def health_check():
    return {"status": "ok", "service": "LifeLens AI WhatsApp Webhook Server"}


@app.post("/webhook")
async def twilio_whatsapp_webhook(request: Request):
    """
    Twilio WhatsApp Webhook Handler.
    Handles incoming media/photo messages & text messages from WhatsApp users.
    """
    form_data = await request.form()
    from_number = form_data.get("From", "")
    body_text   = form_data.get("Body", "").strip()
    num_media   = int(form_data.get("NumMedia", "0"))

    resp = MessagingResponse()

    # Case 1: Inbound Image / Document Media Message
    if num_media > 0:
        media_url          = form_data.get("MediaUrl0", "")
        media_content_type = form_data.get("MediaContentType0", "")

        if not media_content_type.startswith("image/"):
            resp.message("⚠️ LifeLens AI currently processes image documents (JPG, PNG). Please upload a clear photo of your document.")
            return Response(content=str(resp), media_type="application/xml")

        # Download Twilio Media securely using HTTP Basic Authentication
        try:
            auth = (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else None
            res = requests.get(media_url, auth=auth, timeout=15)
            if res.status_code != 200:
                resp.message(f"⚠️ Failed to download media from Twilio (Status {res.status_code}). Please try re-sending the image.")
                return Response(content=str(resp), media_type="application/xml")
            image_bytes = res.content
        except Exception as err:
            resp.message(f"⚠️ Error downloading image: {str(err)[:100]}")
            return Response(content=str(resp), media_type="application/xml")

        # Run Gemini Vision Document Analysis
        ai_reply = ask_gemini_vision(image_bytes, user_text=body_text)

    # Case 2: Text-only Query / Chat Message
    else:
        if not body_text:
            resp.message("👋 Hi! Send me a photo of any document (circular, bill, ticket, lab report) and I will extract key facts and an action plan!")
            return Response(content=str(resp), media_type="application/xml")

        ai_reply = ask_gemini_text(body_text)

    # Respect Twilio 1600-character limit per message
    if len(ai_reply) > 1500:
        ai_reply = ai_reply[:1450] + "\n\n...(truncated for WhatsApp 1600-char limit)"

    resp.message(ai_reply)
    return Response(content=str(resp), media_type="application/xml")
