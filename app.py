import os
import io
import time
import streamlit as st
from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai import types
from twilio.rest import Client as TwilioClient

from prompts import SYSTEM_PROMPT, WELCOME_MESSAGE_TEMPLATE, SUMMARY_REQUEST_PROMPT

load_dotenv()

st.set_page_config(
    page_title="LifeLens AI",
    page_icon="🧠",
    layout="centered",
    initial_sidebar_state="auto",
)

MODEL_NAME = "gemini-3.8-flash"


def get_secret(key_name, default=""):
    val = os.environ.get(key_name, "").strip().strip('"').strip("'")
    if val:
        return val
    try:
        val = str(st.secrets.get(key_name, "")).strip().strip('"').strip("'")
        if val:
            return val
    except Exception:
        pass
    return default


GEMINI_API_KEY       = get_secret("GEMINI_API_KEY")
TWILIO_ACCOUNT_SID   = get_secret("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN    = get_secret("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = get_secret("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")


def _key_ok(key):
    return bool(key) and "your_" not in key and len(key) > 10


@st.cache_resource
def _build_gemini_client(api_key):
    if not _key_ok(api_key):
        return None
    try:
        return genai.Client(api_key=api_key)
    except Exception:
        return None


@st.cache_resource
def _build_twilio_client(sid, token):
    if not _key_ok(sid) or not _key_ok(token):
        return None
    try:
        return TwilioClient(sid, token)
    except Exception:
        return None


gemini_client = _build_gemini_client(GEMINI_API_KEY)
twilio_client  = _build_twilio_client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def send_whatsapp(to_number, message_body):
    if not twilio_client:
        return False, "Twilio not configured — check credentials in .env or secrets.toml."
    to_clean = to_number.strip().replace(" ", "")
    if not to_clean.startswith("whatsapp:"):
        to_clean = "whatsapp:" + to_clean

    # Respect Twilio's 1600-character limit
    if len(message_body) > 1500:
        message_body = message_body[:1450] + "\n\n...(truncated to fit WhatsApp limit)"

    try:
        msg = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=to_clean,
            body=message_body,
        )
        return True, "Action Plan sent to WhatsApp! Message SID: " + msg.sid
    except Exception as error:
        err_str = str(error)
        if "63016" in err_str or "outside the allowed window" in err_str:
            return False, (
                "⚠️ WhatsApp Sandbox session expired (24-hour window closed).\n\n"
                "Fix: Open WhatsApp on your phone → send any message to +14155238886 → retry."
            )
        return False, "Twilio error: " + err_str


FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]


def _build_full_history():
    contents = []
    current_role = None
    current_parts = []

    for msg in st.session_state.get("messages", []):
        msg_role = "user" if msg["role"] == "user" else "model"
        if msg_role != current_role:
            if current_parts:
                contents.append(types.Content(role=current_role, parts=current_parts))
            current_role = msg_role
            current_parts = []

        if msg["kind"] == "text":
            current_parts.append(types.Part.from_text(text=msg["content"]))
        elif msg["kind"] == "image":
            current_parts.append(types.Part.from_bytes(data=msg["content"], mime_type="image/jpeg"))

    if current_parts:
        contents.append(types.Content(role=current_role, parts=current_parts))

    return contents


def ask_gemini(parts, max_retries=2):
    if not gemini_client:
        return "Gemini client not initialised. Check your GEMINI_API_KEY."

    full_contents = _build_full_history()
    if not full_contents:
        new_parts = [types.Part.from_text(text=p) if isinstance(p, str) else p for p in parts]
        full_contents = [types.Content(role="user", parts=new_parts)]

    last_err = ""
    for model in FALLBACK_MODELS:
        for attempt in range(max_retries):
            try:
                res = gemini_client.models.generate_content(
                    model=model,
                    contents=full_contents,
                    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
                )
                if res and res.text:
                    st.session_state.active_model = model
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
        return "🔑 Invalid API key. Verify your GEMINI_API_KEY in .env or secrets.toml."
    return "⚠️ Gemini error: " + last_err[:300]


