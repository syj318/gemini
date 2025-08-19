import os
import io
import hashlib
from datetime import datetime
from typing import Dict, List, Tuple

import streamlit as st
import pandas as pd
import PyPDF2
import google.generativeai as genai

from sqlalchemy import (
    create_engine, String, Integer, Text, DateTime, func, select, Column, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import over
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# ======================= 환경설정 =======================
load_dotenv()

class Config:
    PAGE_TITLE = "Gemini Chatbot"
    MODEL_NAME = "gemini-1.5-flash-8b"
    API_KEY_PATH = os.getenv("GEMINI_API_PATH", "C:/Users/User/Desktop/gemini/api.txt")
    SUPPORTED_EXTENSIONS = ["pdf", "csv", "txt"]
    MAX_FILE_SIZE_MB = 10
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///chat_history.db")
    TIMEZONE = os.getenv("TIMEZONE", "Asia/Seoul")
    FAQ_LIMIT = int(os.getenv("FAQ_LIMIT", "5"))

# ======================= DB =======================
Base = declarative_base()

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    user_text = Column(Text, nullable=False)
    bot_text  = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, index=True, default=func.now())

# 참고: mysql_length는 SQLite에서는 무시됩니다. 원 코드 호환 유지
Index("ix_messages_user_text", Message.user_text, mysql_length=255)

class DB:
    _engine = create_engine(
        Config.DATABASE_URL, pool_size=5, max_overflow=10, pool_pre_ping=True, future=True
    )
    _Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)

    @staticmethod
    def init():
        Base.metadata.create_all(DB._engine)

    @staticmethod
    def now_local_naive() -> datetime:
        return datetime.now(ZoneInfo(Config.TIMEZONE)).replace(tzinfo=None)

    @staticmethod
    def save_qa(session_id: str, user_text: str, bot_text: str) -> None:
        with DB._Session() as s:
            s.add(Message(
                session_id=session_id,
                user_text=user_text,
                bot_text=bot_text,
                created_at=DB.now_local_naive(),
            ))
            s.commit()

    @staticmethod
    def get_top_faqs(limit: int = 5) -> List[Tuple[str, str, int]]:
        qnorm = func.lower(func.trim(Message.user_text)).label("qnorm")
        rn = over(func.row_number(), partition_by=qnorm, order_by=Message.created_at.desc()).label("rn")

        with DB._Session() as s:
            sub = select(
                qnorm,
                Message.user_text.label("raw_q"),
                Message.bot_text.label("a"),
                Message.created_at,
                rn
            ).subquery()

            latest = select(sub.c.qnorm, sub.c.raw_q, sub.c.a).where(sub.c.rn == 1).subquery()
            counts = (
                select(
                    qnorm.label("qnorm"),
                    func.count().label("cnt"),
                    func.max(Message.created_at).label("last_at")
                )
                .group_by(qnorm)
                .subquery()
            )
            q = (
                select(latest.c.raw_q, latest.c.a, counts.c.cnt)
                .join(counts, counts.c.qnorm == latest.c.qnorm)
                .order_by(counts.c.cnt.desc(), counts.c.last_at.desc())
                .limit(limit)
            )
            rows = s.execute(q).all()

        return [(r[0], r[1], r[2]) for r in rows]

    @staticmethod
    def get_recent_messages(limit: int = 10) -> List[Message]:
        with DB._Session() as s:
            return s.query(Message).order_by(Message.created_at.desc()).limit(limit).all()

