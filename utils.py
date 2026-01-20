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
import google.generativeai as genai  # Reverting to the stable namespace

# ==========================================
# 1. CONFIGURATION
# ==========================================
QR_DIR = "qrcodes"
os.makedirs(QR_DIR, exist_ok=True)

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
else:
    print("⚠️ WARNING: GEMINI_API_KEY is missing!")

# ==========================================
# 2. FIREBASE SETUP
# ==========================================
def get_firebase_db():
    FIREBASE_BUCKET_NAME = os.getenv("FIREBASE_BUCKET")
    FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS")

    if not _apps:
        if not FIREBASE_CREDENTIALS:
            return None, None
        try:
            firebase_dict = json.loads(base64.b64decode(FIREBASE_CREDENTIALS).decode("utf-8"))
            cred = credentials.Certificate(firebase_dict)
            initialize_app(cred, {'storageBucket': FIREBASE_BUCKET_NAME})
        except Exception as e:
            print(f"❌ Firebase Init Error: {e}")
            raise e
    return firestore.client(), storage.bucket()

# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================
def sanitize_filename(name):
    # Fixed SyntaxWarning by using a raw string r''
    return re.sub(r'[\\/:"*?<>|]', "_", str(name)).strip()

def generate_qr_image_only(serial, link):
    safe_serial = sanitize_filename(serial)
    size = 500
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(link)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_img = qr_img.resize((size, size), Image.Resampling.LANCZOS)

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
# 4. EXTRACTION ENGINE (MULTI-PAGE READY)
# ==========================================



def extract_with_gemini(file_path, manual_hint=None):
    """
    Uses the stable google-generativeai library.
    Processes the PDF and enforces a JSON LIST return format.
    """
    try:
        # Upload the file to Google's temporary storage
        sample_file = genai.upload_file(path=file_path, display_name="Certificate")
        
        # Wait for processing
        while sample_file.state.name == "PROCESSING":
            time.sleep(2)
            sample_file = genai.get_file(sample_file.name)

        model = genai.GenerativeModel("gemini-1.5-flash")
        
        hint_text = f"Context: This is a '{manual_hint}' document." if manual_hint else ""

        prompt = f"""
        Extract safety technical data from EVERY page of this PDF.
        {hint_text}
        
        Return a JSON LIST of objects. 
        If the PDF contains a Harness on Page 1 and an Absorber on Page 2, return a list of 2 objects.

        Fields required per object:
        - serial (6-digit numeric for WORKGARD or alphanumeric)
        - model (Full Brand/Model name)
        - cal (Date of inspection YYYY-MM-DD)
        - exp (Next inspection/Expiry YYYY-MM-DD)
        - cert (Certificate ID, truncate after .SRV)
        - lot (Report or Lot Number)
        - type (Classify: HARNESS, ABSORBER, GD, EEBD, SCBA, SMOKE HOOD, AREA MONITOR)
        - page (Which page number was this found on)

        Return ONLY raw JSON.
        """

        response = model.generate_content([sample_file, prompt])
        
        # Clean the AI response text
        text_response = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text_response)
        
        # Ensure we always return a list to the processor
        if isinstance(data, dict):
            return [data]
        return data

    except Exception as e:
        print(f"❌ Gemini AI Error: {e}")
        return None

# ==========================================
# 5. MAIN PROCESSOR
# ==========================================
def process_pdf_text(file_path, is_service=False, manual_type=None):
    """
    Orchestrates the extraction and cleanup.
    Returns the array of items for the frontend multi-save loop.
    """
    try:
        items_list = extract_with_gemini(file_path, manual_hint=manual_type)
        
        if not items_list:
            return {"status": "failed", "error": "AI could not extract data"}

        cleaned_data = []
        for item in items_list:
            # FIX: Truncate cert at .SRV
            if item.get("cert") and ".SRV" in item["cert"]:
                item["cert"] = item["cert"].split(".SRV")[0] + ".SRV"
            
            # Use manual type if provided, else AI type
            final_type = manual_type if manual_type else item.get("type", "UNKNOWN")
            item["type"] = final_type.upper().replace("_", " ")
            cleaned_data.append(item)

        # Collection name based on the first item's type
        primary_type = cleaned_data[0]["type"]
        final_collection = primary_type + ("_SERVICE" if is_service else "")

        return {
            "status": "success",
            "type": primary_type,
            "collection": final_collection,
            "data": cleaned_data 
        }

    except Exception as e:
        print(f"❌ Processing Error: {e}")
        return {"error": str(e), "status": "failed"}

# ==========================================
# 6. FIREBASE UPDATERS
# ==========================================
def upload_to_firebase_storage(path, serial, is_qr=False):
    try:
        _, bucket = get_firebase_db()
        if not bucket: return None
        safe_serial = sanitize_filename(serial)
        # Use timestamp to avoid cache issues for PDFs
        ts = int(time.time())
        blob_name = f"qr_codes/qr_{safe_serial}.png" if is_qr else f"certificates/{safe_serial}_{ts}.pdf"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(path)
        blob.make_public()
        return blob.public_url
    except Exception as e:
        return None

def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    try:
        db, _ = get_firebase_db()
        if not db: return False
        safe_doc_id = sanitize_filename(serial)
        doc_ref = db.collection(collection_name).document(safe_doc_id)
        
        doc_data = {
            "cert": data.get("cert", ""), 
            "model": data.get("model", ""),
            "serial": serial, 
            "calibration_date": data.get("cal", ""),
            "expiry_date": data.get("exp", ""), 
            "lot": data.get("lot", ""),
            "pdf_url": pdf_url, 
            "qr_image_url": qr_url, 
            "qr_link": qr_link,
            "source_page": data.get("page", 1),
            "last_updated": firestore.SERVER_TIMESTAMP
        }
        doc_ref.set(doc_data, merge=True)
        return True
    except Exception as e:
        return False
