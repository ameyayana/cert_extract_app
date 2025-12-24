# === Imports ===
import os
import streamlit as st
import qrcode
from PIL import Image, ImageDraw, ImageFont
from urllib.parse import quote_plus
from pathlib import Path

# Import your new logic file
import utils

# Ensure Streamlit is configured for wide mode and page info
st.set_page_config(page_title="QR Cert Extractor", page_icon="📄", layout="wide")

# === Configuration & Firebase Initialization ===

@st.cache_resource
def get_firebase_resources():
    """
    Wrapper to cache the Firebase connection using Streamlit.
    Calls the logic from utils.py.
    """
    try:
        db, bucket = utils.get_firebase_db()
        return db, bucket
    except Exception as e:
        st.error(f"❌ Failed to initialize Firebase: {e}")
        st.stop()

# Initialize Firebase services
db, bucket = get_firebase_resources()

# === Constants & Setup ===
TEMP_DIR = "temp_pdfs"
QR_DIR = "qrcodes"
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(QR_DIR, exist_ok=True)

# === Utility Functions (UI Specific) ===

def generate_qr_image(serial):
    """
    Generates a QR code image with the CHSB logo.
    Kept in app.py because it deals with visual image manipulation for the UI.
    """
    safe_serial = utils.sanitize_filename(serial)
    url = f"https://qrcertificates-30ddb.web.app/?id={quote_plus(serial)}"
    size = 500

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    qr_img = qr_img.resize((size, size), Image.Resampling.NEAREST)

    # Load Fonts
    try:
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "arialbd.ttf"
        ]
        font_path = next((p for p in font_paths if os.path.exists(p)), None)
        font_sn = ImageFont.truetype(font_path, 50) if font_path else ImageFont.load_default()
        font_co = ImageFont.truetype(font_path, 30) if font_path else ImageFont.load_default()
    except:
        font_sn = ImageFont.load_default()
        font_co = ImageFont.load_default()

    # Add Logo
    try:
        logo_path = Path(__file__).parent / "chsb_logo.png"
        logo = Image.open(logo_path).convert("RGBA")
        logo_size = size // 5
        logo.thumbnail((logo_size, logo_size), Image.Resampling.LANCZOS)
        
        bg_size = (logo.size[0] + 20, logo.size[1] + 20)
        background = Image.new("RGBA", bg_size, (255, 255, 255, 0))
        mask = Image.new("L", bg_size, 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([0, 0, bg_size[0], bg_size[1]], radius=bg_size[0]//4, fill=255)
        
        background.paste(logo, ((bg_size[0] - logo.size[0]) // 2, (bg_size[1] - logo.size[1]) // 2), mask=logo)
        pos = ((qr_img.size[0] - background.size[0]) // 2, (qr_img.size[1] - background.size[1]) // 2)
        qr_img.paste(background, pos, mask=mask)
    except Exception:
        pass # Skip logo if not found

    # Label section
    label_height = 160
    label = Image.new("RGBA", (size, label_height), "white")
    draw = ImageDraw.Draw(label)
    
    sn_text = f"SN: {serial}"
    company_text = "Cahaya Hornbill Sdn Bhd"
    
    sn_width = draw.textlength(sn_text, font=font_sn)
    company_width = draw.textlength(company_text, font=font_co)
    
    draw.text(((size - sn_width) // 2, 20), sn_text, font=font_sn, fill="black")
    draw.text(((size - company_width) // 2, 90), company_text, font=font_co, fill="black")

    final = Image.new("RGBA", (size, size + label_height), "white")
    final.paste(qr_img, (0, 0))
    final.paste(label, (0, size))

    path = os.path.join(QR_DIR, f"qr_{safe_serial}.png")
    final.convert("RGB").save(path, quality=95, dpi=(300, 300))
    return url, path

def upload_to_firebase_storage(path, serial, is_qr=False):
    try:
        safe_serial = utils.sanitize_filename(serial)
        blob_name = f"qr_codes/qr_{safe_serial}.png" if is_qr else f"certificates/{safe_serial}.pdf"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(path)
        blob.make_public()
        return blob.public_url
    except Exception as e:
        st.error(f"❌ Firebase Storage upload failed for {serial}: {e}")
        return None

def update_firestore_record(collection_name, serial, data, pdf_url, qr_url, qr_link):
    try:
        safe_doc_id = utils.sanitize_filename(serial)
        # Use utils for database access if preferred, strictly utilizing the global 'db' here for simplicity
        doc_ref = db.collection(collection_name).document(safe_doc_id)
        doc_data = {
            "cert": data["cert"],
            "model": data["model"],
            "serial": serial,
            "calibration_date": data["cal"],
            "expiry_date": data["exp"],
            "lot": data["lot"],
            "pdf_url": pdf_url,
            "qr_image_url": qr_url,
            "qr_link": qr_link,
            "last_updated": utils.firestore.SERVER_TIMESTAMP
        }
        doc_ref.set(doc_data, merge=True)
        return True
    except Exception as e:
        st.error(f"❌ Failed to update Firestore for {serial}: {e}")
        return False

# === Main Streamlit App ===

st.title("📄 Certificate Extractor + QR Generator")
st.write("Upload PDF certs to extract data, generate QR codes, upload to Firebase, and update Firestore.")

if 'clear_files' not in st.session_state:
    st.session_state.clear_files = False
if 'cert_type' not in st.session_state:
    st.session_state.cert_type = "Rental" 

# --- 1. Select Certificate Type ---
st.subheader("1. Select Certificate Type")
col1, col2 = st.columns(2)

with col1:
    if st.button("🔧 **Service Certificate** (Not Our Asset)", 
                 type="primary" if st.session_state.cert_type == "Service (not our asset)" else "secondary", 
                 use_container_width=True):
        st.session_state.cert_type = "Service (not our asset)"
        st.rerun()

with col2:
    if st.button("📋 **Rental Certificate**", 
                 type="primary" if st.session_state.cert_type == "Rental" else "secondary", 
                 use_container_width=True):
        st.session_state.cert_type = "Rental"
        st.rerun()

st.info(f"**Selected:** {st.session_state.cert_type}")

# --- 2. Upload PDF Files ---
st.subheader("2. Upload PDF Files")
uploaded_files = st.file_uploader("📄 Upload PDFs", type=["pdf"], accept_multiple_files=True, key=str(st.session_state.clear_files))

if st.button('Clear All Uploaded Files'):
    st.session_state.clear_files = not st.session_state.clear_files
    st.rerun()

# --- 3. Processing Logic ---
if uploaded_files:
    is_service = st.session_state.cert_type == "Service (not our asset)"
    
    for file in uploaded_files:
        st.divider()
        st.subheader(f"📄 {file.name}")
        
        # Save file locally
        temp_path = os.path.join(TEMP_DIR, file.name)
        with open(temp_path, "wb") as f:
            f.write(file.read())

        # === CALL THE NEW BRAIN (utils.py) ===
        # We pass "manual_type" as None initially
        result = utils.process_pdf_text(temp_path, is_service, manual_type=None)
        
        extracted_data = []
        collection_name = "UNKNOWN"

        # Handle Success
        if result["status"] == "success":
            extracted_data = result["data"]
            collection_name = result["collection"]
            st.success(f"✅ Document Type Detected: **{result['type']}**")
        
        # Handle Failure (Manual Selection)
        elif result["status"] == "unknown" or result["status"] == "failed":
            st.warning("⚠️ Could not automatically detect template.")
            
            with st.form(key=f"manual_{file.name}"):
                # Get the list of available keys from utils map logic
                # Hardcoding list for display based on known types in utils
                options = ["-- Select --", "gas_detector", "eebd", "harness", "absorber", "scba", "area_monitor", "smoke_hood", "rescue_kit"]
                selected = st.selectbox("Manually Select Equipment Type:", options)
                
                if st.form_submit_button("Re-Attempt"):
                    if selected != "-- Select --":
                        # Force extraction with manual type
                        retry_result = utils.process_pdf_text(temp_path, is_service, manual_type=selected)
                        if retry_result["status"] == "success":
                            extracted_data = retry_result["data"]
                            collection_name = retry_result["collection"]
                            st.success(f"✅ Manual extraction successful for **{selected}**")
                        else:
                            st.error("❌ Manual extraction failed. Data pattern not found.")

        # --- Display Edit Form & Upload ---
        if extracted_data:
            with st.form(key=f"form_{file.name}"):
                final_results = []
                for i, item in enumerate(extracted_data):
                    st.markdown(f"**Record {i+1}**")
                    c1, c2 = st.columns(2)
                    with c1:
                        cert = st.text_input("Certificate No.", item.get("cert", ""), key=f"c_{file.name}_{i}")
                        model = st.text_input("Model", item.get("model", ""), key=f"m_{file.name}_{i}")
                        serial = st.text_input("Serial No.", item.get("serial", ""), key=f"s_{file.name}_{i}")
                    with c2:
                        cal = st.text_input("Calibration (YYYY-MM-DD)", item.get("cal", ""), key=f"d1_{file.name}_{i}")
                        exp = st.text_input("Expiry (YYYY-MM-DD)", item.get("exp", ""), key=f"d2_{file.name}_{i}")
                        lot = st.text_input("Lot / Report", item.get("lot", ""), key=f"l_{file.name}_{i}")
                    
                    final_results.append({
                        "cert": cert, "model": model, "serial": serial, 
                        "cal": cal, "exp": exp, "lot": lot
                    })
                
                if st.form_submit_button("🚀 Upload & Generate QR"):
                    st.markdown("### 📋 Results")
                    for data in final_results:
                        s_no = data['serial'].strip()
                        if not s_no or s_no in ["Unknown", "Invalid"]:
                            st.error("❌ Invalid Serial Number.")
                            continue
                        
                        with st.spinner(f"Processing {s_no}..."):
                            # 1. Generate QR (Visual)
                            qr_link, qr_path = generate_qr_image(s_no)
                            
                            # 2. Upload Files
                            pdf_url = upload_to_firebase_storage(temp_path, s_no, is_qr=False)
                            qr_url = upload_to_firebase_storage(qr_path, s_no, is_qr=True)
                            
                            # 3. Update DB
                            if pdf_url and qr_url:
                                if update_firestore_record(collection_name, s_no, data, pdf_url, qr_url, qr_link):
                                    st.success(f"✅ Updated **{s_no}** in **{collection_name}**")
                                    
                                    # Show NFC/QR Data
                                    c_img, c_info = st.columns([1, 3])
                                    with c_img:
                                        st.image(qr_path, width=150)
                                    with c_info:
                                        st.code(f"{qr_link}\nCert:{data['cert']}\nSN:{s_no}\nExp:{data['exp']}")
                                        st.write(f"[PDF Link]({pdf_url})")
                                    
                            # Cleanup QR
                            if os.path.exists(qr_path): os.remove(qr_path)
                            
        # Cleanup Temp PDF (only if not waiting for manual retry)
        # Note: In a stateless web app, we usually keep the file for the session or re-save it. 
        # For simplicity, we leave it in temp_pdfs until next restart or manual cleanup.