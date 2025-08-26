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
    # Prefer project directory; fallback to /tmp if needed
    data_dir = os.environ.get("DATA_DIR", "./data")
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

    # Ensure these exist in existing DBs via ALTER TABLE if needed:
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

    # Required material info
    material_name: Mapped[str] = mapped_column(String, nullable=False)
    cas_number: Mapped[str] = mapped_column(String, nullable=False)
    specifications: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)

    # Required requester info
    requester_first_name: Mapped[str] = mapped_column(String, nullable=False)
    requester_surname: Mapped[str] = mapped_column(String, nullable=False)
    requester_email: Mapped[str] = mapped_column(String, nullable=False)

    # Optional
    comments: Mapped[str] = mapped_column(String, default="")
    attachment_path: Mapped[str] = mapped_column(String, default="")  # saved file path (optional)

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
        sql = """
        SELECT r.id, c.name, r.quantity, r.first_name, r.surname,
               r.requester_email, r.status, r.created_at
        FROM requests r
        JOIN chemicals c ON r.chem_id = c.id
        """
        if status:
            sql += " WHERE r.status = :status ORDER BY r.id DESC"
            return sess.execute(text(sql), {"status": status}).all()
        else:
            sql += " ORDER BY r.id DESC"
            return sess.execute(text(sql)).all()

def set_request_status(req_id: int, new_status: str):
    with Session(engine) as sess:
        req = sess.get(Request, int(req_id))
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
        pr = PurchaseRequest(
            material_name=material_name.strip(),
            cas_number=cas_number.strip(),
            specifications=specifications.strip(),
            amount=float(amount),
            unit=unit.strip(),
            requester_first_name=requester_first_name.strip(),
            requester_surname=requester_surname.strip(),
            requester_email=requester_email.strip(),
            comments=(comments or "").strip(),
            attachment_path=(attachment_path or "").strip(),
            status="pending",
            created_at=datetime.utcnow(),
        )
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
        pr = sess.get(PurchaseRequest, int(req_id))
        if pr:
            pr.status = new_status
            sess.commit()

def approve_request_and_adjust(req_id: int):
    """Approve an in-stock request and deduct the requested quantity from inventory."""
    with Session(engine) as sess:
        req = sess.get(Request, int(req_id))
        if not req:
            return False, "Request not found."
        if req.status in ("approved", "fulfilled"):
            return False, f"Request already {req.status}; no stock changed."
        chem = sess.get(Chemical, req.chem_id)
        if not chem:
            return False, "Chemical not found."
        have, need = float(chem.amount or 0.0), float(req.quantity or 0.0)
        if need <= 0:
            return False, "Invalid quantity."
        if have < need:
            return False, f"Not enough stock: have {have:g} {chem.unit}, need {need:g} {chem.unit}."
        chem.amount = have - need
        req.status = "approved"
        sess.commit()
        return True, f"Approved. New stock of {chem.name}: {chem.amount:g} {chem.unit}"

def get_inventory_df(in_stock_only=True) -> pd.DataFrame:
    rows = list_chemicals("")
    df = pd.DataFrame(
        [{"ID": cid, "Name": n, "Amount": a, "Unit": u, "Location": loc} for cid, n, a, u, loc in rows],
        columns=["ID", "Name", "Amount", "Unit", "Location"],
    )
    if df.empty:
        return df
    if in_stock_only:
        df = df[df["Amount"] > 0]
    return df.sort_values("Name").reset_index(drop=True)

# =============================
# Tiny UX helpers
# =============================
CAS_HELP_URL = "https://commonchemistry.cas.org/"

def basic_email_ok(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()))

def guess_unit(material: str, specs: str) -> str:
    text = f"{material} {specs}".lower()
    if any(w in text for w in ["solution", "solvent", "liquid", "ethanol", "acetone", "chloroform", "methanol", "acid", "hcl", "h2so4"]):
        return "mL"
    if any(w in text for w in ["powder", "salt", "granule", "pellet", "flake", "mxene", "oxide", "sodium", "chloride"]):
        return "g"
    return "g"

def cas_hint():
    st.caption(f"Need a CAS number? Try: [CAS Common Chemistry]({CAS_HELP_URL})")

# =============================
# UI
# =============================
st.set_page_config(page_title="Lab Chemicals", page_icon="🧪", layout="wide")
st.title("🧪 Lab Chemical Inventory")

