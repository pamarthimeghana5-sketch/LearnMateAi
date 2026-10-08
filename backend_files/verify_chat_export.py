from pathlib import Path
import sys

root = Path("c:/Users/Admin/Desktop/Learnmateai/backend_files").resolve()
sys.path.insert(0, str(root))

from app import app
from flask_jwt_extended import create_access_token


with app.app_context():
    token = create_access_token(identity=1)
    headers = {"Authorization": f"Bearer {token}"}

    client = app.test_client()
    response = client.get("/api/chat/123/export?format=txt", headers=headers)
    print("status:", response.status_code)
    print("mimetype:", response.mimetype)
    print(response.get_data(as_text=True)[:300])
