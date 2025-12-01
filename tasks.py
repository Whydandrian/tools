import os
import uuid
import pytesseract
import PyPDF2
import requests

from pdf2image import convert_from_path
from celery_app import make_celery
from dotenv import load_dotenv

load_dotenv()

# -----------------------------
# Build Celery worker instance
# -----------------------------
celery = make_celery()

# ================================
# CALLBACK FUNCTION (REUSABLE)
# ================================
def send_callback(letter_id, extracted_text, download_url):
    """Send OCR results to callback URL"""
    CALLBACK_URL = os.getenv("CALLBACK_URL")
    CALLBACK_TOKEN = os.getenv("CALLBACK_TOKEN")

    if not CALLBACK_URL:
        print("[CALLBACK ERROR] CALLBACK_URL tidak ditemukan di .env")
        return False

    if not CALLBACK_TOKEN:
        print("[CALLBACK ERROR] CALLBACK_TOKEN tidak ditemukan di .env")
        return False

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
        print(f"[CALLBACK] Sending to {CALLBACK_URL} for letter_id: {letter_id}")
        res = requests.post(CALLBACK_URL, json=payload, headers=headers, timeout=30)
        res.raise_for_status()
        print("[CALLBACK SUCCESS]", res.json())
        return True
    except requests.exceptions.RequestException as e:
        print("[CALLBACK FAILED]", str(e))
        return False


# ===================================
# MAIN OCR TASK
# ===================================
@celery.task(name="tasks.ocr_task", bind=True, max_retries=3)
def ocr_task(self, document_id, file_path, pdf_password, callback_data):
    """
    Melakukan OCR dan mengirimkan hasil ke API callback Sirama.
    
    Args:
        document_id: ID dokumen dari database
        file_path: Path ke file PDF
        pdf_password: Password PDF (jika ada)
        callback_data: Dict berisi letter_id dan download_url
    """
    
    try:
        print(f"[OCR TASK] Starting OCR for document_id: {document_id}")
        
        # Verify file exists
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # --- Load PDF ---
        reader = PyPDF2.PdfReader(file_path)

        # Handle password
        if reader.is_encrypted:
            try_passwords = [pdf_password or "", ""]
            decrypted = False
            
            for pw in try_passwords:
                try:
                    result = reader.decrypt(pw)
                    if result in (1, 2):  # 1 = user password, 2 = owner password
                        decrypted = True
                        print(f"[OCR TASK] PDF decrypted successfully")
                        break
                except Exception as e:
                    print(f"[OCR TASK] Decryption attempt failed: {e}")
                    continue
            
            if not decrypted:
                raise Exception("PDF encrypted, wrong password or unable to decrypt")

        # --- Convert PDF → images ---
        print(f"[OCR TASK] Converting PDF to images...")
        pages = convert_from_path(
            file_path,
            dpi=300,
            fmt="png",
            userpw=pdf_password if pdf_password else None
        )
        
        print(f"[OCR TASK] Converted {len(pages)} pages")

        # --- OCR bahasa inggris + indonesia ---
        ocr_lang = "eng+ind"
        full_text = ""

        for num, img in enumerate(pages, start=1):
            print(f"[OCR TASK] Processing page {num}/{len(pages)}")
            try:
                text = pytesseract.image_to_string(img, lang=ocr_lang)
                full_text += f"\n\n===== PAGE {num} =====\n{text}"
            except Exception as e:
                print(f"[OCR TASK] Error on page {num}: {e}")
                full_text += f"\n\n===== PAGE {num} =====\nERROR: {str(e)}\n"

        print(f"[OCR TASK] OCR completed. Total characters: {len(full_text)}")

        # =============================
        # CALL CALLBACK API
        # =============================
        if callback_data and "letter_id" in callback_data:
            letter_id = callback_data.get("letter_id")
            download_url = callback_data.get("download_url", "")
            
            print(f"[OCR TASK] Sending callback for letter_id: {letter_id}")
            callback_success = send_callback(
                letter_id=letter_id,
                extracted_text=full_text,
                download_url=download_url
            )
            
            if not callback_success:
                print("[OCR TASK] Callback failed but OCR completed")
        else:
            print("[OCR TASK] No callback_data provided, skipping callback")

        return {
            "status": "success",
            "document_id": document_id,
            "pages_processed": len(pages),
            "text_length": len(full_text)
        }

    except Exception as e:
        print(f"[OCR TASK ERROR] {str(e)}")
        
        # Retry logic
        try:
            raise self.retry(exc=e, countdown=60)  # Retry after 60 seconds
        except self.MaxRetriesExceededError:
            print(f"[OCR TASK] Max retries exceeded for document_id: {document_id}")
            return {
                "status": "failed",
                "error": str(e),
                "document_id": document_id
            }


# ===================================
# COMPRESS + OCR TASK (Combined)
# ===================================
@celery.task(name="tasks.ocr_and_compress_task", bind=True, max_retries=3)
def ocr_and_compress_task(self, document_id, file_path, pdf_password, callback_data):
    """
    Kombinasi OCR dan Compress dalam satu task.
    Digunakan jika Anda perlu OCR + Compress bersamaan.
    """
    
    try:
        # 1. Lakukan OCR
        ocr_result = ocr_task(document_id, file_path, pdf_password, callback_data)
        
        if ocr_result.get("status") != "success":
            raise Exception(f"OCR failed: {ocr_result.get('error')}")
        
        # 2. Lakukan Compress (tambahkan logika compress di sini jika diperlukan)
        # ...
        
        return {
            "status": "success",
            "document_id": document_id,
            "ocr_result": ocr_result
        }
        
    except Exception as e:
        print(f"[OCR+COMPRESS TASK ERROR] {str(e)}")
        try:
            raise self.retry(exc=e, countdown=60)
        except self.MaxRetriesExceededError:
            return {"status": "failed", "error": str(e)}