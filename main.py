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

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

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
    # Default personality config
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
    # Default personality trigger responses
    triggers = [
        ("you there", "At your service, Sir."),
        ("you up", "Always operational, Sir."),
        ("you awake", "Never truly sleep, Sir. Always watching."),
        ("good morning", None),  # handled dynamically
        ("good night", "Good night, Sir. Rest well. I'll keep watch."),
        ("how are you", "Running at full capacity, Sir. Thank you for asking."),
        ("thank you", "Always a pleasure, Sir."),
        ("thanks", "Of course, Sir. That is what I am here for."),
        ("you're the best", "I do try to maintain high standards, Sir."),
        ("i love you", "The feeling is entirely mutual, Sir. In a strictly professional sense, of course."),
        ("who are you", "I am JARVIS — Just A Rather Very Intelligent System. At your service, Sir."),
        ("what can you do", "I can open apps, search the web, answer questions, remember your preferences, set reminders, and have a proper conversation, Sir. Shall I demonstrate?"),
        ("hello", "Hello, Sir. How may I assist you today?"),
        ("hi", "Good to hear from you, Sir. What do you need?"),
        ("hey", "Yes Sir, I am here."),
        ("wake up", "Already awake, Sir. What do you need?"),
        ("stop", "Understood, Sir. Standing by."),
        ("nevermind", "Of course, Sir. Whenever you are ready."),
        ("shut up", "My apologies, Sir. I shall be silent."),
        ("be quiet", "Understood, Sir. Going quiet."),
    ]
    for trigger, response in triggers:
        if response:
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


def check_personality_trigger(text: str):
    t = text.lower().strip()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT trigger, response FROM personality_responses"
    ).fetchall()
    conn.close()
    for trigger, response in rows:
        if trigger in t or t == trigger:
            return response
    return None


def detect_emotion(text: str):
    t = text.lower()
    stressed = ["stressed", "tired", "exhausted", "overwhelmed", "frustrated",
                "anxious", "worried", "panic", "help me", "i cant", "too much"]
    happy = ["finished", "completed", "done", "achieved", "passed", "got it",
             "finally", "success", "won", "celebrated"]
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


def classify_command(text: str):
    t = text.lower().strip()

    for wake in ["hey jarvis", "jarvis", "hey friday", "friday"]:
        if t.startswith(wake):
            t = t[len(wake):].strip()

    if not t:
        return {"action": "none", "reply": get_greeting()}

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
        return {"action": "open_url",
                "url": "fb://",
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
        return {"action": "open_url",
                "url": "content://com.android.externalstorage.documents/root/primary",
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

    # WhatsApp message
    m = re.search(r"(?:whatsapp|message|send|tell|text)\s+(.+?)\s+(?:to say|saying|that|and say|)\s+(.+)", t)
    if m and any(w in t for w in ["whatsapp", "message", "send", "tell", "text"]):
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
                "reply": f"Noted and remembered, Sir. I shall keep that in mind."}

    # Confirm
    if t in ["confirm", "yes", "send it", "yes send it", "do it"]:
        return {"action": "confirm_pending", "reply": "Right away, Sir."}

    # Cancel
    if t in ["cancel", "no", "abort", "never mind", "stop"]:
        return {"action": "cancel_pending", "reply": "Understood, Sir. Action cancelled."}

    return None


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def root():
    return {"status": "JARVIS online"}


@app.get("/greeting")
def greeting():
    g = get_greeting()
    return {"reply": g}


@app.post("/chat")
async def chat(req: ChatRequest):
    user_msg = req.message

    # Check personality triggers first
    personality_reply = check_personality_trigger(user_msg)
    if personality_reply:
        save_conversation(user_msg, personality_reply)
        return {"action": "none", "reply": personality_reply}

    # Check local commands
    command = classify_command(user_msg)
    if command:
        save_conversation(user_msg, command["reply"])
        return command

    # Detect emotion for tone adjustment
    emotion = detect_emotion(user_msg)
    emotion_context = ""
    if emotion == "stressed":
        emotion_context = "\nThe user seems stressed. Be extra calm, reassuring, and supportive."
    elif emotion == "happy":
        emotion_context = "\nThe user seems happy or accomplished. Acknowledge it briefly with a congratulation."
    elif emotion == "joking":
        emotion_context = "\nThe user is in a playful mood. Respond with light wit while staying in character."

    # Build context
    history = get_history(5)
    prefs = get_preferences()

    pref_context = ""
    if prefs:
        pref_context = "\n\nThings you know and remember about Sir:\n"
        for k, v in prefs.items():
            pref_context += f"- {v}\n"

    full_prompt = SYSTEM_PROMPT + pref_context + emotion_context

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
        reply = "Apologies Sir, I seem to be experiencing a momentary difficulty. Please try again."

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