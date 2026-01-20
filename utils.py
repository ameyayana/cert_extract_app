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
# CRITICAL FIX: Import the new SDK, not the deprecated generativeai
from google import genai
from google.genai import types

# ==============================================================================
# 1. CONFIGURATION & DIRECTORIES
# ==============================================================================
# Using /tmp ensures write permissions on Render's ephemeral filesystem
QR_DIR = "/tmp/qrcodes"
SPLIT_DIR = "/tmp/temp_split_certs"

for d in [QR_DIR, SPLIT_DIR]:
    if not os.path.exists(d):
        os.makedirs(d, mode=0o777, exist_ok=True)

# Using the high-efficiency Flash Lite model from your ListModels output
GEMINI_MODEL = "gemini-flash-lite-latest"

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
client = None
if GEMINI_KEY:
    # This now works because we are using the correct 'google.genai' SDK
    client = genai.Client(api_key=GEMINI_KEY)
else:
    print("⚠️ WARNING: GEMINI_API_KEY is missing from environment variables!")

# ==============================================================================
# 2. FIREBASE SETUP
# ==============================================================================
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

# ==============================================================================
# 3. PDF UTILITIES (SPLIT BEFORE EXTRACTION)
# ==============================================================================
def sanitize_filename(name):
    """Sanitizes strings for safe file naming and Firestore IDs."""
    return re.sub(r'[\\/:"*?<>|]', "_", str(name)).strip()

def split_pdf_to_pages(original_path):
    """
    Physically extracts each page from a merged PDF.
    Ensures each asset has its own unique certificate link.
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

# ==============================================================================
# 4. AI EXTRACTION ENGINE (PARALLEL & BINARY OPTIMIZED)
# ==============================================================================



def extract_single_page_data(page_info, manual_hint=None):
    """
    Sends a pre-split single page to Gemini using the modern Client SDK.
    Optimized to use direct binary upload for maximum speed.
    """
    file_path, page_num = page_info
    if not client:
        return None

    try:
        # Load single page as bytes for faster processing
        with open(file_path, "rb") as f:
            pdf_bytes = f.read()

        prompt = """
        Extract technical data from this safety certificate. Return ONLY raw JSON.
        Required fields:
        - serial (Numeric serial found near 'Serial Number')
        - model (Full brand and model name)
        - cal (Inspection Date YYYY-MM-DD)
        - exp (Next Inspection Date YYYY-MM-DD)
        - cert (Certificate No, stop after .SRV)
        - lot (Report or Lot Number)
        - type (Classify EXACTLY as: HARNESS, ABSORBER, GD, EEBD, SCBA, SMOKE HOOD)
        """
        
        # Modern SDK interaction using the Client object
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                prompt
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        data = json.loads(response.text)
        
        # Save metadata for frontend tracking
        data['page'] = page_num
        data['local_split_path'] = file_path 
        return data
    except Exception as e:
        print(f"❌ Gemini Error on Page {page_num}: {e}")
        return None

# ==============================================================================
# 5. MAIN PROCESSOR (PARALLEL WORKFLOW)
# ==============================================================================
def process_pdf_text(file_path, is_service=False, manual_type=None):
    """
    1. Splits PDF into single pages first.
    2. Processes all pages in PARALLEL using multithreading for speed.
    """
    try:
        # Step 1: Split physically first (Ensures AI sees only one SN at a time)
        pages = split_pdf_to_pages(file_path)
        if not pages:
            return {"status": "failed", "error": "Document splitting failed"}

        cleaned_data = []
        # Step 2: Extract in parallel (Threading) for significant speed boost
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_page = {executor.submit(extract_single_page_data, p, manual_hint=manual_type): p for p in pages}
            for future in concurrent.futures.as_completed(future_to_page):
                result = future.result()
                if result:
                    # Clean cert number strings
                    if result.get("cert") and ".SRV" in result["cert"]:
                        result["cert"] = result["cert"].split(".SRV")[0] + ".SRV"
                    
                    # Individual item routing logic (e.g. Absorber doesn't go to Harness folder)
                    b_type = result.get("type", "UNKNOWN").upper().replace("_", " ")
                    result["target_collection"] = b_type + ("_SERVICE" if is_service else "")
                    cleaned_data.append(result)

        return {"status": "success", "data": cleaned_data}

    except Exception as e:
        print(f"❌ Processing Error: {e}")
        return {"error": str(e), "status": "failed"}

# ==============================================================================
# 6. STORAGE & FIREBASE LOGIC
# ==============================================================================
def generate_qr_image_only(serial, link):
    """Generates QR code with Serial Number label."""
    safe_serial = sanitize_filename(serial)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(link); qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_img = qr_img.resize((500, 500), Image.Resampling.LANCZOS)
    
    final = Image.new("RGBA", (500, 600), "white")
    final.paste(qr_img, (0, 0))
    draw = ImageDraw.Draw(final)
    # Positioning text at bottom label
    draw.text((20, 530), f"SN: {serial}", fill="black")
    
    path = os.path.join(QR_DIR, f"qr_{safe_serial}.png")
    final.convert("RGB").save(path)
    return path

def upload_to_firebase_storage(local_path, serial, is_qr=False):
    """Uploads split cert or QR to Firebase and returns public URL."""
    try:
        _, bucket = get_firebase_db()
        if not bucket: return None
        safe_serial = sanitize_filename(serial)
        ts = int(time.time())
        # Use timestamp to ensure each upload is a unique file in storage
        blob_name = f"qr_codes/qr_{safe_serial}.png" if is_qr else f"certificates/{safe_serial}_{ts}.pdf"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_path)
        blob.make_public()
        return blob.public_url
    except Exception as e:
        print(f"❌ Storage Error: {e}")
        return None

def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    """Saves individual asset data to its specific collection folder in Firestore."""
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
            "pdf_url": pdf_url, # Now links specifically to the one-page cert
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
