import sys
import base64
from unittest.mock import MagicMock, patch

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from app import app
from flask_jwt_extended import create_access_token


def get_auth_headers():
    with app.app_context():
        token = create_access_token(identity="1")
    return {"Authorization": f"Bearer {token}"}


def test_chat_reply_normalizes_compressed_list_spacing():
    from routes.chat_routes import _format_chat_reply

    formatted = _format_chat_reply("1. First point 2. Second point 3. Third point")

    assert formatted == "1. First point\n\n2. Second point\n\n3. Third point"


def test_chat_passes_uploaded_image_to_gemini():
    from routes.chat_routes import generate_chat_reply

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value.text = "It shows a diagram."
    image_data_url = "data:image/png;base64," + base64.b64encode(b"test-image").decode()

    with patch("routes.chat_routes.client", fake_client):
        reply, model = generate_chat_reply("Explain this image", image_data_url=image_data_url)

    assert reply == "It shows a diagram."
    assert model
    contents = fake_client.models.generate_content.call_args.kwargs["contents"]
    assert len(contents) == 2
    assert contents[1].inline_data.mime_type == "image/png"


def test_chat_export_returns_text():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"id": 123}
    fake_cursor.fetchall.return_value = [
        {"role": "user", "content": "Hello there"},
        {"role": "assistant", "content": "Hi! How can I help?"},
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.chat_routes.get_db_connection", return_value=fake_conn):
        client = app.test_client()
        response = client.get("/api/chat/123/export?format=txt", headers=get_auth_headers())

        assert response.status_code == 200
        assert response.mimetype == "text/plain"
        body = response.get_data(as_text=True)
        assert "User:" in body
        assert "Assistant:" in body
        assert "Hello there" in body
        assert "Hi! How can I help?" in body


def test_chat_uses_uploaded_document_context():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"id": 123}
    fake_cursor.fetchall.return_value = [
        {"id": 55, "file_name": "notes.txt", "extracted_text": "Photosynthesis is the process by which plants make food."}
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.chat_routes.get_db_connection", return_value=fake_conn), \
         patch("routes.chat_routes.client", object()), \
         patch("routes.chat_routes.generate_chat_reply", return_value=("Plants make food using sunlight.", "gemini-2.5-flash")) as mock_reply:
        client = app.test_client()
        response = client.post(
            "/api/chat/123/message",
            headers=get_auth_headers(),
            json={"message": "What is photosynthesis?", "document_ids": [55]},
        )

        assert response.status_code == 200
        document_query, document_params = fake_cursor.execute.call_args_list[1].args
        assert "id IN" in document_query
        assert document_params[-1] == 55
        prompt = mock_reply.call_args.args[0]
        assert "Photosynthesis is the process by which plants make food." in prompt
        assert "What is photosynthesis?" in prompt
        assert "Act as a helpful student-friendly AI tutor" in prompt
        assert "NEVER write a long paragraph" in prompt


def test_chat_recovers_document_text_from_stored_file(tmp_path):
    from routes.chat_routes import _get_uploaded_document_context

    file_path = tmp_path / "source.txt"
    file_path.write_text("Stored document text", encoding="utf-8")
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [{
        "id": 55,
        "file_name": "source.txt",
        "extracted_text": "",
        "file_path": str(file_path),
        "file_type": "txt",
    }]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.chat_routes.get_db_connection", return_value=fake_conn):
        context = _get_uploaded_document_context("1", [55])

    assert "Stored document text" in context
    update_query, update_params = fake_cursor.execute.call_args_list[1].args
    assert update_query.startswith("UPDATE documents SET extracted_text")
    assert update_params[:3] == ("Stored document text", 55, "1")


def test_chat_falls_back_to_general_ai_when_document_has_no_answer():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"id": 123}
    fake_cursor.fetchall.return_value = [
        {"id": 55, "file_name": "notes.txt", "extracted_text": "The document covers plants."}
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.chat_routes.get_db_connection", return_value=fake_conn), \
         patch("routes.chat_routes.client", object()), \
         patch(
             "routes.chat_routes.generate_chat_reply",
             side_effect=[("DOCUMENT_ANSWER_NOT_FOUND", "gemini-2.5-flash"), ("General answer.", "gemini-2.5-flash")],
         ) as mock_reply:
        client = app.test_client()
        response = client.post(
            "/api/chat/123/message",
            headers=get_auth_headers(),
            json={"message": "What is gravity?", "document_ids": [55]},
        )

    assert response.status_code == 200
    assert response.get_json()["reply"] == "General answer."
    assert mock_reply.call_count == 2
    assert "The document covers plants." in mock_reply.call_args_list[0].args[0]
    assert mock_reply.call_args_list[1].args[0] == "What is gravity?"


def test_chat_without_selected_documents_uses_plain_message():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"id": 123}
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.chat_routes.get_db_connection", return_value=fake_conn), \
         patch("routes.chat_routes.client", object()), \
         patch("routes.chat_routes.generate_chat_reply", return_value=("A normal chat answer.", "gemini-2.5-flash")) as mock_reply:
        client = app.test_client()
        response = client.post(
            "/api/chat/123/message",
            headers=get_auth_headers(),
            json={"message": "Explain gravity."},
        )

        assert response.status_code == 200
        assert mock_reply.call_args.args[0] == "Explain gravity."
