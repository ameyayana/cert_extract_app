import os
import re
import json
import base64
import qrcode
import time
import io
import concurrent.futures # For parallel speed boost
import google.generativeai as genai
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from firebase_admin import credentials, firestore, storage, initialize_app, _apps
from pypdf import PdfReader, PdfWriter 

# ==========================================
# 1. CONFIGURATION & DIRECTORIES
# ==========================================
QR_DIR = "/tmp/qrcodes"
SPLIT_DIR = "/tmp/temp_split_certs"
os.makedirs(QR_DIR, exist_ok=True)
os.makedirs(SPLIT_DIR, exist_ok=True)

GEMINI_MODEL = "gemini-flash-latest" # Faster than older versions

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
else:
    print("⚠️ WARNING: GEMINI_API_KEY is missing!")

# ==========================================
# 2. PDF PROCESSING UTILITIES
# ==========================================
def sanitize_filename(name):
    return re.sub(r'[\\/:"*?<>|]', "_", str(name)).strip()

def split_pdf_to_pages(original_path):
    """
    Physically splits a PDF into individual single-page files.
    Returns a list of paths to the single-page PDFs.
    """
    page_paths = []
    try:
        reader = PdfReader(original_path)
        for i, page in enumerate(reader.pages):
            output_filename = f"temp_page_{i}_{int(time.time())}.pdf"
            output_path = os.path.join(SPLIT_DIR, output_filename)
            
            writer = PdfWriter()
            writer.add_page(page)
            
            with open(output_path, "wb") as output_file:
                writer.write(output_file)
            page_paths.append((output_path, i + 1)) # Path and 1-indexed page number
        return page_paths
    except Exception as e:
        print(f"❌ PDF Splitting Error: {e}")
        return []

# ==========================================
# 3. AI EXTRACTION ENGINE (SINGLE PAGE FOCUS)
# ==========================================
def extract_single_page_data(page_info, manual_hint=None):
    """
    Sends a single page to Gemini. This is faster and more accurate.
    """
    file_path, page_num = page_info
    try:
        sample_file = genai.upload_file(path=file_path)
        model = genai.GenerativeModel(model_name=GEMINI_MODEL)
        
        prompt = f"""
        Extract technical data from this safety certificate.
        Return ONLY raw JSON. 
        Required fields:
        - serial (6-digit numeric serial)
        - model (Full brand and model)
        - cal (Date of Inspection YYYY-MM-DD)
        - exp (Next Inspection Date YYYY-MM-DD)
        - cert (Certificate No, stop after .SRV)
        - lot (Report or Lot Number)
        - type (Classify: HARNESS, ABSORBER, GD, EEBD, SCBA, SMOKE HOOD)
        """
        
        response = model.generate_content([sample_file, prompt])
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)
        data['page'] = page_num
        data['local_split_path'] = file_path # Keep track of the split file for saving
        return data
    except Exception as e:
        print(f"❌ Error on Page {page_num}: {e}")
        return None

# ==========================================
# 4. MAIN PROCESSOR (SPEED OPTIMIZED)
# ==========================================
def process_pdf_text(file_path, is_service=False, manual_type=None):
    """
    1. Splits PDF first.
    2. Uses ThreadPoolExecutor to process all pages in PARALLEL (much faster).
    """
    try:
        # Step 1: Physical Split
        pages = split_pdf_to_pages(file_path)
        if not pages:
            return {"status": "failed", "error": "Could not split PDF"}

        # Step 2: Parallel Extraction
        # This sends all pages to Gemini at once instead of one-by-one
        cleaned_data = []
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_page = {executor.submit(extract_single_page_data, p, manual_type): p for p in pages}
            for future in concurrent.futures.as_completed(future_to_page):
                result = future.result()
                if result:
                    # Cleanup cert and collection info
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
# 5. REMAINING UTILITIES (FIREBASE/QR)
# ==========================================
# [Keep your existing generate_qr_image_only, get_firebase_db, upload, and update functions here]
