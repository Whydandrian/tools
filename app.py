from flask import Flask, request, jsonify, send_file
from flask_swagger_ui import get_swaggerui_blueprint
from flask_cors import CORS
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

app = Flask(__name__)

# Enable CORS untuk semua routes
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# Konfigurasi
UPLOAD_FOLDER = 'uploads'
OUTPUT_OCR_FOLDER = 'ocr_results'
COMPRESSED_FOLDER = 'compressed'
ALLOWED_EXTENSIONS = {'pdf'}

# Buat folder jika belum ada
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(COMPRESSED_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_OCR_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['COMPRESSED_FOLDER'] = COMPRESSED_FOLDER
app.config['OUTPUT_OCR_FOLDER'] = OUTPUT_OCR_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Max 16MB

# ==================== DATABASE CONFIGURATION ====================
# Konfigurasi untuk DBngin (Mac)
# DBngin biasanya menggunakan port berbeda, cek di aplikasi DBngin
DB_CONFIG = {
    'host': '127.0.0.1',  # atau 'localhost'
    'user': 'root',
    'password': '',  # Ganti dengan password MariaDB Anda (biasanya kosong untuk DBngin)
    'database': 'pdf_tools_db',
    'port': 3306  # Cek port di DBngin, bisa jadi 3306, 13306, atau port lain
}

def get_db_connection():
    """Membuat koneksi ke database MariaDB"""
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        return connection
    except Error as e:
        print(f"Error connecting to MariaDB: {e}")
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
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="dokumi"
    )
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
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="dokumi"
    )
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


def update_ocr_status(ocr_id, status, extracted_text=""):
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="dokumi"
    )
    cursor = conn.cursor()

    sql = """
        UPDATE ocr_files 
        SET status=%s, extracted_text=%s, updated_at=%s
        WHERE id=%s
    """

    now = datetime.now()
    cursor.execute(sql, (status, extracted_text, now, ocr_id))
    conn.commit()

    cursor.close()
    conn.close()

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
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="dokumi"
    )
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
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="dokumi"
    )
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
        "gs",
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

