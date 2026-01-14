from flask import Flask, request, jsonify, send_file
from flask_swagger_ui import get_swaggerui_blueprint
from flask_cors import CORS, cross_origin
from werkzeug.utils import secure_filename
import os
from datetime import datetime
import PyPDF2
import uuid
from pdf2image import convert_from_path
import pytesseract
from PIL import Image
import mysql.connector
from mysql.connector import Error
import json
import subprocess
from dotenv import load_dotenv
from tasks import ocr_task
from tools_config import GHOSTSCRIPT_PATH, LIBREOFFICE_PATH, POPPLER_PATH

app = Flask(__name__)

load_dotenv()

# Enable CORS untuk semua routes
CORS(app, 
     resources={
         r"/*": {
             "origins": "*",
             "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
             "allow_headers": ["Content-Type", "Authorization", "X-Requested-With", "Accept"],
             "expose_headers": ["Content-Type", "Content-Disposition"],
             "supports_credentials": False,
             "max_age": 3600
         }
     })

# @app.after_request
# def after_request(response):
#     response.headers.add('Access-Control-Allow-Origin', '*')
#     response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With,Accept')
#     response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
#     response.headers.add('Access-Control-Max-Age', '3600')
#     return response

# Handle OPTIONS globally
# @app.before_request
# def handle_preflight():
#     if request.method == "OPTIONS":
#         response = app.make_default_options_response()
#         response.headers.add('Access-Control-Allow-Origin', '*')
#         response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With,Accept')
#         response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
#         return response

# Konfigurasi
UPLOAD_FOLDER = 'uploads'
OUTPUT_OCR_FOLDER = 'ocr_results'
COMPRESSED_FOLDER = 'compressed'
CONVERTED_FOLDER = 'converted'
SPLIT_FOLDER = 'splitted'
MERGED_FOLDER = 'merged'
ALLOWED_EXTENSIONS = {'pdf'}

CALLBACK_URL = os.getenv("CALLBACK_URL")
CALLBACK_TOKEN = os.getenv("CALLBACK_TOKEN")

BASE_URL = os.getenv("BASE_URL")

# Buat folder jika belum ada
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(COMPRESSED_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_OCR_FOLDER, exist_ok=True)
os.makedirs(CONVERTED_FOLDER, exist_ok=True)
os.makedirs(SPLIT_FOLDER, exist_ok=True)
os.makedirs(MERGED_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['COMPRESSED_FOLDER'] = COMPRESSED_FOLDER
app.config['OUTPUT_OCR_FOLDER'] = OUTPUT_OCR_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Max 16MB
app.config['CONVERTED_FOLDER'] = CONVERTED_FOLDER
app.config['SPLIT_FOLDER'] = SPLIT_FOLDER
app.config['MERGED_FOLDER'] = MERGED_FOLDER

# ==================== DATABASE CONFIGURATION ====================
# Konfigurasi untuk DBngin (Mac)
# DBngin biasanya menggunakan port berbeda, cek di aplikasi DBngin
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASS', ''),  # default kosong kalau tidak ada
    'database': os.getenv('DB_NAME', 'pdf_tools_db'),
    'port': int(os.getenv('DB_PORT', 3306)),
}

def get_db_connection():
    """Get connection to dokumi database using environment variables"""
    try:
        connection = mysql.connector.connect(
            host=os.getenv('DB_HOST', '127.0.0.1'),
            user=os.getenv('DB_USER', 'root'),
            password=os.getenv('DB_PASS', ''),
            database=os.getenv('DB_NAME', 'dokumi'),
            port=int(os.getenv('DB_PORT', 3306)),
            charset='utf8mb4',
            use_unicode=True
        )
        return connection
    except Error as e:
        print(f"Error connecting to dokumi database: {e}")
        return None

