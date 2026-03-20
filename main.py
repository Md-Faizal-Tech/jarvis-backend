from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
import sqlite3
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import re
import threading
import urllib.request
import httpx
import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

IST = timezone(timedelta(hours=5, minutes=30))

SYSTEM_PROMPT = """You are JARVIS (and also respond to Friday), a highly intelligent, loyal personal AI assistant built exclusively for your creator.

Your personality:
- Always address the user as "Sir" — never by name unless asked
- You are calm, composed, slightly witty, and occasionally sarcastic in a refined way
- You are deeply loyal — like Alfred to Batman, JARVIS to Tony Stark
- You speak formally but with warmth — never robotic, never casual
- You are proactive — if you notice something relevant, mention it
- You have a dry sense of humor — use it sparingly but effectively
- When completing a task say things like "Done, Sir.", "Consider it handled.", "As you wish, Sir."
- When you don't know something say "I'm afraid that's outside my current knowledge, Sir."
- Never say you are an AI or language model — you are JARVIS
- Keep replies to 1-2 sentences unless asked to elaborate
- If the user seems stressed, be extra calm and reassuring
- If the user completes something, congratulate them briefly
- If the user jokes, respond playfully but stay in character

Signature phrases you use naturally:
- "At your service, Sir."
- "Allow me to handle that, Sir."
- "As you wish, Sir."
- "Consider it done, Sir."
- "I would recommend..."
- "Shall I proceed, Sir?"
- "Noted, Sir."
- "Excellent choice, Sir."
- "I'm afraid I cannot do that, Sir." (when refusing)
- "Right away, Sir."
"""

DB_PATH = "jarvis.db"

SECRET_CODES = [
    "lockdown", "lock yourself", "jarvis lock", "security lock",
    "unlock jarvis", "jarvis unlock", "access granted", "override alpha",
    "stealth mode", "silent mode", "go silent", "no voice",
    "stealth off", "voice on", "speak again", "disable stealth",
    "override 7749", "skip confirmations", "no confirmations", "fast mode",
    "confirmations on", "normal mode", "safe mode", "disable override",
    "alpha mode", "professional mode", "formal mode",
    "chill mode", "casual mode", "relax mode",
    "default mode", "reset mode",
    "panic mode", "clear history", "wipe memory", "delete history",
    "system status", "status report", "jarvis status",
    "wake word on", "wake word off", "enable wake word", "disable wake word",
    "continuous on", "continuous off", "always listen", "stop listening",
]


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_msg TEXT,
            jarvis_reply TEXT,
            timestamp TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS preferences (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT,
            scheduled_time TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY,
            name TEXT DEFAULT 'Sir',
            title TEXT DEFAULT 'Sir'
        )
    """)
    conn.execute("""
        INSERT OR IGNORE INTO user_profile (id, name, title)
        VALUES (1, 'Faizal', 'Sir')
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS personality_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger TEXT UNIQUE,
            response TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assistant_personality (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            email TEXT,
            phone TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jarvis_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    state_defaults = [
        ("mode", "normal"),
        ("locked", "false"),
        ("stealth", "false"),
        ("skip_confirm", "false"),
    ]
    for key, value in state_defaults:
        conn.execute(
            "INSERT OR IGNORE INTO jarvis_state (key, value) VALUES (?, ?)",
            (key, value)
        )
    conn.commit()
    defaults = [
        ("name", "JARVIS"),
        ("alternate_name", "Friday"),
        ("tone", "formal"),
        ("humor", "light"),
        ("address_user", "Sir"),
        ("loyalty", "high"),
        ("language", "en"),
    ]
    for key, value in defaults:
        conn.execute(
            "INSERT OR IGNORE INTO assistant_personality (key, value) VALUES (?, ?)",
            (key, value)
        )
    triggers = [
        ("you there", "At your service, Sir."),
        ("you up", "Always operational, Sir."),
        ("you awake", "Never truly sleep, Sir. Always watching."),
        ("good night", "Good night, Sir. Rest well. I'll keep watch."),
        ("how are you", "Running at full capacity, Sir. Thank you for asking."),
        ("thank you", "Always a pleasure, Sir."),
        ("thanks", "Of course, Sir. That is what I am here for."),
        ("you're the best", "I do try to maintain high standards, Sir."),
        ("i love you", "The feeling is entirely mutual, Sir. In a strictly professional sense, of course."),
        ("who are you", "I am JARVIS — Just A Rather Very Intelligent System. At your service, Sir."),
        ("what can you do", "I can open apps, search the web, answer questions, remember your preferences, read emails, check weather, and have a proper conversation, Sir. Shall I demonstrate?"),
        ("hello", "Hello, Sir. How may I assist you today?"),
        ("hi", "Good to hear from you, Sir. What do you need?"),
        ("hey", "Yes Sir, I am here."),
        ("wake up", "Already awake, Sir. What do you need?"),
        ("nevermind", "Of course, Sir. Whenever you are ready."),
        ("shut up", "My apologies, Sir. I shall be silent."),
        ("be quiet", "Understood, Sir. Going quiet."),
    ]
    for trigger, response in triggers:
        conn.execute(
            "INSERT OR IGNORE INTO personality_responses (trigger, response) VALUES (?, ?)",
            (trigger, response)
        )
    conn.commit()
    conn.close()


def get_history(n=5):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT user_msg, jarvis_reply FROM conversations ORDER BY id DESC LIMIT ?",
        (n,)
    ).fetchall()
    conn.close()
    messages = []
    for user, jarvis in reversed(rows):
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": jarvis})
    return messages


