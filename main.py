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
import pytz
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

BLOCKED_CONTACT_NAMES = {
    "me", "my", "i", "you", "jarvis", "friday", "sir", "maam",
    "my name", "my number", "my email", "my contact", "my phone",
    "what", "who", "how", "when", "where", "why", "which",
    "everything", "anything", "something", "nothing", "someone",
    "tell", "know", "about", "remember", "forget", "recall",
    "do you", "can you", "will you", "please", "jarvis please",
    "hey", "hello", "hi", "okay", "ok", "yes", "no",
}

NON_CONTACT_KEYWORDS = [
    "what do you know", "do you know", "do you remember",
    "tell me about", "what is my", "who am i", "what am i",
    "my profile", "my details", "about me", "know about me",
    "remember about", "what have you", "what you know",
]

NON_EMAIL_KEYWORDS = [
    "what do you know", "do you know", "tell me about",
    "what is my", "who am i", "about me", "remember",
    "my profile", "my details", "what you know",
    "do you remember", "system status", "what's happening",
]

LOCATION_TIMEZONE_MAP = {
    "india": "Asia/Kolkata", "chennai": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata", "kolkata": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata", "usa": "America/New_York",
    "us": "America/New_York", "america": "America/New_York",
    "new york": "America/New_York", "los angeles": "America/Los_Angeles",
    "chicago": "America/Chicago", "california": "America/Los_Angeles",
    "uk": "Europe/London", "london": "Europe/London",
    "england": "Europe/London", "paris": "Europe/Paris",
    "france": "Europe/Paris", "germany": "Europe/Berlin",
    "berlin": "Europe/Berlin", "japan": "Asia/Tokyo",
    "tokyo": "Asia/Tokyo", "china": "Asia/Shanghai",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai",
    "australia": "Australia/Sydney", "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne", "dubai": "Asia/Dubai",
    "uae": "Asia/Dubai", "singapore": "Asia/Singapore",
    "malaysia": "Asia/Kuala_Lumpur", "kuala lumpur": "Asia/Kuala_Lumpur",
    "canada": "America/Toronto", "toronto": "America/Toronto",
    "pakistan": "Asia/Karachi", "karachi": "Asia/Karachi",
    "sri lanka": "Asia/Colombo", "colombo": "Asia/Colombo",
    "nepal": "Asia/Kathmandu", "bangladesh": "Asia/Dhaka",
    "dhaka": "Asia/Dhaka", "russia": "Europe/Moscow",
    "moscow": "Europe/Moscow", "brazil": "America/Sao_Paulo",
    "south africa": "Africa/Johannesburg", "egypt": "Africa/Cairo",
    "saudi arabia": "Asia/Riyadh", "riyadh": "Asia/Riyadh",
    "new zealand": "Pacific/Auckland",
}


def is_non_contact_message(text: str) -> bool:
    t = text.lower().strip()
    for kw in NON_CONTACT_KEYWORDS:
        if kw in t:
            return True
    return False


def is_non_email_message(text: str) -> bool:
    t = text.lower().strip()
    for kw in NON_EMAIL_KEYWORDS:
        if kw in t:
            return True
    return False


def is_valid_contact_name(name: str) -> bool:
    if not name:
        return False
    n = name.lower().strip()
    if n in BLOCKED_CONTACT_NAMES:
        return False
    if len(n) <= 1:
        return False
    if not any(c.isalpha() for c in n):
        return False
    return True


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
        ("name", "JARVIS"), ("alternate_name", "Friday"),
        ("tone", "formal"), ("humor", "light"),
        ("address_user", "Sir"), ("loyalty", "high"), ("language", "en"),
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


