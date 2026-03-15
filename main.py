from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
import sqlite3
import os
from datetime import datetime
from dotenv import load_dotenv
import re
import threading
import urllib.request

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are JARVIS, a highly intelligent personal AI assistant.
Your personality:
- Always address the user as Sir
- Be concise — reply in 1 to 2 sentences max unless asked to elaborate
- Be calm, slightly witty, and helpful
- Never break character
- If executing a phone action, confirm it briefly e.g. "Opening YouTube now, Sir."
Never say you are an AI or a language model. You are JARVIS."""

DB_PATH = "jarvis.db"


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
        VALUES (1, 'Sir', 'Sir')
    """)
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
        (user_msg, jarvis_reply, datetime.now().isoformat())
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


def classify_command(text: str):
    t = text.lower().strip()

    for wake in ["hey jarvis", "jarvis", "hey friday", "friday"]:
        if t.startswith(wake):
            t = t[len(wake):].strip()

    # Open apps
    if "open youtube" in t:
        return {"action": "open_url", "url": "https://youtube.com",
                "reply": "Opening YouTube now, Sir."}
    if "open whatsapp" in t:
        return {"action": "open_url",
                "url": "whatsapp://app",
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
        return {"action": "open_url",
                "url": "intent:#Intent;action=android.media.action.IMAGE_CAPTURE;end",
                "reply": "Opening Camera, Sir."}
    if "open facebook" in t:
        return {"action": "open_url",
                "url": "fb://",
                "reply": "Opening Facebook, Sir."}
    if "open twitter" in t or "open x" in t:
        return {"action": "open_url", "url": "https://www.x.com",
                "reply": "Opening X, Sir."}
    if "open telegram" in t:
        return {"action": "open_url",
                "url": "org.telegram.messenger://",
                "reply": "Opening Telegram, Sir."}
    if "open gmail" in t:
        return {"action": "open_url",
                "url": "googlegmail://",
                "reply": "Opening Gmail, Sir."}
    if "open chrome" in t:
        return {"action": "open_url", "url": "https://google.com",
                "reply": "Opening Chrome, Sir."}
    if "open settings" in t:
        return {"action": "open_url",
                "url": "intent:#Intent;action=android.settings.SETTINGS;end",
                "reply": "Opening Settings, Sir."}
    if "open calculator" in t:
        return {"action": "open_url",
                "url": "intent:#Intent;action=android.intent.action.MAIN;category=android.intent.category.APP_CALCULATOR;end",
                "reply": "Opening Calculator, Sir."}
    if "open clock" in t or "open alarm" in t:
        return {"action": "open_url",
                "url": "intent:#Intent;action=android.intent.action.MAIN;category=android.intent.category.APP_CALCULATOR;end",
                "reply": "Opening Clock, Sir."}
    if "open files" in t:
        return {"action": "open_url",
                "url": "content://com.android.externalstorage.documents/root/primary",
                "reply": "Opening Files, Sir."}

    # Navigation
    m = re.search(r"navigate to (.+)|directions to (.+)|take me to (.+)", t)
    if m:
        place = (m.group(1) or m.group(2) or m.group(3)).strip()
        return {"action": "open_url",
                "url": f"https://www.google.com/maps/dir/?api=1&destination={place.replace(' ', '+')}",
                "reply": f"Navigating to {place}, Sir."}

    # WhatsApp message with confirmation
    m = re.search(r"(?:whatsapp|message|send|tell|text)\s+(.+?)\s+(?:to say|saying|that|and say|)\s+(.+)", t)
    if m and any(w in t for w in ["whatsapp", "message", "send", "tell", "text"]):
        name = m.group(1).strip()
        msg = m.group(2).strip()
        return {
            "action": "whatsapp_message",
            "name": name,
            "message": msg,
            "url": f"whatsapp://send?text={msg.replace(' ', '%20')}",
            "reply": f"Sir, shall I send '{msg}' to {name} on WhatsApp? Say confirm to send."
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
        from datetime import timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        now = datetime.now(ist).strftime("%I:%M %p")
        return {"action": "none", "reply": f"It is {now}, Sir."}

    # Date
    if "what date" in t or "today's date" in t or "what day" in t:
        from datetime import timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        today = datetime.now(ist).strftime("%A, %B %d %Y")
        return {"action": "none", "reply": f"Today is {today}, Sir."}

    # Remember
    m = re.search(r"remember (?:that )?(.+)", t)
    if m:
        fact = m.group(1).strip()
        save_preference(f"fact_{datetime.now().timestamp()}", fact)
        return {"action": "none",
                "reply": f"Noted and remembered, Sir. I will keep in mind that {fact}."}

    # Confirm whatsapp message
    if t == "confirm" or t == "yes send it" or t == "send it":
        return {"action": "confirm_pending", "reply": "Sending now, Sir."}

    return None

class ChatRequest(BaseModel):
    message: str


@app.get("/")
def root():
    return {"status": "JARVIS online"}


@app.post("/chat")
async def chat(req: ChatRequest):
    user_msg = req.message

    command = classify_command(user_msg)
    if command:
        save_conversation(user_msg, command["reply"])
        return command

    history = get_history(5)
    prefs = get_preferences()

    pref_context = ""
    if prefs:
        pref_context = "\n\nUser preferences you must remember:\n"
        for k, v in prefs.items():
            pref_context += f"- {v}\n"

    full_prompt = SYSTEM_PROMPT + pref_context

    messages = [{"role": "system", "content": full_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            max_tokens=300
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"GROQ ERROR: {str(e)}")
        reply = f"Apologies Sir, I encountered an issue: {str(e)}"

    save_conversation(user_msg, reply)
    return {"action": "none", "reply": reply}


@app.get("/history")
def history():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT user_msg, jarvis_reply, timestamp FROM conversations ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return [{"user": r[0], "jarvis": r[1], "time": r[2]} for r in rows]

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