import json
import requests
import subprocess
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup, SoupStrainer
from requests.exceptions import RequestException

# -------------------------
# CONFIG
# -------------------------
URL = "https://rajkaj.rajasthan.gov.in/"
# Use Pathlib for cleaner file handling
BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
MD_FILE = BASE_DIR / "rajkaj-orders.md"
NTFY_TOPIC = "rajkaj-orders-dan"

# -------------------------
# HELPERS
# -------------------------
def notify(count, latest_url):
    """Fire-and-forget notification logic."""
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=f"{count} new RajKaj order(s)\n{latest_url}".encode("utf-8"),
            headers={"Title": "New RajKaj Order", "Priority": "high"},
            timeout=5, # Short timeout so script finishes fast
        )
    except RequestException:
        pass # Don't crash script if notification fails

def prepend_markdown(new_entries):
    """
    Reads the file once, prepends data, and writes back.
    Efficiently handles the list in one file operation.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    buffer = []
    
    # Format new entries block
    for title, url in new_entries:
        buffer.append(f"## {timestamp}\n- **{title}**\n  {url}\n\n")
    
    new_content = "".join(buffer)

    if MD_FILE.exists():
        old_content = MD_FILE.read_text(encoding="utf-8")
        MD_FILE.write_text(new_content + old_content, encoding="utf-8")
    else:
        MD_FILE.write_text(new_content, encoding="utf-8")

# -------------------------
# MAIN LOGIC
# -------------------------
def main():
    # 1. Load State
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    
    # 2. Fetch using curl (Fast Fail)
    try:
        result = subprocess.run(
            ["curl", "-k", "-s", "-A", "Mozilla/5.0", "--compressed", URL],
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

    # 4. Parse (The Speed Optimization)
    # Only parse the 'ul' with id='notification'. Ignore the rest of the heavy HTML.
    strainer = SoupStrainer("ul", id="notification")
    soup = BeautifulSoup(page_content, "lxml", parse_only=strainer) # 'lxml' is faster than 'html.parser'

    ul = soup.find("ul")
    if not ul:
        print("Notification list not found")
        return

    # 5. Extract Items
    new_items = []
    last_seen_pdf = state.get("pdf")

    for li in ul.find_all("li"):
        a = li.find("a")
        if not a: continue

        pdf_url = a.get("href", "").strip()
        title = a.get_text(strip=True)

        if pdf_url.startswith("/"):
            pdf_url = "https://rajkaj.rajasthan.gov.in" + pdf_url

        # Stop processing if we hit the last seen item
        if pdf_url == last_seen_pdf:
            break

        new_items.append((title, pdf_url))

    # 6. Act
    if new_items:
        print(f"{len(new_items)} NEW ORDER(S)")
        
        # Reverse to keep chronological order in the Markdown block
        # (Assuming you want the newest on top of the file, but ordered naturally in the block)
        # If you want newest strictly first, remove `reversed`
        prepend_markdown(reversed(new_items))

        # Notify
        notify(len(new_items), new_items[0][1])

        # Save State
        state["pdf"] = new_items[0][1]
        state["title"] = new_items[0][0] # Optional: store title too
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    else:
        print("No new items found")

if __name__ == "__main__":
    main()
