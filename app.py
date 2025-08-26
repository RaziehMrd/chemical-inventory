# app.py
import os
from datetime import datetime
import pandas as pd
import streamlit as st
from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Integer, String,
    create_engine, func, select, text
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, relationship

# =============================
# Config
# =============================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
DB_URL = (os.environ.get("DATABASE_URL") or "").strip()

def _sqlite_url() -> str:
    data_dir = os.environ.get("DATA_DIR", "/opt/render/project/src/data")
    os.makedirs(data_dir, exist_ok=True)
    abs_path = os.path.join(data_dir, "lab_inventory.db")
    return "sqlite:////" + abs_path.lstrip("/")

def _normalize_db_url(url: str) -> str:
    if not url:
        return _sqlite_url()
    low = url.lower()
    if low.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
        low = url.lower()
    if low.startswith("postgresql://") and "sslmode=" not in low:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url

DB_URL = _normalize_db_url(DB_URL)

# =============================
# Models
# =============================
class Base(DeclarativeBase):
    pass

class Chemical(Base):
    __tablename__ = "chemicals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unit: Mapped[str] = mapped_column(String, default="g", nullable=False)
    location: Mapped[str] = mapped_column(String, default="")
    notes: Mapped[str] = mapped_column(String, default="")
    requests: Mapped[list["Request"]] = relationship(back_populates="chemical", cascade="all, delete-orphan")

class Request(Base):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chem_id: Mapped[int] = mapped_column(ForeignKey("chemicals.id"), nullable=False)
    first_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    surname: Mapped[str] = mapped_column(String, nullable=False, default="")
    requester_email: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    chemical: Mapped[Chemical] = relationship(back_populates="requests")

class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    material_name: Mapped[str] = mapped_column(String, nullable=False)
    cas_number: Mapped[str] = mapped_column(String, nullable=False)
    specifications: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)

    # Now required:
    requester_first_name: Mapped[str] = mapped_column(String, nullable=False)
    requester_surname: Mapped[str] = mapped_column(String, nullable=False)
    requester_email: Mapped[str] = mapped_column(String, nullable=False)

    comments: Mapped[str] = mapped_column(String, default="")

    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

# =============================
# Engine
# =============================
connect_args = {}
if DB_URL.startswith("sqlite:"):
    connect_args["check_same_thread"] = False
engine = create_engine(DB_URL, echo=False, future=True, connect_args=connect_args, pool_pre_ping=True)
Base.metadata.create_all(engine)

# =============================
# CRUD helpers
# =============================
def add_purchase_request(material_name, cas_number, specifications, amount, unit,
                         requester_first_name, requester_surname, requester_email, comments=""):
    with Session(engine) as sess:
        pr = PurchaseRequest(
            material_name=material_name.strip(),
            cas_number=cas_number.strip(),
            specifications=specifications.strip(),
            amount=float(amount),
            unit=unit.strip(),
            requester_first_name=requester_first_name.strip(),
            requester_surname=requester_surname.strip(),
            requester_email=requester_email.strip(),
            comments=comments.strip(),
            status="pending",
            created_at=datetime.utcnow(),
        )
        sess.add(pr)
        sess.commit()

def list_purchase_requests(status: str | None = None):
    with Session(engine) as sess:
        if status:
            stmt = text("""
                SELECT id, material_name, cas_number, specifications, amount, unit,
                       requester_first_name, requester_surname, requester_email,
                       comments, status, created_at
                FROM purchase_requests
                WHERE status = :status
                ORDER BY id DESC
            """)
            return sess.execute(stmt, {"status": status}).all()
        else:
            stmt = text("""
                SELECT id, material_name, cas_number, specifications, amount, unit,
                       requester_first_name, requester_surname, requester_email,
                       comments, status, created_at
                FROM purchase_requests
                ORDER BY id DESC
            """)
            return sess.execute(stmt).all()

def set_purchase_request_status(req_id: int, new_status: str):
    with Session(engine) as sess:
        pr = sess.get(PurchaseRequest, int(req_id))
        if pr:
            pr.status = new_status
            sess.commit()

# =============================
# UI (only changed part shown)
# =============================
st.set_page_config(page_title="Lab Chemicals", page_icon="🧪", layout="wide")
st.title("🧪 Lab Chemical Inventory")

tabs = st.tabs(["Search & Request", "Admin"])

with tabs[0]:
    st.subheader("Search inventory")
    q = st.text_input("Search by chemical name", placeholder="e.g., acetone, ethanol, NaCl")
    q_norm = q.lower().strip() if q else ""
    data = []  # (replace with your list_chemicals function call)

    if not data:
        st.info("No chemicals match your search.")
        st.markdown("<span style='color:red;font-weight:700;'>★</span> Required fields", unsafe_allow_html=True)

        req_tab_required, req_tab_optional = st.tabs(["Required ★", "Optional (Comments)"])

        with req_tab_required:
            with st.form("purchase_required_form"):
                col1, col2 = st.columns(2)
                with col1:
                    material_name = st.text_input("Material name ★")
                    cas_number = st.text_input("CAS number ★")
                    amount = st.number_input("Amount ★", min_value=0.0, step=0.1, format="%.3f")
                    requester_first_name = st.text_input("First Name ★")
                    requester_surname = st.text_input("Surname ★")
                with col2:
                    specifications = st.text_area("Specifications ★", height=100)
                    unit = st.selectbox("Unit ★", ["g", "mg", "kg", "mL", "L", "other"])
                    requester_email = st.text_input("Email ★")
                submit_required = st.form_submit_button("Submit purchase request")

        with req_tab_optional:
            comments = st.text_area("Comments (optional)", height=100)

        if submit_required:
            errs = []
            if not material_name.strip(): errs.append("Material name")
            if not cas_number.strip(): errs.append("CAS number")
            if not specifications.strip(): errs.append("Specifications")
            if amount <= 0: errs.append("Amount")
            if not unit: errs.append("Unit")
            if not requester_first_name.strip(): errs.append("First Name")
            if not requester_surname.strip(): errs.append("Surname")
            if not requester_email.strip(): errs.append("Email")

            if errs:
                st.error("Please fill all required fields (★): " + ", ".join(errs))
            else:
                add_purchase_request(material_name, cas_number, specifications, amount, unit,
                                     requester_first_name, requester_surname, requester_email,
                                     comments if "comments" in locals() else "")
                st.success("Purchase request submitted! The lab admin will review it.")

