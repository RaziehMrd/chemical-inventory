# app.py
import os
from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, relationship


# =============================
# Config
# =============================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# Prefer cloud DB if provided; otherwise use a disk-backed SQLite path.
DB_URL = (os.environ.get("DATABASE_URL") or "").strip()

def _sqlite_url() -> str:
    """Return an absolute SQLite URL under a guaranteed-writable path."""
    data_dir = os.environ.get("DATA_DIR", "/opt/render/project/src/data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception:
        data_dir = "/tmp"
        os.makedirs(data_dir, exist_ok=True)
    abs_path = os.path.join(data_dir, "lab_inventory.db")
    return "sqlite:////" + abs_path.lstrip("/")

def _normalize_db_url(url: str) -> str:
    """Ensure Postgres URLs are compatible; add sslmode when missing."""
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
# SQLAlchemy models (2.x style)
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
    requests: Mapped[list["Request"]] = relationship(
        back_populates="chemical", cascade="all, delete-orphan"
    )

class Request(Base):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chem_id: Mapped[int] = mapped_column(ForeignKey("chemicals.id"), nullable=False)

    # New columns (ensure they also exist in your existing DB via ALTER TABLE)
    first_name: Mapped[str] = mapped_column(String, nullable=False, default="")  # NEW
    surname: Mapped[str] = mapped_column(String, nullable=False, default="")     # NEW

    requester_email: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    chemical: Mapped[Chemical] = relationship(back_populates="requests")


# =============================
# Engine & table creation
# =============================
connect_args = {}
if DB_URL.startswith("sqlite:"):
    connect_args["check_same_thread"] = False

engine = create_engine(DB_URL, echo=False, future=True, connect_args=connect_args, pool_pre_ping=True)
Base.metadata.create_all(engine)


# =============================
# CRUD helpers
# =============================
def list_chemicals(search: str = ""):
    with Session(engine) as sess:
        if search:
            stmt = (
                select(Chemical.id, Chemical.name, Chemical.amount, Chemical.unit, Chemical.location)
                .where(Chemical.name.ilike(f"%{search}%"))
                .order_by(Chemical.name.asc())
            )
        else:
            stmt = (
                select(Chemical.id, Chemical.name, Chemical.amount, Chemical.unit, Chemical.location)
                .order_by(Chemical.name.asc())
            )
        return sess.execute(stmt).all()

def upsert_chemical(name: str, amount: float, unit: str, location: str, notes: str = ""):
    name = name.strip()
    unit = unit.strip()
    location = location.strip()
    notes = notes.strip()
    with Session(engine) as sess:
        chem = sess.scalar(select(Chemical).where(Chemical.name == name))
        if chem is None:
            chem = Chemical(name=name, amount=float(amount), unit=unit, location=location, notes=notes)
            sess.add(chem)
        else:
            chem.amount = float(amount)
            chem.unit = unit
            chem.location = location
            chem.notes = notes
        sess.commit()

def update_stock(chem_id: int, delta_amount: float):
    with Session(engine) as sess:
        chem = sess.get(Chemical, int(chem_id))
        if chem is None:
            raise ValueError("Chemical not found")
        chem.amount = float(chem.amount) + float(delta_amount)
        sess.commit()

def add_request(chem_id: int, first_name: str, surname: str, email: str, qty: float):
    with Session(engine) as sess:
        req = Request(
            chem_id=int(chem_id),
            first_name=first_name.strip(),
            surname=surname.strip(),
            requester_email=email.strip(),
            quantity=float(qty),
            status="pending",
            created_at=datetime.utcnow(),
        )
        sess.add(req)
        sess.commit()

def list_requests(status: str | None = None):
    with Session(engine) as sess:
        if status:
            stmt = text("""
                SELECT r.id,
                       c.name,
                       r.quantity,
                       r.first_name,
                       r.surname,
                       r.requester_email,
                       r.status,
                       r.created_at
                FROM requests r
                JOIN chemicals c ON r.chem_id = c.id
                WHERE r.status = :status
                ORDER BY r.id DESC
            """)
            rows = sess.execute(stmt, {"status": status}).all()
        else:
            stmt = text("""
                SELECT r.id,
                       c.name,
                       r.quantity,
                       r.first_name,
                       r.surname,
                       r.requester_email,
                       r.status,
                       r.created_at
                FROM requests r
                JOIN chemicals c ON r.chem_id = c.id
                ORDER BY r.id DESC
            """)
            rows = sess.execute(stmt).all()
        return rows

def set_request_status(req_id: int, new_status: str):
    with Session(engine) as sess:
        req = sess.get(Request, int(req_id))
        if req is None:
            return
        req.status = new_status
        sess.commit()


# =============================
# UI
# =============================
st.set_page_config(page_title="Lab Chemicals", page_icon="🧪", layout="wide")
st.title("🧪 Lab Chemical Inventory")

tabs = st.tabs(["Search & Request", "Admin"])

# --- Search & Request
with tabs[0]:
    st.subheader("Search inventory")
    q = st.text_input("Search by chemical name", placeholder="e.g., acetone, ethanol, NaCl")
    q_norm = q.lower().strip() if q else ""
    data = list_chemicals(q_norm)

    if data:
        st.write("#### Available chemicals")
        st.dataframe(
            [{"ID": r[0], "Chemical": r[1], "Amount": r[2], "Unit": r[3], "Location": r[4]} for r in data],
            use_container_width=True,
        )
    else:
        st.info("No chemicals match your search.")

    st.markdown("---")
    st.subheader("Request a chemical")

    chem_options = {f"{name} ({amount} {unit})": cid for cid, name, amount, unit, _loc in data} if data else {}
    if not chem_options:
        st.warning("No chemicals to request yet. Please ask an admin to add items.")
    else:
        with st.form("request_form"):
            chosen = st.selectbox("Choose chemical", options=list(chem_options.keys()))
            qty = st.number_input("Quantity needed", min_value=0.0, step=0.1, format="%.3f")
            first_name = st.text_input("First Name")
            surname = st.text_input("Surname")
            email = st.text_input("Your email")
            submitted = st.form_submit_button("Submit request")

        if submitted:
            if qty <= 0:
                st.error("Quantity must be > 0.")
            elif not first_name.strip() or not surname.strip():
                st.error("Please enter both first name and surname.")
            elif "@" not in email or "." not in email:
                st.error("Please enter a valid email.")
            else:
                add_request(chem_options[chosen], first_name, surname, email, qty)
                st.success("Request submitted! The lab admin will review it.")

# --- Admin
with tabs[1]:
    st.subheader("Admin")
    pw = st.text_input("Admin password", type="password", placeholder="Enter admin password")
    if pw != ADMIN_PASSWORD:
        st.warning("Enter the correct password to manage inventory.")
        st.stop()

    st.success("Admin mode enabled.")

    # Add / Update chemical
    with st.expander("➕ Add / Update a chemical", expanded=False):
        with st.form("add_chem"):
            col1, col2, col3 = st.columns(3)
            with col1:
                name = st.text_input("Chemical name")
                unit = st.text_input("Unit", value="g")
            with col2:
                amount = st.number_input("Amount", min_value=0.0, step=0.1)
            with col3:
                location = st.text_input("Location", value="")
            notes = st.text_area("Notes (optional)", height=80)
            add_btn = st.form_submit_button("Save")
        if add_btn:
            if not name.strip():
                st.error("Name is required.")
            else:
                try:
                    upsert_chemical(name, amount, unit, location, notes)
                    st.success(f"Saved “{name}”.")
                except Exception as e:
                    st.error(f"Error: {e}")

    # Bulk CSV Import
    with st.expander("📥 Bulk import from CSV (Name,Amount,Unit,Location,Notes)", expanded=False):
        tmpl = """Name,Amount,Unit,Location,Notes
Acetone,2.5,L,Flammables Cabinet,ACS grade
Sodium chloride,500,g,Main Shelf,A.R.
Ethanol,1,L,Flammables Cabinet,96%"""
        st.caption("Expected columns: Name, Amount, Unit, Location, (optional) Notes")
        st.code(tmpl, language="csv")

        file = st.file_uploader("Upload CSV file", type=["csv"])
        if file is not None:
            try:
                df = pd.read_csv(file)
                required = {"Name", "Amount", "Unit", "Location"}
                if not required.issubset(set(df.columns)):
                    st.error(f"CSV must contain columns: {', '.join(sorted(required))}")
                else:
                    st.write("Preview:")
                    st.dataframe(df, use_container_width=True)
                    if st.button("Import rows"):
                        ok, err = 0, 0
                        for _, row in df.iterrows():
                            try:
                                upsert_chemical(
                                    name=str(row["Name"]),
                                    amount=float(row["Amount"]),
                                    unit=str(row["Unit"]),
                                    location=str(row["Location"]),
                                    notes=str(row.get("Notes", "")),
                                )
                                ok += 1
                            except Exception:
                                err += 1
                        st.success(f"Imported {ok} rows. Errors: {err}.")
            except Exception as e:
                st.error(f"Failed to read CSV: {e}")

    # Adjust stock
    with st.expander("🔧 Adjust stock", expanded=False):
        all_chems = list_chemicals("")
        if not all_chems:
            st.info("No chemicals yet.")
        else:
            label_to_id = {f"{n} ({a} {u}) [ID:{cid}]": cid for cid, n, a, u, _ in all_chems}
            sel = st.selectbox("Select chemical", list(label_to_id.keys()))
            delta = st.number_input("Change in amount (use negative to reduce)", step=0.1, format="%.3f")
            if st.button("Apply change"):
                try:
                    update_stock(label_to_id[sel], delta)
                    st.success("Stock updated.")
                except Exception as e:
                    st.error(f"Error updating stock: {e}")

    st.markdown("### Pending requests")
    reqs = list_requests(status="pending")
    if reqs:
        for rid, cname, qty, fname, sname, remail, status, created in reqs:
            with st.container(border=True):
                st.write(f"**[{rid}] {cname}** — requested: **{qty}**")
                st.write(f"Requester: {fname} {sname} ({remail}) • Created: {created} • Status: {status}")

                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("Approve", key=f"approve_{rid}"):
                        set_request_status(rid, "approved")
                        st.success("Approved.")
                with c2:
                    if st.button("Reject", key=f"reject_{rid}"):
                        set_request_status(rid, "rejected")
                        st.info("Rejected.")
                with c3:
                    if st.button("Mark Fulfilled", key=f"fulfill_{rid}"):
                        set_request_status(rid, "fulfilled")
                        st.success("Fulfilled.")
    else:
        st.info("No pending requests.")

    st.markdown("### All requests")
    all_reqs = list_requests()
    if all_reqs:
        st.dataframe(
            [
                {
                    "ID": rid,
                    "Chemical": cname,
                    "Qty": qty,
                    "First Name": fname,
                    "Surname": sname,
                    "Requester": remail,
                    "Status": status,
                    "Created": str(created),
                }
                for rid, cname, qty, fname, sname, remail, status, created in all_reqs
            ],
            use_container_width=True,
        )
    else:
        st.info("No requests yet.")