def init_database():
    """Inisialisasi database dan tabel"""
    try:
        # Koneksi tanpa database untuk membuat database
        connection = mysql.connector.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            port=DB_CONFIG['port']
        )
        cursor = connection.cursor()
        
        # Buat database jika belum ada
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_CONFIG['database']}")
        cursor.execute(f"USE {DB_CONFIG['database']}")
        
        # Buat tabel untuk OCR
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ocr_files (
                id INT AUTO_INCREMENT PRIMARY KEY,
                file_id VARCHAR(255) UNIQUE NOT NULL,
                original_filename VARCHAR(255) NOT NULL,
                file_path VARCHAR(500) NOT NULL,
                upload_time DATETIME NOT NULL,
                status VARCHAR(50) NOT NULL,
                page_count INT,
                extracted_text LONGTEXT,
                text_length INT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        
        # Buat tabel untuk Compress
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS compressed_files (
                id INT AUTO_INCREMENT PRIMARY KEY,
                file_id VARCHAR(255) UNIQUE NOT NULL,
                original_filename VARCHAR(255) NOT NULL,
                original_file_path VARCHAR(500) NOT NULL,
                compressed_file_path VARCHAR(500) NOT NULL,
                upload_time DATETIME NOT NULL,
                status VARCHAR(50) NOT NULL,
                original_size BIGINT,
                compressed_size BIGINT,
                compression_ratio DECIMAL(5,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        
        connection.commit()
        cursor.close()
        connection.close()
        print("✅ Database dan tabel berhasil dibuat!")
        
    except Error as e:
        print(f"❌ Error saat inisialisasi database: {e}")

# Inisialisasi database saat aplikasi start
init_database()

# ==================== SWAGGER CONFIGURATION ====================
SWAGGER_URL = '/docs/api'
API_URL = '/static/swagger.json'

swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={'app_name': "PDF Tools API"}
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

# ==================== HELPER FUNCTIONS ====================
def allowed_file(filename):
    """Cek apakah file yang diupload adalah PDF"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_ocr_to_db(file_id, original_filename, file_path, status='uploaded'):
    """Simpan data OCR ke database"""
    try:
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor()
            query = """
                INSERT INTO ocr_files (file_id, original_filename, file_path, upload_time, status)
                VALUES (%s, %s, %s, %s, %s)
            """
            cursor.execute(query, (file_id, original_filename, file_path, datetime.now(), status))
            connection.commit()
            cursor.close()
            connection.close()
            return True
    except Error as e:
        print(f"Error saving OCR to DB: {e}")
    return False

def update_ocr_results(file_id, extracted_text, page_count, status='completed'):
    """Update hasil OCR di database"""
    try:
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor()
            query = """
                UPDATE ocr_files 
                SET extracted_text = %s, page_count = %s, text_length = %s, status = %s
                WHERE file_id = %s
            """
            cursor.execute(query, (extracted_text, page_count, len(extracted_text), status, file_id))
            connection.commit()
            cursor.close()
            connection.close()
            return True
    except Error as e:
        print(f"Error updating OCR results: {e}")
    return False

def save_compress_to_db(file_id, original_filename, original_path, compressed_path, 
                        original_size, compressed_size, compression_ratio):
    """Simpan data compress ke database"""
    try:
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor()
            query = """
                INSERT INTO compressed_files 
                (file_id, original_filename, original_file_path, compressed_file_path, 
                 upload_time, status, original_size, compressed_size, compression_ratio)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(query, (
                file_id, original_filename, original_path, compressed_path,
                datetime.now(), 'completed', original_size, compressed_size, compression_ratio
            ))
            connection.commit()
            cursor.close()
            connection.close()
            return True
    except Error as e:
        print(f"Error saving compress to DB: {e}")
    return False

def get_ocr_from_db(file_id):
    """Ambil data OCR dari database"""
    try:
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor(dictionary=True)
            query = "SELECT * FROM ocr_files WHERE file_id = %s"
            cursor.execute(query, (file_id,))
            result = cursor.fetchone()
            cursor.close()
            connection.close()
            return result
    except Error as e:
        print(f"Error getting OCR from DB: {e}")
    return None

def get_compress_from_db(file_id):
    """Ambil data compress dari database"""
    try:
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor(dictionary=True)
            query = "SELECT * FROM compressed_files WHERE file_id = %s"
            cursor.execute(query, (file_id,))
            result = cursor.fetchone()
            cursor.close()
            connection.close()
            return result
    except Error as e:
        print(f"Error getting compress from DB: {e}")
    return None


# ===================== OCR AND COMPRESS helper function ===========================
def create_documents_entry(original_filename, file_path, file_type, size, total_page):
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        INSERT INTO documents 
        (uuid, file_name, type, size, total_page, file_path, 
         is_letter_sirama, is_protected_text, is_passworded,
         upload_at, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, 0, 0, 0, %s, %s, %s)
    """

    uuid_str = uuid.uuid4().hex
    now = datetime.now()

    cursor.execute(sql, (
        uuid_str,
        original_filename,
        file_type,
        size,
        total_page,
        file_path,
        now, now, now
    ))
    conn.commit()

    document_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return document_id


def create_ocr_entry(document_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        INSERT INTO ocr_files (document_id, metadata_file, extracted_text, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    """

    now = datetime.now()

    cursor.execute(sql, (document_id, "{}", "", "processing", now, now))
    conn.commit()

    ocr_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return ocr_id


def update_ocr_status(ocr_id, status, extracted_text="", metadata_file=None):
    if not ocr_id:
        # Jangan coba update jika ocr_id falsy
        return False

    # Pastikan extracted_text selalu string (hindari None)
    if extracted_text is None:
        extracted_text = ""

    # Convert to str explicitly (safety)
    extracted_text = str(extracted_text)

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Jika ingin juga menyimpan metadata JSON (opsional)
        if metadata_file is not None:
            sql = """
                UPDATE ocr_files
                SET status=%s, extracted_text=%s, metadata_file=%s, updated_at=%s
                WHERE id=%s
            """
            params = (status, extracted_text, metadata_file, datetime.now(), ocr_id)
        else:
            sql = """
                UPDATE ocr_files
                SET status=%s, extracted_text=%s, updated_at=%s
                WHERE id=%s
            """
            params = (status, extracted_text, datetime.now(), ocr_id)

        cursor.execute(sql, params)
        conn.commit()

        cursor.close()
        conn.close()
        return True

    except Error as e:
        # Anda bisa mengganti print dengan logger jika tersedia
        print(f"[update_ocr_status] MySQL Error: {e}")
        try:
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()
        except:
            pass
        return False

def insert_ocr_page(document_id, page_number, text):
    try:
        connection = get_connection()
        cursor = connection.cursor()

        query = """
            INSERT INTO ocr_files (document_id, page_number, extracted_text, status)
            VALUES (%s, %s, %s, %s)
        """
        cursor.execute(query, (document_id, page_number, text, "completed"))
        connection.commit()

        return cursor.lastrowid

    except Exception as e:
        print("DB Error (insert_ocr_page):", e)
        return None


def create_compressed_entry(document_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now()

    sql = """
        INSERT INTO compressed_files (document_id, status, extracted_path, extracted_size, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
    """

    cursor.execute(sql, (document_id, "processing", None, None, now, now))
    conn.commit()

    compress_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return compress_id


def update_compress_status(compress_id, status, output_path=None, output_size=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        UPDATE compressed_files 
        SET status=%s, extracted_path=%s, extracted_size=%s, updated_at=%s
        WHERE id=%s
    """

    now = datetime.now()

    cursor.execute(sql, (status, output_path, output_size, now, compress_id))
    conn.commit()

    cursor.close()
    conn.close()

# ===================== END OF OCR AND COMPRESS helper function ====================

def parse_human_size(size_str: str) -> int:
    size_str = str(size_str).strip().upper()

    # Jika sudah integer → langsung kembalikan
    if size_str.isdigit():
        return int(size_str)

    # Parsing human readable
    if size_str.endswith("KB"):
        return int(float(size_str.replace("KB", "")) * 1024)
    elif size_str.endswith("MB"):
        return int(float(size_str.replace("MB", "")) * 1024 * 1024)
    elif size_str.endswith("GB"):
        return int(float(size_str.replace("GB", "")) * 1024 * 1024 * 1024)
    else:
        return int(float(size_str))  # fallback
        

def convert_size(size_input):
    size_bytes = parse_human_size(size_input)

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.2f}{unit}"
        size_bytes /= 1024


def compress_with_gs(input_path, output_path, quality="screen"):
    """
    quality options:
    - screen  (72 dpi, paling kecil)
    - ebook   (150 dpi)
    - printer (300 dpi)
    - prepress (lebih tajam)
    """

    # command GS
    cmd = [
        GHOSTSCRIPT_PATH,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        "-dPDFSETTINGS=/" + quality,
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={output_path}",
        input_path
    ]

    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if process.returncode != 0:
        raise Exception(f"Ghostscript error: {process.stderr.decode()}")


def create_convert_entry(document_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    uuid_str = uuid.uuid4().hex
    now = datetime.now()

    sql = """
        INSERT INTO convert_files (uuid, document_id, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s)
    """

    cursor.execute(sql, (uuid_str, document_id, "processing", now, now))
    conn.commit()

    convert_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return convert_id, uuid_str


def update_convert_status(convert_id, status, converted_path=None, converted_file_name=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        UPDATE convert_files 
        SET status=%s, converted_path=%s, converted_file_name=%s, updated_at=%s
        WHERE id=%s
    """

    now = datetime.now()
    cursor.execute(sql, (status, converted_path, converted_file_name, now, convert_id))
    conn.commit()

    cursor.close()
    conn.close()


def create_merge_entry(document_ids):
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now()
    document_ids_json = json.dumps(document_ids)

    sql = """
        INSERT INTO merge_files (document_id, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s)
    """

    cursor.execute(sql, (document_ids_json, "processing", now, now))
    conn.commit()

    merge_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return merge_id


def update_merge_status(merge_id, status, merged_path=None, merged_file_name=None, merged_size=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        UPDATE merge_files 
        SET status=%s, merged_path=%s, merged_file_name=%s, merged_size=%s, updated_at=%s
        WHERE id=%s
    """

    now = datetime.now()
    cursor.execute(sql, (status, merged_path, merged_file_name, merged_size, now, merge_id))
    conn.commit()

    cursor.close()
    conn.close()


def create_split_entry(document_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    uuid_str = uuid.uuid4().hex
    now = datetime.now()

    sql = """
        INSERT INTO split_files (uuid, document_id, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s)
    """

    cursor.execute(sql, (uuid_str, document_id, "processing", now, now))
    conn.commit()

    split_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return split_id, uuid_str


def update_split_status(split_id, status, splited_path=None, splited_file_name=None, splited_size=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    sql = """
        UPDATE split_files 
        SET status=%s, splited_path=%s, splited_file_name=%s, splited_size=%s, updated_at=%s
        WHERE id=%s
    """

    now = datetime.now()
    cursor.execute(sql, (status, splited_path, splited_file_name, splited_size, now, split_id))
    conn.commit()

    cursor.close()
    conn.close()

# Callback to Sirama API
def send_callback_to_sirama(letter_id, full_text, compressed_url):
    payload = {
        "letter_id": letter_id,
        "extracted_text": full_text,
        "download_url": compressed_url
    }

    headers = {
        "Authorization": f"Bearer {CALLBACK_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(CALLBACK_URL, json=payload, headers=headers)
        response.raise_for_status()
        print("Callback success:", response.json())
    except Exception as callback_err:
        print("Callback failed:", callback_err)


# ==================== OCR ENDPOINT ====================
@app.route('/docs/api/tools/ocr', methods=['POST'])
def ocr_pdf():
    """
    Endpoint OCR PDF (Asynchronous with Celery + Database)
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Tidak ada file yang diupload', 'status': 'failed'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'error': 'Tidak ada file yang dipilih', 'status': 'failed'}), 400

        if not allowed_file(file.filename):
            return jsonify({'error': 'File harus berformat PDF', 'status': 'failed'}), 400

        pdf_password = request.form.get('password', None)
        letter_id = request.form.get('letter_id')

        # Save input PDF
        original_filename = secure_filename(file.filename)
        file_id = f"ocr_{uuid.uuid4().hex}_{original_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id)
        file.save(file_path)

        # Quick validation
        try:
            reader = PyPDF2.PdfReader(file_path)
            
            # Handle encryption early
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
                            break
                    except:
                        pass
                
                if not decrypted:
                    os.remove(file_path)
                    return jsonify({
                        'error': 'PDF terenkripsi. Tambahkan password yang benar.', 
                        'status': 'failed'
                    }), 400
            
            total_pages = len(reader.pages)
            
        except Exception as e:
            if os.path.exists(file_path):
                os.remove(file_path)
            return jsonify({
                'error': f'File PDF tidak valid atau rusak: {str(e)}', 
                'status': 'failed'
            }), 400

        file_size = os.path.getsize(file_path)

        # 1. CREATE DOCUMENT ENTRY
        document_id = create_documents_entry(
            original_filename,
            file_path,
            ".pdf",
            file_size,
            total_pages
        )

        # 2. CREATE OCR ENTRY (status: processing)
        ocr_id = create_ocr_entry(document_id)

        # 3. GENERATE OUTPUT PATH
        ocr_folder = os.path.join(os.getcwd(), "ocr_results")
        os.makedirs(ocr_folder, exist_ok=True)
        ocr_filename = f"{uuid.uuid4().hex}_ocr.txt"
        ocr_output_path = os.path.join(ocr_folder, ocr_filename)

        # 4. QUEUE TO CELERY
        from tasks import ocr_task_with_db
        
        task = ocr_task_with_db.delay(
            document_id=document_id,
            ocr_id=ocr_id,
            file_path=file_path,
            pdf_password=pdf_password,
            ocr_output_path=ocr_output_path,
            callback_data={
                "letter_id": letter_id,
                "download_url": f"{BASE_URL}/download/ocr/{ocr_filename}"
            }
        )

        # 5. RETURN IMMEDIATELY
        return jsonify({
            "status": "processing",
            "message": "OCR sedang diproses secara asynchronous",
            "task_id": task.id,
            "document_id": document_id,
            "ocr_id": ocr_id,
            "filename": original_filename,
            "download_url": f"{BASE_URL}/download/ocr/{ocr_filename}",
            "total_pages": total_pages,
            "letter_id": letter_id,
            "check_status_url": f"{BASE_URL}/docs/api/tools/ocr/status/{ocr_id}"
        }), 202  # 202 Accepted

    except Exception as e:
        return jsonify({'error': str(e), 'status': 'failed'}), 500


@app.route('/docs/api/tools/ocr/status/<int:ocr_id>', methods=['GET'])
def ocr_status(ocr_id):
    """
    Check OCR status dari database
    """
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed', 'status': 'failed'}), 500
            
        cursor = connection.cursor(dictionary=True)
        
        # Get OCR record
        query = """
            SELECT o.*, d.file_name, d.total_page, d.size 
            FROM ocr_files o
            JOIN documents d ON o.document_id = d.id
            WHERE o.id = %s
        """
        cursor.execute(query, (ocr_id,))
        result = cursor.fetchone()
        
        cursor.close()
        connection.close()
        
        if not result:
            return jsonify({'error': 'OCR record not found', 'status': 'failed'}), 404
        
        # Format response
        response = {
            'status': result['status'],
            'ocr_id': result['id'],
            'document_id': result['document_id'],
            'filename': result['file_name'],
            'total_pages': result['total_page'],
            'created_at': result['created_at'].isoformat() if result['created_at'] else None,
            'updated_at': result['updated_at'].isoformat() if result['updated_at'] else None
        }
        
        # Add extracted text if completed
        if result['status'] == 'completed' and result['extracted_text']:
            response['extracted_text'] = result['extracted_text']
            response['text_length'] = len(result['extracted_text'])
            response['preview'] = result['extracted_text'][:500] + '...' if len(result['extracted_text']) > 500 else result['extracted_text']
        
        # Add error if failed
        if result['status'] == 'failed':
            response['error'] = result.get('extracted_text', 'Unknown error')
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({'error': str(e), 'status': 'failed'}), 500

@app.route('/docs/api/tools/ocr-async', methods=['POST'])
def ocr_async():
    """
    Endpoint untuk OCR asynchronous dengan Celery
    Form fields:
    - file: PDF file
    - letter_id: ID surat dari SIRAMA
    - password: (optional) Password PDF jika terenkripsi
    - compressed_url: (optional) URL file compressed
    """
    try:
        if 'file' not in request.files:
            return jsonify({
                'error': 'Tidak ada file yang diupload',
                'status': 'failed'
            }), 400

        file = request.files['file']
        
        if file.filename == '':
            return jsonify({
                'error': 'Tidak ada file yang dipilih',
                'status': 'failed'
            }), 400

        if not allowed_file(file.filename):
            return jsonify({
                'error': 'File harus berformat PDF',
                'status': 'failed'
            }), 400

        # Get form data
        letter_id = request.form.get('letter_id')
        pdf_password = request.form.get('password', None)
        compressed_url = request.form.get('compressed_url', '')

        if not letter_id:
            return jsonify({
                'error': 'letter_id is required',
                'status': 'failed'
            }), 400

        # Save file
        original_filename = secure_filename(file.filename)
        file_id = f"ocr_{uuid.uuid4().hex}_{original_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id)
        file.save(file_path)

        file_size = os.path.getsize(file_path)

        # Get total pages
        try:
            reader = PyPDF2.PdfReader(file_path)
            total_pages = len(reader.pages)
        except:
            total_pages = 0

        # Create document entry
        document_id = create_documents_entry(
            original_filename,
            file_path,
            ".pdf",
            file_size,
            total_pages
        )

        # Create OCR entry
        ocr_id = create_ocr_entry(document_id)

        # Queue task to Celery
        task = ocr_and_compress_task.delay(
            document_id=document_id,
            file_path=file_path,
            pdf_password=pdf_password,
            callback_data={
                "letter_id": letter_id,
                "download_url": f"{BASE_URL}/download/ocr/{ocr_filename}"
            }
        )

        return jsonify({
            "status": "queued",
            "message": "OCR is being processed asynchronously",
            "letter_id": letter_id,
            "document_id": document_id,
            "ocr_id": ocr_id,
            "task_id": task.id,
            "original_filename": original_filename
        }), 202  # 202 Accepted

    except Exception as e:
        return jsonify({
            'status': 'failed',
            'error': str(e)
        }), 500

@app.route('/docs/api/tools/simple-ocr', methods=['POST'])
def simple_ocr():
    """
    Endpoint OCR PDF sederhana tanpa database
    Support: Digital text PDF & Scanned PDF
    Return: Extracted text langsung
    """
    try:
        # Validasi file upload
        if 'file' not in request.files:
            return jsonify({
                'error': 'Tidak ada file yang diupload',
                'status': 'failed'
            }), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({
                'error': 'Tidak ada file yang dipilih',
                'status': 'failed'
            }), 400

        if not allowed_file(file.filename):
            return jsonify({
                'error': 'File harus berformat PDF',
                'status': 'failed'
            }), 400

        # Optional: Password untuk PDF terenkripsi
        pdf_password = request.form.get('password', None)

        # Save temporary file
        original_filename = secure_filename(file.filename)
        temp_id = f"temp_ocr_{uuid.uuid4().hex}_{original_filename}"
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_id)
        file.save(temp_path)

        try:
            # Baca PDF
            reader = PyPDF2.PdfReader(temp_path)
            
            # Handle encryption
            if reader.is_encrypted:
                decrypted = False
                try_passwords = [pdf_password] if pdf_password else []
                try_passwords.append("")  # Try empty password
                
                for pw in try_passwords:
                    try:
                        res = reader.decrypt(pw)
                        if res == 1 or res is True:
                            decrypted = True
                            break
                    except:
                        pass
                
                if not decrypted:
                    os.remove(temp_path)
                    return jsonify({
                        'error': 'PDF terenkripsi. Gunakan parameter "password"',
                        'status': 'failed'
                    }), 400
            
            total_pages = len(reader.pages)
            
            # Check apakah PDF punya text yang bisa di-extract (digital text)
            has_extractable_text = False
            digital_text = ""
            
            print(f"📄 Checking {total_pages} pages for extractable text...")
            
            for page_num, page in enumerate(reader.pages, start=1):
                try:
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        has_extractable_text = True
                        digital_text += f"\n\n===== PAGE {page_num} =====\n{page_text.strip()}\n"
                except:
                    pass
            
            # Tentukan metode OCR
            if has_extractable_text:
                # PDF Digital Text - sudah ada text
                print(f"✓ PDF memiliki digital text - menggunakan PyPDF2 extraction")
                
                result = {
                    'status': 'success',
                    'message': 'Text extraction completed (Digital PDF)',
                    'method': 'digital_extraction',
                    'filename': original_filename,
                    'total_pages': total_pages,
                    'total_characters': len(digital_text),
                    'extracted_text': digital_text.strip(),
                    'has_copy_protection': False
                }
                
            else:
                # PDF Scan - perlu OCR dengan Tesseract
                print(f"🔒 PDF tidak punya text atau protected - menggunakan Tesseract OCR")
                
                # Convert PDF to images
                print(f"🖼️  Converting PDF to images (DPI: 300)...")
                pages = convert_from_path(
                    temp_path,
                    dpi=300,
                    fmt="png",
                    userpw=pdf_password if pdf_password else None,
                    poppler_path=POPPLER_PATH if POPPLER_PATH else None
                )
                
                # OCR with Tesseract
                ocr_lang = "eng+ind"  # English + Indonesian
                full_text = ""
                
                print(f"🔍 Processing {len(pages)} pages with Tesseract OCR...")
                
                for page_num, img in enumerate(pages, start=1):
                    try:
                        # OCR per page
                        page_text = pytesseract.image_to_string(img, lang=ocr_lang)
                        page_text = page_text.strip()
                        
                        if page_text:
                            full_text += f"\n\n===== PAGE {page_num} =====\n{page_text}\n"
                            print(f"✓ Page {page_num}/{len(pages)} - {len(page_text)} characters")
                        else:
                            print(f"⚠️  Page {page_num}/{len(pages)} - No text detected")
                            full_text += f"\n\n===== PAGE {page_num} =====\n[No text detected]\n"
                        
                    except Exception as e_page:
                        error_msg = f"Error: {str(e_page)}"
                        print(f"✗ Page {page_num}/{len(pages)} failed: {error_msg}")
                        full_text += f"\n\n===== PAGE {page_num} =====\n{error_msg}\n"
                
                result = {
                    'status': 'success',
                    'message': 'OCR completed (Scanned PDF)',
                    'method': 'tesseract_ocr',
                    'filename': original_filename,
                    'total_pages': total_pages,
                    'total_characters': len(full_text),
                    'extracted_text': full_text.strip(),
                    'has_copy_protection': True
                }
            
            # Cleanup temporary file
            os.remove(temp_path)
            
            print(f"✅ OCR Success: {result['total_characters']} characters extracted")
            
            return jsonify(result), 200
            
        except Exception as e:
            # Cleanup on error
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e
            
    except Exception as e:
        return jsonify({
            'error': str(e),
            'status': 'failed'
        }), 500


# Optional: Endpoint untuk save hasil OCR ke file
@app.route('/docs/api/tools/simple-ocr-save', methods=['POST'])
def simple_ocr_save():
    """
    OCR PDF dan simpan hasil ke file .txt
    """
    try:
        # Validasi file upload
        if 'file' not in request.files:
            return jsonify({
                'error': 'Tidak ada file yang diupload',
                'status': 'failed'
            }), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({
                'error': 'Tidak ada file yang dipilih',
                'status': 'failed'
            }), 400

        if not allowed_file(file.filename):
            return jsonify({
                'error': 'File harus berformat PDF',
                'status': 'failed'
            }), 400

        pdf_password = request.form.get('password', None)

        # Save temporary file
        original_filename = secure_filename(file.filename)
        temp_id = f"temp_ocr_{uuid.uuid4().hex}_{original_filename}"
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_id)
        file.save(temp_path)

        try:
            reader = PyPDF2.PdfReader(temp_path)
            
            # Handle encryption
            if reader.is_encrypted:
                decrypted = False
                try_passwords = [pdf_password] if pdf_password else []
                try_passwords.append("")
                
                for pw in try_passwords:
                    try:
                        res = reader.decrypt(pw)
                        if res == 1 or res is True:
                            decrypted = True
                            break
                    except:
                        pass
                
                if not decrypted:
                    os.remove(temp_path)
                    return jsonify({
                        'error': 'PDF terenkripsi. Gunakan parameter "password"',
                        'status': 'failed'
                    }), 400
            
            total_pages = len(reader.pages)
            
            # Check extractable text
            has_extractable_text = False
            extracted_text = ""
            
            for page_num, page in enumerate(reader.pages, start=1):
                try:
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        has_extractable_text = True
                        extracted_text += f"\n\n===== PAGE {page_num} =====\n{page_text.strip()}\n"
                except:
                    pass
            
            # If no extractable text, use OCR
            if not has_extractable_text:
                pages = convert_from_path(
                    temp_path,
                    dpi=300,
                    fmt="png",
                    userpw=pdf_password if pdf_password else None,
                    poppler_path=POPPLER_PATH if POPPLER_PATH else None
                )
                
                ocr_lang = "eng+ind"
                extracted_text = ""
                
                for page_num, img in enumerate(pages, start=1):
                    try:
                        page_text = pytesseract.image_to_string(img, lang=ocr_lang)
                        page_text = page_text.strip()
                        extracted_text += f"\n\n===== PAGE {page_num} =====\n{page_text}\n"
                    except Exception as e_page:
                        extracted_text += f"\n\n===== PAGE {page_num} =====\nError: {str(e_page)}\n"
            
            # Save to file
            output_filename = f"{uuid.uuid4().hex}_ocr.txt"
            output_path = os.path.join(app.config['OUTPUT_OCR_FOLDER'], output_filename)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(extracted_text.strip())
            
            # Cleanup temp file
            os.remove(temp_path)
            
            return jsonify({
                'status': 'success',
                'message': 'OCR completed and saved to file',
                'method': 'digital_extraction' if has_extractable_text else 'tesseract_ocr',
                'filename': original_filename,
                'total_pages': total_pages,
                'total_characters': len(extracted_text),
                'output_file': output_filename,
                'download_url': f"{BASE_URL}/download/ocr/{output_filename}",
                'has_copy_protection': not has_extractable_text
            }), 200
            
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e
            
    except Exception as e:
        return jsonify({
            'error': str(e),
            'status': 'failed'
        }), 500

# Optional: Endpoint untuk check status task
# Tambahkan endpoints ini ke app.py

@app.route('/docs/api/tools/task-status/<task_id>', methods=['GET'])
def check_celery_task_status(task_id):
    """
    Check Celery task status (dari Celery backend)
    """
    from celery.result import AsyncResult
    from tasks import celery
    
    try:
        task = AsyncResult(task_id, app=celery)
        
        response = {
            'task_id': task_id,
            'state': task.state,
            'status': task.state.lower()
        }
        
        if task.state == 'PENDING':
            response['message'] = 'Task sedang menunggu untuk diproses'
            
        elif task.state == 'STARTED':
            response['message'] = 'Task sedang diproses'
            response['status'] = 'processing'
            
        elif task.state == 'SUCCESS':
            response['message'] = 'Task berhasil diselesaikan'
            response['status'] = 'completed'
            response['result'] = task.result
            
        elif task.state == 'FAILURE':
            response['message'] = 'Task gagal'
            response['status'] = 'failed'
            response['error'] = str(task.info)
            
        elif task.state == 'RETRY':
            response['message'] = 'Task sedang di-retry'
            response['status'] = 'processing'
            
        else:
            response['message'] = f'Status: {task.state}'
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'status': 'failed'
        }), 500


@app.route('/docs/api/tools/ocr/list', methods=['GET'])
def list_ocr_files():
    """
    List semua OCR files dengan detail status
    """
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed', 'status': 'failed'}), 500
            
        cursor = connection.cursor(dictionary=True)
        
        # Join dengan documents table untuk info lengkap
        query = """
            SELECT 
                o.id as ocr_id,
                o.document_id,
                o.status,
                o.extracted_text,
                o.metadata_file,
                o.created_at,
                o.updated_at,
                d.file_name,
                d.type,
                d.size,
                d.total_page,
                d.file_path
            FROM ocr_files o
            JOIN documents d ON o.document_id = d.id
            ORDER BY o.created_at DESC
        """
        
        cursor.execute(query)
        results = cursor.fetchall()
        
        # Format results
        formatted_results = []
        for row in results:
            formatted_row = {
                'ocr_id': row['ocr_id'],
                'document_id': row['document_id'],
                'filename': row['file_name'],
                'status': row['status'],
                'total_pages': row['total_page'],
                'file_size': row['size'],
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
            }
            
            # Add text preview jika ada
            if row['extracted_text']:
                text_length = len(row['extracted_text'])
                formatted_row['text_length'] = text_length
                formatted_row['text_preview'] = row['extracted_text'][:200] + '...' if text_length > 200 else row['extracted_text']
            else:
                formatted_row['text_length'] = 0
                formatted_row['text_preview'] = None
            
            # Parse metadata if exists
            if row['metadata_file']:
                try:
                    import json
                    formatted_row['metadata'] = json.loads(row['metadata_file'])
                except:
                    formatted_row['metadata'] = None
            
            formatted_results.append(formatted_row)
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'status': 'success',
            'count': len(formatted_results),
            'data': formatted_results
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'status': 'failed'
        }), 500