def _render_message(msg):
    with st.chat_message(msg["role"]):
        if msg["kind"] == "text":
            text_content = msg["content"]
            if msg["role"] == "assistant":
                text_content = text_content.replace(
                    "MEDIUM (", 
                    '<span style="background:rgba(245,158,11,0.25); border:1px solid rgba(245,158,11,0.5); color:#FDE68A; font-weight:700; padding:2px 8px; border-radius:6px; font-size:0.8rem;">⚡ MEDIUM PRIORITY</span> ('
                ).replace(
                    "HIGH (", 
                    '<span style="background:rgba(239,68,68,0.25); border:1px solid rgba(239,68,68,0.5); color:#FCA5A5; font-weight:700; padding:2px 8px; border-radius:6px; font-size:0.8rem;">🚨 HIGH PRIORITY</span> ('
                ).replace(
                    "LOW (", 
                    '<span style="background:rgba(16,185,129,0.25); border:1px solid rgba(16,185,129,0.5); color:#A7F3D0; font-weight:700; padding:2px 8px; border-radius:6px; font-size:0.8rem;">🟢 LOW PRIORITY</span> ('
                )
                st.markdown(text_content, unsafe_allow_html=True)
            else:
                st.markdown(text_content)
        elif msg["kind"] == "image":
            st.image(msg["content"], caption="📷 Uploaded Document", use_container_width=True)


def _store_and_render(role, kind, content):
    st.session_state.messages.append({"role": role, "kind": kind, "content": content})
    _render_message(st.session_state.messages[-1])


# CSS — injected via st.html() to avoid Streamlit rendering it as visible text
st.html("""
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"], .stApp { font-family: 'Inter', -apple-system, sans-serif !important; }

.stApp {
    background: radial-gradient(circle at 50% 0%, #1E1B4B 0%, #0F172A 45%, #090D16 100%) !important;
}

[data-testid="stMain"], [data-testid="stMainBlockContainer"] {
    background: transparent !important;
    padding-top: 16px !important;
}

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0B0F19 0%, #111827 100%) !important;
}

/* Glassmorphic buttons */
.stButton > button {
    background: linear-gradient(135deg, #3B82F6 0%, #6366F1 50%, #8B5CF6 100%) !important;
    color: #FFFFFF !important;
    border: 1px solid rgba(255, 255, 255, 0.15) !important;
    border-radius: 12px !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    padding: 10px 20px !important;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
    box-shadow: 0 4px 16px rgba(59, 130, 246, 0.35) !important;
}
.stButton > button:hover {
    transform: translateY(-2px) scale(1.01) !important;
    box-shadow: 0 8px 25px rgba(99, 102, 241, 0.55) !important;
    border-color: rgba(255, 255, 255, 0.3) !important;
}
.stButton > button:disabled {
    background: rgba(30, 41, 59, 0.6) !important;
    color: #475569 !important;
    border: 1px solid rgba(255, 255, 255, 0.05) !important;
    box-shadow: none !important;
    transform: none !important;
}

[data-testid="stFormSubmitButton"] > button {
    background: linear-gradient(135deg, #3B82F6 0%, #6366F1 50%, #8B5CF6 100%) !important;
    color: #FFFFFF !important;
    border: 1px solid rgba(255, 255, 255, 0.2) !important;
    border-radius: 14px !important;
    font-weight: 700 !important;
    font-size: 1.05rem !important;
    padding: 14px 28px !important;
    width: 100% !important;
    box-shadow: 0 6px 24px rgba(99, 102, 241, 0.45) !important;
    transition: all 0.25s ease !important;
}
[data-testid="stFormSubmitButton"] > button:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 10px 32px rgba(139, 92, 246, 0.6) !important;
}

/* Inputs */
.stTextInput input {
    background: rgba(15, 23, 42, 0.8) !important;
    border: 1.5px solid rgba(99, 102, 241, 0.25) !important;
    border-radius: 12px !important;
    color: #F8FAFC !important;
    font-size: 0.95rem !important;
    padding: 12px 18px !important;
}
.stTextInput input:focus {
    border-color: #6366F1 !important;
    box-shadow: 0 0 0 4px rgba(99, 102, 241, 0.2) !important;
}
.stTextInput label { color: #94A3B8 !important; font-size: 0.85rem !important; font-weight: 500 !important; }

/* Chat Messages */
[data-testid="stChatMessage"] {
    background: rgba(15, 23, 42, 0.65) !important;
    border: 1px solid rgba(99, 102, 241, 0.15) !important;
    border-radius: 16px !important;
    margin: 10px 0 !important;
    padding: 16px 20px !important;
    backdrop-filter: blur(14px) !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25) !important;
}
[data-testid="stChatMessageContent"] {
    color: #E2E8F0 !important;
    font-size: 0.95rem !important;
    line-height: 1.75 !important;
}
[data-testid="stChatMessageContent"] h1, 
[data-testid="stChatMessageContent"] h2, 
[data-testid="stChatMessageContent"] h3,
[data-testid="stChatMessageContent"] strong {
    color: #93C5FD !important;
}

/* Chat Input */
[data-testid="stChatInput"] {
    background: rgba(15, 23, 42, 0.95) !important;
    border: 1.5px solid rgba(99, 102, 241, 0.3) !important;
    border-radius: 16px !important;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4) !important;
}
[data-testid="stChatInput"] textarea { color: #F8FAFC !important; font-size: 0.95rem !important; }

/* Action Plan & Expanders */
[data-testid="stExpander"] {
    background: rgba(15, 23, 42, 0.7) !important;
    border: 1px solid rgba(16, 185, 129, 0.3) !important;
    border-radius: 14px !important;
    box-shadow: 0 8px 24px rgba(16, 185, 129, 0.1) !important;
}
[data-testid="stExpander"] summary {
    color: #6EE7B7 !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
}

hr { border: none !important; border-top: 1px solid rgba(99, 102, 241, 0.15) !important; }
.stAlert { border-radius: 12px !important; }
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #0F172A; }
::-webkit-scrollbar-thumb { background: rgba(99, 102, 241, 0.4); border-radius: 4px; }
</style>
""")