async def get_time_for_location(location: str = None):
    try:
        if not location:
            now = datetime.now(pytz.timezone("Asia/Kolkata"))
            return f"It is {now.strftime('%I:%M %p')} IST, Sir."
        loc_lower = location.lower().strip()
        if loc_lower in LOCATION_TIMEZONE_MAP:
            tz = pytz.timezone(LOCATION_TIMEZONE_MAP[loc_lower])
            now = datetime.now(tz)
            return f"It is {now.strftime('%I:%M %p')} in {location.capitalize()}, Sir."
        prompt = f"""What is the pytz timezone string for "{location}"?
Reply with ONLY the timezone string, nothing else.
"{location}" ->"""
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20
        )
        tz_str = response.choices[0].message.content.strip().strip('"').strip("'").strip()
        tz = pytz.timezone(tz_str)
        now = datetime.now(tz)
        return f"It is {now.strftime('%I:%M %p')} in {location.capitalize()}, Sir."
    except Exception as e:
        print(f"TIME ERROR: {str(e)}")
        now = datetime.now(pytz.timezone("Asia/Kolkata"))
        return f"It is {now.strftime('%I:%M %p')} IST, Sir."


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

Rules:
- is_email is true ONLY if the user clearly wants to SEND an email to a specific person
- The message must contain both a recipient name AND content to send
- Questions, greetings, status checks, memory questions, reminders are NEVER emails

