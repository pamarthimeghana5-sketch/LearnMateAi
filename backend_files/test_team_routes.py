from unittest.mock import MagicMock, patch

from app import app
from flask_jwt_extended import create_access_token


def get_auth_headers(user_id="9"):
    with app.app_context():
        token = create_access_token(identity=user_id)
    return {"Authorization": f"Bearer {token}"}


def test_pending_invites_are_scoped_to_logged_in_user():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        {"id": 31, "team_id": 5, "team_name": "Study Group", "invited_by_name": "Owner"}
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.team_routes.get_db_connection", return_value=fake_conn):
        response = app.test_client().get("/api/teams/invites/pending", headers=get_auth_headers())

    assert response.status_code == 200
    assert response.get_json()["invites"][0]["team_name"] == "Study Group"
    query, params = fake_cursor.execute.call_args.args
    assert "LOWER(invitee.email) = LOWER(ti.invited_email)" in query
    assert params == ("9",)


def test_accept_invite_requires_invited_account_email():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = None
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.team_routes.get_db_connection", return_value=fake_conn):
        response = app.test_client().post(
            "/api/teams/invites/31/accept", headers=get_auth_headers("10")
        )

    assert response.status_code == 404
    assert "invitee.id = %s" in fake_cursor.execute.call_args.args[0]
    assert fake_cursor.execute.call_count == 1


def test_accept_invite_adds_member_and_marks_invite_accepted():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"id": 31, "team_id": 5}
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.team_routes.get_db_connection", return_value=fake_conn):
        response = app.test_client().post(
            "/api/teams/invites/31/accept", headers=get_auth_headers()
        )

    assert response.status_code == 200
    assert fake_cursor.execute.call_args_list[1].args == (
        "INSERT IGNORE INTO team_members (team_id, user_id, role) VALUES (%s, %s, 'member')",
        (5, "9"),
    )
    assert fake_cursor.execute.call_args_list[2].args == (
        "UPDATE team_invites SET status = 'accepted' WHERE id = %s",
        (31,),
    )
    fake_conn.commit.assert_called_once()


def test_reject_invite_removes_only_invitee_pending_invite():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"id": 31}
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.team_routes.get_db_connection", return_value=fake_conn):
        response = app.test_client().post(
            "/api/teams/invites/31/reject", headers=get_auth_headers()
        )

    assert response.status_code == 200
    assert fake_cursor.execute.call_args_list[0].args[1] == (31, "9")
    assert fake_cursor.execute.call_args_list[1].args == (
        "DELETE FROM team_invites WHERE id = %s AND status = 'pending'",
        (31,),
    )
    fake_conn.commit.assert_called_once()


def test_team_chat_history_is_loaded_by_team_for_member():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"team_id": 5, "user_id": "9"}
    fake_cursor.fetchall.return_value = [
        {"id": 82, "message": "Shared update", "sent_at": "2026-09-27", "user_id": 10, "full_name": "Member"}
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.team_routes.get_db_connection", return_value=fake_conn):
        response = app.test_client().get("/api/teams/5/chat", headers=get_auth_headers())

    assert response.status_code == 200
    assert response.get_json()["messages"][0]["message"] == "Shared update"
    chat_query, params = fake_cursor.execute.call_args_list[1].args
    assert "WHERE tcm.team_id = %s" in chat_query
    assert params == (5,)


def test_delete_team_removes_team_data_for_creator():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"id": 5, "created_by": "9"}
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.team_routes.get_db_connection", return_value=fake_conn):
        response = app.test_client().delete("/api/teams/5", headers=get_auth_headers())

    assert response.status_code == 200
    statements = [call.args[0] for call in fake_cursor.execute.call_args_list]
    assert any("DELETE bt FROM board_tasks" in statement for statement in statements)
    assert any("DELETE FROM project_boards" in statement for statement in statements)
    assert any("DELETE FROM team_invites" in statement for statement in statements)
    assert any("DELETE FROM team_chat_messages" in statement for statement in statements)
    assert any("DELETE FROM team_members" in statement for statement in statements)
    assert statements[-1] == "DELETE FROM teams WHERE id = %s"
    fake_conn.commit.assert_called_once()


def test_delete_team_is_forbidden_for_non_creator():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"id": 5, "created_by": "22"}
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.team_routes.get_db_connection", return_value=fake_conn):
        response = app.test_client().delete("/api/teams/5", headers=get_auth_headers())

    assert response.status_code == 403
    assert fake_cursor.execute.call_count == 1
    fake_conn.commit.assert_not_called()