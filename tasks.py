import os
import uuid
import pytesseract
import PyPDF2
import requests

from pdf2image import convert_from_path
from celery_app import make_celery
from app import app   # Ambil Flask app

# -----------------------------
# Build Celery worker instance
# -----------------------------
celery = make_celery(app)


# ================================
# CALLBACK FUNCTION (REUSABLE)
# ================================
def send_callback(letter_id, extracted_text, download_url):
    CALLBACK_URL = os.getenv("CALLBACK_URL")
    CALLBACK_TOKEN = os.getenv("CALLBACK_TOKEN")

    if not CALLBACK_URL:
        print("[CALLBACK ERROR] CALLBACK_URL tidak ditemukan")
        return

    if not CALLBACK_TOKEN:
        print("[CALLBACK ERROR] CALLBACK_TOKEN tidak ditemukan")
        return

    headers = {
        "Authorization": f"Bearer {CALLBACK_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "letter_id": letter_id,
        "extracted_text": extracted_text,
        "download_url": download_url
    }

    try:
        res = requests.post(CALLBACK_URL, json=payload, headers=headers)
        res.raise_for_status()
        print("[CALLBACK SUCCESS]", res.json())
    except Exception as e:
        print("[CALLBACK FAILED]", str(e))


# ===================================
# MAIN OCR TASK
# ===================================
@celery.task(name="tasks.ocr_task")
def ocr_task(document_id, file_path, pdf_password, callback_data):
    """
    Melakukan OCR dan mengirimkan hasil ke API callback Sirama.
    """

    try:
        # --- Load PDF ---
        reader = PyPDF2.PdfReader(file_path)

        # Handle password
        if reader.is_encrypted:
            try_passwords = [pdf_password or "", ""]
            decrypted = any(reader.decrypt(pw) for pw in try_passwords)
            if not decrypted:
                return {"status": "failed", "error": "PDF encrypted, wrong password"}

        # --- Convert PDF → images ---
        pages = convert_from_path(
            file_path,
            dpi=300,
            fmt="png",
            userpw=pdf_password if pdf_password else None
        )

        # --- OCR bahasa inggris + indonesia ---
        ocr_lang = "eng+ind"
        full_text = ""

        for num, img in enumerate(pages, start=1):
            text = pytesseract.image_to_string(img, lang=ocr_lang)
            full_text += f"\n\n===== PAGE {num} =====\n{text}"

        # =============================
        # CALL SIRAMA CALLBACK API
        # =============================
        send_callback_to_sirama(letter_id, full_text, compressed_url)
        send_callback(
            letter_id=callback_data["letter_id"],
            extracted_text=full_text,
            download_url=callback_data["download_url"]
        )

        return {"status": "success"}

    except Exception as e:
        print("[OCR TASK ERROR]", str(e))
        return {"status": "failed", "error": str(e)}
