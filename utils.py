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
from pypdf import PdfReader, PdfWriter 

# ==========================================
# 1. CONFIGURATION & DIRECTORIES
# ==========================================
QR_DIR = "/tmp/qrcodes" # Optimized for Render
SPLIT_DIR = "/tmp/temp_split_certs" # Optimized for Render
os.makedirs(QR_DIR, exist_ok=True)
os.makedirs(SPLIT_DIR, exist_ok=True)

GEMINI_MODEL = "gemini-flash-latest"

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
    return re.sub(r'[\\/:"*?<>|]', "_", str(name)).strip()

def split_pdf_to_single_page(original_path, page_number_1_indexed, output_filename):
    output_path = os.path.join(SPLIT_DIR, f"{output_filename}.pdf")
    try:
        reader = PdfReader(original_path)
        writer = PdfWriter()
        writer.add_page(reader.pages[page_number_1_indexed - 1])
        with open(output_path, "wb") as output_file:
            writer.write(output_file)
        return output_path
    except Exception as e:
        print(f"❌ PDF Splitting Error: {e}")
        return original_path 

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
# 4. AI EXTRACTION ENGINE (MULTI-PAGE)
# ==========================================
def extract_with_gemini(file_path, manual_hint=None):
    if not GEMINI_KEY: return None
    try:
        sample_file = genai.upload_file(path=file_path, display_name="Merged Certificate")
        while sample_file.state.name == "PROCESSING":
            time.sleep(1)
            sample_file = genai.get_file(sample_file.name)

        model = genai.GenerativeModel(model_name=GEMINI_MODEL)
        prompt = f"""
        Extract technical data from every certificate page in this PDF.
        Return a JSON LIST of objects. One object per page/equipment.
        
        Required fields for each item:
        - serial: Primary Serial Number (e.g., '000186')
        - model: Equipment Brand/Model (e.g., 'WORKGARD Body Harness')
        - cal: Date of Inspection (YYYY-MM-DD)
        - exp: Next Inspection Date (YYYY-MM-DD)
        - cert: Certificate No (Truncate after .SRV)
        - lot: Lot/Report Number
        - page: The page number where this item is located (1-indexed integer)
        - type: Classify as ['GD', 'EEBD', 'HARNESS', 'ABSORBER', 'SMOKE HOOD', 'SCBA', 'AREA MONITOR', 'RESCUE KIT']
        """
        response = model.generate_content([sample_file, prompt])
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)
        return data if isinstance(data, list) else [data]
    except Exception as e:
        print(f"❌ Gemini Extraction Error: {e}")
        return None

# ==========================================
# 5. MAIN PROCESSOR & SPLITTER
# ==========================================
def process_pdf_text(file_path, is_service=False, manual_type=None):
    try:
        items_list = extract_with_gemini(file_path, manual_hint=manual_type)
        if not items_list: return {"status": "failed", "error": "AI could not extract data"}
        cleaned_data = []
        for item in items_list:
            if item.get("cert") and ".SRV" in item["cert"]:
                item["cert"] = item["cert"].split(".SRV")[0] + ".SRV"
            base_type = item.get("type", "UNKNOWN").upper().replace("_", " ")
            item["target_collection"] = base_type + ("_SERVICE" if is_service else "")
            page_num = item.get("page", 1)
            serial = item.get("serial", f"temp_{int(time.time())}")
            split_cert_path = split_pdf_to_single_page(file_path, page_num, f"split_{sanitize_filename(serial)}")
            item["local_split_path"] = split_cert_path
            cleaned_data.append(item)
        return {"status": "success", "data": cleaned_data}
    except Exception as e:
        return {"error": str(e), "status": "failed"}

# ==========================================
# 6. FIREBASE UPLOADERS
# ==========================================
def upload_to_firebase_storage(local_path, serial, is_qr=False):
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
        print(f"❌ Storage Error: {e}")
        return None

def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    try:
        db, _ = get_firebase_db()
        if not db: return False
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
