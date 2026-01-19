import os
import re
import json
import base64
import qrcode
import time
import io
import google.generativeai as genai
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from firebase_admin import credentials, firestore, storage, initialize_app, _apps
import pdfplumber

# ==========================================
# 1. GLOBAL CONFIGURATION & DIRECTORIES
# ==========================================
QR_DIR = "qrcodes"
TEMP_DIR = "temp_processing"
os.makedirs(QR_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Initialize Google Gemini AI
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
else:
    print("⚠️ CRITICAL WARNING: GEMINI_API_KEY is missing from environment variables!")

# ==========================================
# 2. FIREBASE CORE INITIALIZATION
# ==========================================
def get_firebase_db():
    """
    Initializes and returns Firestore client and Storage bucket.
    Uses Base64 encoded credentials for secure environment deployment.
    """
    FIREBASE_BUCKET_NAME = os.getenv("FIREBASE_BUCKET")
    FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")

    if not _apps:
        if not FIREBASE_CREDENTIALS:
            print("❌ ERROR: FIREBASE_CREDENTIALS missing.")
            return None, None
        try:
            # Decode the base64 credential string into JSON
            firebase_dict = json.loads(base64.b64decode(FIREBASE_CREDENTIALS).decode("utf-8"))
            cred = credentials.Certificate(firebase_dict)
            initialize_app(cred, {'storageBucket': FIREBASE_BUCKET_NAME})
            print("🔥 Firebase Application Initialized Successfully")
        except Exception as e:
            print(f"❌ Firebase Initialization Error: {e}")
            return None, None
            
    return firestore.client(), storage.bucket()

# ==========================================
# 3. FILENAME & QR GENERATION UTILITIES
# ==========================================
def sanitize_filename(name):
    """Removes or replaces characters that are illegal in file paths and Firestore IDs."""
    return re.sub(r'[\\/:"*?<>|]', "_", str(name)).strip()

def generate_qr_with_label(serial, link):
    """
    Generates a high-resolution QR code with a Serial Number label at the bottom.
    Optimized for physical tag printing and scanning.
    """
    safe_serial = sanitize_filename(serial)
    qr_size = 500
    label_height = 100
    
    # Configure QR Code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(link)
    qr.make(fit=True)

    # Create QR Image
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_img = qr_img.resize((qr_size, qr_size), Image.Resampling.LANCZOS)

    # Create Label Background
    final_img = Image.new("RGBA", (qr_size, qr_size + label_height), "white")
    final_img.paste(qr_img, (0, 0))

    # Draw Label Text (Serial Number)
    draw = ImageDraw.Draw(final_img)
    try:
        # Attempt to load a clean font, fallback to default if not found
        font = ImageFont.truetype("arial.ttf", 40)
    except:
        font = ImageFont.load_default()

    text = f"SN: {serial}"
    # Center text horizontally
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((qr_size - w) / 2, qr_size + (label_height - h) / 2 - 10), text, fill="black", font=font)

    # Save to local cache
    path = os.path.join(QR_DIR, f"qr_{safe_serial}.png")
    final_img.convert("RGB").save(path)
    return path

# ==========================================
# 4. AI EXTRACTION LOGIC (MULTI-PAGE AWARE)
# ==========================================



