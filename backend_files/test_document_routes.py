from unittest.mock import MagicMock, patch

from app import app
from flask_jwt_extended import create_access_token


def get_auth_headers():
    with app.app_context():
        token = create_access_token(identity="1")
    return {"Authorization": f"Bearer {token}"}


def make_failed_pdf_document(file_path):
    return {
        "id": 55,
        "user_id": "1",
        "file_name": "study.pdf",
        "file_path": str(file_path),
        "file_type": "pdf",
        "extracted_text": "[Could not extract text automatically: No module named pdfplumber]",
    }


def test_document_detail_recovers_text_for_smart_notes(tmp_path):
    pdf_path = tmp_path / "study.pdf"
    pdf_path.write_bytes(b"test pdf bytes")
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = make_failed_pdf_document(pdf_path)
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.document_routes.get_db_connection", return_value=fake_conn), \
         patch("routes.document_routes.extract_text", return_value="PDF content for summary."):
        response = app.test_client().get("/api/documents/55", headers=get_auth_headers())

    assert response.status_code == 200
    assert response.get_json()["document"]["extracted_text"] == "PDF content for summary."
    update_query, update_params = fake_cursor.execute.call_args_list[1].args
    assert update_query.startswith("UPDATE documents SET extracted_text")
    assert update_params[:3] == ("PDF content for summary.", 55, "1")
    fake_conn.commit.assert_called_once()


def test_document_analysis_uses_recovered_pdf_text(tmp_path):
    pdf_path = tmp_path / "study.pdf"
    pdf_path.write_bytes(b"test pdf bytes")
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = make_failed_pdf_document(pdf_path)
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.document_routes.get_db_connection", return_value=fake_conn), \
         patch("routes.document_routes.extract_text", return_value="PDF content for summary."), \
         patch("routes.document_routes.get_ai_response", return_value="A concise summary.") as mock_ai:
        response = app.test_client().post(
            "/api/documents/55/analyze",
            headers=get_auth_headers(),
            json={"type": "summary"},
        )

    assert response.status_code == 200
    assert response.get_json()["result"] == "A concise summary."
    assert "PDF content for summary." in mock_ai.call_args.args[0][0]["content"]