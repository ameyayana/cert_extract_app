import os
import re
import json
import base64
from datetime import datetime
from PyPDF2 import PdfReader
from firebase_admin import credentials, firestore, storage, initialize_app, _apps

# ==========================================
# 1. FIREBASE SETUP
# ==========================================
def get_firebase_db():
    """Initializes Firebase if not already initialized."""
    FIREBASE_BUCKET_NAME = os.getenv("FIREBASE_BUCKET")
    FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")

    if not _apps:
        if not FIREBASE_CREDENTIALS:
            # Fallback for local testing if env var is missing, though Render needs it
            print("Warning: FIREBASE_CREDENTIALS not found.")
            return None, None
        
        try:
            firebase_dict = json.loads(base64.b64decode(FIREBASE_CREDENTIALS).decode("utf-8"))
            cred = credentials.Certificate(firebase_dict)
            initialize_app(cred, {'storageBucket': FIREBASE_BUCKET_NAME})
        except Exception as e:
            print(f"Firebase Init Error: {e}")
            raise e

    return firestore.client(), storage.bucket()

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================
def format_date(date_str):
    if not date_str or date_str in ["Unknown", "Invalid"]:
        return "Invalid"
    date_str = date_str.strip()
    
    # Try multiple formats
    date_formats = [
        "%B %d, %Y",  # November 14, 2025
        "%d/%m/%Y",   # 14/11/2025
        "%d-%m-%Y",   # 14-11-2025
        "%d/%m/%y",   # 14/11/25
    ]
    for fmt in date_formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str

def sanitize_filename(name):
    return re.sub(r'[\\/:"*?<>|]', "_", str(name))

# ==========================================
# 3. EXTRACTION LOGIC (From your old main.py)
# ==========================================

def extract_template_type(text, lines):
    """Determines the certificate type based on keywords."""
    # Convert text to lower for easier matching
    text_lower = text.lower()
    
    if "absorber" in text: return "absorber"
    if "full body harness" in text or "professional harnesses" in text: return "harness"
    
    # EEBD check
    if any(k in l.lower() for l in lines for k in ["eebd refil", "spiroscape", "interspiro", "escape-15", "eebd"]): return "eebd"
    
    if "self-contained breathing apparatus" in text_lower or "scba" in text_lower: return "scba"
    if "rigrat" in text_lower or "area monitor" in text_lower: return "area_monitor"
    if "smoke hood" in text_lower or "draeger parat" in text_lower: return "smoke_hood"
    if "rescue kit" in text: return "rescue_kit"
    
    # Gas Detector check
    if "certificate" in text_lower and "calibration" in text_lower: return "gas_detector"
    
    return "unknown"

# --- Individual Extractors ---

def extract_eebd(text, lines):
    norm_lines = [l.strip() for l in lines]
    cert_match = re.search(r"\b\d{2}/\d{5}/\d{4}\.SRV\b", text)
    cert = cert_match.group(0) if cert_match else "Unknown"
    
    lot_match = re.search(r"CHSB-[A-Z]{2}-\d{2}-\d{2}", text, re.IGNORECASE)
    lot = lot_match.group(0) if lot_match else "Unknown"
    
    serial = "Unknown"
    for i, line in enumerate(lines):
        if "serial number" in line.lower():
            if i + 1 < len(lines):
                candidate = lines[i + 1].strip()
                candidate_normalized = re.sub(r"\s*-\s*", "-", candidate, flags=re.IGNORECASE)
                match = (
                    re.search(r"[A-Z0-9]{2,}-\d{2,}", candidate_normalized, re.IGNORECASE)
                    or re.search(r"\d+[A-Z]+\-\d+", candidate_normalized, re.IGNORECASE)
                    or re.search(r"[A-Z0-9]{5,}", candidate_normalized, re.IGNORECASE)
                )
                if match: serial = match.group(0).replace(" ", "")
                break
                
    model = "Unknown"
    model_keywords = ["MSA, Escape-15", "Lalizas", "MSA", "Cylinder"]
    for l in norm_lines:
        if any(k.lower() in l.lower() for k in model_keywords):
            model = l.strip()
            if len(model) > 50:
                if "MSA" in model: model = "MSA, Escape-15"
            break
            
    date_pattern = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
    dates = re.findall(date_pattern, text)
    cal_date = dates[0] if len(dates) > 0 else "Invalid"
    exp_date = dates[1] if len(dates) > 1 else "Invalid"
    
    data = {
        "cert": cert,
        "model": model,
        "serial": serial,
        "cal": format_date(cal_date) if cal_date != "Invalid" else "Invalid",
        "exp": format_date(exp_date) if exp_date != "Invalid" else "Invalid",
        "lot": lot
    }
    return [data]

