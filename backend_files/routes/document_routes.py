import os
import uuid
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from werkzeug.utils import secure_filename

from db import get_db_connection
from config import Config
from services.document_service import extract_text, allowed_file, get_file_extension
from services.ai_service import get_ai_response

doc_bp = Blueprint("documents", __name__, url_prefix="/api/documents")


def _get_owned_document(cur, doc_id, user_id):
    cur.execute("SELECT * FROM documents WHERE id = %s AND user_id = %s", (doc_id, user_id))
    return cur.fetchone()


def _get_document_text(cur, doc, user_id):
    stored_text = doc.get("extracted_text")
    text = (stored_text or "").strip()
    if text and not text.startswith("[Could not extract text automatically:"):
        return text, False

    file_path = doc.get("file_path") or ""
    if file_path and not os.path.isabs(file_path):
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        file_path = os.path.join(backend_dir, file_path)
    if not file_path or not os.path.isfile(file_path):
        return "", False

    text = (extract_text(file_path, doc.get("file_type")) or "").strip()
    if not text or text.startswith("[Could not extract text automatically:"):
        return "", False

    cur.execute(
        "UPDATE documents SET extracted_text = %s "
        "WHERE id = %s AND user_id = %s "
        "AND (extracted_text IS NULL OR TRIM(extracted_text) = '' OR extracted_text = %s)",
        (text, doc["id"], user_id, stored_text),
    )
    doc["extracted_text"] = text
    return text, True


@doc_bp.route("/upload", methods=["POST"])
@jwt_required()
def upload_document():
    user_id = get_jwt_identity()

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"success": False, "message": "No file selected"}), 400

    if not allowed_file(file.filename, Config.ALLOWED_EXTENSIONS):
        return jsonify({
            "success": False,
            "message": f"File type not allowed. Allowed: {', '.join(Config.ALLOWED_EXTENSIONS)}"
        }), 400

    original_name = secure_filename(file.filename)
    ext = get_file_extension(original_name)
    unique_name = f"{uuid.uuid4().hex}.{ext}"

    user_folder = os.path.join(Config.UPLOAD_FOLDER, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    saved_path = os.path.join(user_folder, unique_name)
    file.save(saved_path)

    extracted_text = extract_text(saved_path, ext)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (user_id, file_name, file_path, file_type, extracted_text)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, original_name, saved_path, ext, extracted_text),
            )
            conn.commit()
            doc_id = cur.lastrowid

        return jsonify({
            "success": True,
            "document": {
                "id": doc_id,
                "fileName": original_name,
                "fileType": ext,
                "textPreview": (extracted_text or "")[:300],
            }
        }), 201
    finally:
        conn.close()


@doc_bp.route("", methods=["GET"])
@jwt_required()
def my_documents():
    user_id = get_jwt_identity()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, file_name, file_type, uploaded_at, "
                "LEFT(extracted_text, 200) AS text_preview "
                "FROM documents WHERE user_id = %s ORDER BY uploaded_at DESC",
                (user_id,),
            )
            docs = cur.fetchall()
        return jsonify({"success": True, "documents": docs}), 200
    finally:
        conn.close()


@doc_bp.route("/<int:doc_id>", methods=["GET"])
@jwt_required()
def get_document(doc_id):
    user_id = get_jwt_identity()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            doc = _get_owned_document(cur, doc_id, user_id)
            if not doc:
                return jsonify({"success": False, "message": "Document not found"}), 404
            _, recovered = _get_document_text(cur, doc, user_id)
            if recovered:
                conn.commit()
        return jsonify({"success": True, "document": doc}), 200
    finally:
        conn.close()


@doc_bp.route("/<int:doc_id>", methods=["DELETE"])
@jwt_required()
def delete_document(doc_id):
    user_id = get_jwt_identity()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            doc = _get_owned_document(cur, doc_id, user_id)
            if not doc:
                return jsonify({"success": False, "message": "Document not found"}), 404
            cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
            conn.commit()

        try:
            if os.path.exists(doc["file_path"]):
                os.remove(doc["file_path"])
        except OSError:
            pass

        return jsonify({"success": True, "message": "Document deleted"}), 200
    finally:
        conn.close()


@doc_bp.route("/<int:doc_id>/analyze", methods=["POST"])
@jwt_required()
def analyze_document(doc_id):
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    analysis_type = data.get("type", "summary")
    question = (data.get("question") or "").strip()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            doc = _get_owned_document(cur, doc_id, user_id)
            if not doc:
                return jsonify({"success": False, "message": "Document not found"}), 404

            content, _ = _get_document_text(cur, doc, user_id)
            content = content[:12000]

            if not content.strip():
                return jsonify({"success": False, "message": "No readable text found in this document"}), 400

            if analysis_type == "qa":
                if not question:
                    return jsonify({"success": False, "message": "Please provide a question"}), 400
                prompt = (
                    f"Answer the question using the document below. "
                    f"If the answer isn't in the document, say so and then answer generally.\n\n"
                    f"Document:\n{content}\n\nQuestion: {question}"
                )
            elif analysis_type == "keywords":
                prompt = f"List the main keywords/topics from this document:\n\n{content}"
            else:
                prompt = f"Summarize this document clearly in a few short paragraphs:\n\n{content}"

            result = get_ai_response([{"role": "user", "content": prompt}])

            cur.execute(
                """
                INSERT INTO document_analysis (document_id, analysis_type, question, result)
                VALUES (%s, %s, %s, %s)
                """,
                (doc_id, analysis_type, question or None, result),
            )
            conn.commit()

        return jsonify({"success": True, "type": analysis_type, "result": result}), 200
    finally:
        conn.close()


@doc_bp.route("/<int:doc_id>/analysis", methods=["GET"])
@jwt_required()
def get_analyses(doc_id):
    user_id = get_jwt_identity()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            doc = _get_owned_document(cur, doc_id, user_id)
            if not doc:
                return jsonify({"success": False, "message": "Document not found"}), 404

            cur.execute(
                "SELECT id, analysis_type, question, result, created_at "
                "FROM document_analysis WHERE document_id = %s ORDER BY created_at DESC",
                (doc_id,),
            )
            analyses = cur.fetchall()
        return jsonify({"success": True, "analyses": analyses}), 200
    finally:
        conn.close()