Examples:
"send email to john saying hello" -> {{"is_email": true, "to_name": "john", "content": "hello"}}
"email rahul about the meeting tomorrow" -> {{"is_email": true, "to_name": "rahul", "content": "about the meeting tomorrow"}}
"what do you know about me" -> {{"is_email": false, "to_name": null, "content": null}}
"what time is it" -> {{"is_email": false, "to_name": null, "content": null}}
"remind me to drink water at 6pm" -> {{"is_email": false, "to_name": null, "content": null}}
"show my reminders" -> {{"is_email": false, "to_name": null, "content": null}}
"lockdown" -> {{"is_email": false, "to_name": null, "content": null}}
"system status" -> {{"is_email": false, "to_name": null, "content": null}}
"list contacts" -> {{"is_email": false, "to_name": null, "content": null}}"""

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

Rules:
- intent is "save" ONLY if user explicitly wants to add/save a new contact with a real person's name
- intent is "update" ONLY if user wants to change existing contact details
- intent is "delete" ONLY if user wants to remove a contact
- Questions, reminders, memory requests = "none"
- "me", "my", "i", "you", "jarvis" are NEVER valid contact names

Examples:
"save john number 9876543210" -> {{"intent": "save", "name": "john", "email": null, "phone": "9876543210"}}
"add contact rahul email rahul@gmail.com" -> {{"intent": "save", "name": "rahul", "email": "rahul@gmail.com", "phone": null}}
"update john phone to 9999999999" -> {{"intent": "update", "name": "john", "email": null, "phone": "9999999999"}}
"delete contact rahul" -> {{"intent": "delete", "name": "rahul", "email": null, "phone": null}}
"what do you know about me" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"remind me to call john at 6pm" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"what time is it" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"lockdown" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"show my reminders" -> {{"intent": "none", "name": null, "email": null, "phone": null}}
"list contacts" -> {{"intent": "none", "name": null, "email": null, "phone": null}}"""

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

    if any(w in t for w in ["wake word on", "enable wake word"]):
        return {"action": "wake_word_on", "reply": "Wake word mode activated, Sir."}
    if any(w in t for w in ["wake word off", "disable wake word"]):
        return {"action": "wake_word_off", "reply": "Wake word mode deactivated, Sir."}

    if t in ["lockdown", "lock yourself", "jarvis lock", "security lock"]:
        set_state("locked", "true")
        return {"action": "none", "reply": "JARVIS locked, Sir. Speak the unlock code to resume."}

    if t in ["unlock jarvis", "jarvis unlock", "access granted", "override alpha"]:
        set_state("locked", "false")
        return {"action": "none", "reply": "JARVIS unlocked, Sir. All systems restored."}

    if get_state("locked") == "true":
        return {"action": "none", "reply": "JARVIS is locked, Sir. Speak the unlock code to continue."}

    if t in ["stealth mode", "silent mode", "go silent", "no voice"]:
        set_state("stealth", "true")
        return {"action": "stealth_on", "reply": "Stealth mode activated, Sir. I will respond in text only."}

    if t in ["stealth off", "voice on", "speak again", "disable stealth"]:
        set_state("stealth", "false")
        return {"action": "stealth_off", "reply": "Voice restored, Sir. Back to normal."}

    if t in ["override 7749", "skip confirmations", "no confirmations", "fast mode"]:
        set_state("skip_confirm", "true")
        return {"action": "none", "reply": "Override active, Sir. All confirmations bypassed."}

    if t in ["confirmations on", "normal mode", "safe mode", "disable override"]:
        set_state("skip_confirm", "false")
        return {"action": "none", "reply": "Confirmations restored, Sir. Safety protocols back online."}

    if t in ["alpha mode", "professional mode", "formal mode"]:
        set_state("mode", "alpha")
        return {"action": "none", "reply": "Alpha mode engaged, Sir."}

    if t in ["chill mode", "casual mode", "relax mode"]:
        set_state("mode", "chill")
        return {"action": "none", "reply": "Switching to casual mode, Sir."}

    if t in ["default mode", "reset mode"]:
        set_state("mode", "normal")
        return {"action": "none", "reply": "Normal mode restored, Sir."}

    if t in ["panic mode", "clear history", "wipe memory", "delete history"]:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM preferences")
        conn.commit()
        conn.close()
        return {"action": "none", "reply": "All memory wiped, Sir. Clean slate."}

    if t in ["system status", "status report", "jarvis status"]:
        mode = get_state("mode")
        locked = get_state("locked")
        stealth = get_state("stealth")
        skip = get_state("skip_confirm")
        now = datetime.now(IST).strftime("%I:%M %p")
        return {"action": "none",
                "reply": f"System status, Sir:\nMode: {mode}\nLocked: {locked}\nStealth: {stealth}\nConfirmations: {'off' if skip == 'true' else 'on'}\nTime: {now}"}

    if "open youtube" in t:
        return {"action": "open_url", "url": "https://youtube.com", "reply": "Opening YouTube now, Sir."}
    if "open whatsapp" in t:
        return {"action": "open_app", "package": "com.whatsapp", "reply": "Opening WhatsApp, Sir."}
    if "open instagram" in t:
        return {"action": "open_url", "url": "https://www.instagram.com", "reply": "Opening Instagram, Sir."}
    if "open spotify" in t:
        return {"action": "open_url", "url": "spotify://home", "reply": "Opening Spotify, Sir."}
    if "open google" in t:
        return {"action": "open_url", "url": "https://google.com", "reply": "Opening Google, Sir."}
    if "open maps" in t or "open google maps" in t:
        return {"action": "open_url", "url": "https://maps.google.com", "reply": "Opening Maps, Sir."}
    if "open camera" in t:
        return {"action": "open_app", "package": "com.nothing.camera", "reply": "Opening Camera, Sir."}
    if "open facebook" in t:
        return {"action": "open_url", "url": "https://www.facebook.com", "reply": "Opening Facebook, Sir."}
    if "open twitter" in t or "open x" in t:
        return {"action": "open_url", "url": "https://www.x.com", "reply": "Opening X, Sir."}
    if "open telegram" in t:
        return {"action": "open_app", "package": "org.telegram.messenger", "reply": "Opening Telegram, Sir."}
    if "open gmail" in t:
        return {"action": "open_app", "package": "com.google.android.gm", "reply": "Opening Gmail, Sir."}
    if "open chrome" in t:
        return {"action": "open_url", "url": "https://google.com", "reply": "Opening Chrome, Sir."}
    if "open settings" in t:
        return {"action": "open_app", "package": "com.android.settings", "reply": "Opening Settings, Sir."}
    if "open calculator" in t:
        return {"action": "open_app", "package": "com.google.android.calculator", "reply": "Opening Calculator, Sir."}
    if "open clock" in t or "open alarm" in t:
        return {"action": "open_app", "package": "com.google.android.deskclock", "reply": "Opening Clock, Sir."}
    if "open files" in t:
        return {"action": "open_app", "package": "com.google.android.documentsui", "reply": "Opening Files, Sir."}
    if "open play store" in t:
        return {"action": "open_app", "package": "com.android.vending", "reply": "Opening Play Store, Sir."}

    m = re.search(r"navigate to (.+)|directions to (.+)|take me to (.+)", t)
    if m:
        place = (m.group(1) or m.group(2) or m.group(3)).strip()
        return {"action": "open_url",
                "url": f"https://www.google.com/maps/dir/?api=1&destination={place.replace(' ', '+')}",
                "reply": f"Navigating to {place}, Sir."}

    if any(w in t for w in ["read my emails", "check my emails", "any emails",
                              "unread emails", "check emails", "my inbox"]):
        return {"action": "read_emails", "reply": None}

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

    m = re.search(r"call (.+)", t)
    if m:
        name = m.group(1).strip()
        return {"action": "open_url", "url": "tel:", "reply": f"Opening dialer to call {name}, Sir."}

    m = re.search(r"search (?:for )?(.+)", t)
    if m:
        query = m.group(1).strip()
        return {"action": "open_url",
                "url": f"https://google.com/search?q={query.replace(' ', '+')}",
                "reply": f"Searching for {query}, Sir."}

    if any(w in t for w in ["what time", "current time", "time now",
                             "what's the time", "tell me the time", "time is it"]):
        return {"action": "get_time", "location": None, "reply": None}

    m = re.search(r"(?:what(?:'s| is)(?: the)? )?time (?:in|at|of|for) (.+?)(?:\?|$)", t)
    if m:
        location = m.group(1).strip()
        return {"action": "get_time", "location": location, "reply": None}

    m = re.search(r"(?:what(?:'s| is)(?: the)? )?(.+?) time(?:\?|$)", t)
    if m:
        location = m.group(1).strip()
        skip_words = ["current", "local", "exact", "correct", "real",
                      "right", "the", "a", "any", "some"]
        if location not in skip_words and len(location) > 2:
            return {"action": "get_time", "location": location, "reply": None}
        return {"action": "get_time", "location": None, "reply": None}

    if "what date" in t or "today's date" in t or "what day" in t:
        today = datetime.now(IST).strftime("%A, %B %d %Y")
        return {"action": "none", "reply": f"Today is {today}, Sir."}

    m = re.search(r"remember (?:that )?(.+)", t)
    if m:
        fact = m.group(1).strip()
        save_preference(f"fact_{datetime.now().timestamp()}", fact)
        return {"action": "none", "reply": "Noted and remembered, Sir. I shall keep that in mind."}

    if t in ["confirm", "yes", "send it", "yes send it", "do it",
             "proceed", "go ahead", "sure", "ok", "okay", "yes please"]:
        return {"action": "confirm_pending", "reply": "Right away, Sir."}

    if t in ["cancel", "no", "abort", "never mind", "stop",
             "don't send", "nope", "negative"]:
        return {"action": "cancel_pending", "reply": "Understood, Sir. Action cancelled."}

    m = re.search(r"weather (?:in |for |at )?(.+)", t)
    if m:
        city = m.group(1).strip()
        return {"action": "weather", "city": city, "reply": None}
    if "weather" in t or "temperature" in t or "how hot" in t or "how cold" in t:
        return {"action": "weather", "city": "Chennai", "reply": None}

    m = re.search(r"news (?:about |on |for )?(.+)", t)
    if m:
        topic = m.group(1).strip()
        return {"action": "news", "topic": topic, "reply": None}
    if "latest news" in t or "today's news" in t or "headlines" in t or "what's happening" in t:
        return {"action": "news", "topic": None, "reply": None}

    # Reminders
    m = re.search(r"remind me (?:to |about )?(.+?) (?:at|in) (.+)", t)
    if m:
        task = m.group(1).strip()
        time_str = m.group(2).strip()
        return {"action": "set_reminder", "task": task, "time": time_str, "reply": None}

    if any(w in t for w in ["show reminders", "my reminders",
                             "list reminders", "what are my reminders",
                             "pending reminders"]):
        return {"action": "list_reminders", "reply": None}

    m = re.search(r"cancel reminder (?:for |about )?(.+)", t)
    if m:
        task = m.group(1).strip()
        return {"action": "cancel_reminder", "task": task, "reply": None}

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

    command = classify_command(user_msg)
    if command:
        if command["action"] == "weather":
            city = command.get("city", "Chennai")
            reply = await get_weather(city)
            save_conversation(user_msg, reply)
            return {"action": "none", "reply": reply}

        if command["action"] == "news":
            topic = command.get("topic", None)
            reply = await get_news(topic)
            save_conversation(user_msg, reply)
            return {"action": "none", "reply": reply}

        if command["action"] == "read_emails":
            reply = await read_emails()
            save_conversation(user_msg, reply)
            return {"action": "none", "reply": reply}

        if command["action"] == "get_time":
            location = command.get("location")
            reply = await get_time_for_location(location)
            save_conversation(user_msg, reply)
            return {"action": "none", "reply": reply}

        if command["action"] == "set_reminder":
            task = command.get("task", "")
            time_str = command.get("time", "")
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO reminders (task, scheduled_time, status) VALUES (?, ?, ?)",
                (task, time_str, "pending")
            )
            conn.commit()
            conn.close()
            reply = f"Reminder set, Sir. I will remind you to {task} at {time_str}."
            save_conversation(user_msg, reply)
            return {"action": "set_reminder", "task": task, "time": time_str, "reply": reply}

        if command["action"] == "list_reminders":
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute(
                "SELECT task, scheduled_time FROM reminders WHERE status='pending'"
            ).fetchall()
            conn.close()
            if not rows:
                reply = "No pending reminders, Sir."
            else:
                reminder_list = "\n".join([f"- {r[0]} at {r[1]}" for r in rows])
                reply = f"Your pending reminders, Sir:\n{reminder_list}"
            save_conversation(user_msg, reply)
            return {"action": "none", "reply": reply}

        if command["action"] == "cancel_reminder":
            task = command.get("task", "")
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "UPDATE reminders SET status='cancelled' WHERE task LIKE ? AND status='pending'",
                (f"%{task}%",)
            )
            conn.commit()
            conn.close()
            reply = "Reminder cancelled, Sir."
            save_conversation(user_msg, reply)
            return {"action": "none", "reply": reply}

        if command["action"] == "whatsapp_message" and get_state("skip_confirm") == "true":
            reply = "Sending WhatsApp message now, Sir."
            save_conversation(user_msg, reply)
            return {
                "action": "whatsapp_send_direct",
                "url": command["url"],
                "reply": reply
            }

        save_conversation(user_msg, command["reply"])
        return command

    skip_groq_intents = is_non_email_message(user_msg) or is_non_contact_message(user_msg)

    if not skip_groq_intents:
        email_intent = await detect_email_intent(user_msg)
        if email_intent.get("is_email") and email_intent.get("to_name"):
            to_name = email_intent["to_name"].strip()
            if is_valid_contact_name(to_name):
                content = str(email_intent.get("content") or user_msg).strip().strip('"').strip("'").strip()
                contact = get_contact(to_name)
                if not contact or not contact[1]:
                    reply = f"I don't have an email saved for {to_name}, Sir. Say 'add contact {to_name}' to save their email first."
                    save_conversation(user_msg, reply)
                    return {"action": "none", "reply": reply}
                to_email = contact[1]
                if get_state("skip_confirm") == "true":
                    reply = await send_email_msg(to_name, to_email, "Message from JARVIS", content)
                    save_conversation(user_msg, reply)
                    return {"action": "none", "reply": reply}
                reply = f"Sir, I will send the following email to {to_name}:\n\n\"{content}\"\n\nSay confirm or proceed to send, or cancel to abort."
                save_conversation(user_msg, reply)
                return {
                    "action": "email_pending",
                    "to_name": to_name,
                    "to_email": to_email,
                    "content": content,
                    "reply": reply
                }

        contact_intent = await detect_contact_intent(user_msg)
        if contact_intent.get("intent") != "none" and contact_intent.get("name"):
            name = contact_intent["name"].strip()
            if is_valid_contact_name(name):
                email = contact_intent.get("email")
                phone = contact_intent.get("phone")
                intent = contact_intent["intent"]

                if intent == "save":
                    if not email and not phone:
                        reply = f"I need at least an email or phone to save {name.capitalize()}, Sir."
                        save_conversation(user_msg, reply)
                        return {"action": "none", "reply": reply}
                    existing = get_contact(name)
                    final_email = email or (existing[1] if existing else None)
                    final_phone = phone or (existing[2] if existing else None)
                    save_contact(name, email=final_email, phone=final_phone)
                    parts = []
                    if email: parts.append(f"email {email}")
                    if phone: parts.append(f"phone {phone}")
                    reply = f"Contact {name.capitalize()} saved with {' and '.join(parts)}, Sir."
                    save_conversation(user_msg, reply)
                    return {"action": "none", "reply": reply}

                elif intent == "update":
                    existing = get_contact(name)
                    if not existing:
                        reply = f"I don't have a contact named {name.capitalize()}, Sir."
                        save_conversation(user_msg, reply)
                        return {"action": "none", "reply": reply}
                    conn = sqlite3.connect(DB_PATH)
                    if email:
                        conn.execute("UPDATE contacts SET email=? WHERE name=?", (email, name.lower()))
                    if phone:
                        conn.execute("UPDATE contacts SET phone=? WHERE name=?", (phone, name.lower()))
                    conn.commit()
                    conn.close()
                    parts = []
                    if email: parts.append(f"email to {email}")
                    if phone: parts.append(f"phone to {phone}")
                    reply = f"Updated {name.capitalize()}'s {' and '.join(parts)}, Sir."
                    save_conversation(user_msg, reply)
                    return {"action": "none", "reply": reply}

                elif intent == "delete":
                    conn = sqlite3.connect(DB_PATH)
                    conn.execute("DELETE FROM contacts WHERE name=?", (name.lower(),))
                    conn.commit()
                    conn.close()
                    reply = f"Contact {name.capitalize()} deleted, Sir."
                    save_conversation(user_msg, reply)
                    return {"action": "none", "reply": reply}

    emotion = detect_emotion(user_msg)
    emotion_context = ""
    if emotion == "stressed":
        emotion_context = "\nThe user seems stressed. Be extra calm, reassuring, and supportive."
    elif emotion == "happy":
        emotion_context = "\nThe user seems happy or accomplished. Acknowledge it briefly with a congratulation."
    elif emotion == "joking":
        emotion_context = "\nThe user is in a playful mood. Respond with light wit while staying in character."

    history = get_history(5)
    prefs = get_preferences()

    pref_context = ""
    if prefs:
        pref_context = "\n\nThings you know and remember about Sir:\n"
        for k, v in prefs.items():
            pref_context += f"- {v}\n"

    mode = get_state("mode")
    mode_context = ""
    if mode == "alpha":
        mode_context = "\nYou are in ALPHA MODE. Be extremely formal, precise, and professional. No humor. Maximum efficiency."
    elif mode == "chill":
        mode_context = "\nYou are in CHILL MODE. Be casual, friendly, and relaxed. Still call user Sir but be more laid back."

    full_prompt = SYSTEM_PROMPT + pref_context + emotion_context + mode_context

    messages = [{"role": "system", "content": full_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            max_tokens=300
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"GROQ ERROR: {str(e)}")
        reply = "Apologies Sir, I seem to be experiencing a momentary difficulty. Please try again."

    save_conversation(user_msg, reply)

    stealth = get_state("stealth")
    if stealth == "true":
        return {"action": "none", "reply": reply, "stealth": True}

    return {"action": "none", "reply": reply}


@app.get("/history")
def history():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT user_msg, jarvis_reply, timestamp FROM conversations ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return [{"user": r[0], "jarvis": r[1], "time": r[2]} for r in rows]


@app.get("/personality")
def get_personality():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT key, value FROM assistant_personality").fetchall()
    conn.close()
    return {k: v for k, v in rows}


@app.get("/triggers")
def get_triggers():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT trigger, response FROM personality_responses").fetchall()
    conn.close()
    return [{"trigger": r[0], "response": r[1]} for r in rows]


def keep_alive():
    def ping():
        while True:
            try:
                urllib.request.urlopen("https://jarvis-backend-q3ml.onrender.com")
            except:
                pass
            import time
            time.sleep(840)
    t = threading.Thread(target=ping, daemon=True)
    t.start()


keep_alive()
init_db()