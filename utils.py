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
from pypdf import PdfReader, PdfWriter # Use pypdf for physical splitting

# ==========================================
# 1. CONFIGURATION & DIRECTORIES
# ==========================================
QR_DIR = "qrcodes"
SPLIT_DIR = "temp_split_certs"
os.makedirs(QR_DIR, exist_ok=True)
os.makedirs(SPLIT_DIR, exist_ok=True)

# Explicitly using gemini-flash-latest as requested
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

def split_pdf_to_single_page(original_path, page_number_1_indexed, output_filename):
    """
    Physically extracts one page from a PDF and saves it as a new standalone file.
    Returns the local path to the new single-page PDF.
    """
    output_path = os.path.join(SPLIT_DIR, f"{output_filename}.pdf")
    try:
        reader = PdfReader(original_path)
        writer = PdfWriter()
        
        # Add the specific page (0-indexed in pypdf)
        writer.add_page(reader.pages[page_number_1_indexed - 1])
        
        with open(output_path, "wb") as output_file:
            writer.write(output_file)
            
        return output_path
    except Exception as e:
        print(f"❌ PDF Splitting Error: {e}")
        return original_path # Fallback to original if split fails

def generate_qr_image_only(serial, link):
    """Generates a QR code image with a Serial Number label at the bottom."""
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
    """
    Uploads the PDF to Gemini and extracts technical data from all pages.
    Returns a JSON list of objects, one for each certificate found.
    """
    if not GEMINI_KEY:
        return None

    try:
        print(f"📤 Uploading {file_path} to Gemini for Multi-Page Scan...")
        sample_file = genai.upload_file(path=file_path, display_name="Merged Certificate")
        
        while sample_file.state.name == "PROCESSING":
            time.sleep(1)
            sample_file = genai.get_file(sample_file.name)

        # Explicitly using gemini-flash-latest
        model = genai.GenerativeModel(model_name=GEMINI_MODEL)

        hint_text = f"Context: This document contains {manual_hint} certificates." if manual_hint else ""

        prompt = f"""
        Extract safety technical data from every certificate page in this PDF.
        {hint_text}
        
        Return a JSON LIST of objects. One object per page/equipment.
        
        Required fields for each item:
        - serial: Primary Serial Number (e.g., '000186')
        - model: Equipment Brand/Model (e.g., 'WORKGARD Body Harness')
        - cal: Date of Inspection (YYYY-MM-DD)
        - exp: Next Inspection Date (YYYY-MM-DD)
        - cert: Certificate No (Stop at .SRV)
        - lot: Lot/Report Number (e.g., 'CHSB-ES-25-02')
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
    """
    1. Extracts data for all PPE items via AI.
    2. Physically splits the PDF into single-page files per asset.
    3. Maps each asset to its correct collection folder.
    """
    try:
        items_list = extract_with_gemini(file_path, manual_hint=manual_type)
        
        if not items_list:
            return {"status": "failed", "error": "AI could not extract data"}

        cleaned_data = []
        for item in items_list:
            # Clean certificate number
            if item.get("cert") and ".SRV" in item["cert"]:
                item["cert"] = item["cert"].split(".SRV")[0] + ".SRV"

            # Determine the specific collection for THIS item to prevent mixed folders
            # e.g., HARNESS goes to HARNESS folder, ABSORBER to ABSORBER folder
            base_type = item.get("type", "UNKNOWN").upper().replace("_", " ")
            item["target_collection"] = base_type + ("_SERVICE" if is_service else "")
            
            # Physically split the PDF so the link only shows this item's cert
            page_num = item.get("page", 1)
            serial = item.get("serial", f"temp_{int(time.time())}")
            
            # Create a 1-page PDF locally
            split_cert_path = split_pdf_to_single_page(file_path, page_num, f"split_{sanitize_filename(serial)}")
            item["local_split_path"] = split_cert_path
            
            cleaned_data.append(item)

        return {
            "status": "success",
            "data": cleaned_data # Frontend will loop through this list and call /save for each
        }

    except Exception as e:
        print(f"❌ Processing Error: {e}")
        return {"error": str(e), "status": "failed"}

# ==========================================
# 6. FIREBASE UPLOADERS
# ==========================================
def upload_to_firebase_storage(local_path, serial, is_qr=False):
    """Uploads either a QR or the split single-page PDF to Firebase."""
    try:
        _, bucket = get_firebase_db()
        if not bucket: return None
        
        safe_serial = sanitize_filename(serial)
        # Use timestamp to ensure unique file versions in storage
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
    """Saves the individual asset data to its specific collection folder."""
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
            "pdf_url": pdf_url, # Now points to a single-page PDF
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
