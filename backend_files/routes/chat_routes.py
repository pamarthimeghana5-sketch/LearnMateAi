import os
import sys
import time
import traceback
import base64
import binascii
import re
import pymysql
from flask import Blueprint, request, jsonify, Response
from flask_jwt_extended import jwt_required, get_jwt_identity
from dotenv import load_dotenv
from google import genai
from google.genai import types

from config import Config

# Path setup
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(CURRENT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)

from db import get_db_connection
from services.document_service import extract_text
load_dotenv()

# API Key - .env lo AI_API_KEY or GOOGLE_API_KEY ani undali
GEMINI_API_KEY = Config.AI_API_KEY or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

ANSWER_FORMATTING_INSTRUCTIONS = (
    "Act as a helpful student-friendly AI tutor. Use simple English suitable for diploma students, "
    "short sentences, clean spacing, and headings only when useful. NEVER write a long paragraph "
    "when the answer can be shown as points. Each point MUST be on its own bullet or numbered line. "
    "Do not add an unnecessary introduction, repeated conclusion, or extra information. Follow the "
    "required answer format exactly."
)

CHAT_RESPONSE_INSTRUCTIONS = ANSWER_FORMATTING_INSTRUCTIONS

DOCUMENT_RESPONSE_INSTRUCTIONS = (
    f"{ANSWER_FORMATTING_INSTRUCTIONS} Use only the selected document as the primary source and "
    "answer only with information supported by the document. If the answer is not present, respond "
    "with exactly DOCUMENT_ANSWER_NOT_FOUND and no other text. Do not copy large paragraphs from the document; "
    "convert relevant information into short, easy points."
)
DOCUMENT_ANSWER_NOT_FOUND = "DOCUMENT_ANSWER_NOT_FOUND"


def _get_question_format_hint(question, has_image=False):
    question_text = (question or "").strip().lower()
    if has_image:
        if re.search(r"\b(code|program|source code|syntax|script)\b", question_text):
            return "Analyze the image code. Show or quote the code first when readable, then explain it line by line and show the expected output when possible."
        if re.search(r"\b(diagram|structure|flowchart|architecture|lifecycle|life cycle)\b", question_text):
            return "Explain the image diagram part by part. Recreate its flow using arrows, boxes, levels, or an ASCII structure, then give short points for each part."
        if re.search(r"\b(step|steps|solve|solution|calculate|numerical|question|problem)\b", question_text):
            return "Read the image carefully, including handwritten text. If it contains a question or numerical problem, solve it step by step with one clear step per line."
        return "Analyze the image carefully. If it contains a question, answer it step by step; if it contains a diagram, explain it part by part; if it contains handwritten text, read and answer it clearly."
    if re.search(r"\b(code|program|source code|syntax|script)\b", question_text):
        return "Show the code first, then give a short line-by-line explanation and the expected output when possible."
    if re.search(r"\b(numerical|calculate|solve|find the|equation|formula|problem)\b", question_text):
        return "Show the solution step by step, including the formula, substitution, calculation, and final answer."
    if re.search(r"\b(difference|differences|compare|comparison|distinguish)\b", question_text):
        return "Use a simple Markdown comparison table with clear column headings."
    if re.search(r"\b(step|steps|procedure|process|method|algorithm)\b", question_text):
        return "Use numbered steps in the correct order, one step per line."
    if re.search(
        r"\b(advantage|advantages|disadvantage|disadvantages|feature|features|use|uses|benefit|"
        r"benefits|application|applications|type|types|characteristic|characteristics)\b",
        question_text,
    ):
        return "Use clear bullet points, one point per line. Never combine the points into a paragraph."
    if re.search(r"\b(structure|diagram|flowchart|architecture)\b", question_text):
        return "Use a clean indented or ASCII diagram/structure with labels on separate lines."
    if re.search(r"^(what is|define|definition of|meaning of)\b", question_text):
        return "Give ONLY a simple definition in 2-3 short lines. Do not add examples or extra details."
    if re.search(r"\b(explain|explanation|describe)\b", question_text):
        return "Give a short explanation followed by bullet points for the important points. Avoid one long paragraph."
    return "Give a short answer. If there are multiple ideas or items, use bullet points instead of a paragraph."


def _format_chat_reply(text):
    """Normalize spacing when the model compresses list markers onto one line."""
    if not text:
        return ""
    formatted = text.replace("\r\n", "\n").strip()
    formatted = re.sub(r"[ \t]+(?=(?:[-*•]|\d+[.)])\s+)", "\n\n", formatted)
    formatted = re.sub(r"\n[ \t]*(?=(?:[-*•]|\d+[.)])\s+)", "\n\n", formatted)
    formatted = re.sub(r"\n{3,}", "\n\n", formatted)
    return formatted.strip()


