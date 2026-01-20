import os
import re
import json
import base64
import qrcode
import time
import io
import concurrent.futures 
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from firebase_admin import credentials, firestore, storage, initialize_app, _apps
from pypdf import PdfReader, PdfWriter 
from google import genai
from google.genai import types

# ==============================================================================
# 1. CONFIGURATION & DIRECTORIES
# ==============================================================================
QR_DIR = "/tmp/qrcodes"
SPLIT_DIR = "/tmp/temp_split_certs"

for d in [QR_DIR, SPLIT_DIR]:
    if not os.path.exists(d):
        os.makedirs(d, mode=0o777, exist_ok=True)

# FIXED: Using verified aliases from your ListModels output to prevent 404 errors.
# We prioritize "Flash" models to preserve your Pro tier quota for complex tasks.
MODEL_PRIORITY = [
    "gemini-flash-latest",       # Points to stable 1.5 Flash
    "gemini-flash-lite-latest",  # High-efficiency Flash Lite
    "gemini-2.0-flash",          # New 2.0 version
    "gemini-pro-latest",         # Points to stable 1.5 Pro
    "gemini-2.5-pro"             # Latest frontier Pro model
]

# Set default model
GEMINI_MODEL = "gemini-flash-latest"

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
client = None
if GEMINI_KEY:
    # Proper Client initialization for the modern 'google-genai' SDK
    client = genai.Client(api_key=GEMINI_KEY)
else:
    print("⚠️ WARNING: GEMINI_API_KEY is missing!")

# ==============================================================================
# 2. FIREBASE SETUP
# ==============================================================================
def get_firebase_db():
    FIREBASE_BUCKET_NAME = os.getenv("FIREBASE_BUCKET")
    FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")

    if not _apps:
        if not FIREBASE_CREDENTIALS:
            print("❌ FIREBASE_CREDENTIALS missing.")
            return None, None
        try:
            firebase_dict = json.loads(base64.b64decode(FIREBASE_CREDENTIALS).decode("utf-8"))
            cred = credentials.Certificate(firebase_dict)
            initialize_app(cred, {'storageBucket': FIREBASE_BUCKET_NAME})
        except Exception as e:
            print(f"❌ Firebase Init Error: {e}")
            raise e
    return firestore.client(), storage.bucket()

# ==============================================================================
# 3. PDF PROCESSING & SPLITTING
# ==============================================================================
def sanitize_filename(name):
    return re.sub(r'[\\/:"*?<>|]', "_", str(name)).strip()

def split_pdf_to_pages(original_path):
    page_paths = []
    try:
        reader = PdfReader(original_path)
        for i, page in enumerate(reader.pages):
            output_filename = f"split_page_{i}_{int(time.time())}.pdf"
            output_path = os.path.join(SPLIT_DIR, output_filename)
            writer = PdfWriter()
            writer.add_page(page)
            with open(output_path, "wb") as f:
                writer.write(f)
            page_paths.append((output_path, i + 1))
        return page_paths
    except Exception as e:
        print(f"❌ Split Error: {e}")
        return []

# ==============================================================================
# 4. AI EXTRACTION (WITH FALLBACK LOGIC)
# ==============================================================================

def process_pdf_text(file_path, is_service=False, manual_type=None, model_index=0):
    """
    Tries extraction using MODEL_PRIORITY list. 
    If a model hits a quota limit (429), it automatically tries the next one.
    """
    if not client: 
        return {"status": "failed", "error": "AI client not ready"}
    
    # Check if we have exhausted our local list of models
    if model_index >= len(MODEL_PRIORITY):
        return {"status": "failed", "error": "All available Gemini models have exhausted their quota."}

    current_model = MODEL_PRIORITY[model_index]
    print(f"--- Attempting AI extraction with: {current_model} ---")

    try:
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        prompt = """
        Analyze every page of this safety certificate PDF. 
        Identify every unique equipment item (usually one per page).
        
        Return a JSON LIST of objects.
        Fields for each object:
        - serial: 6-digit serial number
        - model: Full brand and description
        - cal: Inspection Date (YYYY-MM-DD)
        - exp: Expiry Date (YYYY-MM-DD)
        - cert: Certificate No (Truncate after .SRV)
        - lot: Lot/Batch No
        - page: The page number where this item was found (1-indexed)
        - type: Classify as HARNESS, ABSORBER, GD, EEBD, SCBA, or SMOKE HOOD
        """

        response = client.models.generate_content(
            model=current_model,
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                prompt
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        items = json.loads(response.text)
        
        pages_dict = {p_path: p_num for p_path, p_num in split_pdf_to_pages(file_path)}

        cleaned_data = []
        for item in items:
            item['local_split_path'] = next((path for path, num in pages_dict.items() if num == item.get('page')), file_path)
            
            if item.get("cert") and ".SRV" in item["cert"]:
                item["cert"] = item["cert"].split(".SRV")[0] + ".SRV"
            
            b_type = item.get("type", "UNKNOWN").upper().replace("_", " ")
            item["target_collection"] = b_type + ("_SERVICE" if is_service else "")
            
            cleaned_data.append(item)

        return {"status": "success", "data": cleaned_data, "model_used": current_model}

    except Exception as e:
        error_msg = str(e)
        
        # Trigger Fallback if Quota is exhausted (HTTP 429)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            print(f"⚠️ {current_model} quota reached. Trying next model...")
            return process_pdf_text(file_path, is_service, manual_type, model_index + 1)
        
        print(f"❌ AI Extraction Error ({current_model}): {e}")
        return {"error": error_msg, "status": "failed"}

# ==============================================================================
# 5. FIREBASE STORAGE & FIRESTORE
# ==============================================================================
def upload_to_firebase_storage(local_path, serial, is_qr=False):
    try:
        _, bucket = get_firebase_db()
        safe_serial = sanitize_filename(serial)
        blob_name = f"qr_codes/qr_{safe_serial}.png" if is_qr else f"certificates/{safe_serial}_{int(time.time())}.pdf"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_path)
        blob.make_public()
        return blob.public_url
    except Exception as e:
        print(f"❌ Upload Error: {e}"); return None

def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    try:
        db, _ = get_firebase_db()
        if not db: return False
        
        doc_id = sanitize_filename(serial)
        doc_ref = db.collection(collection_name).document(doc_id)
        
        doc_data = {
            "serial": serial, 
            "cert": data.get("cert", ""), 
            "model": data.get("model", ""),
            "calibration_date": data.get("cal", ""), 
            "expiry_date": data.get("exp", ""),
            "lot": data.get("lot", ""), 
            "pdf_url": pdf_url, 
            "qr_image_url": qr_url,
            "qr_link": qr_link, 
            "last_updated": firestore.SERVER_TIMESTAMP,
            "source_page": data.get("page", 1)
        }
        
        doc_ref.set(doc_data, merge=True)
        return True
    except Exception as e:
        print(f"❌ Firestore Error: {e}")
        return False

# ==============================================================================
# 6. QR GENERATION
# ==============================================================================
def generate_qr_image_only(serial, link):
    safe_serial = sanitize_filename(serial)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(link); qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_img = qr_img.resize((500, 500), Image.Resampling.LANCZOS)
    
    final = Image.new("RGBA", (500, 600), "white")
    final.paste(qr_img, (0, 0))
    draw = ImageDraw.Draw(final)
    draw.text((20, 530), f"SN: {serial}", fill="black")
    
    path = os.path.join(QR_DIR, f"qr_{safe_serial}.png")
    final.convert("RGB").save(path)
    return path
