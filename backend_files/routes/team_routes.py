from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

from db import get_db_connection

team_bp = Blueprint("team", __name__, url_prefix="/api/teams")


def _is_member(cur, team_id, user_id):
    cur.execute(
        "SELECT * FROM team_members WHERE team_id = %s AND user_id = %s",
        (team_id, user_id),
    )
    return cur.fetchone()


@team_bp.route("", methods=["POST"])
@jwt_required()
def create_team():
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()

    if not name:
        return jsonify({"success": False, "message": "Team name is required"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO teams (name, created_by) VALUES (%s, %s)", (name, user_id))
            team_id = cur.lastrowid
            cur.execute(
                "INSERT INTO team_members (team_id, user_id, role) VALUES (%s, %s, 'admin')",
                (team_id, user_id),
            )
            cur.execute(
                "INSERT INTO project_boards (team_id, name) VALUES (%s, 'Main Board')",
                (team_id,),
            )
            conn.commit()
        return jsonify({"success": True, "team": {"id": team_id, "name": name}}), 201
    finally:
        conn.close()


@team_bp.route("", methods=["GET"])
@jwt_required()
def my_teams():
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id, t.name, t.created_at, tm.role,
                       (SELECT COUNT(*) FROM team_members WHERE team_id = t.id) AS member_count
                FROM teams t
                JOIN team_members tm ON tm.team_id = t.id
                WHERE tm.user_id = %s
                ORDER BY t.created_at DESC
                """,
                (user_id,),
            )
            teams = cur.fetchall()
        return jsonify({"success": True, "teams": teams}), 200
    finally:
        conn.close()


@team_bp.route("/<int:team_id>", methods=["DELETE"])
@jwt_required()
def delete_team(team_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, created_by FROM teams WHERE id = %s FOR UPDATE", (team_id,))
            team = cur.fetchone()
            if not team:
                return jsonify({"success": False, "message": "Team not found"}), 404
            if str(team["created_by"]) != str(user_id):
                return jsonify({"success": False, "message": "Only the team creator can delete this team"}), 403

            cur.execute(
                "DELETE bt FROM board_tasks bt "
                "JOIN project_boards pb ON pb.id = bt.board_id WHERE pb.team_id = %s",
                (team_id,),
            )
            cur.execute("DELETE FROM project_boards WHERE team_id = %s", (team_id,))
            cur.execute("DELETE FROM team_invites WHERE team_id = %s", (team_id,))
            cur.execute("DELETE FROM team_chat_messages WHERE team_id = %s", (team_id,))
            cur.execute("DELETE FROM team_members WHERE team_id = %s", (team_id,))
            cur.execute("DELETE FROM teams WHERE id = %s", (team_id,))
            conn.commit()
        return jsonify({"success": True, "message": "Team deleted"}), 200
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@team_bp.route("/<int:team_id>/invite", methods=["POST"])
@jwt_required()
def invite_member(team_id):
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    invited_email = (data.get("email") or "").strip().lower()

    if not invited_email:
        return jsonify({"success": False, "message": "Email is required"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if not _is_member(cur, team_id, user_id):
                return jsonify({"success": False, "message": "You are not part of this team"}), 403

            cur.execute(
                "INSERT INTO team_invites (team_id, invited_email, invited_by) VALUES (%s, %s, %s)",
                (team_id, invited_email, user_id),
            )
            conn.commit()
            invite_id = cur.lastrowid
        return jsonify({"success": True, "invite": {"id": invite_id, "email": invited_email}}), 201
    finally:
        conn.close()


@team_bp.route("/invites/pending", methods=["GET"])
@jwt_required()
def pending_invites():
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ti.id, ti.team_id, t.name AS team_name,
                       inviter.full_name AS invited_by_name
                FROM team_invites ti
                JOIN users invitee ON LOWER(invitee.email) = LOWER(ti.invited_email)
                JOIN teams t ON t.id = ti.team_id
                LEFT JOIN users inviter ON inviter.id = ti.invited_by
                WHERE invitee.id = %s AND ti.status = 'pending'
                ORDER BY ti.id DESC
                """,
                (user_id,),
            )
            invites = cur.fetchall()
        return jsonify({"success": True, "invites": invites}), 200
    finally:
        conn.close()


@team_bp.route("/invites/<int:invite_id>/accept", methods=["POST"])
@jwt_required()
def accept_invite(invite_id):
    user_id = get_jwt_identity()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ti.* FROM team_invites ti
                JOIN users invitee ON LOWER(invitee.email) = LOWER(ti.invited_email)
                WHERE ti.id = %s AND invitee.id = %s AND ti.status = 'pending'
                """,
                (invite_id, user_id),
            )
            invite = cur.fetchone()
            if not invite:
                return jsonify({"success": False, "message": "Invite not found or already used"}), 404

            cur.execute(
                "INSERT IGNORE INTO team_members (team_id, user_id, role) VALUES (%s, %s, 'member')",
                (invite["team_id"], user_id),
            )
            cur.execute("UPDATE team_invites SET status = 'accepted' WHERE id = %s", (invite_id,))
            conn.commit()
        return jsonify({"success": True, "message": "Joined the team"}), 200
    finally:
        conn.close()


@team_bp.route("/invites/<int:invite_id>/reject", methods=["POST"])
@jwt_required()
def reject_invite(invite_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ti.id FROM team_invites ti
                JOIN users invitee ON LOWER(invitee.email) = LOWER(ti.invited_email)
                WHERE ti.id = %s AND invitee.id = %s AND ti.status = 'pending'
                """,
                (invite_id, user_id),
            )
            if not cur.fetchone():
                return jsonify({"success": False, "message": "Invite not found or already used"}), 404

            cur.execute("DELETE FROM team_invites WHERE id = %s AND status = 'pending'", (invite_id,))
            conn.commit()
        return jsonify({"success": True, "message": "Invite rejected"}), 200
    finally:
        conn.close()


