import json
import re
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from db import get_db_connection
from services.ai_service import get_ai_response

quiz_bp = Blueprint("quiz", __name__, url_prefix="/api/quizzes")


def _get_owned_quiz(cur, quiz_id, user_id):
    cur.execute("SELECT * FROM quizzes WHERE id = %s AND user_id = %s", (quiz_id, user_id))
    return cur.fetchone()


def _extract_json_array(text):
    if not text:
        return text
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    cleaned = cleaned.strip("`").strip()

    start = cleaned.find("[")
    if start == -1:
        return cleaned

    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == "[":
            depth += 1
        elif cleaned[i] == "]":
            depth -= 1
            if depth == 0:
                return cleaned[start:i + 1]

    return cleaned[start:]


def _estimate_document_pages(file_type, file_path, text_sample):
    file_type = (file_type or "").lower()
    sample = (text_sample or "")
    text_length = len(sample)

    try:
        if file_type == "pdf":
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                return max(1, len(pdf.pages))
        if file_type in ("png", "jpg", "jpeg"):
            return 1
        if file_type == "txt":
            return max(1, max(1, text_length // 2000))
        if file_type == "docx":
            return max(1, max(1, len(sample.splitlines()) // 40))
    except Exception:
        pass

    return max(1, min(50, max(1, text_length // 1500)))


def _resolve_question_count_for_document(cur, user_id, document_id, requested_count):
    cur.execute(
        "SELECT file_type, file_path, extracted_text FROM documents WHERE id = %s AND user_id = %s",
        (document_id, user_id),
    )
    doc = cur.fetchone()
    if not doc:
        return int(requested_count) if requested_count else 5

    if requested_count:
        return int(requested_count)

    pages = _estimate_document_pages(doc.get("file_type"), doc.get("file_path"), doc.get("extracted_text"))
    if pages >= 20:
        return 50
    if pages >= 10:
        return 20
    if pages >= 5:
        return 10
    return 5


@quiz_bp.route("", methods=["POST"])
@jwt_required()
def create_quiz():
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    description = data.get("description", "")
    questions = data.get("questions", [])

    if not title:
        return jsonify({"success": False, "message": "Quiz title is required"}), 400
    if not questions:
        return jsonify({"success": False, "message": "At least one question is required"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO quizzes (user_id, title, description, source_type) VALUES (%s, %s, %s, 'manual')",
                (user_id, title, description),
            )
            quiz_id = cur.lastrowid

            for q in questions:
                cur.execute(
                    """
                    INSERT INTO quiz_questions
                        (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        quiz_id, q.get("questionText", ""), q.get("optionA", ""),
                        q.get("optionB", ""), q.get("optionC", ""), q.get("optionD", ""),
                        q.get("correctOption", "A").upper(),
                    ),
                )
            conn.commit()
        return jsonify({"success": True, "quiz": {"id": quiz_id, "title": title, "questionCount": len(questions)}}), 201
    finally:
        conn.close()


@quiz_bp.route("/generate", methods=["POST"])
@jwt_required()
def generate_quiz():
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    document_id = data.get("documentId")
    title = data.get("title") or "Generated Quiz"
    requested_count = data.get("numQuestions")

    try:
        num_questions = int(requested_count) if requested_count else 10
    except (TypeError, ValueError):
        num_questions = 10
    num_questions = max(5, min(num_questions, 50))

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if document_id:
                cur.execute(
                    "SELECT extracted_text, file_type, file_path FROM documents WHERE id = %s AND user_id = %s",
                    (document_id, user_id),
                )
                doc = cur.fetchone()
                if not doc:
                    return jsonify({"success": False, "message": "Document not found"}), 404
                content = (doc["extracted_text"] or "")[:8000]

                max_allowed = _resolve_question_count_for_document(cur, user_id, document_id, requested_count)
                if num_questions > max_allowed:
                    num_questions = max_allowed
            else:
                content = ""

            if not content.strip():
                return jsonify({"success": False, "message": "No source text available to build a quiz from"}), 400

            prompt = (
                f"Create {num_questions} multiple-choice questions from the text below. "
                f"Respond with ONLY a JSON array, no extra words, in this exact format:\n"
                f'[{{"questionText": "...", "optionA": "...", "optionB": "...", '
                f'"optionC": "...", "optionD": "...", "correctOption": "A"}}]\n\n'
                f"Text:\n{content}"
            )
            try:
                ai_text = get_ai_response([{"role": "user", "content": prompt}])
            except RuntimeError as ai_err:
                return jsonify({"success": False, "message": str(ai_err)}), 503

            try:
                questions = json.loads(_extract_json_array(ai_text))
            except (json.JSONDecodeError, TypeError):          
                print("=== RAW AI RESPONSE START ===")
                print(ai_text)
                print("=== RAW AI RESPONSE END ===")
                return jsonify({
                    "success": False,
                    "message": "AI response could not be parsed. Raw preview: " + (ai_text or "")[:300]
                }), 502

            cur.execute(
                "INSERT INTO quizzes (user_id, title, source_type, document_id) VALUES (%s, %s, 'ai-generated', %s)",
                (user_id, title, document_id),
            )
            quiz_id = cur.lastrowid

            for q in questions:
                cur.execute(
                    """
                    INSERT INTO quiz_questions
                        (quiz_id, question_text, option_a, option_b, option_c, option_d, correct_option)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        quiz_id, q.get("questionText", ""), q.get("optionA", ""),
                        q.get("optionB", ""), q.get("optionC", ""), q.get("optionD", ""),
                        (q.get("correctOption") or "A").upper(),
                    ),
                )
            conn.commit()

        return jsonify({"success": True, "quiz": {"id": quiz_id, "title": title, "questionCount": len(questions)}}), 201
    finally:
        conn.close()


@quiz_bp.route("", methods=["GET"])
@jwt_required()
def my_quizzes():
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT q.id, q.title, q.description, q.source_type, q.created_at,
                       (SELECT COUNT(*) FROM quiz_questions WHERE quiz_id = q.id) AS question_count,
                       (SELECT COUNT(*) FROM quiz_attempts WHERE quiz_id = q.id AND user_id = %s) AS attempt_count
                FROM quizzes q
                WHERE q.user_id = %s
                ORDER BY q.created_at DESC
                """,
                (user_id, user_id),
            )
            quizzes = cur.fetchall()
        return jsonify({"success": True, "quizzes": quizzes}), 200
    finally:
        conn.close()


@quiz_bp.route("/<int:quiz_id>", methods=["GET"])
@jwt_required()
def get_quiz_for_taking(quiz_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, description FROM quizzes WHERE id = %s", (quiz_id,))
            quiz = cur.fetchone()
            if not quiz:
                return jsonify({"success": False, "message": "Quiz not found"}), 404

            cur.execute(
                "SELECT id, question_text, option_a, option_b, option_c, option_d "
                "FROM quiz_questions WHERE quiz_id = %s",
                (quiz_id,),
            )
            questions = cur.fetchall()
        return jsonify({"success": True, "quiz": quiz, "questions": questions}), 200
    finally:
        conn.close()


@quiz_bp.route("/<int:quiz_id>/attempt", methods=["POST"])
@jwt_required()
def submit_attempt(quiz_id):
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    answers = data.get("answers", [])

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM quizzes WHERE id = %s", (quiz_id,))
            if not cur.fetchone():
                return jsonify({"success": False, "message": "Quiz not found"}), 404

            cur.execute(
                "SELECT id, question_text, correct_option FROM quiz_questions WHERE quiz_id = %s",
                (quiz_id,),
            )
            questions = cur.fetchall()
            correct_map = {q["id"]: q["correct_option"] for q in questions}
            question_text_map = {q["id"]: q["question_text"] for q in questions}

            score = 0
            graded = []
            for a in answers:
                qid = a.get("questionId")
                selected = (a.get("selectedOption") or "").upper() or None
                correct_answer = correct_map.get(qid)
                is_correct = selected is not None and correct_answer == selected
                if is_correct:
                    score += 1
                graded.append({
                    "questionId": qid,
                    "questionText": question_text_map.get(qid, ""),
                    "selectedOption": selected,
                    "correctOption": correct_answer,
                    "isCorrect": bool(is_correct),
                })

            cur.execute(
                "INSERT INTO quiz_attempts (quiz_id, user_id, score, total_questions) VALUES (%s, %s, %s, %s)",
                (quiz_id, user_id, score, len(correct_map)),
            )
            attempt_id = cur.lastrowid

            for item in graded:
                cur.execute(
                    "INSERT INTO quiz_answers (attempt_id, question_id, selected_option, is_correct) "
                    "VALUES (%s, %s, %s, %s)",
                    (attempt_id, item["questionId"], item["selectedOption"], item["isCorrect"]),
                )
            conn.commit()

        return jsonify({
            "success": True,
            "attemptId": attempt_id,
            "score": score,
            "totalQuestions": len(correct_map),
            "details": graded,
        }), 201
    finally:
        conn.close()


@quiz_bp.route("/<int:quiz_id>/results", methods=["GET"])
@jwt_required()
def quiz_results(quiz_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, score, total_questions, attempted_at FROM quiz_attempts "
                "WHERE quiz_id = %s AND user_id = %s ORDER BY attempted_at DESC",
                (quiz_id, user_id),
            )
            attempts = cur.fetchall()

            cur.execute(
                """
                SELECT qq.id AS question_id, qq.question_text,
                       COALESCE(SUM(qa.is_correct), 0) AS correct_count,
                       COUNT(qa.id) AS attempt_count
                FROM quiz_questions qq
                LEFT JOIN quiz_attempts qat ON qat.quiz_id = qq.quiz_id AND qat.user_id = %s
                LEFT JOIN quiz_answers qa ON qa.question_id = qq.id AND qa.attempt_id = qat.id
                WHERE qq.quiz_id = %s
                GROUP BY qq.id, qq.question_text
                """,
                (user_id, quiz_id),
            )
            per_question = cur.fetchall()

        best_score = max((a["score"] for a in attempts), default=0)
        return jsonify({
            "success": True,
            "attempts": attempts,
            "bestScore": best_score,
            "perQuestionAnalysis": per_question,
        }), 200
    finally:
        conn.close()


@quiz_bp.route("/<int:quiz_id>", methods=["DELETE"])
@jwt_required()
def delete_quiz(quiz_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            quiz = _get_owned_quiz(cur, quiz_id, user_id)
            if not quiz:
                return jsonify({"success": False, "message": "Quiz not found"}), 404
            cur.execute("DELETE FROM quizzes WHERE id = %s", (quiz_id,))
            conn.commit()
        return jsonify({"success": True, "message": "Quiz deleted"}), 200
    finally:
        conn.close()
