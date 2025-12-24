import os
import streamlit as st
import utils # Use the shared logic

st.set_page_config(page_title="CHSB Cert Manager", page_icon="🏢", layout="wide")

st.title("🏢 CHSB Certificate Manager (Web)")

if 'clear_files' not in st.session_state: st.session_state.clear_files = False
if 'cert_type' not in st.session_state: st.session_state.cert_type = "Rental"

# --- 1. Rental / Service Toggle ---
c1, c2 = st.columns(2)
with c1:
    if st.button("🔧 Service (Not our Asset)", type="primary" if st.session_state.cert_type == "Service" else "secondary", use_container_width=True):
        st.session_state.cert_type = "Service"
        st.rerun()
with c2:
    if st.button("📋 Rental (Our Asset)", type="primary" if st.session_state.cert_type == "Rental" else "secondary", use_container_width=True):
        st.session_state.cert_type = "Rental"
        st.rerun()

st.info(f"Mode: **{st.session_state.cert_type}**")

# --- 2. Upload ---
uploaded_files = st.file_uploader("Upload PDFs", type=["pdf"], accept_multiple_files=True, key=str(st.session_state.clear_files))

if uploaded_files:
    is_service = (st.session_state.cert_type == "Service")
    
    for file in uploaded_files:
        st.divider()
        st.subheader(f"📄 {file.name}")
        
        # Save temp
        temp_path = f"temp_{file.name}"
        with open(temp_path, "wb") as f: f.write(file.read())

        # AI Extraction
        with st.spinner("AI Extracting..."):
            result = utils.process_pdf_text(temp_path, is_service=is_service)
        
        if result["status"] == "success":
            data = result["data"][0]
            st.success(f"Detected: **{result['type']}** -> Folder: **{result['collection']}**")
            
            with st.form(key=f"f_{file.name}"):
                c1, c2 = st.columns(2)
                with c1:
                    serial = st.text_input("Serial", data.get("serial", ""))
                    model = st.text_input("Model", data.get("model", ""))
                    cert = st.text_input("Cert No", data.get("cert", ""))
                with c2:
                    cal = st.text_input("Cal Date", data.get("cal", ""))
                    exp = st.text_input("Exp Date", data.get("exp", ""))
                    lot = st.text_input("Lot No", data.get("lot", ""))
                
                if st.form_submit_button("Save to Database"):
                    # Save logic (Simplified for brevity, re-uses utils)
                    pdf_url = utils.upload_to_firebase_storage(temp_path, serial, is_qr=False)
                    qr_link = f"https://qrcertificates-30ddb.web.app/?id={utils.quote_plus(serial)}"
                    qr_path = utils.generate_qr_image_only(serial, qr_link)
                    qr_url = utils.upload_to_firebase_storage(qr_path, serial, is_qr=True)
                    
                    save_data = {"cert": cert, "model": model, "cal": cal, "exp": exp, "lot": lot}
                    utils.update_firestore_record(result["collection"], serial, save_data, pdf_url, qr_url, qr_link)
                    st.success("Saved!")
                    if os.path.exists(qr_path): os.remove(qr_path)
        else:
            st.error("Extraction Failed.")
            
        if os.path.exists(temp_path): os.remove(temp_path)