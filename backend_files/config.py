import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


class Config:
    # MySQL connection settings
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = int(os.getenv("DB_PORT", 3306))
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "learnmate_ai")

    # JWT settings
    JWT_SECRET_KEY = os.getenv(
        "JWT_SECRET_KEY",
        "dev-secret-change-me-use-a-secure-env-value",
    )
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)

    # AI provider settings (Google Gemini)
    AI_API_KEY = (
        os.getenv("AI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or ""
    )
    AI_API_URL = os.getenv("AI_API_URL", "https://generativelanguage.googleapis.com/v1")
    AI_MODEL = os.getenv("AI_MODEL", "gemini-3.8-flash")
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 20 MB per file
    ALLOWED_EXTENSIONS = {"pdf", "docx", "doc", "txt", "png", "jpg", "jpeg"}
