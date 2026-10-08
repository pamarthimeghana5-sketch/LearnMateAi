import requests
from config import Config


def get_ai_response(conversation_history):
    """
    conversation_history: list of {"role": "user"|"assistant", "content": "..."}
    Returns the AI's reply text as a string.
    Raises RuntimeError if the AI could not produce a response.
    """
    if not Config.AI_API_KEY:
        raise RuntimeError(
            "AI service is not configured yet. Add AI_API_KEY in your .env file."
        )

    ordered_models = [
        Config.AI_MODEL,
        "gemini-3.5-flash-lite",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
    ]
    seen = set()
    ordered_models = [m for m in ordered_models if m and not (m in seen or seen.add(m))]

    contents = []
    for msg in conversation_history:
        role = "model" if msg.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})

    payload = {
        "contents": contents,
        "generationConfig": {"temperature": 0.7},
    }

    last_error = None
    for model in ordered_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={Config.AI_API_KEY}"
        try:
            response = requests.post(url, json=payload, timeout=60)

            if response.status_code == 400:
                error = response.json().get("error", {})
                error_text = str(error).lower()
                if "api_key_invalid" in error_text or "api key not valid" in error_text:
                    raise RuntimeError(
                        "Gemini rejected the configured API key. Create a valid key in "
                        "Google AI Studio and set AI_API_KEY in backend_files/.env."
                    )

            if response.status_code in (404, 429, 503):
                last_error = f"{response.status_code} MODEL_UNAVAILABLE"
                continue  # try next model

            response.raise_for_status()
            data = response.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()

        except requests.exceptions.RequestException as e:
            last_error = str(e)
            continue
        except (KeyError, IndexError):
            last_error = "Unexpected response shape from Gemini"
            continue

    raise RuntimeError(
         "The AI service is currently busy or unavailable ")