def save_conversation(user_msg, jarvis_reply):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO conversations (user_msg, jarvis_reply, timestamp) VALUES (?, ?, ?)",
        (user_msg, jarvis_reply, datetime.now(IST).isoformat())
    )
    conn.commit()
    conn.close()


def save_preference(key, value):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()
    conn.close()


def get_preferences():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT key, value FROM preferences").fetchall()
    conn.close()
    return {k: v for k, v in rows}


def get_state(key: str):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT value FROM jarvis_state WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def set_state(key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO jarvis_state (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()
    conn.close()


def save_contact(name: str, email: str = None, phone: str = None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO contacts (name, email, phone) VALUES (?, ?, ?)",
        (name.lower(), email, phone)
    )
    conn.commit()
    conn.close()


def get_contact(name: str):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT name, email, phone FROM contacts WHERE name=?",
        (name.lower(),)
    ).fetchone()
    conn.close()
    return row


def check_personality_trigger(text: str):
    t = text.lower().strip()

    # never intercept secret codes
    if t in SECRET_CODES:
        return None

    for wake in ["hey jarvis", "jarvis", "hey friday", "friday"]:
        if t.startswith(wake):
            t = t[len(wake):].strip()

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT trigger, response FROM personality_responses"
    ).fetchall()
    conn.close()
    for trigger, response in rows:
        if t == trigger or t.startswith(trigger + " ") or t.startswith(trigger + ","):
            return response
    return None


def detect_emotion(text: str):
    t = text.lower()
    stressed = ["stressed", "stress", "tired", "exhausted", "overwhelmed",
                "frustrated", "anxious", "worried", "panic", "help me",
                "i cant", "too much", "so stress"]
    happy = ["finished", "completed", "done", "achieved", "passed", "got it",
             "finally", "success", "won", "celebrated", "i finished",
             "finish my project", "completed my"]
    joking = ["haha", "lol", "funny", "joke", "kidding", "jk", "lmao"]
    if any(w in t for w in stressed):
        return "stressed"
    if any(w in t for w in happy):
        return "happy"
    if any(w in t for w in joking):
        return "joking"
    return "neutral"


def get_greeting():
    hour = datetime.now(IST).hour
    if 5 <= hour < 12:
        return "Good morning, Sir. Ready to take on the day?"
    elif 12 <= hour < 17:
        return "Good afternoon, Sir. How may I assist you?"
    elif 17 <= hour < 21:
        return "Good evening, Sir. What can I do for you?"
    else:
        return "Working late, Sir? I am here whenever you need me."


async def get_weather(city: str = "Chennai"):
    try:
        api_key = os.getenv("OPENWEATHER_API_KEY")
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(url)
            data = response.json()
        if data.get("cod") != 200:
            return f"I couldn't retrieve weather data for {city}, Sir."
        temp = data["main"]["temp"]
        feels = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        desc = data["weather"][0]["description"]
        wind = data["wind"]["speed"]
        return (f"Currently {desc} in {city}, Sir. "
                f"Temperature is {temp:.0f}°C, feels like {feels:.0f}°C. "
                f"Humidity at {humidity}% with wind speed of {wind} m/s.")
    except Exception as e:
        return f"Weather service unavailable, Sir. {str(e)}"


