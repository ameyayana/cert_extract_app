import os
import re
import json
import base64
import qrcode
import time
import io
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from firebase_admin import credentials, firestore, storage, initialize_app, _apps
import pdfplumber
from google import genai 
from google.genai import types

# ==============================================================================
# 1. GLOBAL CONFIGURATION & SYSTEM PATHS
# ==============================================================================
# Directory for local QR code caching before Firebase upload
QR_DIR = "qrcodes"
# Directory for temporary file processing
TEMP_DIR = "temp_processing"

os.makedirs(QR_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Initialize Google Gemini AI Client with Modern SDK
client = None
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    # Use the new genai.Client for improved performance and JSON enforcement
    client = genai.Client(api_key=GEMINI_KEY)
    print("🤖 Gemini AI Engine Initialized Successfully")
else:
    print("⚠️ CRITICAL: GEMINI_API_KEY is missing from Environment Variables!")

# ==============================================================================
# 2. FIREBASE CORE INITIALIZATION
# ==============================================================================
def get_firebase_db():
    """
    Initializes and returns Firestore client and Storage bucket.
    Uses Base64 encoded credentials for secure production deployment.
    """
    FIREBASE_BUCKET_NAME = os.getenv("FIREBASE_BUCKET")
    FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")

    if not _apps:
        if not FIREBASE_CREDENTIALS:
            print("❌ FATAL: FIREBASE_CREDENTIALS environment variable not found.")
            return None, None
        try:
            # Decode the base64 credential string into valid JSON dictionary
            firebase_dict = json.loads(base64.b64decode(FIREBASE_CREDENTIALS).decode("utf-8"))
            cred = credentials.Certificate(firebase_dict)
            initialize_app(cred, {'storageBucket': FIREBASE_BUCKET_NAME})
            print("🔥 Firebase Cloud Environment Connected")
        except Exception as e:
            print(f"❌ Firebase Connection Failed: {str(e)}")
            raise e
            
    return firestore.client(), storage.bucket()

# ==============================================================================
# 3. FILENAME & QR GENERATION UTILITIES
# ==============================================================================
def sanitize_filename(name):
    """
    Removes illegal characters to prevent file system and Firestore document ID errors.
    Target characters: \ / : * ? < > |
    """
    return re.sub(r'[\\/:"*?<>|]', "_", str(name)).strip()

def generate_qr_image_only(serial, link):
    """
    Generates a high-resolution QR code (500x600) including a 
    text label for the Serial Number at the bottom.
    """
    safe_serial = sanitize_filename(serial)
    size = 500
    label_height = 100
    
    # Configure QR Generator parameters
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4
    )
    qr.add_data(link)
    qr.make(fit=True)
    
    # Render QR matrix to PIL image
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_img = qr_img.resize((size, size), Image.Resampling.LANCZOS)

    # Prepare final canvas with space for label
    final = Image.new("RGBA", (size, size + label_height), "white")
    final.paste(qr_img, (0, 0))
    
    # Draw text label
    draw = ImageDraw.Draw(final)
    try:
        # Load font if available in environment, otherwise use default
        font = ImageFont.load_default() 
    except:
        font = None

    text = f"SN: {serial}"
    draw.text((20, size + 20), text, fill="black", font=font)

    # Save and return path
    path = os.path.join(QR_DIR, f"qr_{safe_serial}.png")
    final.convert("RGB").save(path)
    return path

# ==============================================================================
# 4. AI EXTRACTION ENGINE (MULTI-PAGE AWARE)
# ==============================================================================