def extract_absorber(text, lines):
    cert = re.search(r"\d{2}/\d{5}/\d{4}\.SRV", text)
    report = re.search(r"CHSB-\w+-\d{2}-\d{2}", text)
    model_line = next((l for l in lines if "ABSORBING LANYARD" in l or "SHOCK ABSORBER" in l), "Unknown")
    serials = re.findall(r"\d{8}:\d{4}", text)
    first_serial = serials[0] if serials else "Unknown"
    next_date = re.findall(r"\b\d{2}/\d{2}/\d{4}\b", text)
    cal = format_date(next_date[1]) if len(next_date) > 1 else "Invalid"
    exp = format_date(next_date[0]) if next_date else "Invalid"
    return [{
        "cert": cert.group(0) if cert else "Unknown",
        "model": model_line.strip(),
        "serial": first_serial,
        "cal": cal,
        "exp": exp,
        "lot": report.group(0) if report else "Unknown"
    }]

def extract_harness(text, lines):
    cert = re.search(r"\d{2}/\d{5}/\d{4}\.SRV", text)
    report = re.search(r"CHSB-\w+-\d{2}-\d{2}", text)
    model_line = next((l for l in lines if "FULL BODY" in l and "HARNESS" in l), "Unknown")
    serial_match = re.search(r"\d{7}:\d{4}", text)
    date = re.search(r"Date:\s*(\d{2}/\d{2}/\d{4})", text)
    next_date = re.search(r"Next Inspection Date:\s*(\d{2}/\d{2}/\d{4})", text)
    return [{
        "cert": cert.group(0) if cert else "Unknown",
        "model": model_line.strip(),
        "serial": serial_match.group(0) if serial_match else "Unknown",
        "cal": format_date(date.group(1)) if date else "Invalid",
        "exp": format_date(next_date.group(1)) if next_date else "Invalid",
        "lot": report.group(0) if report else "Unknown"
    }]

def extract_scba(text, lines):
    norm_lines = [l.strip() for l in lines]
    cert_match = re.search(r"\b\d{2}/\d{5}/\d{4}\.SRV\b", text)
    cert = cert_match.group(0) if cert_match else "Unknown"
    lot_match = re.search(r"CHSB-[A-Z]{2}-\d{2}-\d{2}", text, re.IGNORECASE)
    lot = lot_match.group(0) if lot_match else "Unknown"
    serial = "Unknown"
    target_pattern = re.compile(r"(C\d{2,3}/\d{3,4})", re.IGNORECASE)
    for l in norm_lines:
        match = target_pattern.search(l)
        if match:
            serial = match.group(1)
            break
    model = "Unknown"
    model_keywords = ["LALIZAS, SCBA", "MSA"]
    for l in norm_lines:
        if "LALIZAS, SCBA" in l:
            model = "LALIZAS, SCBA"
            break
        elif "MSA" in l.upper() and "SCBA" in l.upper():
            model = l.strip()
            break
    date_pattern = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
    dates = re.findall(date_pattern, text)
    cal_date = dates[0] if len(dates) > 0 else "Invalid"
    exp_date = dates[1] if len(dates) > 1 else "Invalid"
    data = {
        "cert": cert,
        "model": model,
        "serial": serial,
        "cal": format_date(cal_date) if cal_date != "Invalid" else "Invalid",
        "exp": format_date(exp_date) if exp_date != "Invalid" else "Invalid",
        "lot": lot
    }
    return [data]