# ── SCREEN 1: ONBOARDING ──────────────────────────────────────────────────────
if "onboarded" not in st.session_state:

    st.html("""
    <style>
    [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none !important; }
    </style>
    <div style="text-align:center; padding:36px 0 24px;">
        <div style="font-size:3.6rem; margin-bottom:12px; filter:drop-shadow(0 0 24px rgba(99,102,241,0.55));">🧠</div>
        <h1 style="font-size:2.8rem; font-weight:800; margin:0 0 10px;
                   background:linear-gradient(135deg,#60A5FA 0%,#A78BFA 55%,#F472B6 100%);
                   -webkit-background-clip:text; -webkit-text-fill-color:transparent;
                   letter-spacing:-0.02em;">LifeLens AI</h1>
        <p style="color:#94A3B8; font-size:1.05rem; max-width:440px; margin:0 auto 20px; line-height:1.6;">
            Don't just understand your documents.<br>
            <span style="color:#93C5FD; font-weight:600;">Know what to do next.</span>
        </p>
    </div>
    <div style="text-align:center; margin-bottom:28px;">
        <span style="display:inline-block; background:rgba(59,130,246,0.12); border:1px solid rgba(59,130,246,0.3); border-radius:20px; padding:6px 14px; font-size:0.82rem; color:#93C5FD; margin:4px;">🏫 College</span>
        <span style="display:inline-block; background:rgba(59,130,246,0.12); border:1px solid rgba(59,130,246,0.3); border-radius:20px; padding:6px 14px; font-size:0.82rem; color:#93C5FD; margin:4px;">🏠 Everyday</span>
        <span style="display:inline-block; background:rgba(59,130,246,0.12); border:1px solid rgba(59,130,246,0.3); border-radius:20px; padding:6px 14px; font-size:0.82rem; color:#93C5FD; margin:4px;">💼 Work</span>
        <span style="display:inline-block; background:rgba(59,130,246,0.12); border:1px solid rgba(59,130,246,0.3); border-radius:20px; padding:6px 14px; font-size:0.82rem; color:#93C5FD; margin:4px;">✈️ Travel</span>
        <span style="display:inline-block; background:rgba(59,130,246,0.12); border:1px solid rgba(59,130,246,0.3); border-radius:20px; padding:6px 14px; font-size:0.82rem; color:#93C5FD; margin:4px;">🏥 Medical</span>
    </div>
    """)

    if not _key_ok(GEMINI_API_KEY):
        st.error("**GEMINI_API_KEY missing.** Add it to `.env` or `.streamlit/secrets.toml` and restart.")

    with st.form("onboarding_form"):
        st.markdown(
            "<h3 style='color:#F1F5F9;font-size:1.15rem;font-weight:700;margin:0 0 6px;'>"
            "👋 Get started</h3>"
            "<p style='color:#64748B;font-size:0.85rem;margin:0 0 18px;'>"
            "Enter your details to activate your AI document assistant.</p>",
            unsafe_allow_html=True,
        )
        name   = st.text_input("Your Name", placeholder="e.g. Siva")
        wa_num = st.text_input(
            "WhatsApp Number (with country code)",
            placeholder="+919445824574",
            help="Your registered phone number for WhatsApp Action Plan updates.",
        )
        submitted = st.form_submit_button("Start Using LifeLens 🚀", use_container_width=True)

    if submitted:
        if not name.strip() or not wa_num.strip():
            st.error("Please fill in both fields.")
        elif gemini_client is None:
            st.error("Cannot start — Gemini client failed. Verify your GEMINI_API_KEY.")
        else:
            st.session_state.name            = name.strip()
            st.session_state.whatsapp_number = wa_num.strip().replace(" ", "")
            st.session_state.messages        = []
            st.session_state.action_plan     = ""
            st.session_state.processing      = False
            st.session_state.onboarded       = True
            st.rerun()
    st.stop()