def extract_with_gemini(file_path, manual_hint=None):
    """
    Processes the PDF using Gemini 1.5 Flash Vision.
    Enforces JSON Schema to ensure the backend receives a list of assets.
    """
    if not client:
        return None

    try:
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        hint_text = f"The documents provided are: {manual_hint}." if manual_hint else ""

        # Construct specific prompt for Industrial Equipment Safety Certificates
        prompt = f"""
        Act as an expert technical data extractor for industrial PPE certificates.
        {hint_text}
        
        Analyze every page of this PDF. If multiple items (e.g. Page 1 Harness, Page 2 Absorber) are found, 
        return a JSON LIST containing an object for each unique serial number.

        JSON SCHEMA PER OBJECT:
        - [cite_start]serial: Primary Serial Number (e.g., '000186') [cite: 22, 23]
        - [cite_start]model: Equipment Brand and Model (e.g., 'WORKGARD Body Harness') [cite: 14, 17]
        - [cite_start]cal: Inspection/Calibration Date in YYYY-MM-DD format [cite: 136]
        - [cite_start]exp: Next Inspection/Expiry Date in YYYY-MM-DD format [cite: 137]
        - [cite_start]cert: Full Certificate Number, but STOP and truncate after '.SRV' [cite: 15]
        - [cite_start]lot: Report Number or Lot Number [cite: 18, 19]
        - page: The specific page number this item was extracted from.
        - type: Classify as one of: HARNESS, ABSORBER, GD, EEBD, SCBA, SMOKE HOOD, AREA MONITOR.

        Return ONLY a JSON list. If only one item is found, still return it in a list [{{...}}].
        """

        # Call Gemini with strict JSON enforcement
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                prompt
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        
        # Parse result
        extracted_data = json.loads(response.text)
        
        # CRITICAL FIX: Ensure return is a list to prevent 'dict' has no attribute 'get' error
        if isinstance(extracted_data, dict):
            return [extracted_data]
        return extracted_data

    except Exception as e:
        print(f"❌ Gemini Extraction Exception: {str(e)}")
        return None

# ==============================================================================
# 5. MAIN PROCESSOR & PIPELINE
# ==============================================================================
def process_pdf_text(file_path, is_service=False, manual_type=None):
    """
    Orchestrates the extraction and normalization pipeline.
    Ensures multi-page documents result in multi-item entries.
    """
    try:
        # Get raw data list from AI
        raw_items_list = extract_with_gemini(file_path, manual_hint=manual_type)
        
        if not raw_items_list:
            return {"status": "failed", "error": "AI Engine could not process document."}

        # Normalization and Cleaning
        processed_data = []
        for item in raw_items_list:
            # Type classification logic
            base_type = manual_type if manual_type else item.get("type", "GENERAL")
            normalized_type = base_type.upper().replace("_", " ")
            item["type"] = normalized_type
            processed_data.append(item)

        # Collection mapping based on first detected asset
        primary_collection = processed_data[0]["type"]
        if is_service:
            primary_collection += "_SERVICE"

        return {
            "status": "success",
            "type": processed_data[0]["type"],
            "collection": primary_collection,
            "data": processed_data # Frontend loops through this array
        }

    except Exception as e:
        print(f"❌ Pipeline Failure: {str(e)}")
        return {"error": str(e), "status": "failed"}

# ==============================================================================
# 6. FIREBASE UPDATERS
# ==============================================================================
def upload_to_firebase_storage(file_path, serial, is_qr=False):
    """Uploads binary assets to Cloud Storage and generates public access URLs."""
    try:
        _, bucket = get_firebase_db()
        if not bucket: return None
        
        safe_serial = sanitize_filename(serial)
        blob_path = f"qr_codes/qr_{safe_serial}.png" if is_qr else f"certificates/{safe_serial}.pdf"
        
        blob = bucket.blob(blob_path)
        blob.upload_from_filename(file_path)
        blob.make_public()
        
        return blob.public_url
    except Exception as e:
        print(f"❌ Storage Upload Error: {str(e)}")
        return None

def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    """Atomic write/merge operation for Firestore entries."""
    try:
        db, _ = get_firebase_db()
        if not db: return False
        
        safe_id = sanitize_filename(serial)
        doc_ref = db.collection(collection_name).document(safe_id)
        
        payload = {
            "serial": serial,
            "cert": data.get("cert", "N/A"),
            "model": data.get("model", "N/A"),
            "calibration_date": data.get("cal", ""),
            "expiry_date": data.get("exp", ""),
            "lot": data.get("lot", "N/A"),
            "pdf_url": pdf_url,
            "qr_image_url": qr_url,
            "qr_link": qr_link,
            "source_page": data.get("page", 1),
            "last_updated": firestore.SERVER_TIMESTAMP
        }
        
        doc_ref.set(payload, merge=True)
        return True
    except Exception as e:
        print(f"❌ Firestore Sync Error: {str(e)}")
        return False