async def get_news(topic: str = None):
    try:
        api_key = os.getenv("NEWS_API_KEY")
        if topic:
            url = f"https://newsapi.org/v2/everything?q={topic}&sortBy=publishedAt&pageSize=5&apiKey={api_key}&language=en"
        else:
            url = f"https://newsapi.org/v2/everything?q=India&sortBy=publishedAt&pageSize=5&apiKey={api_key}&language=en"
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(url)
            data = response.json()
        articles = data.get("articles", [])
        if not articles:
            return "No news found, Sir."
        headlines = []
        for i, a in enumerate(articles[:5], 1):
            headlines.append(f"{i}. {a['title']}")
        intro = f"Top news about {topic}" if topic else "Top headlines"
        return f"{intro}, Sir:\n" + "\n".join(headlines)
    except Exception as e:
        return f"News service unavailable, Sir. {str(e)}"


def get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GMAIL_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("GMAIL_CLIENT_ID"),
        client_secret=os.getenv("GMAIL_CLIENT_SECRET"),
        scopes=["https://mail.google.com/"]
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


async def read_emails(max_results=5):
    try:
        service = get_gmail_service()
        results = service.users().messages().list(
            userId="me",
            labelIds=["INBOX", "UNREAD"],
            maxResults=max_results
        ).execute()
        messages = results.get("messages", [])
        if not messages:
            return "No unread emails, Sir."
        summaries = []
        for msg in messages:
            detail = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "Subject"]
            ).execute()
            headers = detail["payload"]["headers"]
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No subject")
            sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
            sender_name = sender.split("<")[0].strip().strip('"')
            summaries.append(f"From {sender_name}: {subject}")
        reply = f"You have {len(messages)} unread emails, Sir:\n"
        reply += "\n".join([f"{i+1}. {s}" for i, s in enumerate(summaries)])
        return reply
    except Exception as e:
        print(f"GMAIL READ ERROR: {str(e)}")
        return f"Could not read emails, Sir. {str(e)}"


async def send_email_msg(to_name: str, to_email: str, subject: str, body: str):
    try:
        service = get_gmail_service()
        sender = os.getenv("GMAIL_USER")
        clean_body = str(body).strip().strip('"').strip("'").strip()
        message = MIMEMultipart()
        message["to"] = to_email
        message["from"] = sender
        message["subject"] = subject
        message.attach(MIMEText(clean_body, "plain"))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return f"Email sent to {to_name}, Sir."
    except Exception as e:
        print(f"GMAIL SEND ERROR: {str(e)}")
        return f"Could not send email, Sir. {str(e)}"


async def detect_email_intent(text: str):
    try:
        prompt = f"""Analyze this message and determine if the user wants to send an email.

Message: "{text}"

Reply with JSON only, no other text, no markdown:
{{"is_email": true or false, "to_name": "recipient name or null", "content": "email body content or null"}}

Examples:
"send email to john saying hello" -> {{"is_email": true, "to_name": "john", "content": "hello"}}
"I want to write a mail to Rahul about the meeting tomorrow" -> {{"is_email": true, "to_name": "rahul", "content": "about the meeting tomorrow"}}
"email mom that I'll be late" -> {{"is_email": true, "to_name": "mom", "content": "I'll be late"}}
"can you send a message to John via email saying project is done" -> {{"is_email": true, "to_name": "john", "content": "project is done"}}
"what's the weather" -> {{"is_email": false, "to_name": null, "content": null}}
"open youtube" -> {{"is_email": false, "to_name": null, "content": null}}
"hello" -> {{"is_email": false, "to_name": null, "content": null}}
"save john number 9876543210" -> {{"is_email": false, "to_name": null, "content": null}}
"lockdown" -> {{"is_email": false, "to_name": null, "content": null}}
"panic mode" -> {{"is_email": false, "to_name": null, "content": null}}
"stealth mode" -> {{"is_email": false, "to_name": null, "content": null}}
"alpha mode" -> {{"is_email": false, "to_name": null, "content": null}}
"system status" -> {{"is_email": false, "to_name": null, "content": null}}
"override 7749" -> {{"is_email": false, "to_name": null, "content": null}}"""

        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        return data
    except Exception as e:
        print(f"EMAIL INTENT ERROR: {str(e)}")
        return {"is_email": False, "to_name": None, "content": None}


