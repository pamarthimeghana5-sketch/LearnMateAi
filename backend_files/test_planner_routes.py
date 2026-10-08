from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from app import app
from flask_jwt_extended import create_access_token


def get_auth_headers(user_id="9"):
    with app.app_context():
        token = create_access_token(identity=user_id)
    return {"Authorization": f"Bearer {token}"}


def test_my_tasks_serializes_mysql_time_values():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        {
            "id": 12,
            "title": "Read chapter",
            "due_date": date(2026, 9, 27),
            "due_time": timedelta(hours=1, minutes=30),
            "priority": "medium",
            "status": "pending",
        }
    ]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.planner_routes.get_db_connection", return_value=fake_conn):
        response = app.test_client().get("/api/tasks", headers=get_auth_headers())

    assert response.status_code == 200
    task = response.get_json()["tasks"][0]
    assert task["due_date"] == "2026-09-27"
    assert task["due_time"] == "1:30:00"


def test_add_reminder_accepts_datetime_local_value():
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {"id": 12, "user_id": "9"}
    fake_cursor.lastrowid = 44
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch("routes.planner_routes.get_db_connection", return_value=fake_conn):
        response = app.test_client().post(
            "/api/tasks/12/reminders",
            headers=get_auth_headers(),
            json={"remindAt": "2026-09-30T18:35"},
        )

    assert response.status_code == 201
    assert response.get_json()["reminder"]["remindAt"] == "2026-09-30 18:35:00"
    assert fake_cursor.execute.call_args_list[1].args == (
        "INSERT INTO reminders (task_id, remind_at) VALUES (%s, %s)",
        (12, "2026-09-30 18:35:00"),
    )


def test_add_reminder_rejects_invalid_datetime():
    response = app.test_client().post(
        "/api/tasks/12/reminders",
        headers=get_auth_headers(),
        json={"remindAt": "not-a-date"},
    )

    assert response.status_code == 400