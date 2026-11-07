import os
import re
import json
import logging
import requests
import threading
import time
import urllib.parse
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from groq import Groq, RateLimitError
import urllib3
# Add this at the top level
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
client_groq = Groq(api_key=os.environ.get("GROQ_API_KEY") , default_headers={"Groq-Model-Version": "latest"}  # 👈 CRITICAL for visit_website
)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")

DATA_FOLDER = "data"
CACHE_FILE = os.path.join(DATA_FOLDER, "verified_urls.json")
KENET_FILE = "kenet_all_200_institutions.txt"

cache_lock = threading.Lock()
# --- NEW: Lock to pace Groq requests ---
groq_lock = threading.Lock()

def load_kenet_domains():
    domains = {}
    if not os.path.exists(KENET_FILE):
        logging.warning(f"⚠️ KENET file not found at {KENET_FILE}. Proceeding without domain hints.")
        return domains
    
    with open(KENET_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            if '->' in line:
                parts = line.split('->')
                name = parts[0].lower().strip()
                url_match = re.search(r'https?://([a-zA-Z0-9.-]+)', parts[1])
                if url_match:
                    domains[name] = url_match.group(1).replace('www.', '')
    return domains

KENET_DOMAINS = load_kenet_domains()

def get_cached_url(uni_name, course_key):
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r') as f:
            cache = json.load(f)
            return cache.get(uni_name, {}).get(course_key)
    except:
        return None

def save_to_cache(uni_name, course_key, url):
    with cache_lock:
        cache = {}
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r') as f:
                    cache = json.load(f)
            except: pass
        
        if uni_name not in cache:
            cache[uni_name] = {}
        
        cache[uni_name][course_key] = url
        
        os.makedirs(DATA_FOLDER, exist_ok=True)
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=4)

# --- 1. THE COMPOUND AI RETRY ENGINE ---
@retry(
    wait=wait_exponential(multiplier=2, min=3, max=20), 
    stop=stop_after_attempt(4),
    retry=retry_if_exception_type(RateLimitError),
    reraise=True
)
# 👇 MAKE SURE THESE 6 LINES ARE ABOVE THE FUNCTION
@retry(
    wait=wait_exponential(multiplier=2, min=3, max=20), 
    stop=stop_after_attempt(4),
    retry=retry_if_exception_type(RateLimitError),
    reraise=True
)
def call_groq_api(prompt):
    """Wraps the actual Groq call with a lock and a delay to respect limits."""
    with groq_lock:
        try:
            response = client_groq.chat.completions.create(
                # Swapped to a fast, reliable standard model
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are a web URL expert. Based on your knowledge, deduce the most likely official URL. Return ONLY the raw URL string with absolutely no other text."},
                    {"role": "user", "content": prompt}
                ],
                # Notice: The entire extra_body dictionary has been removed!
                temperature=0.1 
            )
            time.sleep(2.0) 
            
            return response.choices[0].message.content
        except Exception as e:
            logging.error(f"Standard Model System Error: {e}")
            return None


        
# --- 2. URL RETRIEVAL & VALIDATION ---
def get_course_url(university_name, course_name, target_type="kuccps"):
    uni_key = university_name.lower().strip()
    course_key = f"{course_name.lower().strip()}_{target_type}"
    

    # Cache Check
    cached = get_cached_url(uni_key, course_key)
    if cached: return cached

    # --- FIX: Define the missing variables ---
    # Retrieve domain hint from KENET list (e.g., 'uonbi.ac.ke')
    domain_hint = next((dom for name, dom in KENET_DOMAINS.items() if name in uni_key), None)
    
    # Create a URL-safe query string for Google fallbacks
    safe_query = urllib.parse.quote_plus(f'"{course_name}" {university_name} Kenya')
    
    # --- 2. Define Prompts (University Links Only) ---
    domain_instruction = f"site:{domain_hint} " if domain_hint else ""
    
    if target_type == "institution":
        base_prompt = (
            f"{domain_instruction}Find the official direct course information page for "
            f"'{course_name}' at '{university_name}' in Kenya. Return ONLY the raw URL string."
        )
        fallback_url = f"https://www.google.com/search?q={safe_query}+{university_name}+official+course+page"
    else:
        # Fallback for general official homepage
        base_prompt = f"Find the official homepage URL for {university_name} in Kenya. Return ONLY the raw URL."
        fallback_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(university_name + ' official website')}"

    max_attempts = 3
    bad_urls = []
    for attempt in range(max_attempts):
        current_prompt = base_prompt
        
        # --- 🚨 THE FIX: PREVENT 413 PAYLOAD ERRORS ---
        if bad_urls:
            # Only keep the 2 most recent dead URLs so the prompt doesn't snowball
            recent_bad_urls = bad_urls[-2:] 
            current_prompt += f"\n\nIMPORTANT: Do NOT return these dead URLs: {', '.join(recent_bad_urls)}. They resulted in a 404. Find an ALTERNATIVE working link."

        try:
            # ASK GROQ
            response_text = call_groq_api(current_prompt)
            
            # Add a safety check in case the API returned None
            if not response_text:
                logging.warning(f"⚠️ Attempt {attempt+1}: Groq returned None. Retrying...")
                continue
                
            # Search the string directly!
            match = re.search(r'(https?://[^\s"\'\`]+)', response_text)
            if match:
                found_url = match.group(1).strip().rstrip('.,')
                
                # 3. ADVANCED PYTHON PING (Catches 404s and Soft 404s)
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                resp = requests.get(found_url, headers=headers, timeout=5, verify=False)
                
                if resp.status_code == 200:
                    html_lower = resp.text.lower()
                    # Detect Soft 404s (pages that load but say "Not Found")
                    is_soft_404 = any(phrase in html_lower for phrase in [
                        "page not found", "404 error", "cannot be found", "could not be found"
                    ])
                    
                    if not is_soft_404:
                        save_to_cache(uni_key, course_key, found_url)
                        logging.info(f"✅ [VERIFIED] {target_type.upper()} Attempt {attempt+1}: {found_url}")
                        return found_url
                    else:
                        logging.warning(f"⚠️ [SOFT 404] Attempt {attempt+1}: {found_url} is dead text. Retrying...")
                        bad_urls.append(found_url)
                else:
                    logging.warning(f"⚠️ [HTTP {resp.status_code}] Attempt {attempt+1}: {found_url} is dead. Retrying...")
                    bad_urls.append(found_url)
            else:
                logging.warning(f"⚠️ Attempt {attempt+1}: Groq didn't return a URL. Retrying...")

        except RateLimitError:
            logging.error(f"⏳ Rate limit hit on attempt {attempt+1}. Stopping loop.")
            break # Exit the loop immediately to avoid hitting the API further
        except Exception as e:
            logging.error(f"🚨 Ping/Search failed on attempt {attempt+1}: {e}")

    # 4. IF ALL ATTEMPTS FAIL: Use the Unbreakable Google Fallback
    logging.warning(f"❌ All {max_attempts} attempts failed for {university_name}. Using Fallback.")
   
