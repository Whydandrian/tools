import os

GHOSTSCRIPT_PATH = "/usr/bin/gs"
LIBREOFFICE_PATH = "/usr/bin/soffice"
POPPLER_PATH = "/usr/bin"
TESSERACT_PATH = "/usr/bin/tesseract"
PDFTOPPM_PATH = "/usr/bin/pdftoppm"

# Verify tools exist
def verify_tools():
    """Check if all tools are available"""
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