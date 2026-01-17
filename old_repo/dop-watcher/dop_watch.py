import json
import sys
import time
import subprocess
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime

# --- CONFIG ---
BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
ARCHIVE_FILE = BASE_DIR / "dop-orders.md"
CONFIG_FILE = BASE_DIR / "config.json"

# Defaults
TARGET_URL = "https://dop.rajasthan.gov.in/Content/news.aspx"
BASE_DOMAIN = "https://dop.rajasthan.gov.in/"
NTFY_TOPIC = "dop_alerts"

# Load Config
if CONFIG_FILE.exists():
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        TARGET_URL = config.get("url", TARGET_URL)
        NTFY_TOPIC = config.get("ntfy_topic", NTFY_TOPIC)
    except Exception as e:
        print(f"Warning: Failed to load config.json: {e}")

# --- HELPERS ---
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except: pass
    return {"seen_urls": []} # We use a list for the 'First Run' to track everything

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

def main():
    state = load_state()
    # Ensure seen_urls exists in state to prevent KeyErrors later
    if "seen_urls" not in state:
        state["seen_urls"] = []
    
    # Use set for O(1) lookups
    seen_urls = set(state["seen_urls"]) 

    try:
        # Use curl because requests/urllib3 has TLS handshake issues with this specific server
        result = subprocess.run(
            ["curl", "-k", "-s", "--compressed", TARGET_URL],
            capture_output=True,
            text=True,
            timeout=90
        )
        if result.returncode != 0:
            print(f"Error fetching page via curl: {result.stderr}")
            return
            
        page_content = result.stdout
    except Exception as e:
        print(f"Error: {e}")
        return

    soup = BeautifulSoup(page_content, "html.parser")
    
    # Rob looks at EVERY link on the page now
    new_items = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.lower().endswith(".pdf"):
            continue
            
        full_url = urljoin(BASE_DOMAIN, href)
        title = " ".join(a.get_text().split()) or "Untitled Order"

        if full_url not in seen_urls:
            new_items.append((title, full_url))

    if not new_items:
        print("Nothing new.")
        return

    print(f"Found {len(new_items)} new items!")

    # Update Archive & Notify
    # We go in REVERSE so the oldest items are added first, 
    # and the newest one stays at the very top of your state/archive.
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Determine header text
    is_first_run = not ARCHIVE_FILE.exists()
    header_suffix = " (Initial Import)" if is_first_run else ""
    archive_content = f"\n## {today}{header_suffix}\n\n"
    
    for title, url in reversed(new_items):
        # 1. Add to Archive String
        archive_content += f"- **{title}**\n  {url}\n\n"
        
        # 2. Fire the Megaphone (Notification)
        try:
            requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", 
                          data=f"New Order: {title}\n{url}".encode("utf-8"),
                          timeout=10)
            time.sleep(2)
        except Exception as e:
            print(f"Failed to send notification: {e}")
        
        # 3. Add to memory
        state["seen_urls"].append(url)

    # Prepend to the diary
    if ARCHIVE_FILE.exists():
        old = ARCHIVE_FILE.read_text(encoding="utf-8")
        ARCHIVE_FILE.write_text(archive_content + old, encoding="utf-8")
    else:
        ARCHIVE_FILE.write_text(f"# DOP Archive\n\n" + archive_content, encoding="utf-8")

    # Save the new memory
    save_state(state)

if __name__ == "__main__":
    main()