def extract_area_monitor(text, lines):
    cert = re.search(r"(\d{1,3}/\d{3,5}/\d{4}\.SRV)", text)
    cert_val = cert.group(1) if cert else "Unknown"
    lot = "Unknown"
    for i, line in enumerate(lines):
        if "cylinder lot" in line.lower():
            if i + 1 < len(lines):
                candidate = lines[i + 1].strip()
                if re.match(r"^\d{6,}$", candidate):
                    lot = candidate
                    break
    if lot == "Unknown":
        lot_match = re.search(r"CHSB-\w+(?:-\d{2})+", text)
        if lot_match: lot = lot_match.group(0)
    serial = "Unknown"
    for i, line in enumerate(lines):
        if "serial number" in line.lower():
            if i + 1 < len(lines):
                serial = lines[i + 1].strip().replace(" ", "")
                break
    model = "Unknown"
    for i, line in enumerate(lines):
        if "honeywell" in line.lower() or "rigrat" in line.lower():
            model = line.strip()
            break
    cal_date = exp_date = "Invalid"
    date_pattern = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
    all_dates = []
    for line in lines:
        matches = re.findall(date_pattern, line, re.IGNORECASE)
        all_dates.extend(matches)
    if len(all_dates) >= 2:
        cal_date, exp_date = all_dates[0], all_dates[1]
    elif all_dates: cal_date = all_dates[0]
    data = {
        "cert": cert_val,
        "model": model,
        "serial": serial,
        "cal": format_date(cal_date) if cal_date != "Invalid" else "Invalid",
        "exp": format_date(exp_date) if exp_date != "Invalid" else "Invalid",
        "lot": lot
    }
    return [data]

def extract_smoke_hood(text, lines):
    norm = [l.strip() for l in lines]
    cert_match = re.search(r"\b\d{1,3}/\d{5}/\d{4}\.SRV\b", text)
    cert = cert_match.group(0) if cert_match else "Unknown"
    report = next((l for l in norm if re.match(r"^CHSB-[A-Z]+-\d{2}-\d{2}$", l)), "Unknown")
    model = next((l for l in norm if "draeger" in l.lower() or "parat" in l.lower()), "Unknown")
    date_re = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
    dates = re.findall(date_re, text)
    cal_date = dates[0] if len(dates) > 0 else "Invalid"
    exp_date = dates[1] if len(dates) > 1 else "Invalid"
    serial = "Unknown"
    bad_words = {"remarks", "good", "done", "service technician"}
    idx = next((i for i, l in enumerate(norm) if "serial number" in l.lower()), -1)
    if idx != -1:
        for j in range(idx + 1, len(norm)):
            cand = norm[j]
            if not cand or cand == ":" or cand.lower() in bad_words: continue
            if re.match(r"^CHSB-\d{3,}$", cand):
                serial = cand
                break
            if re.fullmatch(r"[A-Z0-9-]{5,}", cand) and not re.match(r"^CHSB-[A-Z]+-\d{2}-\d{2}$", cand):
                serial = cand
                break
    data = {
        "cert": cert,
        "model": model,
        "serial": serial,
        "cal": format_date(cal_date) if cal_date != "Invalid" else "Invalid",
        "exp": format_date(exp_date) if exp_date != "Invalid" else "Invalid",
        "lot": report
    }
    return [data]