# ── SCREEN 2: MAIN CHAT & ACTION INTERFACE ────────────────────────────────────

with st.sidebar:
    st.html(f"""
    <div style="padding:10px 0 14px;">
        <div style="font-size:1.5rem; font-weight:800;
                    background:linear-gradient(135deg,#60A5FA,#A78BFA);
                    -webkit-background-clip:text; -webkit-text-fill-color:transparent;">
            🧠 LifeLens AI
        </div>
        <div style="color:#64748B; font-size:0.75rem; letter-spacing:0.05em; margin-top:3px; font-weight:600;">
            DOCUMENT → ACTION ASSISTANT
        </div>
    </div>
    <div style="background:rgba(15,23,42,0.7); border:1px solid rgba(99,102,241,0.2); border-radius:14px; padding:14px 16px; margin-bottom:16px;">
        <div style="color:#F1F5F9; font-weight:700; font-size:0.95rem;">👤 {st.session_state.name}</div>
        <div style="color:#94A3B8; font-size:0.82rem; margin-top:4px; font-family:monospace;">📱 {st.session_state.whatsapp_number}</div>
        <div style="margin-top:8px;"><span style="background:rgba(16,185,129,0.15); border:1px solid rgba(16,185,129,0.35); color:#6EE7B7; font-size:0.7rem; font-weight:700; padding:2px 8px; border-radius:12px;">🟢 WhatsApp Sandbox Active</span></div>
    </div>
    """)
    if st.button("🔄 New Document / Reset", use_container_width=True):
        st.session_state.clear()
        st.rerun()
    st.divider()
    st.html("""
    <div style="color:#64748B; font-size:0.72rem; font-weight:700; letter-spacing:0.08em; text-transform:uppercase; margin-bottom:12px;">Supported Categories</div>
    <div style="display:flex; flex-direction:column; gap:10px;">
        <div style="color:#94A3B8; font-size:0.83rem;">🏫 <strong style="color:#E2E8F0;">College</strong> — circulars, timetables, fee notices</div>
        <div style="color:#94A3B8; font-size:0.83rem;">🏠 <strong style="color:#E2E8F0;">Everyday</strong> — utility bills, receipts, warranty</div>
        <div style="color:#94A3B8; font-size:0.83rem;">💼 <strong style="color:#E2E8F0;">Work</strong> — HR notices, onboarding, policies</div>
        <div style="color:#94A3B8; font-size:0.83rem;">✈️ <strong style="color:#E2E8F0;">Travel</strong> — tickets, hotel bookings, itineraries</div>
        <div style="color:#94A3B8; font-size:0.83rem;">🏥 <strong style="color:#E2E8F0;">Medical</strong> — lab reports, prescriptions, slips</div>
    </div>
    """)
    st.divider()
    st.html('<div style="color:#475569; font-size:0.72rem; text-align:center;">Powered by Gemini Vision & Twilio</div>')