def get_chat_model_candidates():
    """
    Try the configured model first, then stable current models if it is
    rate-limited or unavailable. Avoid the moving gemini-flash-latest alias.
    """
    candidates = [Config.AI_MODEL]
    fallbacks = [
        "gemini-3.5-flash-lite",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]
    for f in fallbacks:
        if f not in candidates:
            candidates.append(f)
    return candidates


def _is_quota_error(exc):
    err_text = str(exc).lower()
    return (
        "429" in str(exc)
        or "resource_exhausted" in err_text
        or "quota exceeded" in err_text
        or "quota_exceeded" in err_text
        or "too many requests" in err_text
    )


def _is_invalid_api_key_error(exc):
    err_text = str(exc).lower()
    return "api_key_invalid" in err_text or "api key not valid" in err_text


def _is_network_error(exc):
    err_text = str(exc).lower()
    return any(
        marker in err_text
        for marker in (
            "winerror 10065",
            "unreachable host",
            "network is unreachable",
            "network unreachable",
            "no route to host",
            "connection error",
            "connecterror",
            "failed to establish a new connection",
        )
    )


def _is_retryable_error(exc):
    err_text = str(exc).lower()
    return (
        "503" in str(exc)
        or "unavailable" in err_text
        or "high demand" in err_text
        or "404" in str(exc)
        or "not_found" in err_text
        or "not found" in err_text
        or "no longer available" in err_text
    )


def _decode_image_data_url(image_data_url):
    if not image_data_url:
        return None
    if not isinstance(image_data_url, str) or len(image_data_url) > 12 * 1024 * 1024:
        raise ValueError("Image is too large. Please choose an image smaller than 8 MB.")

    match = re.fullmatch(r"data:(image/(?:png|jpeg|jpg|webp|gif));base64,([A-Za-z0-9+/=]+)", image_data_url)
    if not match:
        raise ValueError("Unsupported image format. Please upload a PNG, JPG, WEBP, or GIF image.")

    mime_type = "image/jpeg" if match.group(1) == "image/jpg" else match.group(1)
    try:
        image_bytes = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("The uploaded image could not be read.") from exc
    if not image_bytes or len(image_bytes) > 8 * 1024 * 1024:
        raise ValueError("Image is too large. Please choose an image smaller than 8 MB.")
    return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)


def generate_chat_reply(user_msg, image_data_url=None, max_retries_per_model=1, retry_delay_seconds=5):
    """
    Tries each candidate model in order. For quota errors, moves on to the
    next model immediately (retrying the same model won't help). For
    transient/availability errors, retries the same model a couple of times
    before moving on.
    """
    last_error = None
    quota_hit_models = []
    image_part = _decode_image_data_url(image_data_url)
    question_text = user_msg or "Please analyze the attached image and explain clearly what it shows."
    if "\n\nQuestion:" in question_text:
        question_text = question_text.rsplit("\n\nQuestion:", 1)[1].strip()
    format_hint = _get_question_format_hint(question_text, has_image=bool(image_part))
    prompt_text = (
        f"{CHAT_RESPONSE_INSTRUCTIONS}\n"
        f"Required answer format: {format_hint}\n\n"
        f"User request and source context:\n{user_msg or question_text}"
    )
    contents = [types.Part.from_text(text=prompt_text)]
    if image_part:
        contents.append(image_part)

    for model_name in get_chat_model_candidates():
        attempts = 0
        while attempts <= max_retries_per_model:
            attempts += 1
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents
                )
                text = getattr(response, "text", None)
                if text:
                    return _format_chat_reply(text), model_name
                if hasattr(response, "candidates") and response.candidates:
                    candidate_text = response.candidates[0].content.parts[0].text
                    if candidate_text:
                        return _format_chat_reply(candidate_text), model_name
                raise ValueError("Empty response from Gemini")

            except Exception as exc:
                last_error = exc

                if _is_invalid_api_key_error(exc):
                    raise RuntimeError(
                        "Gemini rejected the configured API key. Create a valid key in "
                        "Google AI Studio and set AI_API_KEY in backend_files/.env."
                    ) from exc

                if _is_quota_error(exc):
                    quota_hit_models.append(model_name)
                    break  # no point retrying same model, try next candidate

                if _is_network_error(exc):
                    raise RuntimeError(
                        "Cannot reach Google's Gemini API. Check your internet "
                        "connection, VPN, firewall, and proxy settings, then try again."
                    ) from exc

                if _is_retryable_error(exc):
                    if attempts <= max_retries_per_model:
                        time.sleep(retry_delay_seconds)
                        continue
                    break  # exhausted retries for this model, try next candidate

                # Unknown/unexpected error - don't keep trying other models silently
                raise

    # All candidates exhausted
    if quota_hit_models:
        quota_models = ", ".join(dict.fromkeys(quota_hit_models))
        raise RuntimeError(
            f"Gemini API quota was reached for: {quota_models}. "
            "The configured fallback models did not provide a response. "
            "Wait for the quota to reset or check Google AI Studio usage, billing, "
            "and the limits for each model."
        )

    if last_error:
        raise RuntimeError(f"AI service is temporarily unavailable. Please try again later. ({last_error})")

    raise RuntimeError("AI service is temporarily unavailable. Please try again later.")