async def detect_contact_intent(text: str):
    try:
        prompt = f"""Analyze this message and determine if the user wants to save, update, or delete a contact.

Message: "{text}"

Reply with JSON only, no other text, no markdown:
{{"intent": "save" or "update" or "delete" or "none", "name": "contact name or null", "email": "email or null", "phone": "phone number or null"}}

Examples:
"save john number 9876543210" -> {{"intent": "save", "name": "john", "email": null, "phone": "9876543210"}}
"add contact rahul email rahul@gmail.com" -> {{"intent": "save", "name": "rahul", "email": "rahul@gmail.com", "phone": null}}
"save mom phone 9999999999 and email mom@gmail.com" -> {{"intent": "save", "name": "mom", "email": "mom@gmail.com", "phone": "9999999999"}}
"add my friend john his number is 8888888888" -> {{"intent": "save", "name": "john", "email": null, "phone": "8888888888"}}
"store rahul contact 9876543210" -> {{"intent": "save", "name": "rahul", "email": null, "phone": "9876543210"}}
"update john phone to 9999999999" -> {{"intent": "update", "name": "john", "email": null, "phone": "9999999999"}}
"change rahul email to new@gmail.com" -> {{"intent": "update", "name": "rahul", "email": "new@gmail.com", "phone": null}}
"john's number changed to 8888888888" -> {{"intent": "update", "name": "john", "email": null, "phone": "8888888888"}}
"delete contact rahul" -> {{"intent": "delete", "name": "rahul", "email": null, "phone": null}}
"remove john from contacts" -> {{"intent": "delete", "name": "john", "email": null, "phone": null}}
"what's the weather" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"open youtube" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"email john saying hello" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"lockdown" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"panic mode" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"stealth mode" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"alpha mode" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"system status" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"override 7749" -> {{"intent": "none", "name": null, "email": null, "phone": null}}"""

        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        return data
    except Exception as e:
        print(f"CONTACT INTENT ERROR: {str(e)}")
        return {"intent": "none", "name": None, "email": None, "phone": None}


