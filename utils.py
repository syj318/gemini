import os
import io
import hashlib
import pandas as pd
import PyPDF2
from datetime import datetime
from dateutil.relativedelta import relativedelta
from typing import Dict, List, Tuple

import streamlit as st
from zoneinfo import ZoneInfo
from sqlalchemy import (
    create_engine, String, Integer, Text, DateTime, func, select, Column, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import over

from config import Config

# ======================= DB =======================
Base = declarative_base()

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    user_text = Column(Text, nullable=False)
    bot_text  = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, index=True, default=func.now())
    
    __table_args__ = (
        Index("ix_messages_user_text", user_text, mysql_length=255),
    )


class FaqSummary(Base):
    __tablename__ = "faq_summary"
    id = Column(Integer, primary_key=True, autoincrement=True)
    normalized_question = Column(String(255), unique=True, index=True, nullable=False)
    original_question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    question_count = Column(Integer, default=1, index=True, nullable=False)
    last_seen_at = Column(DateTime, nullable=False, index=True)


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
        normalized_q = " ".join(user_text.lower().strip().split())
        now = DB.now_local_naive()

        with DB._Session() as s:
            s.add(Message(
                session_id=session_id,
                user_text=user_text,
                bot_text=bot_text,
                created_at=now,
            ))

            summary_entry = s.query(FaqSummary).filter_by(normalized_question=normalized_q).first()

            if summary_entry:
                summary_entry.question_count += 1
                summary_entry.original_question = user_text
                summary_entry.answer = bot_text
                summary_entry.last_seen_at = now
            else:
                s.add(FaqSummary(
                    normalized_question=normalized_q,
                    original_question=user_text,
                    answer=bot_text,
                    question_count=1,
                    last_seen_at=now,
                ))
            s.commit()

    @staticmethod
    def get_top_faqs(limit: int = 5) -> List[Tuple[str, str, int]]:
        with DB._Session() as s:
            results = (
                s.query(FaqSummary.original_question, FaqSummary.answer, FaqSummary.question_count)
                .order_by(FaqSummary.question_count.desc(), FaqSummary.last_seen_at.desc())
                .limit(limit)
                .all()
            )
        return results

    @staticmethod
    def get_recent_messages(limit: int = 10) -> List[Message]:
        with DB._Session() as s:
            return s.query(Message).order_by(Message.created_at.desc()).limit(limit).all()

    @staticmethod
    def archive_old_messages(months_ago: int = 6) -> str:
        now = DB.now_local_naive()
        cutoff_date = now - relativedelta(months=months_ago)
        
        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 아카이빙 작업을 시작합니다...")
        print(f"대상: {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')} 이전의 모든 대화 기록")

        with DB._Session() as s:
            old_messages_query = s.query(Message).filter(Message.created_at < cutoff_date)
            
            try:
                df = pd.read_sql(old_messages_query.statement, s.bind)
            except Exception as e:
                return f"오류: 데이터를 읽는 중 문제가 발생했습니다: {e}"

            if df.empty:
                return "완료: 아카이빙할 오래된 데이터가 없습니다."

            num_records = len(df)
            archive_filename = f"archive_{now.strftime('%Y%m%d_%H%M%S')}.csv"

            try:
                df.to_csv(archive_filename, index=False, encoding='utf-8-sig')
                print(f"성공: {num_records}개의 기록을 '{archive_filename}' 파일로 백업했습니다.")

                old_messages_query.delete(synchronize_session=False)
                s.commit()
                print(f"성공: 데이터베이스에서 오래된 기록 {num_records}개를 삭제했습니다.")
                
                return f"완료: 총 {num_records}개의 기록을 성공적으로 아카이빙했습니다. (백업 파일: {archive_filename})"

            except Exception as e:
                s.rollback()
                return f"치명적 오류: 아카이빙 작업 중 문제가 발생하여 모든 변경사항을 되돌렸습니다. 오류: {e}"


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