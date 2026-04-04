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
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
IST = timezone(timedelta(hours=5, minutes=30))
DB_PATH = "jarvis.db"

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

Signature phrases:
"At your service, Sir." "Allow me to handle that, Sir." "As you wish, Sir."
"Consider it done, Sir." "Shall I proceed, Sir?" "Noted, Sir."
"Excellent choice, Sir." "Right away, Sir."
"""

LOCATION_TIMEZONE_MAP = {
    "india": "Asia/Kolkata", "chennai": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "bangalore": "Asia/Kolkata", "kolkata": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata", "usa": "America/New_York", "us": "America/New_York",
    "america": "America/New_York", "new york": "America/New_York",
    "los angeles": "America/Los_Angeles", "chicago": "America/Chicago",
    "california": "America/Los_Angeles", "uk": "Europe/London", "london": "Europe/London",
    "paris": "Europe/Paris", "france": "Europe/Paris", "germany": "Europe/Berlin",
    "japan": "Asia/Tokyo", "tokyo": "Asia/Tokyo", "china": "Asia/Shanghai",
    "australia": "Australia/Sydney", "sydney": "Australia/Sydney",
    "dubai": "Asia/Dubai", "uae": "Asia/Dubai", "singapore": "Asia/Singapore",
    "malaysia": "Asia/Kuala_Lumpur", "canada": "America/Toronto",
    "pakistan": "Asia/Karachi", "sri lanka": "Asia/Colombo",
    "nepal": "Asia/Kathmandu", "bangladesh": "Asia/Dhaka",
    "russia": "Europe/Moscow", "brazil": "America/Sao_Paulo",
    "south africa": "Africa/Johannesburg", "egypt": "Africa/Cairo",
    "saudi arabia": "Asia/Riyadh", "new zealand": "Pacific/Auckland",
    "vietnam": "Asia/Ho_Chi_Minh", "thailand": "Asia/Bangkok",
    "korea": "Asia/Seoul", "indonesia": "Asia/Jakarta",
    "turkey": "Europe/Istanbul", "iran": "Asia/Tehran",
    "israel": "Asia/Jerusalem", "nigeria": "Africa/Lagos",
    "kenya": "Africa/Nairobi", "ghana": "Africa/Accra",
}

# App package map
APP_PACKAGES = {
    "youtube": ("open_url", "https://youtube.com"),
    "whatsapp": ("open_app", "com.whatsapp"),
    "instagram": ("open_url", "https://www.instagram.com"),
    "spotify": ("open_url", "spotify://home"),
    "google": ("open_url", "https://google.com"),
    "maps": ("open_url", "https://maps.google.com"),
    "google maps": ("open_url", "https://maps.google.com"),
    "camera": ("open_app", "com.nothing.camera"),
    "facebook": ("open_url", "https://www.facebook.com"),
    "twitter": ("open_url", "https://www.x.com"),
    "x": ("open_url", "https://www.x.com"),
    "telegram": ("open_app", "org.telegram.messenger"),
    "gmail": ("open_app", "com.google.android.gm"),
    "chrome": ("open_url", "https://google.com"),
    "settings": ("open_app", "com.android.settings"),
    "calculator": ("open_app", "com.google.android.calculator"),
    "clock": ("open_app", "com.google.android.deskclock"),
    "alarm": ("open_app", "com.google.android.deskclock"),
    "files": ("open_app", "com.google.android.documentsui"),
    "play store": ("open_app", "com.android.vending"),
}

# These are handled locally — never go through AI intent
SECRET_CODES = {
    "lockdown", "lock yourself", "jarvis lock", "security lock",
    "unlock jarvis", "jarvis unlock", "access granted", "override alpha",
    "stealth mode", "silent mode", "go silent", "no voice",
    "stealth off", "voice on", "speak again", "disable stealth",
    "override 7749", "skip confirmations", "no confirmations", "fast mode",
    "confirmations on", "safe mode", "disable override",
    "alpha mode", "professional mode", "formal mode",
    "chill mode", "casual mode", "relax mode",
    "default mode", "reset mode",
    "panic mode", "clear history", "wipe memory", "delete history",
    "system status", "status report", "jarvis status",
}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_msg TEXT, jarvis_reply TEXT, timestamp TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT, scheduled_time TEXT, status TEXT DEFAULT 'pending')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS user_profile (
        id INTEGER PRIMARY KEY, name TEXT DEFAULT 'Sir', title TEXT DEFAULT 'Sir')""")
    conn.execute("INSERT OR IGNORE INTO user_profile (id,name,title) VALUES (1,'Faizal','Sir')")
    conn.execute("""CREATE TABLE IF NOT EXISTS personality_responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, trigger TEXT UNIQUE, response TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS assistant_personality (key TEXT PRIMARY KEY, value TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE, email TEXT, phone TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS jarvis_state (key TEXT PRIMARY KEY, value TEXT)""")

    for k, v in [("mode","normal"),("locked","false"),("stealth","false"),("skip_confirm","false")]:
        conn.execute("INSERT OR IGNORE INTO jarvis_state (key,value) VALUES (?,?)", (k,v))

    for k, v in [("name","JARVIS"),("alternate_name","Friday"),("tone","formal"),
                 ("humor","light"),("address_user","Sir"),("loyalty","high"),("language","en")]:
        conn.execute("INSERT OR IGNORE INTO assistant_personality (key,value) VALUES (?,?)", (k,v))

    triggers = [
        ("you there","At your service, Sir."),
        ("you up","Always operational, Sir."),
        ("you awake","Never truly sleep, Sir. Always watching."),
        ("good night","Good night, Sir. Rest well. I'll keep watch."),
        ("how are you","Running at full capacity, Sir. Thank you for asking."),
        ("thank you","Always a pleasure, Sir."),
        ("thanks","Of course, Sir. That is what I am here for."),
        ("you're the best","I do try to maintain high standards, Sir."),
        ("i love you","The feeling is entirely mutual, Sir. In a strictly professional sense, of course."),
        ("who are you","I am JARVIS — Just A Rather Very Intelligent System. At your service, Sir."),
        ("what can you do","I can open apps, answer questions, set reminders, send emails, check weather, news, time, manage contacts, and have a proper conversation, Sir."),
        ("hello","Hello, Sir. How may I assist you today?"),
        ("hi","Good to hear from you, Sir. What do you need?"),
        ("hey","Yes Sir, I am here."),
        ("wake up","Already awake, Sir. What do you need?"),
        ("nevermind","Of course, Sir. Whenever you are ready."),
        ("shut up","My apologies, Sir. I shall be silent."),
        ("be quiet","Understood, Sir. Going quiet."),
    ]
    for trigger, response in triggers:
        conn.execute("INSERT OR IGNORE INTO personality_responses (trigger,response) VALUES (?,?)", (trigger,response))
    conn.commit()
    conn.close()