# ==================== OCR ENDPOINT ====================
@app.route('/docs/api/tools/ocr', methods=['POST'])
def ocr_pdf():
    """
    Endpoint OCR PDF (Bahasa Inggris + Indonesia)
    Optional form field: password (untuk PDF terenkripsi)
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

        # Save input PDF
        original_filename = secure_filename(file.filename)
        file_id = f"ocr_{uuid.uuid4().hex}_{original_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id)
        file.save(file_path)

        file_size = os.path.getsize(file_path)

        # Total pages
        reader = PyPDF2.PdfReader(file_path)
        total_pages = len(reader.pages)

        # INSERT → documents
        document_id = create_documents_entry(
            original_filename,
            file_path,
            ".pdf",
            file_size,
            total_pages
        )

        # INSERT → ocr_files
        ocr_id = create_ocr_entry(document_id)

        # Prepare OCR output folder
        ocr_folder = os.path.join(os.getcwd(), "ocr_results")
        os.makedirs(ocr_folder, exist_ok=True)

        # Generate result filename
        ocr_filename = f"{uuid.uuid4().hex}_ocr.txt"
        ocr_output_path = os.path.join(ocr_folder, ocr_filename)

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
                        break
                except:
                    pass
            if not decrypted:
                update_ocr_status(ocr_id, "failed")
                return jsonify({'error': 'PDF terenkripsi. Tambahkan password.', 'status': 'failed'}), 400

        # Convert PDF → Images
        pages = convert_from_path(
            file_path,
            dpi=300,
            fmt="png",
            userpw=pdf_password if pdf_password else None
        )

        # OCR process
        ocr_lang = "eng+ind"
        full_text = ""
        text_by_page = []

        for page_num, img in enumerate(pages, start=1):
            try:
                page_text = pytesseract.image_to_string(img, lang=ocr_lang)

                # Simpan ke list untuk response
                text_by_page.append({"page": page_num, "text": page_text})

                # ⬅ INSERT ke database: 1 baris per halaman
                insert_ocr_page(document_id, page_num, page_text)

            except Exception as e_page:
                text_by_page.append({"page": page_num, "text": f"ERROR: {e_page}"})

                # tetap insert agar halaman ada record
                insert_ocr_page(document_id, page_num, f"ERROR: {e_page}")

        # Save extracted text
        with open(ocr_output_path, "w", encoding="utf-8") as f:
            f.write(full_text)

        # Update DB
        update_ocr_status(ocr_id, "completed", full_text)

        # Return JSON
        return jsonify({
            "status": "success",
            "message": "OCR completed",
            "document_id": document_id,
            "ocr_id": ocr_id,
            "output_text_path": ocr_output_path,
            "download_url": f"/download/ocr/{ocr_filename}",
            "pages": len(pages),
            "text_by_page": text_by_page
        })

    except Exception as e:
        try:
            update_ocr_status(ocr_id, "failed")
        except:
            pass
        return jsonify({'error': str(e), 'status': 'failed'}), 500

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
            return jsonify({'error': 'File harus berformat PDF', 'status': 'failed'}), 400

        pdf_password = request.form.get('password', None)

        # Save input PDF
        original_filename = secure_filename(file.filename)
        file_id = f"ocr_{uuid.uuid4().hex}_{original_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_id)
        file.save(file_path)

        file_size = os.path.getsize(file_path)

        # Total pages
        reader = PyPDF2.PdfReader(file_path)
        total_pages = len(reader.pages)

        # INSERT → documents
        document_id = create_documents_entry(
            original_filename,
            file_path,
            ".pdf",
            file_size,
            total_pages
        )

        # INSERT → ocr_files
        ocr_id = create_ocr_entry(document_id)

        # Prepare OCR output folder
        ocr_folder = os.path.join(os.getcwd(), "ocr_results")
        os.makedirs(ocr_folder, exist_ok=True)

        # Generate result filename
        ocr_filename = f"{uuid.uuid4().hex}_ocr.txt"
        ocr_output_path = os.path.join(ocr_folder, ocr_filename)

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
                        break
                except:
                    pass
            if not decrypted:
                update_ocr_status(ocr_id, "failed")
                return jsonify({'error': 'PDF terenkripsi. Tambahkan password.', 'status': 'failed'}), 400

        # Convert PDF → Images
        pages = convert_from_path(
            file_path,
            dpi=300,
            fmt="png",
            userpw=pdf_password if pdf_password else None
        )

        # OCR process
        ocr_lang = "eng+ind"
        full_text = ""
        text_by_page = []

        for page_num, img in enumerate(pages, start=1):
            try:
                page_text = pytesseract.image_to_string(img, lang=ocr_lang)
                full_text += f"\n\n--- Halaman {page_num} ---\n\n{page_text}"
                text_by_page.append({"page": page_num, "text": page_text})
            except Exception as e_page:
                text_by_page.append({"page": page_num, "text": f"ERROR: {e_page}"})

        # Save extracted text
        with open(ocr_output_path, "w", encoding="utf-8") as f:
            f.write(full_text)

        # Update DB
        update_ocr_status(ocr_id, "completed", full_text)

        # Return JSON
        return jsonify({
            "status": "success",
            "message": "OCR completed",
            "document_id": document_id,
            "ocr_id": ocr_id,
            "output_text_path": ocr_output_path,
            "download_url": f"/download/ocr/{ocr_filename}",
            "pages": len(pages),
            "text_by_page": text_by_page
        })

    except Exception as e:
        try:
            update_ocr_status(ocr_id, "failed")
        except:
            pass
        return jsonify({'error': str(e), 'status': 'failed'}), 500

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

@app.route('/docs/api/tools/ocr/list', methods=['GET'])
def list_ocr_files():
    """Endpoint untuk mendapatkan semua file OCR dari database"""
    try:
        connection = get_db_connection()
        if connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM ocr_files ORDER BY created_at DESC")
            results = cursor.fetchall()
            
            # Konversi datetime ke string
            for result in results:
                result['upload_time'] = result['upload_time'].isoformat() if result['upload_time'] else None
                result['created_at'] = result['created_at'].isoformat() if result['created_at'] else None
                result['updated_at'] = result['updated_at'].isoformat() if result['updated_at'] else None
                # Hapus text yang panjang dari list view
                result['extracted_text'] = result['extracted_text'][:200] + '...' if result['extracted_text'] and len(result['extracted_text']) > 200 else result['extracted_text']
            
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
def download_file(folder, filename):
    base_path = {
        "ocr": app.config['OUTPUT_OCR_FOLDER'],
        "compressed": app.config['COMPRESSED_FOLDER']
    }.get(folder)

    if not base_path:
        return jsonify({"error": "Invalid folder"}), 400

    file_path = os.path.join(base_path, filename)

    if not os.path.exists(file_path):
        return jsonify({"error": "File not found"}), 404

    return send_file(file_path, as_attachment=True)


# ==================== SWAGGER JSON ====================
@app.route('/static/swagger.json')
def swagger_json():
    """Swagger specification"""
    swagger_spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "PDF Tools API",
            "description": "API untuk OCR dan Kompresi PDF",
            "version": "1.0.0"
        },
        "servers": [
            {
                "url": "http://localhost:5001",
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
            }
        }
    }
    return jsonify(swagger_spec)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)