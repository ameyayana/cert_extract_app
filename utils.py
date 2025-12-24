import os
import re
import json
import base64
import qrcode
import google.generativeai as genai
from urllib.parse import quote_plus
from datetime import datetime
from PyPDF2 import PdfReader
from PIL import Image, ImageDraw, ImageFont
from firebase_admin import credentials, firestore, storage, initialize_app, _apps

# ==========================================
# 1. CONFIGURATION & AI SETUP
# ==========================================
QR_DIR = "qrcodes"
os.makedirs(QR_DIR, exist_ok=True)

# Configure Gemini
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)

# ==========================================
# 2. FIREBASE SETUP
# ==========================================
def get_firebase_db():
    FIREBASE_BUCKET_NAME = os.getenv("FIREBASE_BUCKET")
    FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")

    if not _apps:
        if not FIREBASE_CREDENTIALS:
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
# 3. HELPER FUNCTIONS
# ==========================================
def sanitize_filename(name):
    return re.sub(r'[\\/:"*?<>|]', "_", str(name))

def generate_qr_image_only(serial, link):
    safe_serial = sanitize_filename(serial)
    size = 500
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(link)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_img = qr_img.resize((size, size), Image.Resampling.NEAREST)

    label_height = 100
    label = Image.new("RGBA", (size, label_height), "white")
    draw = ImageDraw.Draw(label)
    try: font = ImageFont.load_default()
    except: font = None

    draw.text((20, 30), f"SN: {serial}", fill="black", font=font)
    final = Image.new("RGBA", (size, size + label_height), "white")
    final.paste(qr_img, (0, 0))
    final.paste(label, (0, size))

    path = os.path.join(QR_DIR, f"qr_{safe_serial}.png")
    final.convert("RGB").save(path)
    return path

# ==========================================
# 4. FIREBASE UPLOADERS
# ==========================================
def upload_to_firebase_storage(path, serial, is_qr=False):
    try:
        _, bucket = get_firebase_db()
        if not bucket: return None
        safe_serial = sanitize_filename(serial)
        blob_name = f"qr_codes/qr_{safe_serial}.png" if is_qr else f"certificates/{safe_serial}.pdf"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(path)
        blob.make_public()
        return blob.public_url
    except Exception as e:
        print(f"Upload Error: {e}")
        return None

def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    try:
        db, _ = get_firebase_db()
        if not db: return False
        safe_doc_id = sanitize_filename(serial)
        doc_ref = db.collection(collection_name).document(safe_doc_id)
        doc_data = {
            "cert": data.get("cert", ""), "model": data.get("model", ""),
            "serial": serial, "calibration_date": data.get("cal", ""),
            "expiry_date": data.get("exp", ""), "lot": data.get("lot", ""),
            "pdf_url": pdf_url, "qr_image_url": qr_url, "qr_link": qr_link,
            "last_updated": firestore.SERVER_TIMESTAMP
        }
        doc_ref.set(doc_data, merge=True)
        return True
    except Exception as e:
        print(f"Firestore Error: {e}")
        return False

# ==========================================
# 5. AI EXTRACTION LOGIC
# ==========================================

def extract_with_gemini(text, manual_hint=None):
    """
    Sends PDF text to Google Gemini to extract JSON data.
    """
    if not os.getenv("GEMINI_API_KEY"):
        print("❌ No Gemini API Key found.")
        return None

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        hint_text = ""
        if manual_hint:
            hint_text = f"The user says this document is of type: '{manual_hint}'. Use this context."

        prompt = f"""
        Extract data from this technical certificate. Return ONLY raw JSON. No markdown formatting.
        
        {hint_text}
        
        Required JSON Fields:
        - serial (The primary Serial Number / ID)
        - model (Equipment Model Name)
        - cal (Calibration Date YYYY-MM-DD)
        - exp (Expiry/Next Due Date YYYY-MM-DD)
        - cert (Certificate Number)
        - lot (Lot Number / Report Number)
        - type (Classify into one of: ['GD', 'EEBD', 'HARNESS', 'ABSORBER', 'SMOKE HOOD', 'SCBA', 'AREA MONITOR', 'RESCUE KIT'])

        If a field is missing, use empty string "".
        
        TEXT CONTENT:
        {text[:15000]}
        """
        
        response = model.generate_content(prompt)
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)

    except Exception as e:
        print(f"AI Extraction Error: {e}")
        return None

# ==========================================
# 6. MAIN PROCESSOR
# ==========================================

def process_pdf_text(file_path, is_service=False, manual_type=None):
    """
    Reads PDF, asks AI for data, applies Service logic, and formats response.
    """
    try:
        # 1. Read Text
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            t = page.extract_text()
            if t: text += "\n" + t.strip()
            
        if not text:
            return {"status": "failed", "error": "No text found in PDF"}

        # 2. Extract using AI
        ai_data = extract_with_gemini(text, manual_hint=manual_type)
        
        if not ai_data:
            return {"status": "failed", "error": "AI extraction failed"}

        # 3. Determine Final Type
        base_type = manual_type if manual_type else ai_data.get("type", "UNKNOWN")
        
        # Normalize keys to standard folder names
        type_map = {
            "GAS DETECTOR": "GD", "GAS_DETECTOR": "GD",
            "AREA_MONITOR": "AREA MONITOR", "SMOKE_HOOD": "SMOKE HOOD",
            "RESCUE_KIT": "RESCUE KIT"
        }
        
        normalized_type = base_type.upper().replace("_", " ")
        if normalized_type in type_map:
            normalized_type = type_map[normalized_type]
            
        # 4. Apply Service Suffix logic
        final_collection = normalized_type
        if is_service:
            final_collection += "_SERVICE"

        # 5. Return in standard API format
        return {
            "status": "success",
            "type": normalized_type,
            "collection": final_collection,
            "data": [ai_data] # Return as list to match frontend expectations
        }

    except Exception as e:
        return {"error": str(e), "status": "failed"}