tabs = st.tabs(["Search & Request", "Admin"])

# --- Search & Request ---
with tabs[0]:
    st.subheader("Search inventory")
    q = st.text_input("Search by chemical name", placeholder="e.g., acetone, ethanol, NaCl")
    q_norm = q.strip().lower() if q else ""
    data = list_chemicals(q_norm)

    if data:
        st.write("#### Available chemicals")
        st.dataframe(
            [{"ID": cid, "Chemical": n, "Amount": a, "Unit": u, "Location": loc} for cid, n, a, u, loc in data],
            use_container_width=True,
        )

        # In-stock Request form
        st.markdown("---")
        st.subheader("Request a chemical (in stock)")
        chem_options = {f"{n} ({a} {u})": cid for cid, n, a, u, _ in data}
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
            elif not basic_email_ok(email):
                st.error("Please enter a valid email.")
            else:
                add_request(chem_options[chosen], first_name, surname, email, qty)
                st.success("Request submitted! The lab admin will review it.")
    else:
        st.info("No chemicals match your search.")

        # Purchase Request form (appears only when no results)
        st.markdown("<div style='margin-top: 1rem; font-weight:600;'>Request to purchase this material</div>", unsafe_allow_html=True)
        st.markdown("<span style='color:red;font-weight:700;'>★</span> Required fields", unsafe_allow_html=True)

        req_tab_required, req_tab_optional = st.tabs(["Required ★", "Optional (Comments & Files)"])

        # Required
        with req_tab_required:
            with st.form("purchase_required_form"):
                st.markdown("**1) Material details**")
                c1, c2 = st.columns([2, 1])
                with c1:
                    material_name = st.text_input("Material name ★", placeholder="e.g., Ethanol, Titanium Carbide MXene")
                with c2:
                    cas_number = st.text_input("CAS number ★", placeholder="e.g., 64-17-5")
                    cas_hint()
                specifications = st.text_area(
                    "Specifications ★",
                    placeholder="Grade, purity, supplier preference, particle size, packaging, etc.",
                    height=100,
                )

                st.divider()
                st.markdown("**2) Quantity**")
                suggested_unit = guess_unit(material_name, specifications)
                qcol1, qcol2 = st.columns([1, 1])
                with qcol1:
                    amount = st.number_input("Amount ★", min_value=0.0, step=0.1, format="%.3f")
                with qcol2:
                    unit_options = ["g", "mg", "kg", "mL", "L", "other"]
                    try:
                        default_idx = unit_options.index(suggested_unit)
                    except ValueError:
                        default_idx = 0
                    unit = st.selectbox("Unit ★", unit_options, index=default_idx)

                st.divider()
                st.markdown("**3) Requester**")
                r1, r2, r3 = st.columns([1, 1, 1.4])
                with r1:
                    requester_first_name = st.text_input("First name ★")
                with r2:
                    requester_surname = st.text_input("Surname ★")
                with r3:
                    requester_email = st.text_input("Email ★", placeholder="name@university.edu")

                submit_purchase_required = st.form_submit_button("Submit purchase request", use_container_width=True)

        # Optional
        with req_tab_optional:
            comments = st.text_area(
                "Comments (optional)",
                placeholder="Budget code, preferred vendor link, urgency, delivery constraints, etc.",
                height=100,
            )
            attachment = st.file_uploader(
                "Attach spec sheet / quote (optional)",
                type=["pdf", "png", "jpg", "jpeg", "csv", "txt"],
                help="Attach any supporting file. It will be stored on the server.",
            )

        # Validation & submit
        if submit_purchase_required:
            errs = []
            if not material_name.strip(): errs.append("Material name")
            if not re.match(r"^\d{2,7}-\d{2}-\d$", cas_number.strip()): errs.append("Valid CAS (e.g., 64-17-5)")
            if not specifications.strip(): errs.append("Specifications")
            if amount <= 0: errs.append("Amount")
            if not unit: errs.append("Unit")
            if not requester_first_name.strip(): errs.append("First name")
            if not requester_surname.strip(): errs.append("Surname")
            if not basic_email_ok(requester_email): errs.append("Valid email")

            if errs:
                st.error("Please fill all required fields (★): " + ", ".join(errs))
            else:
                try:
                    attach_path = save_uploaded_file(attachment, prefix="purchase") if attachment else ""
                    add_purchase_request(
                        material_name, cas_number, specifications, amount, unit,
                        requester_first_name, requester_surname, requester_email,
                        comments=comments if "comments" in locals() else "",
                        attachment_path=attach_path
                    )
                    st.success("Purchase request submitted! The lab admin will review it.")
                except Exception as e:
                    st.error(f"Failed to submit purchase request: {e}")