def get_history(n=6):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT user_msg, jarvis_reply FROM conversations ORDER BY id DESC LIMIT ?", (n,)
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
        "INSERT INTO conversations (user_msg,jarvis_reply,timestamp) VALUES (?,?,?)",
        (user_msg, jarvis_reply, datetime.now(IST).isoformat())
    )
    conn.commit()
    conn.close()


def save_preference(key, value):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO preferences (key,value) VALUES (?,?)", (key,value))
    conn.commit()
    conn.close()


def get_preferences():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT key,value FROM preferences").fetchall()
    conn.close()
    return {k: v for k, v in rows}


def get_state(key):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM jarvis_state WHERE key=?", (key,)).fetchone()
    conn.close()
    return row[0] if row else None


def set_state(key, value):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO jarvis_state (key,value) VALUES (?,?)", (key,value))
    conn.commit()
    conn.close()


def save_contact(name, email=None, phone=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO contacts (name,email,phone) VALUES (?,?,?)",
                 (name.lower(), email, phone))
    conn.commit()
    conn.close()


def get_contact(name):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT name,email,phone FROM contacts WHERE name=?",
                       (name.lower(),)).fetchone()
    conn.close()
    return row


def check_personality_trigger(text):
    t = text.lower().strip()
    if t in SECRET_CODES:
        return None
    for wake in ["hey jarvis","jarvis","hey friday","friday"]:
        if t.startswith(wake):
            t = t[len(wake):].strip()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT trigger,response FROM personality_responses").fetchall()
    conn.close()
    for trigger, response in rows:
        if t == trigger or t.startswith(trigger+" ") or t.startswith(trigger+","):
            return response
    return None


