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
<<<<<<< HEAD
from urllib.parse import quote_plus
from dotenv import load_dotenv

# 1. LOAD ENVIRONMENT VARIABLES
load_dotenv()

=======
from urllib.parse import quote_plus # FIXED: Critical import for URL encoding

# ==============================================================================
# 1. CONFIGURATION & DIRECTORIES
# ==============================================================================
# 2. FIREBASE INITIALIZATION
def get_firebase_db():
    if not _apps:
        # Get credentials from env or provide a fallback
        creds_json = os.getenv("FIREBASE_CREDENTIALS")
        if creds_json:
            # Handle both raw JSON and base64 encoded strings
            try:
                creds_dict = json.loads(creds_json)
            except:
                creds_dict = json.loads(base64.b64decode(creds_json))
            
            cred = credentials.Certificate(creds_dict)
            initialize_app(cred, {
                'storageBucket': os.getenv("FIREBASE_BUCKET", "qrcertificates-30ddb.firebasestorage.app")
            })
        else:
            print("❌ FIREBASE_CREDENTIALS missing from .env")
    
    return firestore.client(), storage.bucket()

# 3. GEMINI INITIALIZATION (Lazy Loading)
def get_gemini_client():
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    return genai.Client(api_key=key)
=======
# PRIORITY LIST: Fallback logic for when specific models hit rate limits
MODEL_PRIORITY = [
    "gemini-flash-latest",       # Stable 1.5 Flash
    "gemini-flash-lite-latest",  # High-efficiency Lite
    "gemini-2.0-flash",          # New 2.0 Flash
    "gemini-pro-latest",         # Stable 1.5 Pro
    "gemini-2.5-pro"             # Frontier Pro
]

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
client = None
if GEMINI_KEY:
    # Initializing modern google-genai Client
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
        try:
            firebase_dict = json.loads(base64.b64decode(FIREBASE_CREDENTIALS).decode("utf-8"))
            cred = credentials.Certificate(firebase_dict)
            initialize_app(cred, {'storageBucket': FIREBASE_BUCKET_NAME})
        except Exception as e:
            print(f"❌ Firebase Init Error: {e}")
            raise e
    return firestore.client(), storage.bucket()
>>>>>>> 8fa8b41733f2e0bbf3837a576bf6d53ecb6996a8

# ==============================================================================
# 3. PDF PROCESSING UTILITIES
# ==============================================================================
def sanitize_filename(name):
    return re.sub(r'[\\/:"*?<>|]', "_", str(name)).strip()

def split_pdf_to_pages(original_path):
    """Physically extracts each page from a PDF so each database entry has a unique file."""
    page_paths = []
    try:
        reader = PdfReader(original_path)
        for i, page in enumerate(reader.pages):
            output_filename = f"split_p{i}_{int(time.time())}.pdf"
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
# 4. OPTIMIZED AI EXTRACTION (SINGLE-CALL)
# ==============================================================================



def process_pdf_text(file_path, is_service=False, manual_type=None, model_index=0):
    """Sends merged PDF in ONE call. Falls back to next model if quota hit."""
    # Ensure pages are split so files are ready even if AI fails (for manual entry)
    pages_list = split_pdf_to_pages(file_path)
    
    if not client: 
        return {"status": "failed", "error": "AI client not ready"}
    
    if model_index >= len(MODEL_PRIORITY):
        return {
            "status": "failed", 
            "error": "All API models exhausted. Use Manual Entry fallback.",
            "can_manual": True,
            "temp_files": [{"page": p[1], "path": p[0]} for p in pages_list]
        }

    current_model = MODEL_PRIORITY[model_index]
    print(f"--- Attempting Single-Call Extraction with: {current_model} ---")

    try:
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        prompt = """
        Analyze every page of this safety certificate PDF. Identify every unique item.
        Return a JSON LIST of objects. Fields:
        - serial (6-digit serial)
        - model (Full brand description)
        - cal (Inspection Date YYYY-MM-DD)
        - exp (Expiry Date YYYY-MM-DD)
        - cert (Certificate No, stop after .SRV)
        - lot (Report or Lot Number)
        - page (1-indexed page number)
        - type (HARNESS, ABSORBER, GD, EEBD, SCBA, or SMOKE HOOD)
        """

        response = client.models.generate_content(
            model=current_model,
            contents=[types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"), prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        items = json.loads(response.text)
        cleaned_data = []
        for item in items:
            # Match the AI data to the physically split page file path
            item['local_split_path'] = next((p[0] for p in pages_list if p[1] == item.get('page')), file_path)
            
            if item.get("cert") and ".SRV" in item["cert"]:
                item["cert"] = item["cert"].split(".SRV")[0] + ".SRV"
            
            b_type = str(item.get("type", "PPE")).upper().replace("_", " ")
            item["target_collection"] = b_type + ("_SERVICE" if is_service else "")
            cleaned_data.append(item)

        return {"status": "success", "data": cleaned_data}

    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            print(f"⚠️ {current_model} quota hit. Retrying with next model...")
            return process_pdf_text(file_path, is_service, manual_type, model_index + 1)
        
        print(f"❌ AI Error ({current_model}): {e}")
        return {"error": error_msg, "status": "failed", "can_manual": True}

# ==============================================================================
# 5. FIXED FIREBASE & SAVE LOGIC
# ==============================================================================
def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    """Corrected Firestore function with proper doc_ref variable definition."""
    try:
        db, _ = get_firebase_db()
        if not db: return False
        
        # FIXED: Variable definition order to prevent NameError
        doc_id = sanitize_filename(serial)
        doc_ref = db.collection(collection_name).document(doc_id)
        
        doc_data = {
            "serial": serial, "cert": data.get("cert", ""), "model": data.get("model", ""),
            "calibration_date": data.get("cal", ""), "expiry_date": data.get("exp", ""),
            "lot": data.get("lot", ""), "pdf_url": pdf_url, "qr_image_url": qr_url,
            "qr_link": qr_link, "last_updated": firestore.SERVER_TIMESTAMP,
            "source_page": data.get("page", 1)
        }
        
        doc_ref.set(doc_data, merge=True)
        return True
    except Exception as e:
        print(f"❌ Firestore Error: {e}")
        return False

def generate_qr_image_only(serial, link):
    safe_serial = sanitize_filename(serial)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(link); qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    
    final = Image.new("RGBA", (500, 600), "white")
    final.paste(qr_img.resize((500, 500)), (0, 0))
    # Using quote_plus indirectly through the link generation logic
    ImageDraw.Draw(final).text((20, 530), f"SN: {serial}", fill="black")
    
    path = os.path.join(QR_DIR, f"qr_{safe_serial}.png")
    final.convert("RGB").save(path)
    return path

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
