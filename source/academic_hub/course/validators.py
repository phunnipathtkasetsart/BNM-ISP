import os
from django.conf import settings
from django.core.exceptions import ValidationError

# Allowed extensions (Google Classroom safe standards)
ALLOWED_EXTENSIONS = {
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'txt', 'csv', 'rtf', 'jpg', 'jpeg', 'png', 'gif',
    'webp', 'mp4', 'mp3', 'zip', 'rar'
}

# Blocked extensions
BLOCKED_EXTENSIONS = {
    'exe', 'bat', 'cmd', 'sh', 'com', 'msi', 'scr', 'pif',
    'application', 'gadget', 'ps1', 'vbs', 'js', 'jar', 'php', 
    'py', 'pl', 'elf', 'dll', 'so', 'dylib', 'cgi'
}

# Dangerous Binary Signatures (Magic Bytes)
DANGEROUS_SIGNATURES = [
    b'MZ',                # Windows Executable / DLL (.exe, .dll, .sys)
    b'\x7fELF',           # Linux Executable / Shared Object (.elf, .so)
    b'#!',                # Shell script shebang (Linux/Mac script execution)
    b'\xca\xfe\xba\xbe',   # Java Class / macOS Mach-O binary
    b'\xfe\xed\xfa\xce',   # macOS Mach-O 32-bit
    b'\xfe\xed\xfa\xcf',   # macOS Mach-O 64-bit
]


def max_upload_bytes():
    return getattr(settings, "MAX_UPLOAD_BYTES", 50 * 1024 * 1024)


def validate_upload_size(file):
    """Reject files over the configured limit (50 MB by default)."""
    limit = max_upload_bytes()
    if file.size > limit:
        raise ValidationError(
            "File is too large. The maximum size is %(mb)d MB.",
            code="file_too_large", params={"mb": limit // (1024 * 1024)},
        )


def validate_file_security(file):
    """Validate file extension and inspect binary signature bytes."""
    # 1. Extension Whitelist & Blacklist Validation
    filename = getattr(file, 'name', '')
    ext = os.path.splitext(filename)[1].lstrip('.').lower()

    if ext in BLOCKED_EXTENSIONS or ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"File format '.{ext}' is strictly prohibited. Allowed formats: PDF, office documents, images, audio, video, zip.",
            code="disallowed_file_extension"
        )

    # 2. Magic Bytes / Header Inspection
    initial_pos = file.tell()
    try:
        file.seek(0)
        header = file.read(512)
        
        for sig in DANGEROUS_SIGNATURES:
            if header.startswith(sig):
                raise ValidationError(
                    "Executable or potentially dangerous binary files are not permitted.",
                    code="dangerous_file_type"
                )
    finally:
        file.seek(initial_pos)  # Restore stream position for Django storage