# ======================= 파일 처리 =======================
class FileProcessor:
    _ENCODINGS = ["utf-8", "cp949", "euc-kr", "latin-1"]

    @staticmethod
    def md5(content: bytes) -> str:
        return hashlib.md5(content).hexdigest()

    @staticmethod
    def _read_text_with_encodings(file) -> str:
        for enc in FileProcessor._ENCODINGS:
            try:
                file.seek(0)
                return file.read().decode(enc)
            except UnicodeDecodeError:
                continue
        return "텍스트 파일을 읽을 수 없습니다."

    @staticmethod
    def process_pdf(file) -> str:
        try:
            pdf_reader = PyPDF2.PdfReader(file)
            parts = []
            for i, page in enumerate(pdf_reader.pages, 1):
                text = (page.extract_text() or "").strip()
                if text:
                    parts.append(f"[Page {i}]\n{text}")
            return "\n\n".join(parts) if parts else "PDF에서 텍스트를 추출할 수 없습니다."
        except Exception as e:
            return f"PDF 처리 오류: {e}"

    @staticmethod
    def process_csv(file) -> str:
        try:
            df = None
            for enc in FileProcessor._ENCODINGS:
                try:
                    file.seek(0)
                    df = pd.read_csv(file, encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            if df is None:
                return "CSV 파일 인코딩을 읽을 수 없습니다."

            summary = (
                f"CSV 파일 정보:\n"
                f"- 행 수: {len(df)}\n"
                f"- 열 수: {len(df.columns)}\n"
                f"- 컬럼명: {', '.join(map(str, df.columns))}\n\n"
            )
            content = df.to_string() if len(df) <= 10 else f"{df.head().to_string()}\n...\n{df.tail().to_string()}"
            return summary + content
        except Exception as e:
            return f"CSV 처리 오류: {e}"

    @staticmethod
    def process_txt(file) -> str:
        return FileProcessor._read_text_with_encodings(file)

    @classmethod
    def process_file(cls, uploaded_file) -> Tuple[str, str]:
        name = uploaded_file.name
        ext = name.lower().split(".")[-1]
        size_mb = uploaded_file.size / (1024 * 1024)

        if size_mb > Config.MAX_FILE_SIZE_MB:
            return name, f"파일 크기가 {Config.MAX_FILE_SIZE_MB}MB를 초과합니다."

        processors = {"pdf": cls.process_pdf, "csv": cls.process_csv, "txt": cls.process_txt}
        if ext not in processors:
            return name, f"지원하지 않는 파일 형식입니다: {ext}"

        content = processors[ext](uploaded_file)
        return name, content

# ======================= UI =======================
class UI:
    @staticmethod
    def css():
        st.markdown("""
        <style>
        .main-container { display:flex; flex-direction:column; height:calc(100vh - 100px); }
        .chat-area { flex:1; overflow-y:auto; padding-bottom:20px; }
        .fixed-bottom { position:fixed; bottom:120px; left:20px; right:20px; background:transparent; padding:10px; z-index:100; }
        .chat-container { display:flex; flex-direction:column; margin:5px 0; }
        .user-bubble { background:#9ED2FF; color:#000; padding:12px 18px; border-radius:16px; margin:5px; max-width:60%; word-wrap:break-word; font-size:16px; align-self:flex-end; }
        .bot-bubble  { background:#EAEAEA; color:#000; padding:12px 18px; border-radius:16px; margin:5px; max-width:60%; word-wrap:break-word; font-size:16px; align-self:flex-start; }
        .file-info { background:#f0f2f6; padding:8px; border-radius:8px; margin:5px 0; font-size:14px; position:sticky; top:0; z-index:100; color: black; }
        .stApp > .main .block-container { padding-bottom:20px !important; }
        </style>
        """, unsafe_allow_html=True)

    @staticmethod
    def bubble(sender: str, text: str):
        css = "user-bubble" if sender == "user" else "bot-bubble"
        st.markdown(f"<div class='chat-container'><div class='{css}'>{text}</div></div>", unsafe_allow_html=True)

    @staticmethod
    def file_info(files: Dict[str, str]):
        if files:
            st.markdown(f"<div class='file-info'>📎 업로드된 파일: {', '.join(files.keys())}</div>", unsafe_allow_html=True)

    @staticmethod
    def faq_bubbles(faqs: List[Tuple[str, str, int]]):
        if not faqs:
            return
        faqs = faqs[:5]
        st.markdown("#### 🔥 자주 묻는 질문")
        cols = st.columns(5, gap="small")
        for i in range(5):
            with cols[i]:
                if i < len(faqs):
                    q, a, _ = faqs[i]
                    display = (q[:25] + "…") if len(q) > 25 else q
                    if st.button(f"🔍 {display}", key=f"faq_{i}", use_container_width=True, help=f"질문: {q}\n답변: {a[:100]}..."):
                        st.session_state.messages.append(("user", q))
                        st.session_state.messages.append(("bot", a))
                        DB.save_qa(st.session_state.session_id, q, a)
                        st.rerun()
                else:
                    st.write("")

# ======================= 챗봇 =======================
class Chatbot:
    @staticmethod
    @st.cache_resource
    def load_model():
        try:
            with open(Config.API_KEY_PATH, "r", encoding="utf-8") as f:
                api_key = f.read().strip()
            genai.configure(api_key=api_key)
            return genai.GenerativeModel(Config.MODEL_NAME)
        except Exception as e:
            st.error(f"모델 로드 실패: {e}")
            st.stop()

    def __init__(self):
        self.model = Chatbot.load_model()
        self._ensure_session()

    @staticmethod
    def _ensure_session():
        DB.init()
        ss = st.session_state
        ss.setdefault("session_id", os.urandom(8).hex())
        ss.setdefault("chat_session", Chatbot.load_model().start_chat(history=[]))
        ss.setdefault("messages", [])
        ss.setdefault("uploaded_files_text", {})
        ss.setdefault("file_hashes", set())
        ss.setdefault("show_db_recent", False)

    @staticmethod
    def _prompt_with_files(user_prompt: str) -> str:
        files = st.session_state.uploaded_files_text
        if not files:
            return user_prompt
        parts = []
        for fname, content in files.items():
            snippet = content[:2000]
            more = "...(내용 생략)" if len(content) > 2000 else ""
            parts.append(f"=== {fname} ===\n{snippet}{more}")
        return f"다음은 업로드된 파일의 내용입니다:\n\n" + "\n\n".join(parts) + f"\n\n사용자 질문: {user_prompt}"

    def reply(self, user_prompt: str) -> str:
        try:
            full = self._prompt_with_files(user_prompt)
            stream = st.session_state.chat_session.send_message(full, stream=True)
            text = ""
            for chunk in stream:
                if hasattr(chunk, "text"):
                    text += chunk.text
            return text
        except Exception as e:
            return f"응답 생성 중 오류가 발생했습니다: {e}"

# ======================= 메인 =======================
def main():
    st.set_page_config(page_title=Config.PAGE_TITLE, layout="wide", initial_sidebar_state="expanded")
    st.title(Config.PAGE_TITLE)
    UI.css()

    bot = Chatbot()

    # ---------- 사이드바 파일 업로드 ----------
    with st.sidebar:
        st.header("📎 파일 업로드")
        uploaded = st.file_uploader(
            "파일 선택",
            type=Config.SUPPORTED_EXTENSIONS,
            help=f"지원 형식: {', '.join(Config.SUPPORTED_EXTENSIONS)} (최대 {Config.MAX_FILE_SIZE_MB}MB)"
        )

        # 업로드된 파일 목록/삭제
        if st.session_state.uploaded_files_text:
            st.markdown("<h3 style='color:#FFFFFF;'>📁 업로드된 파일</h3>", unsafe_allow_html=True)
            for fname in list(st.session_state.uploaded_files_text.keys()):
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.markdown(f"<span style='color:#FFFFFF;'>📄 {fname}</span>", unsafe_allow_html=True)
                with c2:
                    if st.button("❌", key=f"remove_{fname}", help="파일 삭제"):
                        del st.session_state.uploaded_files_text[fname]
                        st.rerun()

    # ---------- 채팅 영역 ----------
    UI.file_info(st.session_state.uploaded_files_text)
    for sender, msg in st.session_state.messages:
        UI.bubble(sender, msg)

    # ---------- 파일 처리 ----------
    if uploaded is not None:
        uploaded.seek(0)
        raw = uploaded.read()
        file_hash = FileProcessor.md5(raw)

        if file_hash not in st.session_state.file_hashes:
            uploaded.seek(0)
            fname, content = FileProcessor.process_file(uploaded)
            if content and not content.startswith("파일 크기가"):
                st.session_state.uploaded_files_text[fname] = content
                st.session_state.file_hashes.add(file_hash)
                st.success(f"✅ 업로드 완료: {fname}")
                st.rerun()
            else:
                st.error(content)

    # ---------- FAQ (입력창 위) ----------
    UI.faq_bubbles(DB.get_top_faqs(limit=Config.FAQ_LIMIT))

    # ---------- 입력 ----------
    if prompt := st.chat_input("메시지를 입력하세요..."):
        st.session_state.messages.append(("user", prompt))
        with st.spinner("💭 답변을 생성하고 있습니다..."):
            reply = bot.reply(prompt)
        st.session_state.messages.append(("bot", reply))
        DB.save_qa(st.session_state.session_id, prompt, reply)
        st.rerun()

    # ---------- 사이드바 토글 ----------
    btn_label = "닫기" if st.session_state.show_db_recent else "DB 최근 대화 확인"
    if st.sidebar.button(btn_label, key="db_recent_toggle"):
        st.session_state.show_db_recent = not st.session_state.show_db_recent
        st.rerun()

    if st.session_state.show_db_recent:
        for m in DB.get_recent_messages():
            st.write(f"[{m.created_at}] ({m.session_id})")
            st.write(f"🙋 {m.user_text}")
            st.write(f"🤖 {m.bot_text}")
            st.markdown("---")

if __name__ == "__main__":
    main()
