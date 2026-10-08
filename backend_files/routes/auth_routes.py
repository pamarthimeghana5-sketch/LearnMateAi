from flask import Blueprint, request, jsonify
from flask_jwt_extended import (
    create_access_token,
    jwt_required,
    get_jwt_identity,
    get_jwt,
)
from db import get_db_connection

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# In-memory blacklist for logged-out tokens (fine for small/college projects;
# swap for a DB table or Redis if you need it to survive a server restart).
BLACKLISTED_TOKENS = set()


def is_token_blacklisted(jwt_payload):
    return jwt_payload["jti"] in BLACKLISTED_TOKENS


# ------------------------------------------------------------------
# SIGNUP  ->  matches signupName / signupEmail / signupPassword fields
# ------------------------------------------------------------------
@auth_bp.route("/signup", methods=["POST"])
def signup():
    from app import bcrypt  # imported here to avoid circular import

    data = request.get_json(silent=True) or {}
    full_name = (data.get("fullName") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not full_name or not email or not password:
        return jsonify({"success": False, "message": "Please fill in all fields"}), 400

    if len(password) < 6:
        return jsonify({"success": False, "message": "Password must be at least 6 characters"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                return jsonify({"success": False, "message": "Email already registered. Please login."}), 409

            password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
            cur.execute(
                """
                INSERT INTO users (full_name, first_name, last_name, email, password_hash, bio)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (full_name, "", "", email, password_hash, "Passionate learner & tech enthusiast"),
            )
            conn.commit()
        return jsonify({"success": True, "message": "Account created successfully! Please login."}), 201
    finally:
        conn.close()


# ------------------------------------------------------------------
# LOGIN  ->  matches loginEmail / loginPassword fields
# ------------------------------------------------------------------
@auth_bp.route("/login", methods=["POST"])
def login():
    from app import bcrypt

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"success": False, "message": "Please enter both email and password"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cur.fetchone()

        if not user:
            return jsonify({"success": False, "message": "Email not found. Please create an account."}), 404

        if not bcrypt.check_password_hash(user["password_hash"], password):
            return jsonify({"success": False, "message": "Incorrect password. Please try again."}), 401

        token = create_access_token(identity=str(user["id"]))

        return jsonify({
            "success": True,
            "token": token,
            "user": {
                "id": user["id"],
                "fullName": user["full_name"],
                "firstName": user["first_name"],
                "lastName": user["last_name"],
                "email": user["email"],
                "bio": user["bio"],
                "profilePic": user["profile_pic"],
                "language": user["language"],
                "theme": user["theme"],
            }
        }), 200
    finally:
        conn.close()


# ------------------------------------------------------------------
# GET PROFILE  ->  loadProfileData() on the frontend
# ------------------------------------------------------------------
@auth_bp.route("/profile", methods=["GET"])
@jwt_required()
def get_profile():
    user_id = get_jwt_identity()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, full_name, first_name, last_name, email, bio, profile_pic, language, theme "
                "FROM users WHERE id = %s",
                (user_id,),
            )
            user = cur.fetchone()

        if not user:
            return jsonify({"success": False, "message": "User not found"}), 404

        return jsonify({
            "success": True,
            "user": {
                "id": user["id"],
                "fullName": user["full_name"],
                "firstName": user["first_name"],
                "lastName": user["last_name"],
                "email": user["email"],
                "bio": user["bio"],
                "profilePic": user["profile_pic"],
                "language": user["language"],
                "theme": user["theme"],
            }
        }), 200
    finally:
        conn.close()


# ------------------------------------------------------------------
# UPDATE PROFILE  ->  editProfileForm submit on the frontend
# ------------------------------------------------------------------
@auth_bp.route("/profile", methods=["PUT"])
@jwt_required()
def update_profile():
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}

    first_name = data.get("firstName", "")
    last_name = data.get("lastName", "")
    full_name = data.get("fullName") or (first_name + " " + last_name).strip() or "User"
    bio = data.get("bio", "Passionate learner & tech enthusiast")
    profile_pic = data.get("profilePic")  # base64 string, optional

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if profile_pic is not None:
                cur.execute(
                    """
                    UPDATE users
                    SET full_name = %s, first_name = %s, last_name = %s, bio = %s, profile_pic = %s
                    WHERE id = %s
                    """,
                    (full_name, first_name, last_name, bio, profile_pic, user_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE users
                    SET full_name = %s, first_name = %s, last_name = %s, bio = %s
                    WHERE id = %s
                    """,
                    (full_name, first_name, last_name, bio, user_id),
                )
            conn.commit()
        return jsonify({"success": True, "message": "Profile updated successfully!"}), 200
    finally:
        conn.close()


# ------------------------------------------------------------------
# UPDATE THEME / LANGUAGE  ->  themeToggle + language switcher
# ------------------------------------------------------------------
@auth_bp.route("/preferences", methods=["PUT"])
@jwt_required()
def update_preferences():
    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    theme = data.get("theme")        # 'light' | 'dark'
    language = data.get("language")  # 'en' | 'te' | 'hi'

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if theme:
                cur.execute("UPDATE users SET theme = %s WHERE id = %s", (theme, user_id))
            if language:
                cur.execute("UPDATE users SET language = %s WHERE id = %s", (language, user_id))
            conn.commit()
        return jsonify({"success": True}), 200
    finally:
        conn.close()


# ------------------------------------------------------------------
# LOGOUT  ->  handleLogout() on the frontend
# ------------------------------------------------------------------
@auth_bp.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    jti = get_jwt()["jti"]
    BLACKLISTED_TOKENS.add(jti)
    return jsonify({"success": True, "message": "Logged out"}), 200


# ------------------------------------------------------------------
# CHANGE PASSWORD  ->  Settings -> "Change Password"
# ------------------------------------------------------------------
@auth_bp.route("/change-password", methods=["PUT"])
@jwt_required()
def change_password():
    from app import bcrypt

    user_id = get_jwt_identity()
    data = request.get_json(silent=True) or {}
    old_password = data.get("oldPassword") or ""
    new_password = data.get("newPassword") or ""

    if not old_password or not new_password:
        return jsonify({"success": False, "message": "Please fill in both fields"}), 400

    if len(new_password) < 6:
        return jsonify({"success": False, "message": "New password must be at least 6 characters"}), 400

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()

            if not user or not bcrypt.check_password_hash(user["password_hash"], old_password):
                return jsonify({"success": False, "message": "Current password is incorrect"}), 401

            new_hash = bcrypt.generate_password_hash(new_password).decode("utf-8")
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, user_id))
            conn.commit()

        return jsonify({"success": True, "message": "Password changed successfully!"}), 200
    finally:
        conn.close()
