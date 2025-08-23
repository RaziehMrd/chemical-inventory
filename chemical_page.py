# chemical_page.py
import os
import sqlite3
from contextlib import closing
from datetime import datetime

import pandas as pd
import streamlit as st

# =============================
# Config (no secrets file used)
# =============================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")  # change default if you like
DB_PATH = "lab_inventory.db"

# =============================
# DB helpers
# =============================
def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn, closing(conn.cursor()) as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS chemicals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            unit TEXT NOT NULL DEFAULT 'g',
            location TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chem_id INTEGER NOT NULL,
            requester_email TEXT NOT NULL,
            quantity REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            FOREIGN KEY (chem_id) REFERENCES chemicals(id)
        );
        """)

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def list_chemicals(search=""):
    with closing(get_conn()) as conn, closing(conn.cursor()) as cur:
        if search:
            cur.execute("""
                SELECT id, name, amount, unit, location
                FROM chemicals
                WHERE LOWER(name) LIKE ?
                ORDER BY name ASC
            """, (f"%{search.lower()}%",))
        else:
            cur.execute("""
                SELECT id, name, amount, unit, location
                FROM chemicals
                ORDER BY name ASC
            """)
        return cur.fetchall()

def add_chemical(name, amount, unit, location, notes=""):
    with closing(get_conn()) as conn, conn, closing(conn.cursor()) as cur:
        cur.execute("""
            INSERT OR IGNORE INTO chemicals (name, amount, unit, location, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (name.strip(), float(amount), unit.strip(), location.strip(), notes.strip()))
        # To overwrite existing entries instead of ignoring, replace the above with an UPSERT:
        # cur.execute("""
        # INSERT INTO chemicals (name, amount, unit, location, notes)
        # VALUES (?, ?, ?, ?, ?)
        # ON CONFLICT(name) DO UPDATE SET amount=excluded.amount, unit=excluded.unit,
        # location=excluded.location, notes=excluded.notes
        # """, (name.strip(), float(amount), unit.strip(), location.strip(), notes.strip()))

def update_stock(chem_id, delta_amount):
    with closing(get_conn()) as conn, conn, closing(conn.cursor()) as cur:
        cur.execute("UPDATE chemicals SET amount = amount + ? WHERE id = ?", (float(delta_amount), int(chem_id)))

def get_chemical(chem_id):
    with closing(get_conn()) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT id, name, amount, unit, location, notes FROM chemicals WHERE id = ?", (int(chem_id),))
        return cur.fetchone()

def add_request(chem_id, email, qty):
    now = datetime.utcnow().isoformat(timespec="seconds")
    with closing(get_conn()) as conn, conn, closing(conn.cursor()) as cur:
        cur.execute("""
            INSERT INTO requests (chem_id, requester_email, quantity, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
        """, (int(chem_id), email.strip(), float(qty), now))

def list_requests(status=None):
    with closing(get_conn()) as conn, closing(conn.cursor()) as cur:
        if status:
            cur.execute("""
                SELECT r.id, c.name, r.quantity, r.requester_email, r.status, r.created_at
                FROM requests r JOIN chemicals c ON r.chem_id = c.id
                WHERE r.status = ?
                ORDER BY r.id DESC
            """, (status,))
        else:
            cur.execute("""
                SELECT r.id, c.name, r.quantity, r.requester_email, r.status, r.created_at
                FROM requests r JOIN chemicals c ON r.chem_id = c.id
                ORDER BY r.id DESC
            """)
        return cur.fetchall()

def set_request_status(req_id, new_status):
    with closing(get_conn()) as conn, conn, closing(conn.cursor()) as cur:
        cur.execute("UPDATE requests SET status = ? WHERE id = ?", (new_status, int(req_id)))

# =============================
# UI
# =============================
st.set_page_config(page_title="Lab Chemicals", page_icon="🧪", layout="wide")
st.title("🧪 Lab Chemical Inventory")

init_db()

tabs = st.tabs(["Search & Request", "Admin"])

# -----------------------------
# Tab 1: Search & Request
# -----------------------------
with tabs[0]:
    st.subheader("Search inventory")
    q = st.text_input("Search by chemical name", placeholder="e.g., acetone, ethanol, NaCl")
    data = list_chemicals(q)

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
            email = st.text_input("Your email")
            submitted = st.form_submit_button("Submit request")

        if submitted:
            if qty <= 0:
                st.error("Quantity must be > 0.")
            elif "@" not in email or "." not in email:
                st.error("Please enter a valid email.")
            else:
                chem_id = chem_options[chosen]
                add_request(chem_id, email, qty)
                st.success("Request submitted! The lab admin will review it.")

# -----------------------------
# Tab 2: Admin
# -----------------------------
with tabs[1]:
    st.subheader("Admin")
    pw = st.text_input("Admin password", type="password", placeholder="Enter admin password")
    if pw != ADMIN_PASSWORD:
        st.warning("Enter the correct password to manage inventory.")
        st.stop()

    st.success("Admin mode enabled.")

    # Add new chemical
    with st.expander("➕ Add a new chemical", expanded=False):
        with st.form("add_chem"):
            col1, col2, col3 = st.columns(3)
            with col1:
                name = st.text_input("Chemical name")
                unit = st.text_input("Unit", value="g")
            with col2:
                amount = st.number_input("Initial amount", min_value=0.0, step=0.1)
            with col3:
                location = st.text_input("Location", value="")
            notes = st.text_area("Notes (optional)", height=80)
            add_btn = st.form_submit_button("Add / Save")
        if add_btn:
            if not name.strip():
                st.error("Name is required.")
            else:
                try:
                    add_chemical(name, amount, unit, location, notes)
                    st.success(f"Added “{name}”. If it already existed, the insert is ignored (see code comment to use UPSERT).")
                except Exception as e:
                    st.error(f"Error adding chemical: {e}")

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
                                add_chemical(
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
        for rid, cname, qty, remail, status, created in reqs:
            with st.container(border=True):
                st.write(f"**[{rid}] {cname}** — requested: **{qty}**")
                st.write(f"Requester: {remail} • Created: {created} • Status: {status}")

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
                    "Requester": remail,
                    "Status": status,
                    "Created": created,
                }
                for rid, cname, qty, remail, status, created in all_reqs
            ],
            use_container_width=True,
        )
    else:
        st.info("No requests yet.")
