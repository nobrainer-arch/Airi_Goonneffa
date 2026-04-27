import requests
import time
import random
import string
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== SUPABASE DETAILS ==========
SUPABASE_URL = "https://hvaujoxdpowcvbcgoefk.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imh2YXVqb3hkcG93Y3ZiY2dvZWZrIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzY4Mzk0NzMsImV4cCI6MjA5MjQxNTQ3M30.TXL8M0LIXUTiOc_-GeEIcTPPpVUPLwon2qCDzuMyApg"

HEADERS = {
    "apikey": ANON_KEY,
    "Authorization": f"Bearer {ANON_KEY}",
    "Content-Type": "application/json",
    "Content-Profile": "public",
    "User-Agent": "PythonLoadTest/1.0"
}

CONCURRENT_USERS = 1    # rate limits
TOTAL_CYCLES = 1         # Total complete user workflows
PASSWORD = "loadtest123"

# Endpoints 
REGISTER_URL = f"{SUPABASE_URL}/rest/v1/users?columns=%22username%22%2C%22password%22"
SEND_MSG_URL = f"{SUPABASE_URL}/rest/v1/messages?columns=%22content%22%2C%22username%22"
FETCH_MSGS_URL = f"{SUPABASE_URL}/rest/v1/messages?select=*&order=created_at.asc"

def random_username():
    ts = int(time.time() * 1000)
    rand = ''.join(random.choices(string.ascii_lowercase, k=4))
    return f"test_{ts}_{rand}"

def random_message():
    msgs = ["hello!", "how are you?", "testing load", "hi there", "sup?", "lol", "load test msg"]
    return random.choice(msgs) + random.choice(["", " 😊", " 🚀", " 🔥"])

def user_workflow(username, password):
    session = requests.Session()
    session.headers.update(HEADERS)

    # --- 1. Register ---
    try:
        r = session.post(REGISTER_URL, json={"username": username, "password": password}, timeout=10)
        if r.status_code != 201:
            return False, f"Register failed ({r.status_code})"
    except Exception as e:
        return False, f"Register exception: {e}"
    time.sleep(0.5)

    # --- 2. Login check ---
    login_url = (f"{SUPABASE_URL}/rest/v1/users?select=*"
                 f"&username=eq.{requests.utils.quote(username)}"
                 f"&password=eq.{requests.utils.quote(password)}")
    try:
        r = session.get(login_url, timeout=10)
        data = r.json()
        if r.status_code != 200 or not data:
            return False, "Login failed (no user found)"
    except Exception as e:
        return False, f"Login exception: {e}"
    time.sleep(0.5)

    # --- 3. Fetch messages ---
    try:
        r = session.get(FETCH_MSGS_URL, timeout=10)
        if r.status_code != 200:
            return False, f"Fetch messages failed ({r.status_code})"
    except Exception as e:
        return False, f"Fetch messages exception: {e}"
    time.sleep(0.5)

    # --- 4. Send a message ---
    msg_text = random_message()
    payload = {
        "content": msg_text,
        "username": username
    }
    try:
        r = session.post(SEND_MSG_URL, json=payload, timeout=10)
        if r.status_code == 201:
            return True, f"Message sent: '{msg_text}'"
        else:
            return False, f"Send message failed ({r.status_code})"
    except Exception as e:
        return False, f"Send message exception: {e}"

def main():
    print(f"Starting chat load test: {CONCURRENT_USERS} concurrent, {TOTAL_CYCLES} full workflows")
    start = time.time()
    success = 0
    fail = 0

    with ThreadPoolExecutor(max_workers=CONCURRENT_USERS) as executor:
        futures = []
        for _ in range(TOTAL_CYCLES):
            uname = random_username()
            futures.append(executor.submit(user_workflow, uname, PASSWORD))
            time.sleep(0.1)  # slight stagger

        for future in as_completed(futures):
            ok, msg = future.result()
            if ok:
                success += 1
            else:
                fail += 1
                print(f"❌ {msg}")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s – Success: {success}, Fail: {fail}")

if __name__ == "__main__":
    main()