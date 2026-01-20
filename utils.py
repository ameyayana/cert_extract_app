import os
import re
import json
import base64
import qrcode
import time
import io
import concurrent.futures 
import google.generativeai as genai
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from firebase_admin import credentials, firestore, storage, initialize_app, _apps
from pypdf import PdfReader, PdfWriter 

# ==========================================
# 1. CONFIGURATION & DIRECTORIES
# ==========================================
# Using /tmp ensures write permissions on Render's ephemeral filesystem
QR_DIR = "/tmp/qrcodes"
SPLIT_DIR = "/tmp/temp_split_certs"
os.makedirs(QR_DIR, exist_ok=True)
os.makedirs(SPLIT_DIR, exist_ok=True)

GEMINI_MODEL = "gemini-1.5-flash-latest" 

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
else:
    print("⚠️ WARNING: GEMINI_API_KEY is missing!")

# ==========================================
# 2. FIREBASE SETUP
# ==========================================
def get_firebase_db():
    """Initializes Firebase using Base64 encoded credentials."""
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

# ==========================================
# 3. PDF PROCESSING UTILITIES
# ==========================================
def sanitize_filename(name):
    """Sanitizes strings for safe file naming and Firestore IDs."""
    return re.sub(r'[\\/:"*?<>|]', "_", str(name)).strip()

def split_pdf_to_pages(original_path):
    """
    Physically extracts each page from a merged PDF and saves as individual files.
    This ensures each asset has its own unique certificate link.
    """
    page_paths = []
    try:
        reader = PdfReader(original_path)
        for i, page in enumerate(reader.pages):
            output_filename = f"split_page_{i}_{int(time.time())}.pdf"
            output_path = os.path.join(SPLIT_DIR, output_filename)
            
            writer = PdfWriter()
            writer.add_page(page)
            
            with open(output_path, "wb") as output_file:
                writer.write(output_file)
            page_paths.append((output_path, i + 1)) 
        return page_paths
    except Exception as e:
        print(f"❌ PDF Splitting Error: {e}")
        return []

# ==========================================
# 4. AI EXTRACTION ENGINE (PARALLEL READY)
# ==========================================
def extract_single_page_data(page_info, manual_hint=None):
    """Sends a single-page PDF to Gemini for high-speed technical extraction."""
    file_path, page_num = page_info
    try:
        sample_file = genai.upload_file(path=file_path)
        model = genai.GenerativeModel(model_name=GEMINI_MODEL)
        
        prompt = f"""
        Extract safety technical data from this certificate. Return ONLY raw JSON.
        Fields:
        - serial: Primary ID (Numeric for Workgard)
        - model: Brand and Model Name
        - cal: Inspection Date (YYYY-MM-DD)
        - exp: Expiry Date (YYYY-MM-DD)
        - cert: Cert No (Truncate after .SRV)
        - lot: Lot/Report Number
        - type: Classify as [HARNESS, ABSORBER, GD, EEBD, SCBA, SMOKE HOOD]
        """
        
        response = model.generate_content([sample_file, prompt])
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)
        data['page'] = page_num
        data['local_split_path'] = file_path # Used for Firebase upload later
        return data
    except Exception as e:
        print(f"❌ Gemini Error on Page {page_num}: {e}")
        return None

# ==========================================
# 5. MAIN PROCESSOR (SPEED OPTIMIZED)
# ==========================================
def process_pdf_text(file_path, is_service=False, manual_type=None):
    """
    1. Splits PDF into single pages first.
    2. Processes all pages simultaneously using multithreading.
    """
    try:
        pages = split_pdf_to_pages(file_path)
        if not pages:
            return {"status": "failed", "error": "Document splitting failed"}

        cleaned_data = []
        # concurrent.futures reduces processing time by ~70% for multi-page docs
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_page = {executor.submit(extract_single_page_data, p, manual_type): p for p in pages}
            for future in concurrent.futures.as_completed(future_to_page):
                result = future.result()
                if result:
                    # Individual cleanup and collection routing
                    if result.get("cert") and ".SRV" in result["cert"]:
                        result["cert"] = result["cert"].split(".SRV")[0] + ".SRV"
                    
                    base_type = result.get("type", "UNKNOWN").upper().replace("_", " ")
                    result["target_collection"] = base_type + ("_SERVICE" if is_service else "")
                    cleaned_data.append(result)

        return {"status": "success", "data": cleaned_data}

    except Exception as e:
        print(f"❌ Processing Error: {e}")
        return {"error": str(e), "status": "failed"}

# ==========================================
# 6. STORAGE & QR UTILITIES
# ==========================================
def generate_qr_image_only(serial, link):
    """Generates QR code with SN label."""
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

def upload_to_firebase_storage(local_path, serial, is_qr=False):
    """Uploads single-page cert or QR to Firebase Storage."""
    try:
        _, bucket = get_firebase_db()
        if not bucket: return None
        safe_serial = sanitize_filename(serial)
        ts = int(time.time())
        blob_name = f"qr_codes/qr_{safe_serial}.png" if is_qr else f"certificates/{safe_serial}_{ts}.pdf"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_path)
        blob.make_public()
        return blob.public_url
    except Exception as e:
        print(f"❌ Firebase Upload Error: {e}")
        return None

def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    """Creates or updates an individual asset entry."""
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
        print(f"❌ Firestore Sync Error: {e}")
        return False