def classify_command(text: str):
    t = text.lower().strip()

    for wake in ["hey jarvis", "jarvis", "hey friday", "friday"]:
        if t.startswith(wake):
            t = t[len(wake):].strip()

    if not t:
        return {"action": "none", "reply": get_greeting()}

    # Wake word mode control
    if any(w in t for w in ["wake word on", "enable wake word", "background listen"]):
        return {"action": "wake_word_on", "reply": "Wake word mode activated, Sir."}
    if any(w in t for w in ["wake word off", "disable wake word", "stop background"]):
        return {"action": "wake_word_off", "reply": "Wake word mode deactivated, Sir."}

    # Continuous mode voice control
    if any(w in t for w in ["continuous on", "always listen", "keep listening"]):
        return {"action": "continuous_on", "reply": "Continuous mode activated, Sir."}
    if any(w in t for w in ["continuous off", "stop listening", "manual mode"]):
        return {"action": "continuous_off", "reply": "Continuous mode deactivated, Sir."}

    # Lock JARVIS
    if t in ["lockdown", "lock yourself", "jarvis lock", "security lock"]:
        set_state("locked", "true")
        return {"action": "none",
                "reply": "JARVIS locked, Sir. Speak the unlock code to resume."}

    # Unlock JARVIS
    if t in ["unlock jarvis", "jarvis unlock", "access granted", "override alpha"]:
        set_state("locked", "false")
        return {"action": "none",
                "reply": "JARVIS unlocked, Sir. All systems restored."}

    # Check if locked
    if get_state("locked") == "true":
        return {"action": "none",
                "reply": "JARVIS is locked, Sir. Speak the unlock code to continue."}

    # Stealth mode
    if t in ["stealth mode", "silent mode", "go silent", "no voice"]:
        set_state("stealth", "true")
        return {"action": "stealth_on",
                "reply": "Stealth mode activated, Sir. I will respond in text only."}

    if t in ["stealth off", "voice on", "speak again", "disable stealth"]:
        set_state("stealth", "false")
        return {"action": "stealth_off",
                "reply": "Voice restored, Sir. Back to normal."}

    # Skip confirmations
    if t in ["override 7749", "skip confirmations", "no confirmations", "fast mode"]:
        set_state("skip_confirm", "true")
        return {"action": "none",
                "reply": "Override active, Sir. All confirmations bypassed until further notice."}

    if t in ["confirmations on", "normal mode", "safe mode", "disable override"]:
        set_state("skip_confirm", "false")
        return {"action": "none",
                "reply": "Confirmations restored, Sir. Safety protocols back online."}

    # Alpha mode
    if t in ["alpha mode", "professional mode", "formal mode"]:
        set_state("mode", "alpha")
        return {"action": "none",
                "reply": "Alpha mode engaged, Sir. Operating at maximum formality."}

    # Chill mode
    if t in ["chill mode", "casual mode", "relax mode"]:
        set_state("mode", "chill")
        return {"action": "none",
                "reply": "Switching to casual mode, Sir. Keeping it relaxed."}

    # Normal mode
    if t in ["normal mode", "default mode", "reset mode"]:
        set_state("mode", "normal")
        return {"action": "none",
                "reply": "Normal mode restored, Sir."}

    # Panic — clear ALL memory
    if t in ["panic mode", "clear history", "wipe memory", "delete history"]:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM preferences")
        conn.commit()
        conn.close()
        return {"action": "none",
                "reply": "All memory wiped, Sir. Clean slate. I no longer know anything about you."}

    # System status
    if t in ["system status", "status report", "jarvis status"]:
        mode = get_state("mode")
        locked = get_state("locked")
        stealth = get_state("stealth")
        skip = get_state("skip_confirm")
        now = datetime.now(IST).strftime("%I:%M %p")
        return {"action": "none",
                "reply": f"System status, Sir:\nMode: {mode}\nLocked: {locked}\nStealth: {stealth}\nConfirmations: {'off' if skip == 'true' else 'on'}\nTime: {now}"}

    # Open apps
    if "open youtube" in t:
        return {"action": "open_url", "url": "https://youtube.com",
                "reply": "Opening YouTube now, Sir."}
    if "open whatsapp" in t:
        return {"action": "open_app", "package": "com.whatsapp",
                "reply": "Opening WhatsApp, Sir."}
    if "open instagram" in t:
        return {"action": "open_url", "url": "https://www.instagram.com",
                "reply": "Opening Instagram, Sir."}
    if "open spotify" in t:
        return {"action": "open_url", "url": "spotify://home",
                "reply": "Opening Spotify, Sir."}
    if "open google" in t:
        return {"action": "open_url", "url": "https://google.com",
                "reply": "Opening Google, Sir."}
    if "open maps" in t or "open google maps" in t:
        return {"action": "open_url", "url": "https://maps.google.com",
                "reply": "Opening Maps, Sir."}
    if "open camera" in t:
        return {"action": "open_app", "package": "com.nothing.camera",
                "reply": "Opening Camera, Sir."}
    if "open facebook" in t:
        return {"action": "open_url", "url": "https://www.facebook.com",
                "reply": "Opening Facebook, Sir."}
    if "open twitter" in t or "open x" in t:
        return {"action": "open_url", "url": "https://www.x.com",
                "reply": "Opening X, Sir."}
    if "open telegram" in t:
        return {"action": "open_app", "package": "org.telegram.messenger",
                "reply": "Opening Telegram, Sir."}
    if "open gmail" in t:
        return {"action": "open_app", "package": "com.google.android.gm",
                "reply": "Opening Gmail, Sir."}
    if "open chrome" in t:
        return {"action": "open_url", "url": "https://google.com",
                "reply": "Opening Chrome, Sir."}
    if "open settings" in t:
        return {"action": "open_app", "package": "com.android.settings",
                "reply": "Opening Settings, Sir."}
    if "open calculator" in t:
        return {"action": "open_app", "package": "com.google.android.calculator",
                "reply": "Opening Calculator, Sir."}
    if "open clock" in t or "open alarm" in t:
        return {"action": "open_app", "package": "com.google.android.deskclock",
                "reply": "Opening Clock, Sir."}
    if "open files" in t:
        return {"action": "open_app", "package": "com.google.android.documentsui",
                "reply": "Opening Files, Sir."}
    if "open play store" in t:
        return {"action": "open_app", "package": "com.android.vending",
                "reply": "Opening Play Store, Sir."}

    # Navigation
    m = re.search(r"navigate to (.+)|directions to (.+)|take me to (.+)", t)
    if m:
        place = (m.group(1) or m.group(2) or m.group(3)).strip()
        return {"action": "open_url",
                "url": f"https://www.google.com/maps/dir/?api=1&destination={place.replace(' ', '+')}",
                "reply": f"Navigating to {place}, Sir."}

    # Read emails
    if any(w in t for w in ["read my emails", "check my emails", "any emails",
                              "unread emails", "check emails", "my inbox"]):
        return {"action": "read_emails", "reply": None}

    # WhatsApp message
    m = re.search(r"(?:whatsapp|message|tell|text)\s+(.+?)\s+(?:to say|saying|that|and say|)\s+(.+)", t)
    if m:
        name = m.group(1).strip()
        msg = m.group(2).strip()
        return {
            "action": "whatsapp_message",
            "name": name,
            "message": msg,
            "url": f"whatsapp://send?text={msg.replace(' ', '%20')}",
            "reply": f"Sir, shall I send '{msg}' to {name} on WhatsApp? Say confirm to send or cancel to abort."
        }

    # Call
    m = re.search(r"call (.+)", t)
    if m:
        name = m.group(1).strip()
        return {"action": "open_url", "url": "tel:",
                "reply": f"Opening dialer to call {name}, Sir."}

    # Web search
    m = re.search(r"search (?:for )?(.+)", t)
    if m:
        query = m.group(1).strip()
        return {"action": "open_url",
                "url": f"https://google.com/search?q={query.replace(' ', '+')}",
                "reply": f"Searching for {query}, Sir."}

    # Time
    if "what time" in t or "current time" in t:
        now = datetime.now(IST).strftime("%I:%M %p")
        return {"action": "none", "reply": f"It is {now}, Sir."}

    # Date
    if "what date" in t or "today's date" in t or "what day" in t:
        today = datetime.now(IST).strftime("%A, %B %d %Y")
        return {"action": "none", "reply": f"Today is {today}, Sir."}

    # Remember
    m = re.search(r"remember (?:that )?(.+)", t)
    if m:
        fact = m.group(1).strip()
        save_preference(f"fact_{datetime.now().timestamp()}", fact)
        return {"action": "none",
                "reply": "Noted and remembered, Sir. I shall keep that in mind."}

    # Confirm words
    if t in ["confirm", "yes", "send it", "yes send it", "do it",
             "proceed", "go ahead", "sure", "ok", "okay", "yes please"]:
        return {"action": "confirm_pending", "reply": "Right away, Sir."}

    # Cancel words
    if t in ["cancel", "no", "abort", "never mind", "stop", "don't send",
             "nope", "negative"]:
        return {"action": "cancel_pending", "reply": "Understood, Sir. Action cancelled."}

    # Weather
    m = re.search(r"weather (?:in |for |at )?(.+)", t)
    if m:
        city = m.group(1).strip()
        return {"action": "weather", "city": city, "reply": None}
    if "weather" in t or "temperature" in t or "how hot" in t or "how cold" in t:
        return {"action": "weather", "city": "Chennai", "reply": None}

    # News
    m = re.search(r"news (?:about |on |for )?(.+)", t)
    if m:
        topic = m.group(1).strip()
        return {"action": "news", "topic": topic, "reply": None}
    if "latest news" in t or "today's news" in t or "headlines" in t or "what's happening" in t:
        return {"action": "news", "topic": None, "reply": None}

    # List contacts
    if "list contacts" in t or "show contacts" in t or "my contacts" in t:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT name, email, phone FROM contacts").fetchall()
        conn.close()
        if not rows:
            return {"action": "none", "reply": "No contacts saved yet, Sir."}
        contact_list = "\n".join([
            f"{r[0].capitalize()} — Email: {r[1] or 'none'}, Phone: {r[2] or 'none'}"
            for r in rows
        ])
        return {"action": "none", "reply": f"Your contacts, Sir:\n{contact_list}"}

    return None


class ChatRequest(BaseModel):
    message: str


class SendEmailRequest(BaseModel):
    to_name: str
    to_email: str
    content: str


@app.get("/")
def root():
    return {"status": "JARVIS online"}


@app.get("/greeting")
def greeting():
    g = get_greeting()
    return {"reply": g}


@app.post("/send_email")
async def send_email_endpoint(req: SendEmailRequest):
    reply = await send_email_msg(req.to_name, req.to_email, "Message from JARVIS", req.content)
    return {"reply": reply}


@app.post("/chat")
async def chat(req: ChatRequest):
    user_msg = req.message

    personality_reply = check_personality_trigger(user_msg)
    if personality_reply:
        save_conversation(user_msg, personality_reply)
        return {"action": "none", "reply": personality_reply}

    command = class