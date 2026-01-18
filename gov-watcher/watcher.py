import json
import logging
import subprocess
import time
import tempfile
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

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
        except json.JSONDecodeError as e:
            logging.error(f"JSON decode error in {path}: {e}")
            return {}
        except Exception as e:
            logging.error(f"Error loading {path}: {e}")
            return {}
    return {}

def save_json(path, data):
    """Atomically write JSON to file using a temporary file and rename."""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp_path = tmp.name
        Path(tmp_path).replace(path)
    except Exception as e:
        logging.error(f"Error saving {path}: {e}")
        raise

def fetch_html(url):
    """
    Fetches HTML using curl to bypass legacy SSL/TLS issues common on gov sites.
    """
    try:
        # Try curl first (handles legacy SSL/TLS on some sites)
        result = subprocess.run(
            ["curl", "-k", "-s", "-A", "Mozilla/5.0", "--compressed", url],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return result.stdout
        logging.debug(f"Curl failed for {url}: {result.stderr}")
    except (FileNotFoundError, Exception) as e:
        logging.debug(f"Curl unavailable/failed: {e}")

    # Fallback to requests
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            verify=False,
            timeout=60,
        )
        response.raise_for_status()
        return response.text
    except Exception as e:
        logging.error(f"Failed to fetch {url}: {e}")
        return None

def notify(topic, title, link):
    """Sends a notification to ntfy.sh"""
    try:
        response = requests.post(
            f"https://ntfy.sh/{topic}",
            data=f"{title}\n{link}".encode("utf-8"),
            headers={"Title": "New Gov Order"},
            timeout=5,
        )
        if response.status_code not in (200, 201):
            logging.warning(
                f"ntfy.sh returned {response.status_code} for {topic}: {response.text[:100]}"
            )
        time.sleep(0.5)  # Be nice to the API
    except Exception as e:
        logging.warning(f"Notification failed for {topic}: {e}")

def update_history(site_name, items):
    """Prepends new items to the history markdown file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_block = f"### {site_name} - {timestamp}\n"
    for title, link in items:
        # Escape common markdown-breaking chars in title
        title_escaped = title.replace("]", "\\]").replace("[", "\\[")
        # Use angle brackets for URLs to handle parentheses
        new_block += f"- [{title_escaped}](<{link}>)\n"
    new_block += "\n---\n\n"

    current_content = ""
    if HISTORY_FILE.exists():
        try:
            current_content = HISTORY_FILE.read_text(encoding="utf-8")
        except Exception as e:
            logging.error(f"Error reading history: {e}")

    try:
        HISTORY_FILE.write_text(new_block + current_content, encoding="utf-8")
    except Exception as e:
        logging.error(f"Error writing history: {e}")

# --- MAIN LOGIC ---

def run_watcher():
    logging.info(f"Starting Watcher at {datetime.now()}")

    config = load_json(CONFIG_FILE)
    if not config:
        logging.error("No config found!")
        return

    # Load state or init empty
    # State structure: { "site_id": { "seen_urls": [], "last_seen": "url" } }
    state = load_json(STATE_FILE)

    for i, site in enumerate(config):
        site_id = site.get("id")
        name = site.get("name")
        url = site.get("url")
        selector = site.get("selector")
        base_url = site.get("base_url", "")
        strategy = site.get("strategy", "track_latest")
        topic = site.get("topic", "general_alerts")

        # Validate config entry
        if not all([site_id, name, url]):
            logging.warning(f"Config entry {i} missing required fields (id, name, url), skipping")
            continue

        logging.info(f"Checking {name} ({url})...")

        html = fetch_html(url)
        if not html:
            logging.warning(f"Failed to fetch {name}, skipping")
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
            if not href:
                continue

            # Filter for PDFs if no selector provided (generic fallback)
            if not selector and not href.lower().endswith(".pdf"):
                continue

            # Use url as fallback if base_url is empty
            resolve_base = base_url or url
            full_link = urljoin(resolve_base, href)
            title = " ".join(a.get_text().split()) or "Untitled Document"
            current_items.append((title, full_link))

        if not current_items:
            logging.info(f"  No items found on {name}")
            continue

        # --- DIFFERENCING STRATEGY ---
        
        site_state = state.get(site_id, {"seen_urls": []})
        
        # Ensure seen_urls is a list for ordering
        seen_urls_list = site_state.get("seen_urls", [])
        if not isinstance(seen_urls_list, list):
            seen_urls_list = list(seen_urls_list) if hasattr(seen_urls_list, '__iter__') else []

        seen_urls_set = set(seen_urls_list)
        new_items = []

        if strategy == "track_all":
            # Check every item on the page against history
            for title, link in current_items:
                if link not in seen_urls_set:
                    new_items.append((title, link))
                    seen_urls_list.append(link)
                    seen_urls_set.add(link)

            # Limit unbounded growth: keep only recent URLs (max 1000)
            if len(seen_urls_list) > 1000:
                logging.warning(f"{site_id} seen_urls exceeds 1000, pruning oldest entries")
                seen_urls_list = seen_urls_list[-1000:]
                seen_urls_set = set(seen_urls_list)

            # Update state list
            site_state["seen_urls"] = seen_urls_list

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
            logging.info(f"  Found {len(new_items)} new items for {name}")
            update_history(name, new_items)

            # Notify for each new item (or summarize if too many)
            for title, link in reversed(new_items):  # Notify oldest first
                notify(topic, f"{name}: {title}", link)
        else:
            logging.debug(f"  No new updates for {name}")

        # Update global state object
        state[site_id] = site_state

    # Save state at the end
    save_json(STATE_FILE, state)
    logging.info("Watcher run completed")

if __name__ == "__main__":
    run_watcher()