from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from db import get_db_connection
from services.ai_service import get_ai_response

notes_bp = Blueprint("notes", __name__, url_prefix="/api/notes")


def _get_owned_note(cur, note_id, user_id):
    cur.execute("SELECT * FROM notes WHERE id = %s AND user_id = %s", (note_id, user_id))
    return cur.fetchone()


@notes_bp.route("", methods=["POST"])
@jwt_required()
def create_note():
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    content = data.get("content", "")

    if not title:
        return jsonify({"success": False, "message": "Note title is required"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notes (user_id, title, content) VALUES (%s, %s, %s)",
                (user_id, title, content),
            )
            conn.commit()
            note_id = cur.lastrowid
        return jsonify({"success": True, "note": {"id": note_id, "title": title, "content": content}}), 201
    finally:
        conn.close()


@notes_bp.route("", methods=["GET"])
@jwt_required()
def all_notes():
    user_id = get_jwt_identity()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, LEFT(content, 150) AS preview, "
                "(ai_summary IS NOT NULL) AS has_summary, created_at, updated_at "
                "FROM notes WHERE user_id = %s ORDER BY updated_at DESC",
                (user_id,),
            )
            notes = cur.fetchall()
        return jsonify({"success": True, "notes": notes}), 200
    finally:
        conn.close()


@notes_bp.route("/<int:note_id>", methods=["GET"])
@jwt_required()
def get_note(note_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            note = _get_owned_note(cur, note_id, user_id)
            if not note:
                return jsonify({"success": False, "message": "Note not found"}), 404
        return jsonify({"success": True, "note": note}), 200
    finally:
        conn.close()


@notes_bp.route("/<int:note_id>", methods=["PUT"])
@jwt_required()
def update_note(note_id):
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    title = data.get("title")
    content = data.get("content")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            note = _get_owned_note(cur, note_id, user_id)
            if not note:
                return jsonify({"success": False, "message": "Note not found"}), 404

            cur.execute(
                "UPDATE notes SET title = %s, content = %s WHERE id = %s",
                (title if title is not None else note["title"],
                 content if content is not None else note["content"],
                 note_id),
            )
            conn.commit()
        return jsonify({"success": True, "message": "Note updated"}), 200
    finally:
        conn.close()


@notes_bp.route("/<int:note_id>", methods=["DELETE"])
@jwt_required()
def delete_note(note_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            note = _get_owned_note(cur, note_id, user_id)
            if not note:
                return jsonify({"success": False, "message": "Note not found"}), 404
            cur.execute("DELETE FROM notes WHERE id = %s", (note_id,))
            conn.commit()
        return jsonify({"success": True, "message": "Note deleted"}), 200
    finally:
        conn.close()


@notes_bp.route("/<int:note_id>/summarize", methods=["POST"])
@jwt_required()
def summarize_note(note_id):
    user_id = get_jwt_identity()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            note = _get_owned_note(cur, note_id, user_id)
            if not note:
                return jsonify({"success": False, "message": "Note not found"}), 404

            if not (note["content"] or "").strip():
                return jsonify({"success": False, "message": "Note has no content to summarize"}), 400

            prompt = f"Summarize this note in 2-4 short sentences:\n\n{note['content']}"
            summary = get_ai_response([{"role": "user", "content": prompt}])

            cur.execute("UPDATE notes SET ai_summary = %s WHERE id = %s", (summary, note_id))
            conn.commit()

        return jsonify({"success": True, "summary": summary}), 200
    finally:
        conn.close()