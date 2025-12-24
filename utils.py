import os
import re
import json
import base64
from datetime import datetime
from PyPDF2 import PdfReader
from firebase_admin import credentials, firestore, storage, initialize_app, _apps

# === 1. Firebase Setup (Generic) ===
def get_firebase_db():
    """Initializes Firebase if not already initialized."""
    FIREBASE_BUCKET_NAME = os.getenv("FIREBASE_BUCKET")
    FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")

    if not _apps:
        if not FIREBASE_CREDENTIALS:
            raise ValueError("FIREBASE_CREDENTIALS env var missing")
        
        firebase_dict = json.loads(base64.b64decode(FIREBASE_CREDENTIALS).decode("utf-8"))
        cred = credentials.Certificate(firebase_dict)
        initialize_app(cred, {'storageBucket': FIREBASE_BUCKET_NAME})

    return firestore.client(), storage.bucket()

# === 2. Helper Functions ===
def format_date(date_str):
    if not date_str or date_str in ["Unknown", "Invalid"]:
        return "Invalid"
    date_str = date_str.strip()
    date_formats = ["%B %d, %Y", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"]
    for fmt in date_formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str

def sanitize_filename(name):
    return re.sub(r'[\\/:"*?<>|]', "_", name)

# === 3. Extraction Logic (Copied from your code) ===
def extract_template_type(text, lines):
    # (Paste your existing extract_template_type logic here)
    if "ABSORBER" in text: return "absorber"
    if "FULL BODY HARNESS" in text or "PROFESSIONAL HARNESSES" in text: return "harness"
    if any(k in l.lower() for l in lines for k in ["eebd refil", "spiroscape", "interspiro", "escape-15", "eebd"]): return "eebd"
    if "self-contained breathing apparatus" in text.lower() or "scba" in text.lower(): return "scba"
    if "rigrat" in text.lower() or "area monitor" in text.lower(): return "area_monitor"
    if "smoke hood" in text.lower() or "draeger parat" in text.lower(): return "smoke_hood"
    if "RESCUE KIT" in text: return "rescue_kit"
    if "certificate" in text.lower() and "calibration" in text.lower(): return "gas_detector"
    return "unknown"

# ... (Paste all your individual extractor functions here: extract_eebd, extract_gas_detector, etc.) ...
# Ensure they return standard Python lists/dictionaries, NOT Streamlit objects.

def process_pdf_text(file_path, is_service=False):
    """
    Reads PDF and returns extracted data.
    Replaces your 'extract_from_pdf' to be API-friendly.
    """
    try:
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            t = page.extract_text()
            if t: text += "\n" + t.strip()
        lines = text.splitlines()
    except Exception as e:
        return {"error": str(e), "status": "failed"}

    template = extract_template_type(text, lines)
    
    # Map logic
    collection_suffix = "_SERVICE" if is_service else ""
    
    # NOTE: You will need to import your extract functions here or ensure they are in scope
    extractor_map = {
        # Ensure these function names match what you pasted into this file
        "gas_detector": ("GD" + collection_suffix, extract_gas_detector),
        "eebd": ("EEBD" + collection_suffix, extract_eebd),
        # ... Add other mappings ...
    }

    if template in extractor_map:
        coll_name, func = extractor_map[template]
        data = func(text, lines)
        return {"status": "success", "type": template, "collection": coll_name, "data": data}
    
    return {"status": "unknown", "text_snippet": text[:500]}