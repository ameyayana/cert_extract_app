from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import shutil
import os
import utils
import utils

app = FastAPI()

# 1. Mount Static Files (For Logo)
# Create a folder named 'static' in backend and put chsb_logo.png there
app.mount("/static", StaticFiles(directory="static"), name="static")

# 2. Setup Templates
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- WEB DASHBOARD ---
@app.get("/", response_class=HTMLResponse)
async def serve_admin_panel(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- ADMIN API ENDPOINTS ---

@app.get("/api/collections")
def list_collections():
    """List all 16 folder types"""
    base = ["GD", "EEBD", "HARNESS", "ABSORBER", "SMOKE HOOD", "SCBA", "AREA MONITOR", "RESCUE KIT"]
    # Generate Rental + Service versions
    all_cols = []
    for b in base:
        all_cols.append(b)
        all_cols.append(f"{b}_SERVICE")
    return {"collections": all_cols}

@app.get("/api/collection/{name}")
def get_collection_data(name: str):
    db, _ = utils.get_firebase_db()
    docs = db.collection(name).stream()
    data = []
    for doc in docs:
        d = doc.to_dict()
        d['id'] = doc.id
        if 'last_updated' in d and d['last_updated']:
            d['last_updated'] = d['last_updated'].isoformat()
        data.append(d)
    return {"data": data}

@app.delete("/api/collection/{name}/{doc_id}")
def delete_document(name: str, doc_id: str):
    db, _ = utils.get_firebase_db()
    db.collection(name).document(doc_id).delete()
    return {"status": "deleted"}

# --- MOBILE / SEARCH ENDPOINTS ---

@app.get("/api/search")
def search_all(q: str):
    db, _ = utils.get_firebase_db()
    results = []
    # Search all 16 collections
    base = ["GD", "EEBD", "HARNESS", "ABSORBER", "SMOKE HOOD", "SCBA", "AREA MONITOR", "RESCUE KIT"]
    collections = base + [f"{b}_SERVICE" for b in base]
    
    safe_q = utils.sanitize_filename(q)
    
    for col in collections:
        doc = db.collection(col).document(safe_q).get()
        if doc.exists:
            d = doc.to_dict()
            d['id'] = doc.id
            d['collection'] = col
            results.append(d)
            
    return {"results": results}

@app.post("/api/update_record")
async def update_record(
    collection: str = Form(...),
    serial: str = Form(...),
    model: str = Form(...),
    cal: str = Form(...),
    exp: str = Form(...),
    cert: str = Form(...),
    lot: str = Form(...)
):
    db, _ = utils.get_firebase_db()
    data = {
        "model": model, "calibration_date": cal, 
        "expiry_date": exp, "cert": cert, "lot": lot
    }
    db.collection(collection).document(utils.sanitize_filename(serial)).update(data)
    return {"status": "success"}

# --- EXTRACTION & UPLOAD ---

@app.post("/extract")
async def extract_pdf(file: UploadFile = File(...), is_service: str = Form("false")):
    temp_path = f"temp_{file.filename}"
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        service_bool = is_service.lower() == 'true'
        # Call the AI logic
        result = utils.process_pdf_text(temp_path, is_service=service_bool)
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

@app.post("/save")
async def save_record(
    file: UploadFile = File(...),
    serial: str = Form(...), model: str = Form(...),
    cal: str = Form(...), exp: str = Form(...),
    cert: str = Form(...), collection: str = Form(...),
    lot: str = Form(...)
):
    temp_path = f"save_{file.filename}"
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        pdf_url = utils.upload_to_firebase_storage(temp_path, serial, is_qr=False)
        qr_link = f"https://qrcertificates-30ddb.web.app/?id={utils.quote_plus(serial)}"
        qr_local_path = utils.generate_qr_image_only(serial, qr_link)
        qr_image_url = utils.upload_to_firebase_storage(qr_local_path, serial, is_qr=True)

        data = {"cert": cert, "model": model, "cal": cal, "exp": exp, "lot": lot}
        utils.update_firestore_record(collection, serial, data, pdf_url, qr_image_url, qr_link)

        return {"status": "success", "web_link": qr_link, "serial": serial}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)
        qr_p = f"qrcodes/qr_{utils.sanitize_filename(serial)}.png"
        if os.path.exists(qr_p): os.remove(qr_p)