"""Central configuration. All values come from environment variables
(no secrets are ever committed)."""
import os


def get_settings():
    class S:
        pass

    s = S()
    s.llm_provider = os.getenv("LLM_PROVIDER", "groq").lower()
    # Groq (free tier, very fast) - https://console.groq.com/keys
    s.groq_api_key = os.getenv("GROQ_API_KEY", "")
    s.groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    # Google Gemini (free tier) - https://aistudio.google.com
    s.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    s.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    return s