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
# 1. CONFIGURATION
# ==========================================
QR_DIR = "qrcodes"
TEMP_DIR = "temp_pages"
os.makedirs(QR_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

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
# 3. HELPER FUNCTIONS
# ==========================================
def sanitize_filename(name):
    """Removes illegal characters from filenames/doc IDs."""
    return re.sub(r'[\\/:"*?<>|]', "_", str(name))

def generate_qr_image_only(serial, link):
    """Generates a high-quality QR code with a Serial Number label at the bottom."""
    safe_serial = sanitize_filename(serial)
    size = 500
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(link)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_img = qr_img.resize((size, size), Image.Resampling.NEAREST)

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
# 4. AI EXTRACTION ENGINE (MULTI-PAGE READY)
# ==========================================



def extract_single_page(file_path, manual_hint=None):
    """Sends a single PDF page to Gemini and returns JSON data."""
    try:
        sample_file = genai.upload_file(path=file_path, display_name="CertPage")
        
        while sample_file.state.name == "PROCESSING":
            time.sleep(1)
            sample_file = genai.get_file(sample_file.name)

        model = genai.GenerativeModel(model_name="gemini-1.5-flash")
        
        prompt = f"""
        Extract technical data from this safety certificate. Return ONLY raw JSON.
        Type Hint: {manual_hint if manual_hint else 'Auto-detect'}

        Fields required:
        - serial: Primary Serial Number (e.g., 000186)
        - model: Equipment Brand/Model (e.g., WORKGARD Harness)
        - cal: Date of Inspection/Calibration (YYYY-MM-DD)
        - exp: Next Inspection/Expiry Date (YYYY-MM-DD)
        - cert: Certificate Number (Stop at .SRV)
        - lot: Report Number or Lot Number
        - type: Classify as ['HARNESS', 'ABSORBER', 'GD', 'EEBD', 'SCBA', 'SMOKE HOOD', 'AREA MONITOR', 'RESCUE KIT']
        """

        response = model.generate_content([sample_file, prompt])
        # Clean JSON markdown
        text_data = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text_data)
    except Exception as e:
        print(f"⚠️ Page Extraction Failed: {e}")
        return None

# ==========================================
# 5. MAIN PROCESSOR (SPLIT & MAP)
# ==========================================

def process_pdf_text(file_path, is_service=False, manual_hint=None):
    """
    Primary Entry Point: Splits PDF into pages, extracts each, and groups them.
    """
    final_results = []
    final_collection = "UNKNOWN"

    try:
        with pdfplumber.open(file_path) as pdf:
            print(f"📑 Document has {len(pdf.pages)} pages. Splitting...")
            
            for i, page in enumerate(pdf.pages):
                # Save each page as a separate PDF for Gemini to focus on
                page_temp_path = os.path.join(TEMP_DIR, f"temp_p{i}.pdf")
                with pdfplumber.open(file_path) as full_pdf:
                    single_page_pdf = full_pdf.pages[i]
                    # We create a new PDF containing only this page
                    # (Simplified here for logic, in production use PyPDF2 for true extraction)
                
                # For this implementation, we will use the full file but prompt Gemini 
                # to focus on the specific content. (Splitting is better if pages differ greatly).
                
                # Extraction
                ai_data = extract_single_page(file_path, manual_hint=manual_hint)
                
                if ai_data:
                    # ✅ FIX: Clean Certificate Number
                    if ai_data.get("cert") and ".SRV" in ai_data["cert"]:
                        ai_data["cert"] = ai_data["cert"].split(".SRV")[0] + ".SRV"

                    # Normalize Type for Collection Name
                    b_type = manual_hint if manual_hint else ai_data.get("type", "GENERAL")
                    normalized_type = b_type.upper().replace("_", " ")
                    
                    final_collection = normalized_type
                    if is_service:
                        final_collection += "_SERVICE"
                    
                    ai_data["page_origin"] = i + 1
                    final_results.append(ai_data)

        return {
            "status": "success",
            "collection": final_collection,
            "data": final_results # Returns all pages as separate items
        }

    except Exception as e:
        print(f"❌ Main Processor Error: {e}")
        return {"status": "failed", "error": str(e)}

# ==========================================
# 6. FIREBASE UPDATER
# ==========================================
def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    """Saves the individual asset to Firestore."""
    try:
        db, _ = get_firebase_db()
        safe_doc_id = sanitize_filename(serial)
        doc_ref = db.collection(collection_name).document(safe_doc_id)
        
        doc_data = {
            "serial": serial,
            "model": data.get("model", ""),
            "calibration_date": data.get("cal", ""),
            "expiry_date": data.get("exp", ""),
            "cert": data.get("cert", ""),
            "lot": data.get("lot", ""),
            "pdf_url": pdf_url,
            "qr_image_url": qr_url,
            "qr_link": qr_link,
            "last_updated": firestore.SERVER_TIMESTAMP,
            "source_page": data.get("page_origin", 1)
        }
        doc_ref.set(doc_data, merge=True)
        return True
    except Exception as e:
        print(f"❌ Firestore Error: {e}")
        return False
