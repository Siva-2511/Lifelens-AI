# 🧠 LifeLens AI — Document to Action Assistant

> **"Don't just understand your documents. Know what to do next."**

LifeLens AI is an AI-powered Streamlit chatbot that transforms real-world documents into structured, actionable plans. Upload a photo of any document — a college circular, a utility bill, a flight ticket, a lab report — and LifeLens AI extracts the key facts, identifies deadlines, and delivers a clear step-by-step action checklist. You can then send the Action Plan directly to your WhatsApp.

---

## ✨ Features

| Feature | Detail |
|---|---|
| 📸 **Document Vision** | Powered by Gemini Vision — understands photos of real documents |
| 🗂️ **5 Categories** | College, Everyday, Work, Travel, Medical |
| ⚡ **Instant Action Plan** | Priority, deadlines, and a step-by-step action checklist |
| 💬 **Follow-up Chat** | Ask questions about the document in a persistent session |
| 📤 **WhatsApp Delivery** | Send the Action Plan to your phone via Twilio Sandbox |
| 🔒 **Zero hardcoded secrets** | All credentials loaded from `.env` or Streamlit secrets |

---

## 🚀 Running Locally

### 1. Clone the repository

```bash
git clone https://github.com/your-username/LifelensAI.git
cd LifelensAI
```

### 2. Create a virtual environment

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set up credentials

Copy the secrets template and fill in your API keys:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml`:

```toml
GEMINI_API_KEY       = "your_gemini_api_key"
TWILIO_ACCOUNT_SID   = "your_twilio_account_sid"
TWILIO_AUTH_TOKEN    = "your_twilio_auth_token"
TWILIO_WHATSAPP_FROM = "whatsapp:+14155238886"
```

> 🔑 Get your Gemini API key at [aistudio.google.com](https://aistudio.google.com).  
> 📱 Get Twilio credentials at [console.twilio.com](https://console.twilio.com).

### 5. Activate the Twilio WhatsApp Sandbox

Send any message from your phone to **+14155238886** on WhatsApp (join the sandbox) before using the "Send to WhatsApp" feature.

### 6. Run the app

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## 🌐 Deploying to Streamlit Community Cloud

1. Push your repository to GitHub (ensure `.streamlit/secrets.toml` is **not** committed).
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect your GitHub repo.
3. Add all secrets from `secrets.toml.example` in the **Secrets** section of the app settings.
4. Deploy.

---

## 📁 Project Structure

```
LifelensAI/
├── app.py                          # Main Streamlit application
├── prompts.py                      # Gemini system prompt and message templates
├── requirements.txt                # Python dependencies
├── .gitignore                      # Excludes secrets and venv
├── .streamlit/
│   ├── config.toml                 # Streamlit theme configuration
│   └── secrets.toml.example        # Safe template — fill in and rename
└── README.md
```

---

## 🔒 Security

- `.streamlit/secrets.toml` (real credentials) is listed in `.gitignore` and **never committed**.
- Only `secrets.toml.example` (a safe template with placeholder values) is tracked in Git.
- No API keys are hardcoded anywhere in the source code.

---

## 🛠️ Tech Stack

- **[Streamlit](https://streamlit.io)** — web chat interface
- **[Google Gemini API](https://aistudio.google.com)** (`google-genai`) — Vision + text AI
- **[Twilio](https://twilio.com)** — WhatsApp message delivery
- **[Pillow](https://pillow.readthedocs.io)** — image validation
- **[python-dotenv](https://pypi.org/project/python-dotenv/)** — local environment variable loading
