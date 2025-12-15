from celery_app import make_celery
import os, PyPDF2, pytesseract
from pdf2image import convert_from_path
import requests
from dotenv import load_dotenv

load_dotenv()

celery = make_celery()

# Get Poppler path if needed
POPPLER_PATH = os.getenv("POPPLER_PATH", None)


@celery.task(name="tasks.ocr_task", bind=True, max_retries=3)
def ocr_task(self, file_path, pdf_password, ocr_output_path, callback_data):
    """
    Async task untuk OCR PDF - tanpa database
    """
    try:
        print(f"🔄 Starting OCR task for file: {file_path}")
        
        # Validasi file exists
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File tidak ditemukan: {file_path}")

        # Read PDF
        reader = PyPDF2.PdfReader(file_path)

        # Handle encryption
        if reader.is_encrypted:
            decrypted = False
            try_passwords = []
            if pdf_password:
                try_passwords.append(pdf_password)
            try_passwords.append("")
            
            for pw in try_passwords:
                try:
                    res = reader.decrypt(pw)
                    if res == 1 or res is True:
                        decrypted = True
                        print(f"✓ PDF decrypted successfully")
                        break
                except:
                    pass
            
            if not decrypted:
                raise Exception("PDF terenkripsi dan password tidak valid")

        # Check if PDF has copy protection (image-only PDF)
        has_extractable_text = False
        try:
            for page in reader.pages:
                text = page.extract_text()
                if text and text.strip():
                    has_extractable_text = True
                    break
        except:
            has_extractable_text = False

        if has_extractable_text:
            print(f"ℹ️  PDF memiliki text yang bisa di-extract (tidak fully protected)")
        else:
            print(f"🔒 PDF protected atau image-only, menggunakan OCR")

        print(f"🖼️  Converting PDF to images...")
        # Convert PDF → Images untuk OCR
        pages = convert_from_path(
            file_path,
            dpi=300,
            fmt="png",
            userpw=pdf_password if pdf_password else None,
            poppler_path=POPPLER_PATH if POPPLER_PATH else None
        )

        # OCR process
        ocr_lang = "eng+ind"
        full_text = ""
        text_by_page = []

        print(f"🔍 Processing {len(pages)} pages with Tesseract OCR...")
        for page_num, img in enumerate(pages, start=1):
            try:
                # Perform OCR on image
                page_text = pytesseract.image_to_string(img, lang=ocr_lang)
                
                # Clean up text
                page_text = page_text.strip()
                
                print(f"✓ Page {page_num}/{len(pages)} processed - {len(page_text)} characters extracted")
                
                # Simpan ke list untuk response
                text_by_page.append({
                    "page": page_num,
                    "text": page_text,
                    "char_count": len(page_text)
                })

                full_text += f"\n\n===== PAGE {page_num} =====\n{page_text}\n"

            except Exception as e_page:
                error_msg = f"ERROR OCR: {str(e_page)}"
                print(f"✗ Page {page_num} failed: {error_msg}")
                
                text_by_page.append({
                    "page": page_num,
                    "text": error_msg,
                    "error": True
                })
                
                full_text += f"\n\n===== PAGE {page_num} =====\n{error_msg}\n"

        # Save extracted text to file
        print(f"💾 Saving extracted text to: {ocr_output_path}")
        with open(ocr_output_path, "w", encoding="utf-8") as f:
            f.write(full_text)

        # Send callback to SIRAMA if letter_id provided
        if callback_data and callback_data.get("letter_id"):
            print(f"📤 Sending callback for letter_id: {callback_data.get('letter_id')}")
            send_callback(
                letter_id=callback_data["letter_id"],
                extracted_text=full_text,
                download_url=callback_data.get("download_url"),
                has_protection=not has_extractable_text,
                total_pages=len(pages)
            )

        print(f"✅ OCR completed successfully - Total: {len(full_text)} characters")
        
        return {
            "status": "success",
            "pages_processed": len(pages),
            "has_copy_protection": not has_extractable_text,
            "output_file": ocr_output_path,
            "total_characters": len(full_text),
            "text_by_page": text_by_page
        }

    except Exception as e:
        error_msg = str(e)
        print(f"❌ OCR task failed: {error_msg}")
        
        # Retry mechanism
        try:
            print(f"🔄 Retrying task in 60 seconds...")
            raise self.retry(exc=e, countdown=60)
        except self.MaxRetriesExceededError:
            print(f"❌ Max retries exceeded")
            # Send failed callback if applicable
            if callback_data and callback_data.get("letter_id"):
                send_callback_failed(
                    letter_id=callback_data["letter_id"],
                    error=error_msg
                )
            
            return {
                "status": "failed",
                "error": error_msg
            }


def send_callback(letter_id, extracted_text, download_url, has_protection=False, total_pages=0):
    """
    Kirim callback ke SIRAMA setelah OCR selesai
    """
    CALLBACK_URL = os.getenv("CALLBACK_URL")
    CALLBACK_TOKEN = os.getenv("CALLBACK_TOKEN")

    if not CALLBACK_URL:
        print("⚠️  Callback URL tidak diatur di environment - skip callback")
        return False

    headers = {
        "Authorization": f"Bearer {CALLBACK_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "letter_id": letter_id,
        "extracted_text": extracted_text,
        "download_url": download_url,
        "has_copy_protection": has_protection,
        "total_pages": total_pages,
        "status": "completed"
    }

    try:
        print(f"📡 Sending callback to: {CALLBACK_URL}")
        r = requests.post(CALLBACK_URL, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        print(f"✓ Callback berhasil dikirim untuk letter_id: {letter_id}")
        return True
    except Exception as e:
        print(f"✗ Callback gagal untuk letter_id {letter_id}: {str(e)}")
        return False


def send_callback_failed(letter_id, error):
    """
    Kirim callback ke SIRAMA jika OCR gagal
    """
    CALLBACK_URL = os.getenv("CALLBACK_URL")
    CALLBACK_TOKEN = os.getenv("CALLBACK_TOKEN")

    if not CALLBACK_URL:
        return False

    headers = {
        "Authorization": f"Bearer {CALLBACK_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "letter_id": letter_id,
        "status": "failed",
        "error": error
    }

    try:
        r = requests.post(CALLBACK_URL, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        print(f"✓ Failed callback sent for letter_id: {letter_id}")
        return True
    except Exception as e:
        print(f"✗ Failed callback error: {str(e)}")
        return False