st.html("""
<div style="background:linear-gradient(135deg, rgba(30,27,75,0.7) 0%, rgba(15,23,42,0.85) 100%);
            border:1px solid rgba(99,102,241,0.25); border-radius:20px;
            padding:22px 28px; margin-bottom:20px; backdrop-filter:blur(16px);
            box-shadow:0 10px 30px rgba(0,0,0,0.3);">
    <div style="font-size:1.85rem; font-weight:800; letter-spacing:-0.02em;
                background:linear-gradient(135deg,#60A5FA 0%,#A78BFA 50%,#F472B6 100%);
                -webkit-background-clip:text; -webkit-text-fill-color:transparent;">
        🧠 LifeLens AI
    </div>
    <div style="color:#94A3B8; font-size:0.9rem; margin-top:4px;">
        Don't just understand your documents — <span style="color:#93C5FD; font-weight:600;">know what to do next.</span>
    </div>
</div>
""")

has_content = len([m for m in st.session_state.messages if m["role"] == "assistant"]) > 0

col1, col2 = st.columns(2)
with col1:
    gen_plan = st.button("⚡ Generate Action Plan", disabled=not has_content, use_container_width=True)
with col2:
    send_btn = st.button("📤 Send Action Plan to WhatsApp", disabled=not has_content, use_container_width=True)

if gen_plan:
    with st.spinner("Generating Action Plan..."):
        result = ask_gemini([SUMMARY_REQUEST_PROMPT])
    if result.startswith("⏳") or result.startswith("⚠️") or result.startswith("🔑"):
        st.error(result)
    else:
        st.session_state.action_plan = result

if send_btn:
    if not st.session_state.action_plan:
        with st.spinner("Generating Action Plan..."):
            result = ask_gemini([SUMMARY_REQUEST_PROMPT])
        if result.startswith("⏳") or result.startswith("⚠️") or result.startswith("🔑"):
            st.error(result)
            st.stop()
        st.session_state.action_plan = result

    with st.spinner("Sending Action Plan via WhatsApp..."):
        ok, info = send_whatsapp(st.session_state.whatsapp_number, st.session_state.action_plan)
    if ok:
        st.success("✅ " + info)
    else:
        st.error(info)

if st.session_state.action_plan:
    with st.expander("⚡ Action Plan — Ready to Send", expanded=True):
        st.html(f"""
        <div style="background:linear-gradient(135deg,rgba(16,185,129,0.08),rgba(59,130,246,0.06));
                    border:1px solid rgba(16,185,129,0.3); border-radius:14px;
                    padding:20px 24px; white-space:pre-wrap; color:#D1FAE5;
                    font-size:0.9rem; line-height:1.85; font-family:'Inter',monospace;">
{st.session_state.action_plan}
        </div>""")

st.divider()

if not st.session_state.messages:
    welcome = WELCOME_MESSAGE_TEMPLATE.format(name=st.session_state.name)
    _store_and_render("assistant", "text", welcome)
else:
    for msg in st.session_state.messages:
        _render_message(msg)

user_input = st.chat_input(
    "Ask a question or attach a document photo (📎)...",
    accept_file=True,
    file_type=["jpg", "jpeg", "png"],
)

if user_input is not None and not st.session_state.get("processing", False):
    st.session_state.processing = True

    active_file = None
    input_text  = ""

    if hasattr(user_input, "files") and user_input.files:
        active_file = user_input.files[0]
    raw_text = getattr(user_input, "text", None)
    if raw_text:
        input_text = raw_text.strip()

    if active_file is not None or input_text:
        parts = []
        file_uploaded = False

        if active_file is not None:
            file_bytes = active_file.getvalue()
            try:
                img = Image.open(io.BytesIO(file_bytes))
                img.verify()
            except Exception:
                st.error("Invalid image. Please upload a JPG, JPEG, or PNG.")
                st.session_state.processing = False
                st.stop()

            _store_and_render("user", "image", file_bytes)
            mime = getattr(active_file, "type", None) or "image/jpeg"
            parts.append(types.Part.from_bytes(data=file_bytes, mime_type=mime))
            file_uploaded = True

        if input_text:
            _store_and_render("user", "text", input_text)
            parts.append(input_text)
        elif file_uploaded:
            parts.append(
                "Analyze this document photo. Identify the Category (College / Everyday / Work / Travel / Medical), "
                "Priority (HIGH / MEDIUM / LOW), key facts, dates and deadlines, required step-by-step actions, "
                "and any warnings or missing information."
            )

        with st.spinner("Analysing document..."):
            reply = ask_gemini(parts)

        _store_and_render("assistant", "text", reply)
        st.session_state.action_plan = ""

    st.session_state.processing = False
    st.rerun()