chat_bp = Blueprint("chat", __name__, url_prefix="/api")


def _get_uploaded_document_context(user_id, document_ids=None):
    if not document_ids:
        return ""

    valid_document_ids = []
    for document_id in document_ids:
        try:
            parsed_id = int(document_id)
        except (TypeError, ValueError):
            continue
        if parsed_id > 0 and parsed_id not in valid_document_ids:
            valid_document_ids.append(parsed_id)
    if not valid_document_ids:
        return ""

    conn = get_db_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            placeholders = ', '.join(['%s'] * len(valid_document_ids))
            cur.execute(
                f"SELECT id, file_name, extracted_text, file_path, file_type FROM documents "
                f"WHERE user_id = %s AND id IN ({placeholders}) ORDER BY uploaded_at DESC",
                (user_id, *valid_document_ids),
            )
            docs = cur.fetchall()
    finally:
        conn.close()

    if not docs:
        return ""

    context_parts = []
    recovered_texts = []
    for doc in docs:
        stored_text = doc.get("extracted_text")
        text = (stored_text or "").strip()
        if not text or text.startswith("[Could not extract text automatically:"):
            file_path = doc.get("file_path") or ""
            if file_path and not os.path.isabs(file_path):
                backend_relative_path = os.path.join(BACKEND_DIR, file_path)
                if os.path.isfile(backend_relative_path):
                    file_path = backend_relative_path
            if file_path and os.path.isfile(file_path):
                text = (extract_text(file_path, doc.get("file_type")) or "").strip()
                if text and not text.startswith("[Could not extract text automatically:"):
                    recovered_texts.append((doc.get("id"), text, stored_text))
        if not text:
            continue
        if text.startswith("[Could not extract text automatically:"):
            continue
        context_parts.append(f"Document: {doc.get('file_name') or 'Uploaded file'}\n{text}")

    if recovered_texts:
        try:
            update_conn = get_db_connection()
            try:
                with update_conn.cursor(pymysql.cursors.DictCursor) as cur:
                    for document_id, text, stored_text in recovered_texts:
                        cur.execute(
                            "UPDATE documents SET extracted_text = %s "
                            "WHERE id = %s AND user_id = %s "
                            "AND (extracted_text IS NULL OR TRIM(extracted_text) = '' OR extracted_text = %s)",
                            (text, document_id, user_id, stored_text),
                        )
                update_conn.commit()
            finally:
                update_conn.close()
        except Exception:
            pass

    return "\n\n".join(context_parts)


@chat_bp.route("/chat/new", methods=["POST"])
@jwt_required()
def new_chat():
    conn = get_db_connection()
    try:
        user_id = get_jwt_identity()
        data = request.get_json(silent=True) or {}
        title = data.get("title", "New Chat")
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("INSERT INTO chats (user_id, title) VALUES (%s, %s)", (user_id, title))
            chat_id = cur.lastrowid
            conn.commit()
        return jsonify({"success": True, "chat": {"id": chat_id, "title": title}}), 201
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()


