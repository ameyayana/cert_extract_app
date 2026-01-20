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

# ==========================================
# 1. CONFIGURATION & DIRECTORIES
# ==========================================
QR_DIR = "/tmp/qrcodes"
SPLIT_DIR = "/tmp/temp_split_certs"
os.makedirs(QR_DIR, exist_ok=True)
os.makedirs(SPLIT_DIR, exist_ok=True)

# Using the verified model name from your ListModels output
GEMINI_MODEL = "gemini-1.5-flash" 

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
client = None
if GEMINI_KEY:
    client = genai.Client(api_key=GEMINI_KEY)
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

def split_pdf_to_pages(original_path):
    """Physically splits PDF into single-page files before extraction."""
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

# ==========================================
# 4. NEW AI ENGINE (USING GOOGLE-GENAI SDK)
# ==========================================
def extract_single_page_data(page_info, manual_hint=None):
    """Processes one page using the modern SDK. Much faster & precise."""
    file_path, page_num = page_info
    if not client: return None

    try:
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        prompt = """
        Extract technical safety data from this certificate. Return ONLY raw JSON.
        Required fields:
        - serial (6-digit numeric)
        - model (Brand and Model)
        - cal (Inspection Date YYYY-MM-DD)
        - exp (Expiry Date YYYY-MM-DD)
        - cert (Cert No, stop after .SRV)
        - lot (Lot/Report Number)
        - type (Classify: HARNESS, ABSORBER, GD, EEBD, SCBA, SMOKE HOOD)
        """

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                prompt
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        data = json.loads(response.text)
        data['page'] = page_num
        data['local_split_path'] = file_path
        return data
    except Exception as e:
        print(f"❌ AI Error on Page {page_num}: {e}")
        return None

# ==========================================
# 5. MAIN PROCESSOR (PARALLEL WORKFLOW)
# ==========================================
def process_pdf_text(file_path, is_service=False, manual_type=None):
    try:
        # Step 1: Split physically first
        pages = split_pdf_to_pages(file_path)
        if not pages: return {"status": "failed", "error": "Split failed"}

        # Step 2: Extract in parallel (Threading)
        cleaned_data = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_page = {executor.submit(extract_single_page_data, p, manual_type): p for p in pages}
            for future in concurrent.futures.as_completed(future_to_page):
                result = future.result()
                if result:
                    # Clean cert number
                    if result.get("cert") and ".SRV" in result["cert"]:
                        result["cert"] = result["cert"].split(".SRV")[0] + ".SRV"
                    
                    # Target collection
                    b_type = result.get("type", "UNKNOWN").upper().replace("_", " ")
                    result["target_collection"] = b_type + ("_SERVICE" if is_service else "")
                    cleaned_data.append(result)

        return {"status": "success", "data": cleaned_data}
    except Exception as e:
        return {"error": str(e), "status": "failed"}

# ==========================================
# 6. STORAGE & QR UTILITIES
# ==========================================
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
        doc_ref = db.collection(collection_name).document(sanitize_filename(serial))
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
        print(f"❌ Firestore Error: {e}"); return False
