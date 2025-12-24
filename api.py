from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import shutil
import os
import utils

app = FastAPI()

# Allow connection from mobile app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "Cert Extractor API is Live 🚀"}

@app.post("/extract")
async def extract_pdf(file: UploadFile = File(...), is_service: str = Form("false")):
    """
    Phase 1: Just extracts text and guesses data. Does NOT save to Firebase yet.
    """
    temp_path = f"temp_{file.filename}"
    try:
        # Save uploaded file temporarily
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Run extraction logic
        service_bool = is_service.lower() == 'true'
        result = utils.process_pdf_text(temp_path, is_service=service_bool)
        
        return result

    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.post("/save")
async def save_record(
    file: UploadFile = File(...),
    serial: str = Form(...),
    model: str = Form(...),
    cal: str = Form(...),
    exp: str = Form(...),
    cert: str = Form(...),
    collection: str = Form(...),
    lot: str = Form(...)  # <--- ✅ ADDED LOT HERE
):
    """
    Phase 2: Receives EDITED data from mobile, uploads to Firebase, 
    updates Firestore, and returns the Web Link.
    """
    temp_path = f"save_{file.filename}"
    try:
        # 1. Save PDF locally
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 2. Upload PDF to Firebase Storage
        pdf_url = utils.upload_to_firebase_storage(temp_path, serial, is_qr=False)
        if not pdf_url:
            raise HTTPException(status_code=500, detail="Failed to upload PDF to Firebase")

        # 3. Generate QR Link
        qr_link = f"https://qrcertificates-30ddb.web.app/?id={utils.quote_plus(serial)}"
        
        # 4. Generate QR Image (Headless - No Streamlit)
        qr_local_path = utils.generate_qr_image_only(serial, qr_link)
        qr_image_url = utils.upload_to_firebase_storage(qr_local_path, serial, is_qr=True)

        # 5. Save Data to Firestore
        data_packet = {
            "cert": cert,
            "model": model,
            "cal": cal,
            "exp": exp,
            "lot": lot  # <--- ✅ SAVING LOT TO DB
        }
        
        success = utils.update_firestore_record(
            collection, serial, data_packet, pdf_url, qr_image_url, qr_link
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to save to Firestore")

        return {
            "status": "success",
            "web_link": qr_link,
            "serial": serial,
            "message": "Saved to Cloud"
        }

    except Exception as e:
        print(f"Save Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup
        if os.path.exists(temp_path): os.remove(temp_path)
        qr_temp = f"qrcodes/qr_{utils.sanitize_filename(serial)}.png"
        if os.path.exists(qr_temp): os.remove(qr_temp)
