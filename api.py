from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import shutil
import os
import utils

# --- SETUP ---
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("temp_pdfs", exist_ok=True)

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- WEB ---
@app.get("/", response_class=HTMLResponse)
async def serve_admin_panel(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# --- COLLECTIONS ---
@app.get("/api/collections")
def list_collections():
    base = ["GD", "EEBD", "HARNESS", "ABSORBER", "SMOKE HOOD", "SCBA", "AREA MONITOR", "RESCUE KIT"]
    return {
        "collections": [c for b in base for c in (b, f"{b}_SERVICE")]
    }

@app.get("/api/collection/{name}")
def get_collection_data(name: str):
    db, _ = utils.get_firebase_db()
    docs = db.collection(name).stream()

    data = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id

        if d.get("last_updated"):
            d["last_updated"] = d["last_updated"].isoformat()

        data.append(d)

    return {"data": data}

# --- SEARCH ---
@app.get("/api/search")
def search_all(q: str):
    db, _ = utils.get_firebase_db()
    q = q.strip()

    base = ["GD", "EEBD", "HARNESS", "ABSORBER", "SMOKE HOOD", "SCBA", "AREA MONITOR", "RESCUE KIT"]
    collections = base + [f"{b}_SERVICE" for b in base]

    results = []

    safe_q = utils.sanitize_filename(q)

    for col in collections:
        doc = db.collection(col).document(safe_q).get()
        if doc.exists:
            d = doc.to_dict()
            d["id"] = doc.id
            d["collection"] = col
            results.append(d)
            continue

        for doc in db.collection(col).where("serial", "==", q).stream():
            d = doc.to_dict()
            d["id"] = doc.id
            d["collection"] = col
            results.append(d)

    return {"results": results}

# --- UPDATE ---
@app.post("/api/update_record")
async def update_record(
    collection: str = Form(...),
    serial: str = Form(...),
    model: str = Form(""),
    cal: str = Form(""),
    exp: str = Form(""),
    cert: str = Form(""),
    lot: str = Form("")
):
    db, _ = utils.get_firebase_db()

    db.collection(collection).document(
        utils.sanitize_filename(serial)
    ).update({
        "model": model,
        "cal": cal,
        "exp": exp,
        "cert": cert,
        "lot": lot
    })

    return {"status": "success"}

# --- EXTRACT ---
@app.post("/extract")
async def extract_pdf(
    file: UploadFile = File(...),
    is_service: str = Form("false")
):
    temp_path = f"temp_pdfs/{file.filename}"

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        return utils.process_pdf_text(
            temp_path,
            is_service=is_service.lower() == "true"
        )

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# --- SAVE ---
@app.post("/save")
async def save_record(
    file: UploadFile = File(...),
    serial: str = Form(...),
    model: str = Form(""),
    cal: str = Form(""),
    exp: str = Form(""),
    cert: str = Form(""),
    lot: str = Form(""),
    collection: str = Form(...)
):
    temp_path = f"temp_pdfs/{file.filename}"

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        pdf_url = utils.upload_to_firebase_storage(temp_path, serial, is_qr=False)
        qr_link = f"https://qrcertificates-30ddb.web.app/?id={utils.quote_plus(serial)}"
        qr_path = utils.generate_qr_image_only(serial, qr_link)
        qr_image_url = utils.upload_to_firebase_storage(qr_path, serial, is_qr=True)

        utils.update_firestore_record(
            collection,
            serial,
            {
                "model": model,
                "cal": cal,
                "exp": exp,
                "cert": cert,
                "lot": lot
            },
            pdf_url,
            qr_image_url,
            qr_link
        )

        return {"status": "success", "web_link": qr_link}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