# --- Admin ---
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

    # Download inventory
    st.markdown("### Download inventory")
    col_dl1, col_dl2 = st.columns([2, 1])
    with col_dl1:
        only_in_stock = st.checkbox("Only items with Amount > 0 (in stock)", value=True)
        df_inv = get_inventory_df(in_stock_only=only_in_stock)
        st.caption(f"{len(df_inv)} item(s) will be included in the download.")
    with col_dl2:
        csv_bytes = df_inv.to_csv(index=False).encode("utf-8")
        from datetime import datetime as _dt
        fname = f"inventory_{'instock' if only_in_stock else 'all'}_{_dt.utcnow().strftime('%Y%m%d')}.csv"
        st.download_button(
            label="⬇️ Download CSV",
            data=csv_bytes,
            file_name=fname,
            mime="text/csv",
            use_container_width=True,
        )

    # Pending in-stock requests
    st.markdown("### Pending in-stock requests")
    reqs = list_requests(status="pending")
    if reqs:
        for rid, cname, qty, fname, sname, remail, status, created in reqs:
            with st.container(border=True):
                st.write(f"**[{rid}] {cname}** — requested: **{qty:g}**")
                st.write(f"Requester: {fname} {sname} ({remail}) • Created: {created} • Status: {status}")

                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("Approve & deduct stock", key=f"approve_{rid}"):
                        ok, msg = approve_request_and_adjust(rid)
                        if ok:
                            st.success(msg)
                        else:
                            st.error(msg)
                with c2:
                    if st.button("Reject", key=f"reject_{rid}"):
                        set_request_status(rid, "rejected")
                        st.info("Rejected.")
                with c3:
                    if st.button("Mark Fulfilled", key=f"fulfill_{rid}"):
                        # No additional stock change here; approval already deducted.
                        set_request_status(rid, "fulfilled")
                        st.success("Marked as fulfilled.")
    else:
        st.info("No pending in-stock requests.")

    # Pending purchase requests
    st.markdown("### Pending purchase requests")
    pending_purchases = list_purchase_requests(status="pending")
    if pending_purchases:
        for (pid, mname, cas, specs, amt, unit, pfname, psname, pmail, pcomments, pattach, pstatus, pcreated) in pending_purchases:
            with st.container(border=True):
                st.write(f"**[{pid}] {mname}** — {amt} {unit} • CAS: {cas}")
                st.write(f"Specifications: {specs}")
                st.write(f"Requester: {pfname} {psname} ({pmail}) • Created: {pcreated} • Status: {pstatus}")
                if pcomments:
                    st.caption(f"Comments: {pcomments}")
                if pattach:
                    st.caption(f"Attachment: {os.path.basename(pattach)}")

                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("Approve purchase", key=f"papprove_{pid}"):
                        set_purchase_request_status(pid, "approved")
                        st.success("Purchase request approved.")
                with c2:
                    if st.button("Reject purchase", key=f"preject_{pid}"):
                        set_purchase_request_status(pid, "rejected")
                        st.info("Purchase request rejected.")
                with c3:
                    if st.button("Mark Purchased", key=f"pfulfill_{pid}"):
                        set_purchase_request_status(pid, "purchased")
                        st.success("Marked as purchased.")
    else:
        st.info("No pending purchase requests.")

    # All purchase requests
    st.markdown("### All purchase requests")
    all_purchases = list_purchase_requests()
    if all_purchases:
        st.dataframe(
            [
                {
                    "ID": pid,
                    "Material": mname,
                    "CAS": cas,
                    "Specs": specs,
                    "Amount": amt,
                    "Unit": unit,
                    "First Name": pfname,
                    "Surname": psname,
                    "Email": pmail,
                    "Comments": pcomments,
                    "Attachment": os.path.basename(pattach) if pattach else "",
                    "Status": pstatus,
                    "Created": str(pcreated),
                }
                for (pid, mname, cas, specs, amt, unit, pfname, psname, pmail, pcomments, pattach, pstatus, pcreated)
                in all_purchases
            ],
            use_container_width=True,
        )
    else:
        st.info("No purchase requests yet.")