def extract_rescue_kit(text, lines):
    cert_match = re.search(r"\b\d{1,3}/\d{5}/\d{4}\.SRV\b", text)
    cert = cert_match.group(0) if cert_match else "Unknown"
    report_match = re.search(r"CHSB-\w+-\d{2}-\d{2}", text)
    report = report_match.group(0) if report_match else "Unknown"
    model = "Unknown"
    for i, l in enumerate(lines):
        if "brand/model" in l.lower() and i + 1 < len(lines):
            model = lines[i + 1].strip()
            break
    serials = re.findall(r"\d{7,8}:\d{3,4}", text)
    first_serial = serials[0] if serials else "Unknown"
    service_date = re.search(r"Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    next_date = re.search(r"Next Inspection Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    cal = format_date(service_date.group(1)) if service_date else "Invalid"
    exp = format_date(next_date.group(1)) if next_date else "Invalid"
    return [{
        "cert": cert,
        "model": model,
        "serial": first_serial,
        "cal": cal,
        "exp": exp,
        "lot": report
    }]

def extract_gas_detector(text, lines):
    cert = "Unknown"
    for line in lines:
        match = re.search(r"(\d{1,3}/\d{1,5}/\d{4}\.SRV)", line)
        if match:
            cert = match.group(1)
            break
            
    lot = "Unknown"
    for i, line in enumerate(lines):
        if "cylinder lot#" in line.lower():
            if i + 1 < len(lines):
                lot_candidate = lines[i + 1].strip()
                if re.match(r'^\d{6,}$', lot_candidate):
                    lot = lot_candidate
                    break
    if lot == "Unknown":
        for line in lines:
            match = re.search(r"CHSB-\w+(?:-\d{2})+", line)
            if match:
                lot = match.group(0)
                break
                
    serial = "Unknown"
    for i, line in enumerate(lines):
        if "serial number" in line.lower():
            if i + 1 < len(lines):
                candidate = lines[i + 1].strip()
                candidate_normalized = re.sub(r"\s*-\s*", "-", candidate, flags=re.IGNORECASE)
                match = (
                    re.search(r"[A-Z0-9]{2,}-\d{2,}", candidate_normalized, re.IGNORECASE)
                    or re.search(r"\d+[A-Z]+\-\d+", candidate_normalized, re.IGNORECASE)
                    or re.search(r"[A-Z0-9]{5,}", candidate_normalized, re.IGNORECASE)
                )
                if match: serial = match.group(0).replace(" ", "")
                break
                
    model = "Unknown"
    for i, line in enumerate(lines):
        if lines[i].strip() == serial and i - 1 >= 0:
            model_candidate = lines[i - 1].strip()
            if not re.search(r"serial number", model_candidate.lower()):
                model = model_candidate
                break
    if model == "Unknown":
        model_keywords = ["ISC", "Radius", "BZ1", "T40", "PDM+", "SAFEGAS", "MSA","HONEYWELL"]
        model = next((l.strip() for l in lines if any(k.lower() in l.lower() for k in model_keywords)), "Unknown")
        
    cal_date = exp_date = "Invalid"
    date_pattern = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
    all_dates = []
    for line in lines:
        matches = re.findall(date_pattern, line, re.IGNORECASE)
        all_dates.extend(matches)
    if len(all_dates) >= 2:
        cal_date, exp_date = all_dates[0], all_dates[1]
    elif all_dates: cal_date = all_dates[0]
    
    data = {
        "cert": cert,
        "model": model,
        "serial": serial,
        "cal": format_date(cal_date) if cal_date != "Invalid" else "Invalid",
        "exp": format_date(exp_date) if exp_date != "Invalid" else "Invalid",
        "lot": lot
    }
    return [data]

# ==========================================
# 4. MAIN PROCESSOR (For API)
# ==========================================

def process_pdf_text(file_path, is_service=False):
    """
    Reads PDF and returns extracted data.
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
    
    # Map template names to (Collection Name, Function)
    extractor_map = {
        "gas_detector": ("GD" + collection_suffix, extract_gas_detector),
        "eebd": ("EEBD" + collection_suffix, extract_eebd),
        "harness": ("HARNESS" + collection_suffix, extract_harness),
        "absorber": ("ABSORBER" + collection_suffix, extract_absorber),
        "smoke_hood": ("SMOKE HOOD" + collection_suffix, extract_smoke_hood),
        "scba": ("SCBA" + collection_suffix, extract_scba),
        "area_monitor": ("AREA MONITOR" + collection_suffix, extract_area_monitor),
        "rescue_kit": ("RESCUE KIT" + collection_suffix, extract_rescue_kit),
    }

    if template in extractor_map:
        coll_name, func = extractor_map[template]
        # Functions return a list [data_dict], we usually just want the first item for the API
        extracted_list = func(text, lines)
        return {
            "status": "success", 
            "type": template, 
            "collection": coll_name, 
            "data": extracted_list
        }
    
    return {"status": "unknown", "text_snippet": text[:500]}