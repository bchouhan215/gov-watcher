import json
import subprocess
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "state.json"
HISTORY_FILE = BASE_DIR / "history.md"

# --- CORE FUNCTIONS ---

def load_json(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_json(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def fetch_html(url):
    """
    Fetches HTML using curl to bypass legacy SSL/TLS issues common on gov sites.
    """
    try:
        # -k: Insecure (ignore SSL), -s: Silent, --compressed: Handle gzip
        result = subprocess.run(
            ["curl", "-k", "-s", "-A", "Mozilla/5.0", "--compressed", url],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode != 0:
            print(f"  [Error] Curl failed: {result.stderr}")
            return None
        return result.stdout
    except Exception as e:
        print(f"  [Error] Exception during fetch: {e}")
        return None

def notify(topic, title, link):
    """Sends a notification to ntfy.sh"""
    try:
        import requests
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=f"{title}\n{link}".encode("utf-8"),
            headers={"Title": "New Gov Order"},
            timeout=5
        )
        time.sleep(1) # Be nice to the API
    except Exception as e:
        print(f"  [Warning] Notification failed: {e}")

def update_history(site_name, items):
    """Prepends new items to the history markdown file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_block = f"### {site_name} - {timestamp}\n"
    for title, link in items:
        new_block += f"- [{title}]({link})\n"
    new_block += "\n---\n\n"

    current_content = ""
    if HISTORY_FILE.exists():
        current_content = HISTORY_FILE.read_text(encoding="utf-8")
    
    HISTORY_FILE.write_text(new_block + current_content, encoding="utf-8")

# --- MAIN LOGIC ---

def run_watcher():
    print(f"--- Starting Watcher at {datetime.now()} ---")
    
    config = load_json(CONFIG_FILE)
    if not config:
        print("No config found!")
        return

    # Load state or init empty
    # State structure: { "site_id": { "seen_urls": [], "last_seen": "url" } }
    state = load_json(STATE_FILE)

    for site in config:
        site_id = site.get("id")
        name = site.get("name")
        url = site.get("url")
        selector = site.get("selector")
        base_url = site.get("base_url", "")
        strategy = site.get("strategy", "track_latest")
        topic = site.get("topic", "general_alerts")

        print(f"Checking {name} ({url})...")

        html = fetch_html(url)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        
        # Select items
        # If selector is explicit, use select(), else find all 'a' with .pdf
        links = []
        if selector:
            elements = soup.select(selector)
            # If selector targets 'ul' or 'table', we need to find 'a' inside
            # But usually we define selector to target the 'a' directly
            for el in elements:
                if el.name == 'a':
                    links.append(el)
                else:
                    links.extend(el.find_all('a'))
        else:
            links = soup.find_all("a", href=True)

        # Process found links into a standardized list of (title, full_url)
        current_items = []
        for a in links:
            href = a.get("href", "").strip()
            if not href: continue
            
            # Filter for PDFs if no selector provided (generic fallback)
            if not selector and not href.lower().endswith(".pdf"):
                continue

            full_link = urljoin(base_url, href)
            title = " ".join(a.get_text().split()) or "Untitled Document"
            current_items.append((title, full_link))

        if not current_items:
            print("  No items found on page.")
            continue

        # --- DIFFERENCING STRATEGY ---
        
        site_state = state.get(site_id, {"seen_urls": []})
        seen_urls = set(site_state.get("seen_urls", []))
        new_items = []

        if strategy == "track_all":
            # Check every item on the page against history
            # Reverse generic list so we process bottom-up (usually older first) if needed,
            # but usually top is newest. We want to find *all* unknown ones.
            for title, link in current_items:
                if link not in seen_urls:
                    new_items.append((title, link))
                    seen_urls.add(link) # Add to set immediately to avoid dupes in this run
            
            # Update state list (convert set back to list)
            site_state["seen_urls"] = list(seen_urls)

        elif strategy == "track_latest":
            # Only check if the *top* item is different from last time
            # Assumes the first item in DOM is the newest
            top_title, top_link = current_items[0]
            last_seen = site_state.get("last_seen_url")
            
            if top_link != last_seen:
                # It's new!
                # Ideally we'd find *how many* are new, but for "track_latest" 
                # we often just grab the top one to be safe/simple.
                # Let's try to grab all until we hit the last_seen
                for title, link in current_items:
                    if link == last_seen:
                        break
                    new_items.append((title, link))
                
                site_state["last_seen_url"] = top_link
        
        # --- NOTIFICATION & SAVING ---
        
        if new_items:
            print(f"  Found {len(new_items)} new items!")
            update_history(name, new_items)
            
            # Notify for each new item (or summarize if too many)
            for title, link in reversed(new_items): # Notify oldest first
                notify(topic, f"{name}: {title}", link)
        else:
            print("  No new updates.")

        # Update global state object
        state[site_id] = site_state

    # Save state at the end
    save_json(STATE_FILE, state)
    print("Done.")

if __name__ == "__main__":
    run_watcher()
