from datetime import datetime

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from db import get_db_connection

planner_bp = Blueprint("planner", __name__, url_prefix="/api")


def _get_owned_task(cur, task_id, user_id):
    cur.execute("SELECT * FROM tasks WHERE id = %s AND user_id = %s", (task_id, user_id))
    return cur.fetchone()


@planner_bp.route("/tasks", methods=["POST"])
@jwt_required()
def add_task():
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    description = data.get("description", "")
    due_date = data.get("dueDate")
    due_time = data.get("dueTime")
    priority = data.get("priority", "medium")

    if not title:
        return jsonify({"success": False, "message": "Task title is required"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tasks (user_id, title, description, due_date, due_time, priority)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (user_id, title, description, due_date, due_time, priority),
            )
            conn.commit()
            task_id = cur.lastrowid
        return jsonify({"success": True, "task": {"id": task_id, "title": title}}), 201
    finally:
        conn.close()


@planner_bp.route("/tasks", methods=["GET"])
@jwt_required()
def my_tasks():
    user_id = get_jwt_identity()
    status = request.args.get("status")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT * FROM tasks WHERE user_id = %s AND status = %s "
                    "ORDER BY due_date IS NULL, due_date ASC, due_time ASC",
                    (user_id, status),
                )
            else:
                cur.execute(
                    "SELECT * FROM tasks WHERE user_id = %s "
                    "ORDER BY due_date IS NULL, due_date ASC, due_time ASC",
                    (user_id,),
                )
            tasks = cur.fetchall()
        for task in tasks:
            due_date = task.get("due_date")
            if due_date is not None and hasattr(due_date, "isoformat"):
                task["due_date"] = due_date.isoformat()
            if task.get("due_time") is not None:
                task["due_time"] = str(task["due_time"])
        return jsonify({"success": True, "tasks": tasks}), 200
    finally:
        conn.close()


@planner_bp.route("/tasks/<int:task_id>", methods=["PUT"])
@jwt_required()
def update_task(task_id):
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            task = _get_owned_task(cur, task_id, user_id)
            if not task:
                return jsonify({"success": False, "message": "Task not found"}), 404

            cur.execute(
                """
                UPDATE tasks
                SET title = %s, description = %s, due_date = %s, due_time = %s,
                    priority = %s, status = %s
                WHERE id = %s
                """,
                (
                    data.get("title", task["title"]),
                    data.get("description", task["description"]),
                    data.get("dueDate", task["due_date"]),
                    data.get("dueTime", task["due_time"]),
                    data.get("priority", task["priority"]),
                    data.get("status", task["status"]),
                    task_id,
                ),
            )
            conn.commit()
        return jsonify({"success": True, "message": "Task updated"}), 200
    finally:
        conn.close()


@planner_bp.route("/tasks/<int:task_id>", methods=["DELETE"])
@jwt_required()
def delete_task(task_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            task = _get_owned_task(cur, task_id, user_id)
            if not task:
                return jsonify({"success": False, "message": "Task not found"}), 404
            cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
            conn.commit()
        return jsonify({"success": True, "message": "Task deleted"}), 200
    finally:
        conn.close()


@planner_bp.route("/tasks/calendar", methods=["GET"])
@jwt_required()
def calendar_view():
    user_id = get_jwt_identity()
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if year and month:
                cur.execute(
                    """
                    SELECT id, title, due_date, due_time, priority, status
                    FROM tasks
                    WHERE user_id = %s AND YEAR(due_date) = %s AND MONTH(due_date) = %s
                    ORDER BY due_date ASC, due_time ASC
                    """,
                    (user_id, year, month),
                )
            else:
                cur.execute(
                    "SELECT id, title, due_date, due_time, priority, status "
                    "FROM tasks WHERE user_id = %s AND due_date IS NOT NULL "
                    "ORDER BY due_date ASC, due_time ASC",
                    (user_id,),
                )
            tasks = cur.fetchall()

        by_date = {}
        for t in tasks:
            key = t["due_date"].isoformat() if t["due_date"] else "no_date"
            by_date.setdefault(key, []).append(t)

        return jsonify({"success": True, "tasksByDate": by_date}), 200
    finally:
        conn.close()


@planner_bp.route("/tasks/<int:task_id>/reminders", methods=["POST"])
@jwt_required()
def add_reminder(task_id):
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    remind_at = (data.get("remindAt") or "").strip()

    if not remind_at:
        return jsonify({"success": False, "message": "remindAt is required"}), 400

    try:
        remind_at = datetime.fromisoformat(remind_at).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return jsonify({"success": False, "message": "Enter a valid reminder date and time"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            task = _get_owned_task(cur, task_id, user_id)
            if not task:
                return jsonify({"success": False, "message": "Task not found"}), 404

            cur.execute(
                "INSERT INTO reminders (task_id, remind_at) VALUES (%s, %s)",
                (task_id, remind_at),
            )
            conn.commit()
            reminder_id = cur.lastrowid
        return jsonify({"success": True, "reminder": {"id": reminder_id, "remindAt": remind_at}}), 201
    finally:
        conn.close()


@planner_bp.route("/reminders", methods=["GET"])
@jwt_required()
def list_reminders():
    user_id = get_jwt_identity()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.remind_at, r.is_sent, t.id AS task_id, t.title AS task_title
                FROM reminders r
                JOIN tasks t ON t.id = r.task_id
                WHERE t.user_id = %s
                ORDER BY r.remind_at ASC
                """,
                (user_id,),
            )
            reminders = cur.fetchall()
        return jsonify({"success": True, "reminders": reminders}), 200
    finally:
        conn.close()


@planner_bp.route("/reminders/<int:reminder_id>", methods=["DELETE"])
@jwt_required()
def delete_reminder(reminder_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id FROM reminders r
                JOIN tasks t ON t.id = r.task_id
                WHERE r.id = %s AND t.user_id = %s
                """,
                (reminder_id, user_id),
            )
            if not cur.fetchone():
                return jsonify({"success": False, "message": "Reminder not found"}), 404

            cur.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
            conn.commit()
        return jsonify({"success": True, "message": "Reminder deleted"}), 200
    finally:
        conn.close()