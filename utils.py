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
from urllib.parse import quote_plus

# ==============================================================================
# 1. CONFIGURATION & DIRECTORIES
# ==============================================================================
# Render requires /tmp for write permissions
QR_DIR = "/tmp/qrcodes"
SPLIT_DIR = "/tmp/temp_split_certs"

for d in [QR_DIR, SPLIT_DIR]:
    if not os.path.exists(d):
        os.makedirs(d, mode=0o777, exist_ok=True)

# FIXED: Verified stable aliases to prevent 404 errors.
# We use a priority list to automatically fallback if a model is exhausted.
MODEL_PRIORITY = [
    "gemini-flash-latest",       # Points to stable 1.5 Flash
    "gemini-flash-lite-latest",  # High-efficiency Flash Lite
    "gemini-2.0-flash",          # New 2.0 version
    "gemini-pro-latest",         # Stable 1.5 Pro
    "gemini-2.5-pro"             # Frontier model
]

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
client = None
if GEMINI_KEY:
    # Modern google-genai Client initialization
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
    """Sanitizes strings for safe file naming and Firestore IDs."""
    return re.sub(r'[\\/:"*?<>|]', "_", str(name)).strip()

def split_pdf_to_pages(original_path):
    """
    Physically extracts each page from a PDF. 
    This allows each SN in the database to link to only its relevant page.
    """
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
        print(f"❌ PDF Split Error: {e}")
        return []

# ==============================================================================
# 4. AI EXTRACTION (STRATEGY A: OPTIMIZED SINGLE-CALL)
# ==============================================================================



def process_pdf_text(file_path, is_service=False, manual_type=None, model_index=0):
    """
    Tries extraction using MODEL_PRIORITY. Sends entire merged PDF in ONE call.
    This makes your daily quota last 4x-10x longer.
    """
    if not client: 
        return {"status": "failed", "error": "AI client not ready"}
    
    if model_index >= len(MODEL_PRIORITY):
        return {"status": "failed", "error": "All Gemini models exhausted."}

    current_model = MODEL_PRIORITY[model_index]
    print(f"--- Attempting Single-Call Extraction with: {current_model} ---")

    try:
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        prompt = """
        Analyze every page of this safety certificate PDF. 
        Identify every unique equipment item (usually one per page).
        
        Return a JSON LIST of objects.
        Fields for each object:
        - serial: 6-digit numeric serial number
        - model: Full brand and model description
        - cal: Inspection Date (YYYY-MM-DD)
        - exp: Next Inspection Date (YYYY-MM-DD)
        - cert: Certificate Number (Stop after .SRV)
        - lot: Report or Lot Number
        - page: The page number where this specific item was found (1-indexed)
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
        
        # Slicing PDF after AI extraction so we have unique links for the dashboard
        pages_dict = {p_path: p_num for p_path, p_num in split_pdf_to_pages(file_path)}

        cleaned_data = []
        for item in items:
            # Map the item to the specific split PDF page file
            item['local_split_path'] = next(
                (path for path, num in pages_dict.items() if num == item.get('page')), 
                file_path
            )
            
            # Metadata cleanup
            if item.get("cert") and ".SRV" in item["cert"]:
                item["cert"] = item["cert"].split(".SRV")[0] + ".SRV"
            
            # Dynamic routing based on PPE type
            b_type = str(item.get("type", "UNKNOWN")).upper().replace("_", " ")
            item["target_collection"] = b_type + ("_SERVICE" if is_service else "")
            
            cleaned_data.append(item)

        return {"status": "success", "data": cleaned_data, "model_used": current_model}

    except Exception as e:
        error_msg = str(e)
        # Automatic fallback for Quota errors (429)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            print(f"⚠️ {current_model} exhausted. Falling back to next model...")
            return process_pdf_text(file_path, is_service, manual_type, model_index + 1)
        
        print(f"❌ AI Error ({current_model}): {e}")
        return {"error": error_msg, "status": "failed"}

# ==============================================================================
# 5. STORAGE & FIRESTORE UTILITIES
# ==============================================================================
def upload_to_firebase_storage(local_path, serial, is_qr=False):
    """Uploads split certificates or QR images to Firebase Storage."""
    try:
        _, bucket = get_firebase_db()
        safe_serial = sanitize_filename(serial)
        ts = int(time.time())
        blob_name = f"qr_codes/qr_{safe_serial}.png" if is_qr else f"certificates/{safe_serial}_{ts}.pdf"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_path)
        blob.make_public()
        return blob.public_url
    except Exception as e:
        print(f"❌ Firebase Storage Error: {e}")
        return None

def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    """Creates/Updates the PPE record in the specific Firestore folder."""
    try:
        db, _ = get_firebase_db()
        if not db: return False
        
        # FIXED: Ensure doc_ref is defined before setting data
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
        print(f"❌ Firestore Sync Error: {e}")
        return False

# ==============================================================================
# 6. QR GENERATION
# ==============================================================================
def generate_qr_image_only(serial, link):
    """Generates a high-quality QR code with SN label for labels."""
    safe_serial = sanitize_filename(serial)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(link)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_img = qr_img.resize((500, 500), Image.Resampling.LANCZOS)
    
    # Adding extra space at bottom for SN text
    final = Image.new("RGBA", (500, 600), "white")
    final.paste(qr_img, (0, 0))
    draw = ImageDraw.Draw(final)
    # Positioning the SN label
    draw.text((20, 530), f"SN: {serial}", fill="black")
    
    path = os.path.join(QR_DIR, f"qr_{safe_serial}.png")
    final.convert("RGB").save(path)
    return path
