import os
import shutil
import platform

GHOSTSCRIPT_PATH = "/usr/bin/gs"
LIBREOFFICE_PATH = "/usr/bin/soffice"
POPPLER_PATH = "/usr/bin"
TESSERACT_PATH = "/usr/bin/tesseract"
PDFTOPPM_PATH = "/usr/bin/pdftoppm"

def detect_ghostscript():
    """
    Mencari Ghostscript secara otomatis di macOS dan Linux.
    Prioritas:
    1. PATH environment (shutil.which)
    2. Lokasi umum Linux
    3. Lokasi umum macOS
    4. Variabel default (/usr/bin/gs)
    """
    # 1. Coba deteksi via PATH
    gs = shutil.which("gs")
    if gs:
        return gs

    # 2. Lokasi umum Linux (Debian/Ubuntu/CentOS)
    linux_paths = [
        "/usr/bin/gs",
        "/usr/local/bin/gs",
        "/snap/bin/gs",
        "/bin/gs",
    ]
    for path in linux_paths:
        if os.path.exists(path):
            return path

    # 3. Lokasi umum macOS
    mac_paths = [
        "/opt/homebrew/bin/gs",    # Apple Silicon
        "/usr/local/bin/gs",       # Intel Mac
    ]
    for path in mac_paths:
        if os.path.exists(path):
            return path

    # 4. Jika semuanya gagal, pakai default
    return GHOSTSCRIPT_PATH

# Verify tools exist
def verify_tools():
    """Check if all tools are available"""
    global GHOSTSCRIPT_PATH
    GHOSTSCRIPT_PATH = detect_ghostscript()

    tools = {
        "Ghostscript": GHOSTSCRIPT_PATH,
        "LibreOffice": LIBREOFFICE_PATH,
        "Tesseract": TESSERACT_PATH,
    }
    
    missing = []
    for name, path in tools.items():
        if not os.path.exists(path):
            missing.append(f"{name} at {path}")
    
    if missing:
        print(f"⚠️  Warning: Missing tools: {', '.join(missing)}")
    else:
        print("✅ All external tools available")
    
    return len(missing) == 0

# Run verification
verify_tools()