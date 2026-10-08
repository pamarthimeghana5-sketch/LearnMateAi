import os


def extract_text(file_path, file_type):
    file_type = (file_type or "").lower()

    try:
        if file_type == "txt":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()

        elif file_type == "pdf":
            import pdfplumber
            text_parts = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            return "\n".join(text_parts)

        elif file_type in ("docx", "doc"):
            import docx
            doc = docx.Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs if p.text)

        elif file_type in ("png", "jpg", "jpeg"):
            import pytesseract
            from PIL import Image
            image = Image.open(file_path)
            return pytesseract.image_to_string(image, lang="eng+tel+hin")

        else:
            return ""

    except Exception as e:
        return f"[Could not extract text automatically: {str(e)}]"


def allowed_file(filename, allowed_extensions):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def get_file_extension(filename):
    return filename.rsplit(".", 1)[1].lower() if "." in filename else ""