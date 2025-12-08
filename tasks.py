from celery_app import make_celery
import os, PyPDF2, pytesseract
from pdf2image import convert_from_path
import requests
from dotenv import load_dotenv
load_dotenv()

celery = make_celery()

@celery.task(name="tasks.ocr_task", bind=True, max_retries=3)
def ocr_task(self, document_id, file_path, pdf_password, callback_data):
    try:
        if not os.path.exists(file_path):
            raise FileNotFoundError("File hilang / sudah dihapus")

        reader = PyPDF2.PdfReader(file_path)

        if reader.is_encrypted:
            try:
                reader.decrypt(pdf_password or "")
            except:
                raise Exception("Password PDF salah")

        pages = convert_from_path(
            file_path,
            dpi=300,
            fmt="png",
            userpw=pdf_password
        )

        full_text = ""
        for page_num, img in enumerate(pages, start=1):
            try:
                text = pytesseract.image_to_string(img, lang="eng+ind")
                full_text += f"\n\n===== PAGE {page_num} =====\n{text}"
            except Exception as e:
                full_text += f"\n\n===== PAGE {page_num} =====\nERROR: {str(e)}\n"

        # update DB
        update_ocr_status(document_id, "completed", full_text)

        # send callback only if letter_id provided
        if callback_data and callback_data.get("letter_id"):
            send_callback(
                letter_id=callback_data["letter_id"],
                extracted_text=full_text,
                download_url=callback_data.get("download_url")
            )

        return {
            "status": "success",
            "document_id": document_id,
            "pages_processed": len(pages)
        }

    except Exception as e:
        try:
            raise self.retry(exc=e, countdown=60)
        except self.MaxRetriesExceededError:
            update_ocr_status(document_id, "failed", str(e))
            return {"status": "failed", "error": str(e)}

def send_callback(letter_id, extracted_text, download_url):
    CALLBACK_URL = os.getenv("CALLBACK_URL")
    CALLBACK_TOKEN = os.getenv("CALLBACK_TOKEN")

    if not CALLBACK_URL:
        print("Callback URL tidak diatur")
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
        r = requests.post(CALLBACK_URL, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:
        print("Callback gagal:", e)
        return False