def detect_emotion(text):
    t = text.lower()
    if any(w in t for w in ["stressed","stress","tired","exhausted","overwhelmed",
                              "frustrated","anxious","worried","i cant","too much"]):
        return "stressed"
    if any(w in t for w in ["finished","completed","achieved","passed","success",
                              "won","i finished","completed my"]):
        return "happy"
    if any(w in t for w in ["haha","lol","funny","joke","kidding","jk","lmao"]):
        return "joking"
    return "neutral"


def get_greeting():
    hour = datetime.now(IST).hour
    if 5 <= hour < 12:   return "Good morning, Sir. Ready to take on the day?"
    elif 12 <= hour < 17: return "Good afternoon, Sir. How may I assist you?"
    elif 17 <= hour < 21: return "Good evening, Sir. What can I do for you?"
    else:                  return "Working late, Sir? I am here whenever you need me."


async def get_time_for_location(location=None):
    try:
        if not location:
            now = datetime.now(pytz.timezone("Asia/Kolkata"))
            return f"It is {now.strftime('%I:%M %p')} IST, Sir."
        loc = location.lower().strip()
        if loc in LOCATION_TIMEZONE_MAP:
            tz = pytz.timezone(LOCATION_TIMEZONE_MAP[loc])
            now = datetime.now(tz)
            return f"It is {now.strftime('%I:%M %p')} in {location.capitalize()}, Sir."
        # Groq fallback for unknown locations
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role":"user","content":f'What is the pytz timezone string for "{location}"? Reply with ONLY the string.'}],
            max_tokens=20
        )
        tz_str = response.choices[0].message.content.strip().strip('"').strip("'")
        now = datetime.now(pytz.timezone(tz_str))
        return f"It is {now.strftime('%I:%M %p')} in {location.capitalize()}, Sir."
    except Exception as e:
        now = datetime.now(pytz.timezone("Asia/Kolkata"))
        return f"It is {now.strftime('%I:%M %p')} IST, Sir."


async def get_weather(city="Chennai"):
    try:
        api_key = os.getenv("OPENWEATHER_API_KEY")
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"
        async with httpx.AsyncClient() as c:
            r = await c.get(url)
            data = r.json()
        if data.get("cod") != 200:
            return f"I couldn't retrieve weather for {city}, Sir."
        temp = data["main"]["temp"]
        feels = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        desc = data["weather"][0]["description"]
        wind = data["wind"]["speed"]
        return (f"Currently {desc} in {city}, Sir. "
                f"{temp:.0f}°C, feels like {feels:.0f}°C. "
                f"Humidity {humidity}%, wind {wind} m/s.")
    except Exception as e:
        return f"Weather unavailable, Sir."


async def get_news(topic=None):
    try:
        api_key = os.getenv("NEWS_API_KEY")
        q = topic or "India"
        url = f"https://newsapi.org/v2/everything?q={q}&sortBy=publishedAt&pageSize=5&apiKey={api_key}&language=en"
        async with httpx.AsyncClient() as c:
            r = await c.get(url)
            data = r.json()
        articles = data.get("articles", [])
        if not articles:
            return "No news found, Sir."
        headlines = [f"{i+1}. {a['title']}" for i, a in enumerate(articles[:5])]
        intro = f"Top news about {topic}" if topic else "Top headlines"
        return f"{intro}, Sir:\n" + "\n".join(headlines)
    except:
        return "News unavailable, Sir."


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
    return build("gmail","v1",credentials=creds)