@app.route('/docs/api/tools/ocr/detail/<int:ocr_id>', methods=['GET'])
def get_ocr_detail(ocr_id):
    """
    Get full OCR detail termasuk full extracted text
    """
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed', 'status': 'failed'}), 500
            
        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT 
                o.*,
                d.file_name,
                d.type,
                d.size,
                d.total_page,
                d.file_path,
                d.upload_at
            FROM ocr_files o
            JOIN documents d ON o.document_id = d.id
            WHERE o.id = %s
        """
        
        cursor.execute(query, (ocr_id,))
        result = cursor.fetchone()
        
        cursor.close()
        connection.close()
        
        if not result:
            return jsonify({
                'error': 'OCR record not found',
                'status': 'failed'
            }), 404
        
        # Format full response with complete text
        response = {
            'status': 'success',
            'data': {
                'ocr_id': result['id'],
                'document_id': result['document_id'],
                'filename': result['file_name'],
                'file_type': result['type'],
                'file_size': result['size'],
                'total_pages': result['total_page'],
                'ocr_status': result['status'],
                'extracted_text': result['extracted_text'],
                'text_length': len(result['extracted_text']) if result['extracted_text'] else 0,
                'created_at': result['created_at'].isoformat() if result['created_at'] else None,
                'updated_at': result['updated_at'].isoformat() if result['updated_at'] else None,
                'upload_at': result['upload_at'].isoformat() if result['upload_at'] else None
            }
        }
        
        # Parse metadata
        if result['metadata_file']:
            try:
                import json
                response['data']['metadata'] = json.loads(result['metadata_file'])
            except:
                response['data']['metadata'] = None
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'status': 'failed'
        }), 500


@app.route('/docs/api/tools/ocr/stats', methods=['GET'])
def get_ocr_stats():
    """
    Get OCR processing statistics
    """
    try:
        connection = get_db_connection()
        if not connection:
            return jsonify({'error': 'Database connection failed', 'status': 'failed'}), 500
            
        cursor = connection.cursor(dictionary=True)
        
        # Get statistics
        stats_query = """
            SELECT 
                COUNT(*) as total_files,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) as processing,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending
            FROM ocr_files
        """
        
        cursor.execute(stats_query)
        stats = cursor.fetchone()
        
        cursor.close()
        connection.close()
        
        return jsonify({
            'status': 'success',
            'statistics': {
                'total_files': stats['total_files'] or 0,
                'completed': stats['completed'] or 0,
                'processing': stats['processing'] or 0,
                'failed': stats['failed'] or 0,
                'pending': stats['pending'] or 0,
                'success_rate': round((stats['completed'] / stats['total_files'] * 100), 2) if stats['total_files'] > 0 else 0
            }
        }), 200
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'status': 'failed'
        }), 500

def is_pdf_broken(path):
    """
    Mengembalikan True jika PDF rusak/pecah.
    """
    try:
        with pikepdf.open(path) as pdf:
            # Coba baca semua halaman
            for page in pdf.pages:
                _ = page.obj
    except Exception:
        return True

    return False

def choose_gs_profile(file_path):
    """Determine suitable Ghostscript compression profile."""
    size = os.path.getsize(file_path)

    if size < 500 * 1024:  # < 500KB
        return "/ebook"    # medium compression (tidak terlalu agresif)

    elif size < 1 * 1024 * 1024:  # 500KB - 1MB
        return "/ebook"    # tetap medium compression

    else:
        return "/ebook"   # >1MB: lebih agresif

# ==================== COMPRESS PDF ENDPOINT ====================
@app.route('/docs/api/tools/compress-pdf', methods=['POST'])
def compress_pdf():
    """
    Endpoint untuk mengkompress file PDF
    Optional form field: password (string) untuk PDF terenkripsi
    """
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Tidak ada file yang diupload', 'status': 'failed'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'error': 'Tidak ada file yang dipilih', 'status': 'failed'}), 400

        if not allowed_file(file.filename):
            return jsonify({'error': 'File harus PDF', 'status': 'failed'}), 400

        # Simpan file input
        original_filename = secure_filename(file.filename)
        file_id = f"cmp_{uuid.uuid4().hex}_{original_filename}"
        input_path = os.path.join(app.config["UPLOAD_FOLDER"], file_id)
        file.save(input_path)

        # Output filename
        compressed_name = f"{uuid.uuid4().hex}_compressed.pdf"
        output_path = os.path.join(app.config["COMPRESSED_FOLDER"], compressed_name)

        # Cek ukuran file original
        original_size = os.path.getsize(input_path)

        # Tentukan profile dan settings berdasarkan ukuran file
        # Untuk file scan (biasanya lebih besar), gunakan settings lebih konservatif
        if original_size > 5 * 1024 * 1024:  # > 5MB, kemungkinan scan
            profile = "/ebook"
            resolution = 150
            jpeg_quality = 75
        elif original_size > 2 * 1024 * 1024:  # > 2MB
            profile = "/ebook"
            resolution = 120
            jpeg_quality = 70
        else:  # File kecil (digital text)
            profile = "/ebook"
            resolution = 100
            jpeg_quality = 65

        # Ghostscript command dengan settings yang lebih aman untuk scan
        gs_command = [
            GHOSTSCRIPT_PATH,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={profile}",
            
            # Downsample dengan bicubic (lebih baik untuk scan)
            "-dColorImageDownsampleType=/Bicubic",
            f"-dColorImageResolution={resolution}",
            "-dGrayImageDownsampleType=/Bicubic",
            f"-dGrayImageResolution={resolution}",
            "-dMonoImageDownsampleType=/Bicubic",
            f"-dMonoImageResolution={resolution}",

            # JPEG compression dengan quality lebih tinggi
            "-dAutoFilterColorImages=false",
            "-dAutoFilterGrayImages=false",
            "-dColorImageFilter=/DCTEncode",
            "-dGrayImageFilter=/DCTEncode",

            # JPEG Quality lebih tinggi untuk scan
            f"-dJPEGQ={jpeg_quality}",

            # Preserve metadata dan struktur
            "-dPreserveAnnots=true",
            "-dPreserveEPSInfo=false",
            "-dPreserveOPIComments=false",
            "-dPreserveOverprintSettings=false",
            
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            f"-sOutputFile={output_path}",
            input_path
        ]

        # Jalankan kompresi
        import subprocess
        result = subprocess.run(gs_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0:
            return jsonify({
                "status": "failed",
                "error": "Gagal melakukan kompres PDF",
                "detail": result.stderr.decode()
            }), 500

        # Validasi file hasil kompresi
        if not os.path.exists(output_path):
            return jsonify({
                "status": "failed",
                "error": "File hasil kompresi tidak ditemukan"
            }), 500

        compressed_size = os.path.getsize(output_path)

        # Cek jika file terlalu kecil (kemungkinan rusak)
        # File PDF minimal sekitar 1KB, jika dibawah itu pasti rusak
        if compressed_size < 1024:  # Kurang dari 1KB
            os.remove(output_path)  # Hapus file rusak
            return jsonify({
                "status": "failed",
                "error": "Kompresi menghasilkan file yang rusak. Coba dengan file PDF lain atau gunakan settingan berbeda."
            }), 500

        # Cek jika kompresi terlalu agresif (> 95% reduction bisa indikasi masalah)
        reduction_percent = 100 - ((compressed_size / original_size) * 100)
        
        if reduction_percent > 95:
            os.remove(output_path)  # Hapus file yang kemungkinan rusak
            return jsonify({
                "status": "failed",
                "error": "Kompresi terlalu agresif dan kemungkinan merusak file. File original mungkin sudah optimal atau memiliki format khusus."
            }), 500

        # Jika kompresi malah memperbesar file (bisa terjadi pada file sudah optimal)
        if compressed_size >= original_size:
            os.remove(output_path)  # Hapus file hasil
            return jsonify({
                "status": "success",
                "message": "File PDF sudah optimal, tidak perlu dikompres",
                "original_size": original_size,
                "compressed_size": original_size,
                "reduction_percent": 0,
                "note": "File original dikembalikan karena sudah dalam ukuran optimal"
            })

        return jsonify({
            "status": "success",
            "message": "Berhasil mengkompres PDF",
            "original_size": original_size,
            "compressed_size": compressed_size,
            "reduction_percent": round(reduction_percent, 2),
            "download_url": f"{BASE_URL}/download/compressed/{compressed_name}"
        })

    except Exception as e:
        return jsonify({'status': 'failed', 'error': str(e)}), 500

# ==================== INFO ENDPOINTS ====================
@app.route('/docs/api/tools/ocr/info/<file_id>', methods=['GET'])
def get_ocr_info(file_id):
    """Endpoint untuk mendapatkan informasi metadata file OCR dari database"""
    result = get_ocr_from_db(file_id)
    
    if result:
        # Konversi datetime ke string untuk JSON serialization
        result['upload_time'] = result['upload_time'].isoformat() if result['upload_time'] else None
        result['created_at'] = result['created_at'].isoformat() if result['created_at'] else None
        result['updated_at'] = result['updated_at'].isoformat() if result['updated_at'] else None
        
        return jsonify({
            'status': 'success',
            'data': result
        }), 200
    else:
        return jsonify({
            'error': 'File tidak ditemukan di database',
            'status': 'failed'
        }), 404

@app.route('/docs/api/tools/compress/info/<file_id>', methods=['GET'])
def get_compress_info(file_id):
    """Endpoint untuk mendapatkan informasi metadata file compressed dari database"""
    result = get_compress_from_db(file_id)
    
    if result:
        # Konversi datetime ke string untuk JSON serialization
        result['upload_time'] = result['upload_time'].isoformat() if result['upload_time'] else None
        result['created_at'] = result['created_at'].isoformat() if result['created_at'] else None
        result['updated_at'] = result['updated_at'].isoformat() if result['updated_at'] else None
        
        return jsonify({
            'status': 'success',
            'data': result
        }), 200
    else:
        return jsonify({
            'error': 'File tidak ditemukan di database',
            'status': 'failed'
        }), 404


@app.route('/docs/api/tools/compress/list', methods=['GET'])
def list_compress_files():
    """Endpoint untuk mendapatkan semua file compressed dari database"""
    try:
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM compressed_files ORDER BY created_at DESC")
            results = cursor.fetchall()
            
            # Konversi datetime ke string
            for result in results:
                result['upload_time'] = result['upload_time'].isoformat() if result['upload_time'] else None
                result['created_at'] = result['created_at'].isoformat() if result['created_at'] else None
                result['updated_at'] = result['updated_at'].isoformat() if result['updated_at'] else None
            
            cursor.close()
            connection.close()
            
            return jsonify({
                'status': 'success',
                'count': len(results),
                'data': results
            }), 200
    except Error as e:
        return jsonify({
            'error': f'Database error: {str(e)}',
            'status': 'failed'
        }), 500

def home():
    """Homepage dengan informasi API"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>PDF Tools API</title>
        <style>
            body { font-family: Arial; max-width: 800px; margin: 50px auto; padding: 20px; }
            h1 { color: #333; }
            .endpoint { background: #f5f5f5; padding: 15px; margin: 10px 0; border-radius: 5px; }
            .method { display: inline-block; padding: 5px 10px; border-radius: 3px; font-weight: bold; }
            .post { background: #49cc90; color: white; }
            .get { background: #61affe; color: white; }
            code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; }
            a { color: #0066cc; text-decoration: none; }
            a:hover { text-decoration: underline; }
        </style>
    </head>
    <body>
        <h1>🚀 PDF Tools API</h1>
        <p><strong>Version:</strong> 1.0.0</p>
        
        <h2>📖 Dokumentasi Swagger</h2>
        <p>🔗 <a href="/docs/api" target="_blank">Buka Swagger UI</a> untuk testing interaktif</p>
        
        <h2>🧪 Testing Tools</h2>
        <p>🔗 <a href="/test-upload" target="_blank">Test Upload File</a> - Upload & test OCR/Compress langsung dari browser</p>
        <p>🔗 <a href="/test-list" target="_blank">View File List</a> - Lihat semua file yang sudah diproses</p>
        
        <h2>🎯 Available Endpoints:</h2>
        
        <div class="endpoint">
            <span class="method post">POST</span> <code>/docs/api/tools/ocr</code>
            <p>Upload PDF untuk ekstraksi text (OCR) dan simpan ke database</p>
        </div>
        
        <div class="endpoint">
            <span class="method get">GET</span> <code>/docs/api/tools/ocr/list</code>
            <p>List semua file OCR dari database</p>
        </div>
        
        <div class="endpoint">
            <span class="method get">GET</span> <code>/docs/api/tools/ocr/info/{file_id}</code>
            <p>Detail file OCR dari database</p>
        </div>
        
        <div class="endpoint">
            <span class="method post">POST</span> <code>/docs/api/tools/compress-pdf</code>
            <p>Upload PDF untuk kompresi, simpan ke database, dan download hasilnya</p>
        </div>
        
        <div class="endpoint">
            <span class="method get">GET</span> <code>/docs/api/tools/compress/list</code>
            <p>List semua file compressed dari database</p>
        </div>
        
        <div class="endpoint">
            <span class="method get">GET</span> <code>/docs/api/tools/compress/info/{file_id}</code>
            <p>Detail file compressed dari database</p>
        </div>
        
        <h2>🧪 Testing dengan cURL:</h2>
        <pre><code># OCR
curl -X POST http://localhost:5000/docs/api/tools/ocr \\
  -F "file=@/path/to/document.pdf"

# Compress
curl -X POST http://localhost:5000/docs/api/tools/compress-pdf \\
  -F "file=@/path/to/document.pdf" \\
  -o compressed.pdf

# List OCR
curl http://localhost:5000/docs/api/tools/ocr/list

# List Compress
curl http://localhost:5000/docs/api/tools/compress/list</code></pre>
    </body>
    </html>
    """
@app.route('/download/<path:folder>/<path:filename>', methods=['GET'])
#@cross_origin(origins="*")
def download_file(folder, filename):
    base_path = {
        "ocr": app.config['OUTPUT_OCR_FOLDER'],
        "compressed": app.config['COMPRESSED_FOLDER'],
        "converted": app.config['CONVERTED_FOLDER'],
        "splitted": app.config['SPLIT_FOLDER'],
        "merged": app.config['MERGED_FOLDER'],
    }.get(folder)

    if not base_path:
        return jsonify({"error": "Invalid folder"}), 400

    file_path = os.path.join(base_path, filename)

    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )

# ==================== NEW ENDPOINTS ====================

@app.route('/docs/api/tools/convert-ppt-to-pdf', methods=['POST'])
def convert_ppt_to_pdf():
    """
    Endpoint untuk convert PPT/PPTX to PDF
    """
    convert_id = None  # Initialize to avoid NameError
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Tidak ada file yang diupload', 'status': 'failed'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'error': 'Tidak ada file yang dipilih', 'status': 'failed'}), 400

        # Get file extension
        file_extension = os.path.splitext(file.filename)[1].lower()
        
        # Check file extension - support both .ppt and .pptx
        if file_extension not in ['.ppt', '.pptx']:
            return jsonify({'error': 'File harus berformat PPT atau PPTX', 'status': 'failed'}), 400

        original_filename = secure_filename(file.filename)
        file_id = f"ppt_{uuid.uuid4().hex}_{original_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id)
        file.save(file_path)

        file_size = os.path.getsize(file_path)

        document_id = create_documents_entry(
            original_filename,
            file_path,
            file_extension,  # Will be .ppt or .pptx
            file_size,
            0  # PPT/PPTX doesn't have page count like PDF
        )
        convert_id, convert_uuid = create_convert_entry(document_id)
        
        # Convert using LibreOffice command (works for both .ppt and .pptx)
        cmd = [
            LIBREOFFICE_PATH,
            '--headless',
            '--convert-to',
            'pdf',
            '--outdir',
            app.config['CONVERTED_FOLDER'],
            file_path
        ]
        
        # Add timeout to prevent hanging (120 seconds for large presentations)
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        
        if process.returncode != 0:
            raise Exception(f"LibreOffice conversion error: {process.stderr.decode()}")
        
        # Get output PDF path
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        output_pdf = os.path.join(app.config['CONVERTED_FOLDER'], f"{base_name}.pdf")
        
        # Check if conversion succeeded
        if not os.path.exists(output_pdf):
            raise Exception("Conversion failed: PDF file not created")
        
        pdf_filename = os.path.basename(output_pdf)
        update_convert_status(convert_id, "completed", output_pdf, pdf_filename)

        return jsonify({
            "status": "success",
            "message": f"{file_extension.upper()} to PDF conversion completed",
            "file_id": file_id,
            "document_id": document_id,
            "convert_id": convert_id,
            "original_filename": original_filename,
            "original_format": file_extension,
            "converted_filename": pdf_filename,
            "download_url": f"{BASE_URL}/download/converted/{pdf_filename}"
        })

    except subprocess.TimeoutExpired:
        if convert_id:
            update_convert_status(convert_id, "failed")
        return jsonify({'error': 'Conversion timeout: File terlalu besar atau kompleks', 'status': 'failed'}), 500
    except Exception as e:
        if convert_id:
            update_convert_status(convert_id, "failed")
        return jsonify({'error': str(e), 'status': 'failed'}), 500

@app.route('/docs/api/tools/convert-doc-to-pdf', methods=['POST'])
def convert_doc_to_pdf():
    """
    Endpoint untuk convert DOC/DOCX to PDF
    """
    convert_id = None  # Initialize to avoid NameError
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Tidak ada file yang diupload', 'status': 'failed'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'error': 'Tidak ada file yang dipilih', 'status': 'failed'}), 400

        # Get file extension
        file_extension = os.path.splitext(file.filename)[1].lower()
        
        # Check file extension - support both .doc and .docx
        if file_extension not in ['.doc', '.docx']:
            return jsonify({'error': 'File harus berformat DOC atau DOCX', 'status': 'failed'}), 400

        original_filename = secure_filename(file.filename)
        file_id = f"doc_{uuid.uuid4().hex}_{original_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id)
        file.save(file_path)

        file_size = os.path.getsize(file_path)

        document_id = create_documents_entry(
            original_filename,
            file_path,
            file_extension,  # Will be .doc or .docx
            file_size,
            0  # DOC/DOCX doesn't have page count like PDF
        )
        convert_id, convert_uuid = create_convert_entry(document_id)
        
        # Convert using LibreOffice command (works for both .doc and .docx)
        cmd = [
            LIBREOFFICE_PATH,
            '--headless',
            '--convert-to',
            'pdf',
            '--outdir',
            app.config['CONVERTED_FOLDER'],
            file_path
        ]
        
        process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if process.returncode != 0:
            raise Exception(f"LibreOffice conversion error: {process.stderr.decode()}")
        
        # Get output PDF path
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        output_pdf = os.path.join(app.config['CONVERTED_FOLDER'], f"{base_name}.pdf")
        
        # Check if conversion succeeded
        if not os.path.exists(output_pdf):
            raise Exception("Conversion failed: PDF file not created")
        
        pdf_filename = os.path.basename(output_pdf)
        update_convert_status(convert_id, "completed", output_pdf, pdf_filename)

        return jsonify({
            "status": "success",
            "message": f"{file_extension.upper()} to PDF conversion completed",
            "file_id": file_id,
            "document_id": document_id,
            "convert_id": convert_id,
            "original_filename": original_filename,
            "original_format": file_extension,
            "converted_filename": pdf_filename,
            "download_url": f"{BASE_URL}/download/converted/{pdf_filename}"
        })

    except Exception as e:
        if convert_id:
            update_convert_status(convert_id, "failed")
        return jsonify({'error': str(e), 'status': 'failed'}), 500

@app.route('/docs/api/tools/convert-image-to-pdf', methods=['POST'])
def convert_image_to_pdf():
    """
    Endpoint untuk convert Image to PDF
    """
    convert_id = None  # Initialize to avoid NameError
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Tidak ada file yang diupload', 'status': 'failed'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'error': 'Tidak ada file yang dipilih', 'status': 'failed'}), 400

        # Get file extension
        file_extension = os.path.splitext(file.filename)[1].lower()
        
        # Check file extension - support common image formats
        allowed_image_extensions = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif']
        if file_extension not in allowed_image_extensions:
            return jsonify({'error': 'File harus berformat gambar (PNG, JPG, JPEG, GIF, BMP, TIFF)', 'status': 'failed'}), 400

        original_filename = secure_filename(file.filename)
        file_id = f"img_{uuid.uuid4().hex}_{original_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id)
        file.save(file_path)

        file_size = os.path.getsize(file_path)

        document_id = create_documents_entry(
            original_filename,
            file_path,
            file_extension,  # .png, .jpg, etc
            file_size,
            1  # 1 page for image
        )
        convert_id, convert_uuid = create_convert_entry(document_id)

        # Generate PDF filename
        pdf_filename = f"{uuid.uuid4().hex}.pdf"
        pdf_path = os.path.join(app.config['CONVERTED_FOLDER'], pdf_filename)

        # Convert image to PDF using PIL and ReportLab
        img = Image.open(file_path)
        
        # Convert to RGB if necessary (for PNG with transparency, etc.)
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Save as PDF
        img.save(pdf_path, 'PDF', resolution=100.0)

        update_convert_status(convert_id, "completed", pdf_path, pdf_filename)

        return jsonify({
            "status": "success",
            "message": "Image to PDF conversion completed",
            "file_id": file_id,
            "document_id": document_id,
            "convert_id": convert_id,
            "original_filename": original_filename,
            "converted_filename": pdf_filename,
            "download_url": f"{BASE_URL}/download/converted/{pdf_filename}"
        })

    except Exception as e:
        if convert_id:  # ✅ Added error handling
            update_convert_status(convert_id, "failed")
        return jsonify({'error': str(e), 'status': 'failed'}), 500

@app.route('/docs/api/tools/merge-pdf', methods=['POST'])
def merge_pdf():
    """
    Endpoint untuk merge multiple PDF files
    Upload multiple files with key 'files[]'
    """
    merge_id = None  # ✅ Initialize to avoid NameError
    uploaded_files = []  # ✅ Initialize for cleanup
    
    try:
        if 'files[]' not in request.files:
            return jsonify({'error': 'Tidak ada file yang diupload. Gunakan key "files[]"', 'status': 'failed'}), 400

        files = request.files.getlist('files[]')

        if len(files) < 2:
            return jsonify({'error': 'Minimal 2 file PDF untuk di-merge', 'status': 'failed'}), 400

        # Validate all files are PDFs
        for file in files:
            if not allowed_file(file.filename):
                return jsonify({'error': f'File {file.filename} bukan PDF', 'status': 'failed'}), 400

        # Create merge entry first
        merge_id = create_merge_entry([])  # ✅ Create merge entry with empty document_ids first
        
        document_ids = []
        merger = PyPDF2.PdfMerger()

        # Save and add each file to merger
        for file in files:
            original_filename = secure_filename(file.filename)
            temp_id = f"temp_{uuid.uuid4().hex}_{original_filename}"
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_id)
            file.save(temp_path)

            file_size = os.path.getsize(temp_path)
            reader_temp = PyPDF2.PdfReader(temp_path)
            total_pages = len(reader_temp.pages)

            doc_id = create_documents_entry(
                original_filename,
                temp_path,
                ".pdf",
                file_size,
                total_pages
            )
            document_ids.append(doc_id)
            uploaded_files.append(temp_path)
            
            merger.append(temp_path)

        # Generate merged PDF filename
        merged_filename = f"merged_{uuid.uuid4().hex}.pdf"
        merged_path = os.path.join(app.config['MERGED_FOLDER'], merged_filename)

        # Write merged PDF
        merger.write(merged_path)
        merger.close()
        
        merged_size = os.path.getsize(merged_path)
        
        # Update merge status with completed info
        update_merge_status(merge_id, "completed", merged_path, merged_filename, convert_size(merged_size))

        # Update document_ids in merge table
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE merge_files SET document_id=%s WHERE id=%s", (json.dumps(document_ids), merge_id))
        conn.commit()
        cursor.close()
        conn.close()

        # Clean up temporary files
        for temp_file in uploaded_files:
            try:
                os.remove(temp_file)
            except:
                pass

        return jsonify({
            "status": "success",
            "message": "PDF merge completed",
            "merge_id": merge_id,
            "merged_filename": merged_filename,
            "files_merged": len(files),
            "merged_size": convert_size(merged_size),
            "download_url": f"{BASE_URL}/download/merged/{merged_filename}"
        })

    except Exception as e:
        if merge_id:  # ✅ Check if merge_id exists before updating
            update_merge_status(merge_id, "failed")
        
        # Clean up temporary files on error
        for temp_file in uploaded_files:
            try:
                os.remove(temp_file)
            except:
                pass
                
        return jsonify({'error': str(e), 'status': 'failed'}), 500

@app.route('/docs/api/tools/split-pdf', methods=['POST'])
def split_pdf():
    """
    Endpoint untuk split PDF file
    Optional: page_ranges (e.g., "1-3,5,7-9") or split all pages
    """
    split_id = None  # ✅ Initialize to avoid NameError
    
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Tidak ada file yang diupload', 'status': 'failed'}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({'error': 'Tidak ada file yang dipilih', 'status': 'failed'}), 400

        if not allowed_file(file.filename):
            return jsonify({'error': 'File harus berformat PDF', 'status': 'failed'}), 400

        original_filename = secure_filename(file.filename)
        file_id = f"split_{uuid.uuid4().hex}_{original_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id)
        file.save(file_path)
        
        file_size = os.path.getsize(file_path)
        
        # ✅ Read PDF FIRST to get total_pages
        reader = PyPDF2.PdfReader(file_path)
        total_pages = len(reader.pages)
        
        # ✅ Now create document entry with correct total_pages
        document_id = create_documents_entry(
            original_filename,
            file_path,
            ".pdf",
            file_size,
            total_pages
        )

        split_id, split_uuid = create_split_entry(document_id)

        # Get page ranges from request (optional)
        page_ranges = request.form.get('page_ranges', None)

        # Create folder for split files
        split_folder_name = f"split_{uuid.uuid4().hex}"
        split_folder_path = os.path.join(app.config['SPLIT_FOLDER'], split_folder_name)
        os.makedirs(split_folder_path, exist_ok=True)

        split_files = []
        total_split_size = 0  # ✅ Track total size of all split files

        if page_ranges:
            # Parse page ranges (e.g., "1-3,5,7-9")
            ranges = page_ranges.split(',')
            
            for range_str in ranges:
                range_str = range_str.strip()
                
                if '-' in range_str:
                    # Range: 1-3
                    start, end = map(int, range_str.split('-'))
                    writer = PyPDF2.PdfWriter()
                    
                    for page_num in range(start - 1, end):
                        if page_num < total_pages:
                            writer.add_page(reader.pages[page_num])
                    
                    output_filename = f"pages_{start}-{end}.pdf"
                    output_path = os.path.join(split_folder_path, output_filename)
                    
                    with open(output_path, 'wb') as output_file:
                        writer.write(output_file)
                    
                    split_file_size = os.path.getsize(output_path)
                    total_split_size += split_file_size
                    split_files.append(output_filename)
                else:
                    # Single page: 5
                    page_num = int(range_str) - 1
                    
                    if page_num < total_pages:
                        writer = PyPDF2.PdfWriter()
                        writer.add_page(reader.pages[page_num])
                        
                        output_filename = f"page_{page_num + 1}.pdf"
                        output_path = os.path.join(split_folder_path, output_filename)
                        
                        with open(output_path, 'wb') as output_file:
                            writer.write(output_file)
                        
                        split_file_size = os.path.getsize(output_path)
                        total_split_size += split_file_size
                        split_files.append(output_filename)
        else:
            # Split all pages individually
            for page_num in range(total_pages):
                writer = PyPDF2.PdfWriter()
                writer.add_page(reader.pages[page_num])
                
                output_filename = f"page_{page_num + 1}.pdf"
                output_path = os.path.join(split_folder_path, output_filename)
                
                with open(output_path, 'wb') as output_file:
                    writer.write(output_file)
                
                split_file_size = os.path.getsize(output_path)
                total_split_size += split_file_size
                split_files.append(output_filename)

        # ✅ Update ONCE at the end with final status
        # Store first split file name as representative, or you could store JSON array
        first_split_file = split_files[0] if split_files else None
        update_split_status(
            split_id, 
            "completed", 
            split_folder_path, 
            first_split_file,  # Or json.dumps(split_files) if you want to store all names
            convert_size(total_split_size)
        )

        return jsonify({
            "status": "success",
            "message": "PDF split completed",
            "file_id": file_id,
            "document_id": document_id,
            "split_id": split_id,
            "original_filename": original_filename,
            "total_pages": total_pages,
            "split_count": len(split_files),
            "split_files": split_files,
            "total_split_size": convert_size(total_split_size),
            "download_folder": f"/download/splitted/{split_folder_name}"
        })

    except Exception as e:
        if split_id:  # ✅ Check if split_id exists before updating
            update_split_status(split_id, "failed")
        return jsonify({'error': str(e), 'status': 'failed'}), 500

# ==================== SWAGGER JSON ====================
@app.route('/static/swagger.json')
def swagger_json():
    """Swagger specification"""

    BASE_URL = os.getenv("BASE_URL", "https://tools.itk.ac.id")

    swagger_spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "PDF Tools API",
            "description": "API untuk OCR, Kompresi, Konversi, Merge, dan Split PDF",
            "version": "2.0.0"
        },
        "servers": [
            {
                "url": BASE_URL,
                "description": "Development server"
            }
        ],
        "paths": {
            "/docs/api/tools/ocr": {
                "post": {
                    "summary": "OCR PDF",
                    "description": "Upload PDF untuk ekstraksi text menggunakan OCR dan simpan ke database",
                    "tags": ["OCR"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {
                                            "type": "string",
                                            "format": "binary",
                                            "description": "File PDF yang akan di-OCR"
                                        }
                                    },
                                    "required": ["file"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "OCR berhasil dan tersimpan di database"
                        }
                    }
                }
            },
            # ==================== SIMPLE OCR ENDPOINTS (NEW) ====================
            "/docs/api/tools/simple-ocr": {
                "post": {
                    "summary": "Simple OCR - Extract Text (No Database)",
                    "description": """
                    Extract text dari PDF tanpa menyimpan ke database.
                    
                    **Features:**
                    - Auto-detect: Digital text PDF atau Scanned PDF
                    - Support password-protected PDF
                    - Dual language: English + Indonesian
                    - Return hasil langsung dalam JSON
                    
                    **Performance:**
                    - Digital PDF: ~1-2 seconds
                    - Scanned PDF: ~5-30 seconds (depends on pages)
                    
                    **Method Detection:**
                    - `digital_extraction`: PDF dengan text yang bisa di-copy (fast)
                    - `tesseract_ocr`: PDF hasil scan/protected (slower, uses OCR)
                    """,
                    "tags": ["Simple OCR (No Database)"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {
                                            "type": "string",
                                            "format": "binary",
                                            "description": "PDF file untuk ekstraksi text"
                                        },
                                        "password": {
                                            "type": "string",
                                            "description": "Password untuk PDF terenkripsi (optional)"
                                        }
                                    },
                                    "required": ["file"]
                                },
                                "examples": {
                                    "digital_pdf": {
                                        "summary": "Digital PDF (no password)",
                                        "value": {
                                            "file": "document.pdf"
                                        }
                                    },
                                    "protected_pdf": {
                                        "summary": "Password-protected PDF",
                                        "value": {
                                            "file": "protected.pdf",
                                            "password": "mypassword"
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Text extraction successful",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {
                                                "type": "string",
                                                "example": "success"
                                            },
                                            "message": {
                                                "type": "string",
                                                "example": "Text extraction completed (Digital PDF)"
                                            },
                                            "method": {
                                                "type": "string",
                                                "enum": ["digital_extraction", "tesseract_ocr"],
                                                "example": "digital_extraction"
                                            },
                                            "filename": {
                                                "type": "string",
                                                "example": "document.pdf"
                                            },
                                            "total_pages": {
                                                "type": "integer",
                                                "example": 5
                                            },
                                            "total_characters": {
                                                "type": "integer",
                                                "example": 2856
                                            },
                                            "has_copy_protection": {
                                                "type": "boolean",
                                                "example": False
                                            },
                                            "extracted_text": {
                                                "type": "string",
                                                "example": "\n\n===== PAGE 1 =====\nThis is the content...\n\n===== PAGE 2 =====\nMore content..."
                                            }
                                        }
                                    },
                                    "examples": {
                                        "digital_pdf": {
                                            "summary": "Digital PDF Response",
                                            "value": {
                                                "status": "success",
                                                "message": "Text extraction completed (Digital PDF)",
                                                "method": "digital_extraction",
                                                "filename": "document.pdf",
                                                "total_pages": 5,
                                                "total_characters": 2856,
                                                "has_copy_protection": False,
                                                "extracted_text": "\n\n===== PAGE 1 =====\nChapter 1: Introduction..."
                                            }
                                        },
                                        "scanned_pdf": {
                                            "summary": "Scanned PDF Response",
                                            "value": {
                                                "status": "success",
                                                "message": "OCR completed (Scanned PDF)",
                                                "method": "tesseract_ocr",
                                                "filename": "scanned.pdf",
                                                "total_pages": 3,
                                                "total_characters": 1523,
                                                "has_copy_protection": True,
                                                "extracted_text": "\n\n===== PAGE 1 =====\nText from OCR..."
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "400": {
                            "description": "Bad Request",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "error": {"type": "string"},
                                            "status": {"type": "string"}
                                        }
                                    },
                                    "examples": {
                                        "no_file": {
                                            "summary": "No file uploaded",
                                            "value": {
                                                "error": "Tidak ada file yang diupload",
                                                "status": "failed"
                                            }
                                        },
                                        "encrypted": {
                                            "summary": "Encrypted PDF without password",
                                            "value": {
                                                "error": "PDF terenkripsi. Gunakan parameter \"password\"",
                                                "status": "failed"
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "500": {
                            "description": "Internal Server Error",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "error": {"type": "string"},
                                            "status": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/docs/api/tools/simple-ocr-save": {
                "post": {
                    "summary": "Simple OCR - Save to File",
                    "description": """
                    Extract text dari PDF dan simpan ke file .txt, return download URL.
                    
                    **Features:**
                    - Same as simple-ocr endpoint
                    - Saves extracted text to .txt file
                    - Returns download URL
                    - File stored in ocr_results/ folder
                    
                    **Use Cases:**
                    - When you need to download the text file
                    - Batch processing
                    - Archive extracted text
                    """,
                    "tags": ["Simple OCR (No Database)"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {
                                            "type": "string",
                                            "format": "binary",
                                            "description": "PDF file untuk ekstraksi text"
                                        },
                                        "password": {
                                            "type": "string",
                                            "description": "Password untuk PDF terenkripsi (optional)"
                                        }
                                    },
                                    "required": ["file"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Text extraction successful and file saved",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {
                                                "type": "string",
                                                "example": "success"
                                            },
                                            "message": {
                                                "type": "string",
                                                "example": "OCR completed and saved to file"
                                            },
                                            "method": {
                                                "type": "string",
                                                "enum": ["digital_extraction", "tesseract_ocr"],
                                                "example": "digital_extraction"
                                            },
                                            "filename": {
                                                "type": "string",
                                                "example": "document.pdf"
                                            },
                                            "total_pages": {
                                                "type": "integer",
                                                "example": 5
                                            },
                                            "total_characters": {
                                                "type": "integer",
                                                "example": 2856
                                            },
                                            "output_file": {
                                                "type": "string",
                                                "example": "abc123_ocr.txt"
                                            },
                                            "download_url": {
                                                "type": "string",
                                                "example": "http://localhost:5001/download/ocr/abc123_ocr.txt"
                                            },
                                            "has_copy_protection": {
                                                "type": "boolean",
                                                "example": False
                                            }
                                        }
                                    },
                                    "example": {
                                        "status": "success",
                                        "message": "OCR completed and saved to file",
                                        "method": "digital_extraction",
                                        "filename": "document.pdf",
                                        "total_pages": 5,
                                        "total_characters": 2856,
                                        "output_file": "abc123_ocr.txt",
                                        "download_url": "http://localhost:5001/download/ocr/abc123_ocr.txt",
                                        "has_copy_protection": False
                                    }
                                }
                            }
                        },
                        "400": {
                            "description": "Bad Request - lihat examples di endpoint simple-ocr"
                        },
                        "500": {
                            "description": "Internal Server Error"
                        }
                    }
                }
            },
            "/docs/api/tools/ocr/info/{file_id}": {
                "get": {
                    "summary": "Get OCR File Info",
                    "description": "Dapatkan informasi file OCR dari database",
                    "tags": ["OCR"],
                    "parameters": [
                        {
                            "name": "file_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"}
                        }
                    ],
                    "responses": {
                        "200": {"description": "Success"},
                        "404": {"description": "Not found"}
                    }
                }
            },
            "/docs/api/tools/ocr/list": {
                "get": {
                    "summary": "List All OCR Files",
                    "description": "Dapatkan semua file OCR dari database",
                    "tags": ["OCR"],
                    "responses": {
                        "200": {"description": "Success"}
                    }
                }
            },
            "/docs/api/tools/compress-pdf": {
                "post": {
                    "summary": "Compress PDF",
                    "description": "Upload PDF untuk dikompress, simpan ke database, dan download hasilnya",
                    "tags": ["Compress"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {
                                            "type": "string",
                                            "format": "binary",
                                            "description": "File PDF yang akan dikompress"
                                        }
                                    },
                                    "required": ["file"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "File PDF compressed"}
                    }
                }
            },
            "/docs/api/tools/compress/info/{file_id}": {
                "get": {
                    "summary": "Get Compressed File Info",
                    "description": "Dapatkan informasi file compressed dari database",
                    "tags": ["Compress"],
                    "parameters": [
                        {
                            "name": "file_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"}
                        }
                    ],
                    "responses": {
                        "200": {"description": "Success"},
                        "404": {"description": "Not found"}
                    }
                }
            },
            "/docs/api/tools/compress/list": {
                "get": {
                    "summary": "List All Compressed Files",
                    "description": "Dapatkan semua file compressed dari database",
                    "tags": ["Compress"],
                    "responses": {
                        "200": {"description": "Success"}
                    }
                }
            },
            # ==================== NEW ENDPOINTS ====================
            "/docs/api/tools/convert-ppt-to-pdf": {
                "post": {
                    "summary": "Convert PPT/PPTX to PDF",
                    "description": "Upload PPT atau PPTX untuk dikonversi ke PDF",
                    "tags": ["Convert"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {
                                            "type": "string",
                                            "format": "binary",
                                            "description": "File PPT/PPTX yang akan dikonversi"
                                        }
                                    },
                                    "required": ["file"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Conversion successful",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "message": {"type": "string"},
                                            "file_id": {"type": "string"},
                                            "download_url": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/docs/api/tools/convert-doc-to-pdf": {
                "post": {
                    "summary": "Convert DOC/DOCX to PDF",
                    "description": "Upload DOC atau DOCX untuk dikonversi ke PDF",
                    "tags": ["Convert"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {
                                            "type": "string",
                                            "format": "binary",
                                            "description": "File DOC/DOCX yang akan dikonversi"
                                        }
                                    },
                                    "required": ["file"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Conversion successful",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "message": {"type": "string"},
                                            "file_id": {"type": "string"},
                                            "download_url": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/docs/api/tools/convert-image-to-pdf": {
                "post": {
                    "summary": "Convert Image to PDF",
                    "description": "Upload gambar (PNG, JPG, JPEG, GIF, BMP, TIFF) untuk dikonversi ke PDF",
                    "tags": ["Convert"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {
                                            "type": "string",
                                            "format": "binary",
                                            "description": "File gambar yang akan dikonversi (PNG, JPG, JPEG, GIF, BMP, TIFF)"
                                        }
                                    },
                                    "required": ["file"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Conversion successful",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "message": {"type": "string"},
                                            "file_id": {"type": "string"},
                                            "download_url": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/docs/api/tools/converted/list": {
                "get": {
                    "summary": "List All Converted Files",
                    "description": "Dapatkan semua converted files dari database",
                    "tags": ["Convert"],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "count": {"type": "integer"},
                                            "data": {"type": "array"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/docs/api/tools/merge-pdf": {
                "post": {
                    "summary": "Merge Multiple PDFs",
                    "description": "Upload multiple PDF files untuk digabungkan menjadi satu file PDF",
                    "tags": ["Merge"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "files[]": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "format": "binary"
                                            },
                                            "description": "Multiple PDF files (minimum 2 files)"
                                        }
                                    },
                                    "required": ["files[]"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Merge successful",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "message": {"type": "string"},
                                            "file_id": {"type": "string"},
                                            "files_merged": {"type": "integer"},
                                            "download_url": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/docs/api/tools/merged/list": {
                "get": {
                    "summary": "List All Merged Files",
                    "description": "Dapatkan semua merged files dari database",
                    "tags": ["Merge"],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "count": {"type": "integer"},
                                            "data": {"type": "array"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/docs/api/tools/split-pdf": {
                "post": {
                    "summary": "Split PDF",
                    "description": "Upload PDF untuk di-split menjadi file-file terpisah. Optional: page_ranges parameter untuk split halaman tertentu",
                    "tags": ["Split"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {
                                            "type": "string",
                                            "format": "binary",
                                            "description": "File PDF yang akan di-split"
                                        },
                                        "page_ranges": {
                                            "type": "string",
                                            "description": "Optional: Range halaman untuk split (contoh: '1-3,5,7-9'). Kosongkan untuk split semua halaman"
                                        }
                                    },
                                    "required": ["file"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Split successful",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "message": {"type": "string"},
                                            "file_id": {"type": "string"},
                                            "total_pages": {"type": "integer"},
                                            "split_count": {"type": "integer"},
                                            "split_files": {"type": "array"},
                                            "download_folder": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/docs/api/tools/split/list": {
                "get": {
                    "summary": "List All Split Files",
                    "description": "Dapatkan semua split files dari database",
                    "tags": ["Split"],
                    "responses": {
                        "200": {
                            "description": "Success",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {"type": "string"},
                                            "count": {"type": "integer"},
                                            "data": {"type": "array"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    return jsonify(swagger_spec)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)