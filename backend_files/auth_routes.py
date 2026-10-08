from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_bcrypt import Bcrypt

from config import Config
from routes.auth_routes import auth_bp, is_token_blacklisted
from routes.chat_routes import chat_bp
from routes.document_routes import doc_bp
from routes.notes_routes import notes_bp
from routes.planner_routes import planner_bp
from routes.team_routes import team_bp
from routes.quiz_routes import quiz_bp

app = Flask(__name__)
app.config["JWT_SECRET_KEY"] = Config.JWT_SECRET_KEY
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = Config.JWT_ACCESS_TOKEN_EXPIRES
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_CONTENT_LENGTH

CORS(app)
bcrypt = Bcrypt(app)
jwt = JWTManager(app)


@jwt.token_in_blocklist_loader
def check_if_token_revoked(jwt_header, jwt_payload):
    return is_token_blacklisted(jwt_payload)


@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    return jsonify({"success": False, "message": "Session expired, please login again"}), 401


@jwt.invalid_token_loader
def invalid_token_callback(reason):
    return jsonify({"success": False, "message": "Invalid token"}), 401


@jwt.unauthorized_loader
def missing_token_callback(reason):
    return jsonify({"success": False, "message": "Login required"}), 401


app.register_blueprint(auth_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(doc_bp)
app.register_blueprint(notes_bp)
app.register_blueprint(planner_bp)
app.register_blueprint(team_bp)
app.register_blueprint(quiz_bp)


@app.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({"status": "LearnMate AI backend is running"}), 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)