def extract_content_with_ai(file_path, manual_hint=None):
    """
    Sends the entire PDF to Gemini 1.5 Flash to identify all PPE items.
    Returns a LIST of items, even if only one is found.
    """
    if not os.getenv("GEMINI_API_KEY"):
        return None

    try:
        print(f"📤 Uploading {file_path} to Gemini for Multi-Page Analysis...")
        sample_file = genai.upload_file(path=file_path, display_name="Multi-Page Certificate")
        
        while sample_file.state.name == "PROCESSING":
            time.sleep(1)
            sample_file = genai.get_file(sample_file.name)

        model = genai.GenerativeModel(model_name="gemini-1.5-flash")
        
        prompt = f"""
        Analyze this document and extract technical safety data for EVERY piece of equipment listed. 
        If the PDF contains a Harness on Page 1 and an Absorber on Page 2, you must return a LIST of two JSON objects.
        
        Required fields for each object:
        - serial (The serial number, often numeric like '000186')
        - model (Full brand and equipment description)
        - cal (Inspection/Calibration date in YYYY-MM-DD)
        - exp (Next inspection date in YYYY-MM-DD)
        - cert (The Certificate No., e.g., '1/0016/2026.SRV')
        - lot (The Report No. or Lot No.)
        - type (Classify into: ['HARNESS', 'ABSORBER', 'GD', 'EEBD', 'SCBA', 'SMOKE HOOD', 'AREA MONITOR', 'RESCUE KIT'])
        - page (The page number where this specific item was found)

        Hint from user: {manual_hint if manual_hint else "Auto-detect PPE category"}
        Return ONLY a raw JSON list.
        """

        response = model.generate_content([sample_file, prompt])
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        
        extracted_list = json.loads(clean_json)
        
        # Critical Fix: Ensure the response is always a list for the frontend loop
        if isinstance(extracted_list, dict):
            return [extracted_list]
        return extracted_list

    except Exception as e:
        print(f"❌ AI Engine Error: {e}")
        return None

# ==========================================
# 5. MAIN PROCESSOR & CLEANER
# ==========================================
def process_pdf_text(file_path, is_service=False, manual_type=None):
    """
    Primary processing pipeline. 
    1. Extracts data from all pages.
    2. Cleans certificate numbers (stops at .SRV).
    3. Normalizes types for database collection mapping.
    """
    try:
        items = extract_content_with_ai(file_path, manual_hint=manual_type)
        
        if not items:
            return {"status": "failed", "error": "AI failed to parse document."}

        cleaned_items = []
        for item in items:
            # Clean Certificate String (Truncate extra text after .SRV)
            if item.get("cert") and ".SRV" in item["cert"]:
                item["cert"] = item["cert"].split(".SRV")[0] + ".SRV"

            # Normalize data types for specific collection naming
            raw_type = item.get("type", "GENERAL").upper().replace("_", " ")
            type_map = {"GAS DETECTOR": "GD", "SMOKE_HOOD": "SMOKE HOOD"}
            item["type"] = type_map.get(raw_type, raw_type)
            
            cleaned_items.append(item)

        # Map the primary collection name based on the first item detected
        primary_type = cleaned_items[0]["type"]
        final_collection = primary_type + ("_SERVICE" if is_service else "")

        return {
            "status": "success",
            "type": primary_type,
            "collection": final_collection,
            "data": cleaned_items  # Array of items for the frontend multi-save loop
        }

    except Exception as e:
        print(f"❌ Processor Exception: {e}")
        return {"status": "failed", "error": str(e)}

# ==========================================
# 6. STORAGE & FIRESTORE INTEGRATION
# ==========================================
def upload_to_firebase_storage(local_path, serial, is_qr=False):
    """Uploads files to Firebase Cloud Storage and makes them public."""
    try:
        _, bucket = get_firebase_db()
        if not bucket: return None
        
        safe_serial = sanitize_filename(serial)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if is_qr:
            blob_path = f"qr_codes/qr_{safe_serial}.png"
        else:
            blob_path = f"certificates/{safe_serial}_{timestamp}.pdf"
            
        blob = bucket.blob(blob_path)
        blob.upload_from_filename(local_path)
        blob.make_public()
        
        return blob.public_url
    except Exception as e:
        print(f"❌ Storage Upload Failed: {e}")
        return None

def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    """
    Creates or merges a record in Firestore for a specific Serial Number.
    """
    try:
        db, _ = get_firebase_db()
        if not db: return False
        
        safe_doc_id = sanitize_filename(serial)
        doc_ref = db.collection(collection_name).document(safe_doc_id)
        
        payload = {
            "serial": serial,
            "model": data.get("model", "N/A"),
            "cert": data.get("cert", "N/A"),
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
        print(f"❌ Firestore Document Update Failed: {e}")
        return False