async def read_emails(max_results=5):
    try:
        service = get_gmail_service()
        results = service.users().messages().list(
            userId="me", labelIds=["INBOX","UNREAD"], maxResults=max_results
        ).execute()
        messages = results.get("messages",[])
        if not messages:
            return "No unread emails, Sir."
        summaries = []
        for msg in messages:
            detail = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From","Subject"]
            ).execute()
            headers = detail["payload"]["headers"]
            subject = next((h["value"] for h in headers if h["name"]=="Subject"), "No subject")
            sender = next((h["value"] for h in headers if h["name"]=="From"), "Unknown")
            sender_name = sender.split("<")[0].strip().strip('"')
            summaries.append(f"From {sender_name}: {subject}")
        reply = f"You have {len(messages)} unread emails, Sir:\n"
        reply += "\n".join([f"{i+1}. {s}" for i,s in enumerate(summaries)])
        return reply
    except Exception as e:
        return f"Could not read emails, Sir. {str(e)}"


async def send_email_msg(to_name, to_email, subject, body):
    try:
        service = get_gmail_service()
        sender = os.getenv("GMAIL_USER")
        clean_body = str(body).strip().strip('"').strip("'")
        message = MIMEMultipart()
        message["to"] = to_email
        message["from"] = sender
        message["subject"] = subject
        message.attach(MIMEText(clean_body,"plain"))
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId="me",body={"raw":raw}).execute()
        return f"Email sent to {to_name}, Sir."
    except Exception as e:
        return f"Could not send email, Sir. {str(e)}"


