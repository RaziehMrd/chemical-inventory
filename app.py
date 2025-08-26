# app.py
import os
from datetime import datetime
from pathlib import Path
import re

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
    # Prefer Render's project dir; fallback to /tmp if needed
    data_dir = os.environ.get("DATA_DIR", "/opt/render/project/src/data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception:
        data_dir = "/tmp"
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

# Folder for optional file uploads (spec sheets/quotes)
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "./uploads")
Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

# =============================
# Models (SQLAlchemy 2.x)
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

    requester_first_name: Mapped[str] = mapped_column(String, nullable=False)
    requester_surname: Mapped[str] = mapped_column(String, nullable=False)
    requester_email: Mapped[str] = mapped_column(String, nullable=False)

    comments: Mapped[str] = mapped_column(String, default="")
    attachment_path: Mapped[str] = mapped_column(String, default="")

    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

# =============================
# Engine & tables
# =============================
connect_args = {}
if DB_URL.startswith("sqlite:"):
    connect_args["check_same_thread"] = False
engine = create_engine(DB_URL, echo=False, future=True, connect_args=connect_args, pool_pre_ping=True)
Base.metadata.create_all(engine)

# =============================
# Helpers
# =============================

def list_chemicals(search: str = ""):
    with Session(engine) as sess:
        stmt = select(Chemical.id, Chemical.name, Chemical.amount, Chemical.unit, Chemical.location)
        if search:
            stmt = stmt.where(Chemical.name.ilike(f"%{search}%"))
        stmt = stmt.order_by(Chemical.name.asc())
        return sess.execute(stmt).all()

def upsert_chemical(name: str, amount: float, unit: str, location: str, notes: str = ""):
    with Session(engine) as sess:
        chem = sess.scalar(select(Chemical).where(Chemical.name == name.strip()))
        if chem is None:
            chem = Chemical(name=name.strip(), amount=float(amount), unit=unit.strip(),
                            location=location.strip(), notes=notes.strip())
            sess.add(chem)
        else:
            chem.amount = float(amount)
            chem.unit = unit.strip()
            chem.location = location.strip()
            chem.notes = notes.strip()
        sess.commit()

def update_stock(chem_id: int, delta_amount: float):
    with Session(engine) as sess:
        chem = sess.get(Chemical, int(chem_id))
        if chem:
            chem.amount = float(chem.amount) + float(delta_amount)
            sess.commit()

def add_request(chem_id: int, first_name: str, surname: str, email: str, qty: float):
    with Session(engine) as sess:
        req = Request(chem_id=chem_id, first_name=first_name.strip(),
                      surname=surname.strip(), requester_email=email.strip(),
                      quantity=float(qty), status="pending",
                      created_at=datetime.utcnow())
        sess.add(req)
        sess.commit()

def list_requests(status: str | None = None):
    with Session(engine) as sess:
        sql = """
        SELECT r.id, c.name, r.quantity, r.first_name, r.surname,
               r.requester_email, r.status, r.created_at
        FROM requests r
        JOIN chemicals c ON r.chem_id = c.id
        """
        if status:
            sql += " WHERE r.status = :status"
            sql += " ORDER BY r.id DESC"
            return sess.execute(text(sql), {"status": status}).all()
        else:
            sql += " ORDER BY r.id DESC"
            return sess.execute(text(sql)).all()

def set_request_status(req_id: int, new_status: str):
    with Session(engine) as sess:
        req = sess.get(Request, req_id)
        if req:
            req.status = new_status
            sess.commit()

def save_uploaded_file(upload, prefix="attach") -> str:
    if upload is None:
        return ""
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", upload.name)
    filename = f"{prefix}_{int(datetime.utcnow().timestamp())}_{safe_name}"
    dest = os.path.join(UPLOAD_DIR, filename)
    with open(dest, "wb") as f:
        f.write(upload.getbuffer())
    return dest

