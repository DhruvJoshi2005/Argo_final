from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from download_file import run_download
from insetion_in_database import run_all
from chat_logic import main as chat_main, OPENAI_API_KEY
from chat_main_optimised import main as chat_main_optimised


# ===============================
# APP INIT
# ===============================
app = FastAPI(title="ARGO Data Backend")


# ===============================
# CORS CONFIG
# ===============================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===============================
# SCHEMAS
# ===============================
class ChatRequest(BaseModel):
    question: str


class Timing(BaseModel):
    intent_ms: float
    sql_ms: float
    total_ms: float
    cache_hit: bool


class ChatResponse(BaseModel):
    answer: str
    sql: str | None = None          # 🔥 ADDED
    timing: Timing | None = None
    error: str | None = None


# ===============================
# ROUTES
# ===============================
@app.get("/")
def home():
    return {"message": "Backend is running!"}


@app.post("/refresh_data")
def refresh_data():
    download_result = run_download()
    run_all()
    return {
        "status": "success",
        "download_result": download_result,
        "message": "Data refreshed and inserted into the database"
    }


# ===============================
# UNOPTIMISED CHAT
# ===============================
@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    try:
        result = chat_main(request.question)
        # result = {
        #   "answer": str,
        #   "sql": str,
        #   "timing": {...}
        # }

        return {
            "answer": result["answer"],
            "sql": result.get("sql"),      # 🔥 ADDED
            "timing": result["timing"],
            "error": None
        }

    except Exception as e:
        return {
            "answer": "",
            "sql": None,
            "timing": None,
            "error": f"{e} | key_used: {OPENAI_API_KEY[:20]}..."
        }


# ===============================
# OPTIMISED CHAT
# ===============================
@app.post("/chat_optimised", response_model=ChatResponse)
def chat_optimised_endpoint(request: ChatRequest):
    try:
        result = chat_main_optimised(request.question)
        # result = {
        #   "answer": str,
        #   "sql": str,
        #   "timing": {...}
        # }

        return {
            "answer": result["answer"],
            "sql": result.get("sql"),      # 🔥 ADDED
            "timing": result["timing"],
            "error": None
        }

    except Exception as e:
        return {
            "answer": "",
            "sql": None,
            "timing": None,
            "error": f"{e} | key_used: {OPENAI_API_KEY[:20]}..."
        }
