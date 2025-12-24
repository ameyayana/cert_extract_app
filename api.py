from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import shutil
import os
import utils  # Import the file we just created

app = FastAPI()

# Allow your mobile app to talk to this server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DIR = "temp_api_pdfs"
os.makedirs(TEMP_DIR, exist_ok=True)

@app.get("/")
def read_root():
    return {"message": "Certificate Extractor API is running"}

@app.post("/extract")
async def extract_data(file: UploadFile = File(...), is_service: bool = Form(False)):
    """
    Mobile App sends a PDF here.
    Server extracts data and returns JSON.
    """
    file_location = os.path.join(TEMP_DIR, file.filename)
    
    # 1. Save uploaded file temporarily
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # 2. Run extraction logic from utils.py
    try:
        result = utils.process_pdf_text(file_location, is_service)
    finally:
        # 3. Cleanup: Delete file after processing to save space
        if os.path.exists(file_location):
            os.remove(file_location)
            
    return result