def add_purchase_request(material_name, cas_number, specifications, amount, unit,
                         requester_first_name, requester_surname, requester_email,
                         comments="", attachment_path=""):
    with Session(engine) as sess:
        pr = PurchaseRequest(material_name=material_name.strip(), cas_number=cas_number.strip(),
                             specifications=specifications.strip(), amount=float(amount),
                             unit=unit.strip(), requester_first_name=requester_first_name.strip(),
                             requester_surname=requester_surname.strip(),
                             requester_email=requester_email.strip(),
                             comments=(comments or "").strip(),
                             attachment_path=(attachment_path or "").strip(),
                             status="pending", created_at=datetime.utcnow())
        sess.add(pr)
        sess.commit()

def list_purchase_requests(status: str | None = None):
    with Session(engine) as sess:
        sql = """
        SELECT id, material_name, cas_number, specifications, amount, unit,
               requester_first_name, requester_surname, requester_email,
               comments, attachment_path, status, created_at
        FROM purchase_requests
        """
        if status:
            sql += " WHERE status = :status ORDER BY id DESC"
            return sess.execute(text(sql), {"status": status}).all()
        else:
            sql += " ORDER BY id DESC"
            return sess.execute(text(sql)).all()

def set_purchase_request_status(req_id: int, new_status: str):
    with Session(engine) as sess:
        pr = sess.get(PurchaseRequest, req_id)
        if pr:
            pr.status = new_status
            sess.commit()

def approve_request_and_adjust(req_id: int):
    with Session(engine) as sess:
        req = sess.get(Request, req_id)
        if not req:
            return False, "Request not found."
        if req.status in ("approved", "fulfilled"):
            return False, f"Request already {req.status}."
        chem = sess.get(Chemical, req.chem_id)
        if not chem:
            return False, "Chemical not found."
        have, need = float(chem.amount), float(req.quantity)
        if need <= 0:
            return False, "Invalid quantity."
        if have < need:
            return False, f"Not enough stock: have {have}, need {need}."
        chem.amount -= need
        req.status = "approved"
        sess.commit()
        return True, f"Approved. New stock of {chem.name}: {chem.amount} {chem.unit}"

def get_inventory_df(in_stock_only=True) -> pd.DataFrame:
    rows = list_chemicals("")
    df = pd.DataFrame([{"ID": cid, "Name": n, "Amount": a, "Unit": u, "Location": loc}
                       for cid, n, a, u, loc in rows])
    if df.empty:
        return df
    if in_stock_only:
        df = df[df["Amount"] > 0]
    return df.sort_values("Name").reset_index(drop=True)

# =============================
# UI
# =============================
st.set_page_config(page_title="Lab Chemicals", page_icon="🧪", layout="wide")
st.title("🧪 Lab Chemical Inventory")

tabs = st.tabs(["Search & Request", "Admin"])

# --- Search & Request ---
with tabs[0]:
    st.subheader("Search inventory")
    q = st.text_input("Search chemical")
    data = list_chemicals(q.strip().lower()) if q else list_chemicals("")
    if data:
        st.dataframe([{"ID": cid, "Name": n, "Amount": a, "Unit": u, "Location": l}
                      for cid, n, a, u, l in data])
    else:
        st.info("No chemicals found. (Purchase request UI here...)")

# --- Admin ---
with tabs[1]:
    pw = st.text_input("Admin password", type="password")
    if pw != ADMIN_PASSWORD:
        st.stop()
    st.success("Admin mode enabled.")

    # Download inventory
    st.markdown("### Download Inventory")
    only_in_stock = st.checkbox("Only items in stock", value=True)
    df_inv = get_inventory_df(in_stock_only=only_in_stock)
    st.caption(f"{len(df_inv)} item(s).")
    if not df_inv.empty:
        st.download_button("⬇️ Download CSV",
                           df_inv.to_csv(index=False).encode(),
                           file_name=f"inventory_{datetime.utcnow().strftime('%Y%m%d')}.csv",
                           mime="text/csv")