@chat_bp.route("/chat/history", methods=["GET"])
@jwt_required()
def chat_history():
    conn = get_db_connection()
    try:
        user_id = get_jwt_identity()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT c.id, c.title, c.created_at, c.updated_at, "
                "(SELECT m.content FROM messages m WHERE m.chat_id = c.id "
                "ORDER BY m.created_at DESC LIMIT 1) AS last_message "
                "FROM chats c WHERE c.user_id = %s ORDER BY c.updated_at DESC",
                (user_id,)
            )
            chats = cur.fetchall()
        return jsonify({"success": True, "chats": chats}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()


@chat_bp.route("/chat/<int:chat_id>/messages", methods=["GET"])
@jwt_required()
def get_messages(chat_id):
    conn = get_db_connection()
    try:
        user_id = get_jwt_identity()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT id FROM chats WHERE id = %s AND user_id = %s", (chat_id, user_id))
            if not cur.fetchone():
                return jsonify({"success": False, "message": "Chat not found"}), 404
            cur.execute(
                "SELECT id, chat_id, user_id, role, content, created_at "
                "FROM messages WHERE chat_id = %s ORDER BY created_at ASC",
                (chat_id,)
            )
            messages = cur.fetchall()
        return jsonify({"success": True, "messages": messages}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()


@chat_bp.route("/chat/<int:chat_id>/message", methods=["POST"])
@jwt_required()
def send_message(chat_id):
    conn = get_db_connection()
    try:
        user_id = get_jwt_identity()
        data = request.get_json(silent=True) or {}
        user_msg = data.get("message", "").strip()
        document_ids = data.get("document_ids") or []
        image_data_url = data.get("image_data_url")

        if not user_msg and not image_data_url:
            return jsonify({"success": False, "message": "Message or image required"}), 400
        if not client:
            return jsonify({"success": False, "message": "API Key missing in .env"}), 500

        try:
            _decode_image_data_url(image_data_url)
        except ValueError as image_error:
            return jsonify({"success": False, "message": str(image_error)}), 400

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT id FROM chats WHERE id = %s AND user_id = %s", (chat_id, user_id))
            if not cur.fetchone():
                return jsonify({"success": False, "message": "Chat not found"}), 404

        doc_context = _get_uploaded_document_context(user_id, document_ids if isinstance(document_ids, list) else [])
        prompt = user_msg
        if doc_context:
            prompt = (
                f"{DOCUMENT_RESPONSE_INSTRUCTIONS}\n\n"
                f"{doc_context}\n\nQuestion: {user_msg}"
            )

        try:
            ai_reply, model_used = generate_chat_reply(prompt, image_data_url=image_data_url)
            if doc_context and DOCUMENT_ANSWER_NOT_FOUND in ai_reply:
                ai_reply, model_used = generate_chat_reply(user_msg, image_data_url=image_data_url)
        except RuntimeError as ai_err:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    "INSERT INTO messages (chat_id, user_id, role, content) VALUES (%s, %s, %s, %s)",
                    (chat_id, user_id, "user", user_msg or "[Image uploaded for analysis]")
                )
                conn.commit()
            return jsonify({"success": False, "message": str(ai_err)}), 503

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "INSERT INTO messages (chat_id, user_id, role, content) VALUES (%s, %s, %s, %s)",
                (chat_id, user_id, "user", user_msg or "[Image uploaded for analysis]")
            )
            cur.execute(
                "INSERT INTO messages (chat_id, user_id, role, content) VALUES (%s, %s, %s, %s)",
                (chat_id, user_id, "assistant", ai_reply)
            )
            cur.execute("UPDATE chats SET updated_at = NOW() WHERE id = %s", (chat_id,))
            conn.commit()

        return jsonify({"success": True, "reply": ai_reply, "model": model_used}), 200

    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        message = str(e)
        if "temporarily unavailable" in message.lower() or "503" in message or "unavailable" in message.lower():
            message = "The AI service is currently busy. Please try again in a few moments."
        return jsonify({"success": False, "message": message}), 500
    finally:
        conn.close()


@chat_bp.route("/chat/<int:chat_id>/export", methods=["GET"])
@jwt_required()
def export_chat(chat_id):
    conn = get_db_connection()
    try:
        user_id = get_jwt_identity()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT id FROM chats WHERE id = %s AND user_id = %s", (chat_id, user_id))
            if not cur.fetchone():
                return jsonify({"success": False, "message": "Chat not found"}), 404

            cur.execute(
                "SELECT role, content, created_at FROM messages WHERE chat_id = %s ORDER BY created_at ASC",
                (chat_id,),
            )
            messages = cur.fetchall()

        if not messages:
            export_text = "No messages in this chat."
        else:
            export_lines = []
            for msg in messages:
                role = "User" if msg.get("role") == "user" else "Assistant"
                content = (msg.get("content") or "").strip()
                export_lines.append(f"{role}: {content}")
            export_text = "\n\n".join(export_lines)

        return Response(
            export_text,
            mimetype="text/plain",
            headers={"Content-Disposition": "attachment; filename=chat_export.txt"},
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()


@chat_bp.route("/chat/<int:chat_id>", methods=["DELETE"])
@jwt_required()
def delete_chat(chat_id):
    conn = get_db_connection()
    try:
        user_id = get_jwt_identity()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT id FROM chats WHERE id = %s AND user_id = %s", (chat_id, user_id))
            if not cur.fetchone():
                return jsonify({"success": False, "message": "Chat not found"}), 404
            cur.execute("DELETE FROM messages WHERE chat_id = %s", (chat_id,))
            cur.execute("DELETE FROM chats WHERE id = %s AND user_id = %s", (chat_id, user_id))
            conn.commit()
        return jsonify({"success": True, "message": "Chat deleted"}), 200
    except Exception as e:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()