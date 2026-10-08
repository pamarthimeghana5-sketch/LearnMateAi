import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

API_KEY = os.getenv("AI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

if not API_KEY:
    print("No API key found in environment. Check your .env file.")
    exit(1)

client = genai.Client(api_key=API_KEY)

print("Models available to this API key:\n")
for model in client.models.list():
    # Only show models that support generateContent (chat)
    if "generateContent" in getattr(model, "supported_actions", []) or True:
        print(f"- {model.name}")