async def detect_intent(text: str, history_context: list = None):
    """
    Single AI call that detects ALL intents.
    Returns structured JSON with action and all needed parameters.
    """
    try:
        contacts_raw = []
        try:
            conn = sqlite3.connect(DB_PATH)
            contacts_raw = conn.execute("SELECT name,email,phone FROM contacts").fetchall()
            conn.close()
        except:
            pass
        contact_names = [r[0] for r in contacts_raw] if contacts_raw else []

        today = datetime.now(IST).strftime("%A, %B %d %Y")
        current_time = datetime.now(IST).strftime("%I:%M %p")

        prompt = f"""You are an intent detector for JARVIS AI assistant. Analyze the user message and return JSON.

Today: {today}
Current time IST: {current_time}
Known contacts: {contact_names if contact_names else "none saved yet"}

User message: "{text}"

Return ONLY valid JSON, no markdown, no explanation:

{{
  "intent": "one of the intents listed below",
  "params": {{}}
}}

INTENTS AND THEIR PARAMS:

"open_app" → {{"app": "app name"}}
  Examples: "open youtube", "launch spotify", "start whatsapp"

"web_search" → {{"query": "search query"}}
  Examples: "search for python tutorials", "google elon musk"

"navigate" → {{"destination": "place name"}}
  Examples: "navigate to airport", "take me to marina beach", "directions to Chennai"

"call" → {{"name": "contact name or number"}}
  Examples: "call mom", "call 9876543210"

"whatsapp_message" → {{"name": "recipient name", "message": "message text"}}
  Examples: "whatsapp john saying I am late", "send message to mom on whatsapp"

"weather" → {{"city": "city name or Chennai if not specified"}}
  Examples: "what's the weather", "weather in Mumbai", "is it raining in Delhi"

"news" → {{"topic": "topic or null for general"}}
  Examples: "latest news", "news about cricket", "what's happening"

"time" → {{"location": "location name or null for local IST"}}
  Examples: "what time is it", "time in Japan", "current time in USA"

"date" → {{}}
  Examples: "what's today's date", "what day is it"

"read_emails" → {{}}
  Examples: "check my emails", "any unread emails", "read inbox"

"send_email" → {{"to_name": "name", "content": "email body"}}
  Examples: "email john saying the meeting is tomorrow", "send mail to mom that I'll be late"

"set_reminder" → {{"task": "what to remind", "time": "time string like '2 minutes' or '6pm' or '30 minutes'"}}
  Examples: "remind me to drink water in 2 minutes", "remind me at 6pm to call mom"

"list_reminders" → {{}}
  Examples: "show my reminders", "what are my pending reminders"

"cancel_reminder" → {{"task": "task keyword"}}
  Examples: "cancel reminder water", "delete reminder call mom"

"save_contact" → {{"name": "person name", "email": "email or null", "phone": "phone or null"}}
  Examples: "save john number 9876543210", "add contact rahul email rahul@gmail.com"

"update_contact" → {{"name": "person name", "email": "new email or null", "phone": "new phone or null"}}
  Examples: "update john phone to 9999999999", "change rahul email to new@gmail.com"

"delete_contact" → {{"name": "person name"}}
  Examples: "delete contact rahul", "remove john from contacts"

"list_contacts" → {{}}
  Examples: "show my contacts", "list all contacts"

"remember" → {{"fact": "the fact to remember"}}
  Examples: "remember that my name is Faizal", "remember I like coffee"

"conversation" → {{}}
  For anything else — general questions, jokes, calculations, etc.

IMPORTANT RULES:
- "me", "my", "i", "you", "jarvis" are NEVER contact names
- Questions about memory/self ("what do you know about me") → "conversation"
- Sleep commands ("sleep", "goodbye") → "conversation"
- Secret codes → "conversation"
- If message is ambiguous → "conversation"
"""

        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role":"user","content":prompt}],
            max_tokens=200,
            temperature=0.1
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json","").replace("```","").strip()
        data = json.loads(raw)
        print(f"INTENT: {data}")
        return data
    except Exception as e:
        print(f"INTENT ERROR: {str(e)}")
        return {"intent": "conversation", "params": {}}


def handle_secret_codes(t: str):
    """Handle secret codes locally without AI."""
    if t in ["lockdown","lock yourself","jarvis lock","security lock"]:
        set_state("locked","true")
        return {"action":"none","reply":"JARVIS locked, Sir. Speak the unlock code to resume."}
    if t in ["unlock jarvis","jarvis unlock","access granted","override alpha"]:
        set_state("locked","false")
        return {"action":"none","reply":"JARVIS unlocked, Sir. All systems restored."}
    if t in ["stealth mode","silent mode","go silent","no voice"]:
        set_state("stealth","true")
        return {"action":"stealth_on","reply":"Stealth mode activated, Sir. Text only."}
    if t in ["stealth off","voice on","speak again","disable stealth"]:
        set_state("stealth","false")
        return {"action":"stealth_off","reply":"Voice restored, Sir. Back to normal."}
    if t in ["override 7749","skip confirmations","no confirmations","fast mode"]:
        set_state("skip_confirm","true")
        return {"action":"none","reply":"Override active, Sir. All confirmations bypassed."}
    if t in ["confirmations on","safe mode","disable override"]:
        set_state("skip_confirm","false")
        return {"action":"none","reply":"Confirmations restored, Sir."}
    if t in ["alpha mode","professional mode","formal mode"]:
        set_state("mode","alpha")
        return {"action":"none","reply":"Alpha mode engaged, Sir."}
    if t in ["chill mode","casual mode","relax mode"]:
        set_state("mode","chill")
        return {"action":"none","reply":"Switching to casual mode, Sir."}
    if t in ["default mode","reset mode","normal mode"]:
        set_state("mode","normal")
        return {"action":"none","reply":"Normal mode restored, Sir."}
    if t in ["panic mode","clear history","wipe memory","delete history"]:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM preferences")
        conn.commit()
        conn.close()
        return {"action":"none","reply":"All memory wiped, Sir. Clean slate."}
    if t in ["system status","status report","jarvis status"]:
        mode = get_state("mode")
        locked = get_state("locked")
        stealth = get_state("stealth")
        skip = get_state("skip_confirm")
        now = datetime.now(IST).strftime("%I:%M %p")
        return {"action":"none","reply":f"Status, Sir: Mode={mode}, Locked={locked}, Stealth={stealth}, Confirmations={'off' if skip=='true' else 'on'}, Time={now}"}
    return None


class ChatRequest(BaseModel):
    message: str

class SendEmailRequest(BaseModel):
    to_name: str
    to_email: str
    content: str


@app.get("/")
def root():
    return {"status":"JARVIS online"}

@app.get("/greeting")
def greeting():
    return {"reply": get_greeting()}

@app.post("/send_email")
async def send_email_endpoint(req: SendEmailRequest):
    reply = await send_email_msg(req.to_name, req.to_email, "Message from JARVIS", req.content)
    return {"reply": reply}


@app.post("/chat")
async def chat(req: ChatRequest):
    user_msg = req.message

    # 1. Personality trigger check (instant, no AI)
    personality_reply = check_personality_trigger(user_msg)
    if personality_reply:
        save_conversation(user_msg, personality_reply)
        return {"action":"none","reply":personality_reply}

    # 2. Secret codes (instant, no AI)
    t = user_msg.lower().strip()
    for wake in ["hey jarvis","jarvis","hey friday","friday"]:
        if t.startswith(wake):
            t = t[len(wake):].strip()

    secret = handle_secret_codes(t)
    if secret:
        save_conversation(user_msg, secret["reply"])
        return secret

    # 3. Check locked
    if get_state("locked") == "true":
        return {"action":"none","reply":"JARVIS is locked, Sir. Speak the unlock code."}

    # 4. AI intent detection
    intent_data = await detect_intent(user_msg)
    intent = intent_data.get("intent","conversation")
    params = intent_data.get("params",{})

    # ── open app ────────────────────────────────────────────────────────────
    if intent == "open_app":
        app_name = params.get("app","").lower().strip()
        for key, (action_type, value) in APP_PACKAGES.items():
            if key in app_name or app_name in key:
                reply = f"Opening {app_name.capitalize()}, Sir."
                save_conversation(user_msg, reply)
                if action_type == "open_app":
                    return {"action":"open_app","package":value,"reply":reply}
                else:
                    return {"action":"open_url","url":value,"reply":reply}
        reply = f"I don't have {app_name} in my system, Sir."
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── web search ──────────────────────────────────────────────────────────
    if intent == "web_search":
        query = params.get("query","")
        reply = f"Searching for {query}, Sir."
        save_conversation(user_msg, reply)
        return {"action":"open_url","url":f"https://google.com/search?q={query.replace(' ','+')}","reply":reply}

    # ── navigate ────────────────────────────────────────────────────────────
    if intent == "navigate":
        dest = params.get("destination","")
        reply = f"Navigating to {dest}, Sir."
        save_conversation(user_msg, reply)
        return {"action":"open_url","url":f"https://www.google.com/maps/dir/?api=1&destination={dest.replace(' ','+')}","reply":reply}

    # ── call ────────────────────────────────────────────────────────────────
    if intent == "call":
        name = params.get("name","")
        contact = get_contact(name)
        if contact and contact[2]:
            reply = f"Calling {name.capitalize()}, Sir."
            save_conversation(user_msg, reply)
            return {"action":"open_url","url":f"tel:{contact[2]}","reply":reply}
        reply = f"Opening dialer for {name}, Sir."
        save_conversation(user_msg, reply)
        return {"action":"open_url","url":"tel:","reply":reply}

    # ── whatsapp ────────────────────────────────────────────────────────────
    if intent == "whatsapp_message":
        name = params.get("name","")
        message = params.get("message","")
        contact = get_contact(name)
        phone = contact[2] if contact and contact[2] else None
        encoded_msg = message.replace(" ","%20")

        if get_state("skip_confirm") == "true":
            url = f"whatsapp://send?phone={phone}&text={encoded_msg}" if phone else f"whatsapp://send?text={encoded_msg}"
            reply = f"Sending WhatsApp to {name}, Sir."
            save_conversation(user_msg, reply)
            return {"action":"whatsapp_send_direct","url":url,"reply":reply}

        url = f"whatsapp://send?phone={phone}&text={encoded_msg}" if phone else f"whatsapp://send?text={encoded_msg}"
        reply = f"Sir, shall I send '{message}' to {name} on WhatsApp? Say confirm or cancel."
        save_conversation(user_msg, reply)
        return {"action":"whatsapp_message","name":name,"message":message,"url":url,"reply":reply}

    # ── weather ─────────────────────────────────────────────────────────────
    if intent == "weather":
        city = params.get("city","Chennai")
        reply = await get_weather(city)
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── news ────────────────────────────────────────────────────────────────
    if intent == "news":
        topic = params.get("topic") or None
        reply = await get_news(topic)
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── time ────────────────────────────────────────────────────────────────
    if intent == "time":
        location = params.get("location") or None
        reply = await get_time_for_location(location)
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── date ────────────────────────────────────────────────────────────────
    if intent == "date":
        today = datetime.now(IST).strftime("%A, %B %d %Y")
        reply = f"Today is {today}, Sir."
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── read emails ─────────────────────────────────────────────────────────
    if intent == "read_emails":
        reply = await read_emails()
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── send email ──────────────────────────────────────────────────────────
    if intent == "send_email":
        to_name = params.get("to_name","").strip()
        content = params.get("content","").strip()
        contact = get_contact(to_name)
        if not contact or not contact[1]:
            reply = f"I don't have an email for {to_name}, Sir. Save their contact first."
            save_conversation(user_msg, reply)
            return {"action":"none","reply":reply}
        to_email = contact[1]
        if get_state("skip_confirm") == "true":
            reply = await send_email_msg(to_name, to_email, "Message from JARVIS", content)
            save_conversation(user_msg, reply)
            return {"action":"none","reply":reply}
        reply = f"Sir, I will send to {to_name}:\n\n\"{content}\"\n\nSay confirm to send or cancel."
        save_conversation(user_msg, reply)
        return {"action":"email_pending","to_name":to_name,"to_email":to_email,"content":content,"reply":reply}

    # ── set reminder ────────────────────────────────────────────────────────
    if intent == "set_reminder":
        task = params.get("task","")
        time_str = params.get("time","")
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT INTO reminders (task,scheduled_time,status) VALUES (?,?,?)",
                     (task,time_str,"pending"))
        conn.commit()
        conn.close()
        reply = f"Reminder set, Sir. I will remind you to {task} at {time_str}."
        save_conversation(user_msg, reply)
        return {"action":"set_reminder","task":task,"time":time_str,"reply":reply}

    # ── list reminders ──────────────────────────────────────────────────────
    if intent == "list_reminders":
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT task,scheduled_time FROM reminders WHERE status='pending'"
        ).fetchall()
        conn.close()
        if not rows:
            reply = "No pending reminders, Sir."
        else:
            items = "\n".join([f"- {r[0]} at {r[1]}" for r in rows])
            reply = f"Your pending reminders, Sir:\n{items}"
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── cancel reminder ─────────────────────────────────────────────────────
    if intent == "cancel_reminder":
        task = params.get("task","")
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "UPDATE reminders SET status='cancelled' WHERE task LIKE ? AND status='pending'",
            (f"%{task}%",)
        )
        conn.commit()
        conn.close()
        reply = "Reminder cancelled, Sir."
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── save contact ────────────────────────────────────────────────────────
    if intent == "save_contact":
        name = params.get("name","").strip().lower()
        email = params.get("email")
        phone = params.get("phone")
        blocked = {"me","my","i","you","jarvis","friday","sir"}
        if not name or name in blocked or len(name) <= 1:
            reply = "I need a valid contact name, Sir."
            save_conversation(user_msg, reply)
            return {"action":"none","reply":reply}
        if not email and not phone:
            reply = f"I need at least an email or phone to save {name.capitalize()}, Sir."
            save_conversation(user_msg, reply)
            return {"action":"none","reply":reply}
        existing = get_contact(name)
        final_email = email or (existing[1] if existing else None)
        final_phone = phone or (existing[2] if existing else None)
        save_contact(name, email=final_email, phone=final_phone)
        parts = []
        if email: parts.append(f"email {email}")
        if phone: parts.append(f"phone {phone}")
        reply = f"Contact {name.capitalize()} saved with {' and '.join(parts)}, Sir."
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── update contact ──────────────────────────────────────────────────────
    if intent == "update_contact":
        name = params.get("name","").strip().lower()
        email = params.get("email")
        phone = params.get("phone")
        existing = get_contact(name)
        if not existing:
            reply = f"No contact named {name.capitalize()}, Sir."
            save_conversation(user_msg, reply)
            return {"action":"none","reply":reply}
        conn = sqlite3.connect(DB_PATH)
        if email: conn.execute("UPDATE contacts SET email=? WHERE name=?", (email,name))
        if phone: conn.execute("UPDATE contacts SET phone=? WHERE name=?", (phone,name))
        conn.commit()
        conn.close()
        parts = []
        if email: parts.append(f"email to {email}")
        if phone: parts.append(f"phone to {phone}")
        reply = f"Updated {name.capitalize()}'s {' and '.join(parts)}, Sir."
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── delete contact ──────────────────────────────────────────────────────
    if intent == "delete_contact":
        name = params.get("name","").strip().lower()
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM contacts WHERE name=?", (name,))
        conn.commit()
        conn.close()
        reply = f"Contact {name.capitalize()} deleted, Sir."
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── list contacts ───────────────────────────────────────────────────────
    if intent == "list_contacts":
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT name,email,phone FROM contacts").fetchall()
        conn.close()
        if not rows:
            reply = "No contacts saved yet, Sir."
        else:
            items = "\n".join([f"{r[0].capitalize()} — Email: {r[1] or 'none'}, Phone: {r[2] or 'none'}" for r in rows])
            reply = f"Your contacts, Sir:\n{items}"
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── remember ────────────────────────────────────────────────────────────
    if intent == "remember":
        fact = params.get("fact","")
        save_preference(f"fact_{datetime.now().timestamp()}", fact)
        reply = "Noted and remembered, Sir. I shall keep that in mind."
        save_conversation(user_msg, reply)
        return {"action":"none","reply":reply}

    # ── conversation (Groq) ─────────────────────────────────────────────────
    emotion = detect_emotion(user_msg)
    emotion_ctx = ""
    if emotion == "stressed": emotion_ctx = "\nUser seems stressed. Be extra calm and reassuring."
    elif emotion == "happy":  emotion_ctx = "\nUser seems happy. Acknowledge briefly."
    elif emotion == "joking": emotion_ctx = "\nUser is playful. Respond with light wit."

    prefs = get_preferences()
    pref_ctx = ""
    if prefs:
        pref_ctx = "\n\nThings you remember about Sir:\n" + "".join([f"- {v}\n" for v in prefs.values()])

    mode = get_state("mode")
    mode_ctx = ""
    if mode == "alpha": mode_ctx = "\nALPHA MODE: Maximum formality, no humor, pure efficiency."
    elif mode == "chill": mode_ctx = "\nCHILL MODE: Casual and relaxed but still call user Sir."

    full_prompt = SYSTEM_PROMPT + pref_ctx + emotion_ctx + mode_ctx

    history = get_history(6)
    messages = [{"role":"system","content":full_prompt}]
    messages.extend(history)
    messages.append({"role":"user","content":user_msg})

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            max_tokens=300
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        reply = "Apologies Sir, I am experiencing a momentary difficulty."

    save_conversation(user_msg, reply)

    if get_state("stealth") == "true":
        return {"action":"none","reply":reply,"stealth":True}

    return {"action":"none","reply":reply}


@app.get("/history")
def get_history_endpoint():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT user_msg,jarvis_reply,timestamp FROM conversations ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return [{"user":r[0],"jarvis":r[1],"time":r[2]} for r in rows]


@app.get("/personality")
def get_personality():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT key,value FROM assistant_personality").fetchall()
    conn.close()
    return {k:v for k,v in rows}


@app.get("/triggers")
def get_triggers():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT trigger,response FROM personality_responses").fetchall()
    conn.close()
    return [{"trigger":r[0],"response":r[1]} for r in rows]


def keep_alive():
    def ping():
        while True:
            try: urllib.request.urlopen("https://jarvis-backend-q3ml.onrender.com")
            except: pass
            import time; time.sleep(840)
    threading.Thread(target=ping, daemon=True).start()


keep_alive()
init_db()