@team_bp.route("/<int:team_id>/roster", methods=["GET"])
@jwt_required()
def team_roster(team_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if not _is_member(cur, team_id, user_id):
                return jsonify({"success": False, "message": "You are not part of this team"}), 403

            cur.execute(
                """
                SELECT u.id, u.full_name, u.email, u.profile_pic, tm.role, tm.joined_at
                FROM team_members tm
                JOIN users u ON u.id = tm.user_id
                WHERE tm.team_id = %s
                ORDER BY tm.role = 'admin' DESC, tm.joined_at ASC
                """,
                (team_id,),
            )
            roster = cur.fetchall()
        return jsonify({"success": True, "roster": roster}), 200
    finally:
        conn.close()


@team_bp.route("/<int:team_id>/board", methods=["GET"])
@jwt_required()
def get_board(team_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if not _is_member(cur, team_id, user_id):
                return jsonify({"success": False, "message": "You are not part of this team"}), 403

            cur.execute("SELECT id, name FROM project_boards WHERE team_id = %s", (team_id,))
            board = cur.fetchone()
            if not board:
                return jsonify({"success": False, "message": "Board not found"}), 404

            cur.execute(
                """
                SELECT bt.id, bt.title, bt.description, bt.status, bt.created_at,
                       u.id AS assigned_to_id, u.full_name AS assigned_to_name
                FROM board_tasks bt
                LEFT JOIN users u ON u.id = bt.assigned_to
                WHERE bt.board_id = %s
                ORDER BY bt.created_at ASC
                """,
                (board["id"],),
            )
            tasks = cur.fetchall()

        columns = {"todo": [], "in_progress": [], "done": []}
        for t in tasks:
            columns.setdefault(t["status"], []).append(t)

        return jsonify({"success": True, "board": {"id": board["id"], "name": board["name"]}, "columns": columns}), 200
    finally:
        conn.close()


@team_bp.route("/<int:team_id>/board/tasks", methods=["POST"])
@jwt_required()
def add_board_task(team_id):
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    description = data.get("description", "")
    assigned_to = data.get("assignedTo")

    if not title:
        return jsonify({"success": False, "message": "Task title is required"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if not _is_member(cur, team_id, user_id):
                return jsonify({"success": False, "message": "You are not part of this team"}), 403

            cur.execute("SELECT id FROM project_boards WHERE team_id = %s", (team_id,))
            board = cur.fetchone()
            if not board:
                return jsonify({"success": False, "message": "Board not found"}), 404

            cur.execute(
                "INSERT INTO board_tasks (board_id, title, description, assigned_to) VALUES (%s, %s, %s, %s)",
                (board["id"], title, description, assigned_to),
            )
            conn.commit()
            task_id = cur.lastrowid
        return jsonify({"success": True, "task": {"id": task_id, "title": title, "status": "todo"}}), 201
    finally:
        conn.close()


@team_bp.route("/board/tasks/<int:task_id>", methods=["PUT"])
@jwt_required()
def update_board_task(task_id):
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT bt.*, pb.team_id FROM board_tasks bt
                JOIN project_boards pb ON pb.id = bt.board_id
                WHERE bt.id = %s
                """,
                (task_id,),
            )
            task = cur.fetchone()
            if not task:
                return jsonify({"success": False, "message": "Task not found"}), 404
            if not _is_member(cur, task["team_id"], user_id):
                return jsonify({"success": False, "message": "You are not part of this team"}), 403

            cur.execute(
                "UPDATE board_tasks SET title = %s, description = %s, status = %s, assigned_to = %s WHERE id = %s",
                (
                    data.get("title", task["title"]),
                    data.get("description", task["description"]),
                    data.get("status", task["status"]),
                    data.get("assignedTo", task["assigned_to"]),
                    task_id,
                ),
            )
            conn.commit()
        return jsonify({"success": True, "message": "Board task updated"}), 200
    finally:
        conn.close()


@team_bp.route("/board/tasks/<int:task_id>", methods=["DELETE"])
@jwt_required()
def delete_board_task(task_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT bt.id, pb.team_id FROM board_tasks bt
                JOIN project_boards pb ON pb.id = bt.board_id
                WHERE bt.id = %s
                """,
                (task_id,),
            )
            task = cur.fetchone()
            if not task:
                return jsonify({"success": False, "message": "Task not found"}), 404
            if not _is_member(cur, task["team_id"], user_id):
                return jsonify({"success": False, "message": "You are not part of this team"}), 403

            cur.execute("DELETE FROM board_tasks WHERE id = %s", (task_id,))
            conn.commit()
        return jsonify({"success": True, "message": "Board task deleted"}), 200
    finally:
        conn.close()


@team_bp.route("/<int:team_id>/chat", methods=["GET"])
@jwt_required()
def get_team_chat(team_id):
    user_id = get_jwt_identity()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if not _is_member(cur, team_id, user_id):
                return jsonify({"success": False, "message": "You are not part of this team"}), 403

            cur.execute(
                """
                SELECT tcm.id, tcm.message, tcm.sent_at, u.id AS user_id, u.full_name
                FROM team_chat_messages tcm
                JOIN users u ON u.id = tcm.user_id
                WHERE tcm.team_id = %s
                ORDER BY tcm.sent_at ASC
                """,
                (team_id,),
            )
            messages = cur.fetchall()
        return jsonify({"success": True, "messages": messages}), 200
    finally:
        conn.close()


@team_bp.route("/<int:team_id>/chat", methods=["POST"])
@jwt_required()
def send_team_message(team_id):
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"success": False, "message": "Message cannot be empty"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if not _is_member(cur, team_id, user_id):
                return jsonify({"success": False, "message": "You are not part of this team"}), 403

            cur.execute(
                "INSERT INTO team_chat_messages (team_id, user_id, message) VALUES (%s, %s, %s)",
                (team_id, user_id, message),
            )
            conn.commit()
            msg_id = cur.lastrowid
        return jsonify({"success": True, "message_id": msg_id}), 201
    finally:
        conn.close()