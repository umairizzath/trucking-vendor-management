import base64
import mimetypes
import re
import html
import uuid
import sqlite3
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "trucking_vendors.db"
CSV_PATH = APP_DIR / "OTR Trucker Information(1).csv"
DOCUMENTS_DIR = APP_DIR / "carrier_documents"
LOGO_PATHS = [APP_DIR / "Linkedin Logo.jfif", APP_DIR / "Linkedin Logo.jfif.jfif", APP_DIR / "logo.jfif", APP_DIR / "logo.jpg", APP_DIR / "logo.png"]

STATE_COLUMNS = [
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC"
]

BOOLEAN_COLUMNS = ["BONDED & FTZ", "LOCAL", "DIRECT DRAY", "OTR"]

DISPLAY_COLUMNS = [
    "VENDOR INFO", "SCAC", "MC#", "USDOT", "Insurance", "Website", "CONTACT/TITLE",
    "PHONE", "MOBILE ", "EMAIL ", "TRK BASE STATE ", "BASE ZIP",
    "BONDED & FTZ", "LOCAL", "DIRECT DRAY", "OTR", "OTHER", "All/Nationwide"
]

DOCUMENT_TYPES = ["Insurance Certificate", "W9", "Agreement", "Authority", "Other"]






@st.cache_resource
def get_runtime_token():
    """A temporary token that survives browser refreshes while the Streamlit server is running."""
    return uuid.uuid4().hex


def get_logo_html(width=74, refresh_link=False):
    """Return logo HTML. In the app header, the logo refreshes the same tab without logging out."""
    logo_path = next((p for p in LOGO_PATHS if p.exists()), None)
    href = "#"
    if refresh_link:
        href = f"?auth={get_runtime_token()}&refresh={uuid.uuid4().hex[:8]}"
    if logo_path:
        encoded = base64.b64encode(logo_path.read_bytes()).decode("utf-8")
        ext = logo_path.suffix.lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        return (
            f'<a class="awl-logo-link" title="Refresh Portal" href="{href}" target="_self">'
            f'<img src="data:{mime};base64,{encoded}" alt="Ancora Logo" />'
            f'</a>'
        )
    return f'<a class="awl-logo-link" title="Refresh Portal" href="{href}" target="_self"><div class="awl-logo-fallback">AWL</div></a>'

def refresh_data():
    st.cache_data.clear()
    st.rerun()

def clean_value(value):
    if pd.isna(value):
        return ""
    if isinstance(value, bool):
        return "Y" if value else ""
    return str(value).strip()

def load_csv():
    df = pd.read_csv(CSV_PATH, encoding="cp1252")
    if "Unnamed: 5" in df.columns:
        df = df.drop(columns=["Unnamed: 5"])
    for col in df.columns:
        df[col] = df[col].map(clean_value)
    return df

def db_exists():
    return DB_PATH.exists()

def get_connection():
    return sqlite3.connect(DB_PATH)

def ensure_support_tables():
    DOCUMENTS_DIR.mkdir(exist_ok=True)
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            vendor_info TEXT,
            timestamp TEXT NOT NULL,
            details TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS carrier_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            carrier_rowid INTEGER NOT NULL,
            vendor_info TEXT,
            document_type TEXT,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            notes TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deleted_carriers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_rowid INTEGER,
            deleted_at TEXT NOT NULL,
            vendor_info TEXT,
            row_data TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def init_db_if_missing():
    if not db_exists():
        conn = sqlite3.connect(DB_PATH)
        df = load_csv()
        df.to_sql("carriers", conn, if_exists="replace", index=False)
        conn.commit()
        conn.close()
    ensure_support_tables()

def read_carriers():
    conn = get_connection()
    df = pd.read_sql_query("SELECT rowid AS id, * FROM carriers", conn)
    conn.close()
    return sort_carriers_df(df)

def update_carrier(row_id, updates):
    conn = get_connection()
    set_clause = ", ".join([f'"{col}" = ?' for col in updates.keys()])
    values = list(updates.values()) + [row_id]
    conn.execute(f'UPDATE carriers SET {set_clause} WHERE rowid = ?', values)
    vendor = updates.get("VENDOR INFO", "")
    conn.execute(
        "INSERT INTO audit_log(action, vendor_info, timestamp, details) VALUES (?, ?, ?, ?)",
        ("UPDATE", vendor, datetime.now().isoformat(timespec="seconds"), str(updates))
    )
    conn.commit()
    conn.close()

def insert_carrier(values):
    conn = get_connection()
    columns = list(values.keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_clause = ", ".join([f'"{c}"' for c in columns])
    conn.execute(
        f'INSERT INTO carriers ({col_clause}) VALUES ({placeholders})',
        list(values.values())
    )
    conn.execute(
        "INSERT INTO audit_log(action, vendor_info, timestamp, details) VALUES (?, ?, ?, ?)",
        ("INSERT", values.get("VENDOR INFO", ""), datetime.now().isoformat(timespec="seconds"), str(values))
    )
    conn.commit()
    conn.close()

def delete_carrier(row_id, vendor_info):
    conn = get_connection()
    row = pd.read_sql_query("SELECT * FROM carriers WHERE rowid = ?", conn, params=(row_id,))
    if not row.empty:
        row_json = row.iloc[0].to_json()
        conn.execute(
            "INSERT INTO deleted_carriers(source_rowid, deleted_at, vendor_info, row_data) VALUES (?, ?, ?, ?)",
            (row_id, datetime.now().isoformat(timespec="seconds"), vendor_info, row_json)
        )
    conn.execute("DELETE FROM carriers WHERE rowid = ?", (row_id,))
    conn.execute(
        "INSERT INTO audit_log(action, vendor_info, timestamp, details) VALUES (?, ?, ?, ?)",
        ("DELETE", vendor_info, datetime.now().isoformat(timespec="seconds"), f"Deleted carrier rowid {row_id}")
    )
    conn.commit()
    conn.close()


def list_deleted_carriers():
    conn = get_connection()
    try:
        deleted = pd.read_sql_query(
            "SELECT id, source_rowid, deleted_at, vendor_info, row_data FROM deleted_carriers ORDER BY deleted_at DESC",
            conn
        )
    except Exception:
        deleted = pd.DataFrame(columns=["id", "source_rowid", "deleted_at", "vendor_info", "row_data"])
    conn.close()
    return deleted

def restore_deleted_carrier(deleted_id):
    conn = get_connection()
    deleted = pd.read_sql_query(
        "SELECT * FROM deleted_carriers WHERE id = ?",
        conn,
        params=(deleted_id,)
    )
    if deleted.empty:
        conn.close()
        return False

    row_data = pd.read_json(deleted.iloc[0]["row_data"], typ="series").to_dict()
    columns = list(row_data.keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_clause = ", ".join([f'"{c}"' for c in columns])
    conn.execute(
        f'INSERT INTO carriers ({col_clause}) VALUES ({placeholders})',
        list(row_data.values())
    )

    vendor_info = deleted.iloc[0]["vendor_info"]
    conn.execute("DELETE FROM deleted_carriers WHERE id = ?", (deleted_id,))
    conn.execute(
        "INSERT INTO audit_log(action, vendor_info, timestamp, details) VALUES (?, ?, ?, ?)",
        ("RESTORE", vendor_info, datetime.now().isoformat(timespec="seconds"), f"Restored deleted carrier id {deleted_id}")
    )
    conn.commit()
    conn.close()
    return True

def search_filter(df, query):
    if not query:
        return df
    query = query.lower().strip()
    searchable = df.astype(str).apply(lambda row: " ".join(row.values).lower(), axis=1)
    return df[searchable.str.contains(query, na=False)]

def yes_filter(df, column, selected=True):
    if not selected or column not in df.columns:
        return df
    return df[df[column].astype(str).str.upper().isin(["Y", "YES", "TRUE", "1"])]

def base_state_filter(df, base_state):
    if not base_state or "TRK BASE STATE " not in df.columns:
        return df
    return df[df["TRK BASE STATE "].astype(str).str.strip().str.upper() == base_state.upper()]


def normalize_vendor_name(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()

def vendor_exists(df, vendor_name, exclude_id=None):
    normalized = normalize_vendor_name(vendor_name)
    if not normalized or "VENDOR INFO" not in df.columns:
        return False
    check_df = df.copy()
    if exclude_id is not None and "id" in check_df.columns:
        check_df = check_df[check_df["id"] != exclude_id]
    return check_df["VENDOR INFO"].map(normalize_vendor_name).eq(normalized).any()

def insurance_expired(value):
    if not str(value).strip():
        return False

    date_value = pd.to_datetime(value, errors="coerce")

    if pd.isna(date_value):
        return False

    return date_value.date() < datetime.today().date()


def format_mmddyyyy(value):
    value = str(value or "").strip()
    if not value:
        return ""
    date_value = pd.to_datetime(value, errors="coerce")
    if pd.isna(date_value):
        return value
    return date_value.strftime("%m/%d/%Y")

def parse_insurance_date(value):
    value = str(value or "").strip()
    date_value = pd.to_datetime(value, errors="coerce")
    if pd.isna(date_value):
        return datetime.today().date()
    return date_value.date()

def get_data_quality_checks():
    return {
        "Missing Website": "Website",
        "Missing Insurance": "Insurance",
        "Missing MC#": "MC#",
        "Missing USDOT": "USDOT",
        "Missing Contact/Title": "CONTACT/TITLE",
        "Missing Phone": "PHONE",
        "Missing Email": "EMAIL ",
        "Missing Base State": "TRK BASE STATE ",
        "Missing Base ZIP": "BASE ZIP",
    }

def vendors_for_quality_issue(df, issue_label):
    checks = get_data_quality_checks()
    col = checks.get(issue_label)
    if not col or col not in df.columns:
        return df.iloc[0:0].copy()
    issue_df = df[df[col].astype(str).str.strip() == ""].copy()
    return sort_carriers_df(issue_df)

def render_edit_form_for_record(form_key, selected_id, record, df, allow_duplicate_name=False):
    with st.form(form_key):
        updates = {}
        existing_states = [state for state in STATE_COLUMNS if truthy(record.get(state, ""))]
        existing_nationwide = truthy(record.get("All/Nationwide", ""))

        for col in df.columns:
            if col == "id" or col in STATE_COLUMNS or col == "All/Nationwide":
                continue

            if col in BOOLEAN_COLUMNS:
                updates[col] = "Y" if st.checkbox(col, value=truthy(record.get(col, "")), key=f"{form_key}_{col}") else ""
            elif col in ["COMMENTS", "CONTACT/TITLE", "PHONE", "MOBILE ", "EMAIL ", "OTHER"]:
                updates[col] = st.text_area(col, value=str(record.get(col, "")), height=90, key=f"{form_key}_{col}")
            elif col == "Insurance":
                insurance_date = st.date_input(
                    "Insurance Expiration Date",
                    value=parse_insurance_date(record.get(col, "")),
                    key=f"{form_key}_{col}"
                )
                updates[col] = insurance_date.strftime("%m/%d/%Y")
            else:
                updates[col] = st.text_input(
                    col,
                    value=str(record.get(col, "")),
                    key=f"{form_key}_{col}"
                )

        st.markdown("### Coverage")
        updated_nationwide = st.checkbox(
            "All/Nationwide",
            value=existing_nationwide,
            key=f"{form_key}_nationwide_{selected_id}"
        )
        updated_states = st.multiselect(
            "State Coverage",
            STATE_COLUMNS,
            default=existing_states,
            key=f"{form_key}_states_{selected_id}",
            help="Type to search and select all states this carrier covers."
        )

        updates["All/Nationwide"] = "Y" if updated_nationwide else ""
        for state in STATE_COLUMNS:
            updates[state] = "Y" if state in updated_states else ""

        submitted = st.form_submit_button("Save Changes")
        if submitted:
            vendor_name = str(updates.get("VENDOR INFO", "")).strip()
            if not vendor_name:
                st.error("Carrier Name / Vendor Info is required.")
                return False
            if not allow_duplicate_name and vendor_exists(df, vendor_name, exclude_id=selected_id):
                st.error("A carrier with this Vendor Info already exists. Changes were not saved.")
                return False
            update_carrier(selected_id, updates)
            st.success("Carrier updated. Refresh the page to see changes.")
            return True
    return False


def missing_dashboard(df):
    checks = get_data_quality_checks()
    results = []
    for label, col in checks.items():
        if col in df.columns:
            missing = (df[col].astype(str).str.strip() == "").sum()
            results.append({"Issue": label, "Count": int(missing)})
    return pd.DataFrame(results)




def apply_branding():
    st.markdown("""
    <style>
        :root {
            --awl-navy: #10233F;
            --awl-blue: #184E77;
            --awl-cyan: #16A3B8;
            --awl-bg: #F5F7FB;
            --awl-card: #FFFFFF;
            --awl-border: #E4E9F2;
            --awl-muted: #667085;
            --awl-green: #12B76A;
            --awl-orange: #F79009;
        }

        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}

        .stApp {
            background: linear-gradient(180deg, #F7FAFF 0%, #EEF3FA 45%, #F7F9FC 100%);
        }

        [data-testid="stSidebar"] {
            background: #0F223D;
        }

        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            color: #FFFFFF !important;
        }

        /* Plain, stable password/input styling */
        [data-testid="stSidebar"] div[data-testid="stTextInput"] input,
        div[data-testid="stTextInput"] input,
        div[data-testid="stTextArea"] textarea {
            background-color: #FFFFFF !important;
            color: #111827 !important;
            border: 1px solid #B8C5D8 !important;
            border-radius: 8px !important;
            box-shadow: none !important;
            outline: none !important;
        }

        [data-testid="stSidebar"] div[data-testid="stTextInput"] input:focus,
        div[data-testid="stTextInput"] input:focus,
        div[data-testid="stTextArea"] textarea:focus {
            border: 1px solid #184E77 !important;
            box-shadow: none !important;
            outline: none !important;
        }

        div[data-baseweb="select"] > div {
            background-color: #FFFFFF !important;
            color: #111827 !important;
            border: 1px solid #B8C5D8 !important;
            border-radius: 8px !important;
            box-shadow: none !important;
        }

        div[data-baseweb="select"] span {
            color: #111827 !important;
        }

        .block-container {
            padding-top: 1.3rem;
            padding-bottom: 3rem;
            max-width: 1500px;
        }

        .awl-hero {
            background: linear-gradient(135deg, #0F223D 0%, #184E77 52%, #16A3B8 100%);
            border-radius: 22px;
            padding: 28px 32px;
            color: white;
            box-shadow: 0 18px 45px rgba(16, 35, 63, 0.22);
            margin-bottom: 22px;
        }

        .awl-hero-grid {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
        }

        .awl-logo-button {
            appearance: none;
            -webkit-appearance: none;
            border: none;
            padding: 0;
            margin: 0;
            width: 74px;
            height: 74px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: white;
            border: 1px solid rgba(255,255,255,0.5);
            box-shadow: 0 4px 14px rgba(0,0,0,0.22);
            overflow: hidden;
            text-decoration: none !important;
            cursor: pointer;
        }

        .awl-logo-button:hover {
            transform: scale(1.025);
            transition: 120ms ease;
        }

        .awl-logo-button img {
            width: 74px;
            height: 74px;
            object-fit: cover;
            display: block;
        }

        .awl-logo-fallback {
            width: 74px;
            height: 74px;
            border-radius: 50%;
            background: rgba(255,255,255,0.18);
            border: 1px solid rgba(255,255,255,0.45);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 22px;
            font-weight: 800;
            color: white;
        }

        .awl-title {
            font-size: 32px;
            font-weight: 800;
            margin: 0;
            letter-spacing: -0.5px;
        }

        .awl-subtitle {
            font-size: 15px;
            margin-top: 6px;
            opacity: 0.88;
        }

        .awl-company {
            text-align: right;
            font-size: 13px;
            opacity: 0.9;
            line-height: 1.6;
        }

        .awl-card {
            background: var(--awl-card);
            border: 1px solid var(--awl-border);
            border-radius: 12px;
            padding: 14px 16px;
            box-shadow: 0 6px 16px rgba(16, 35, 63, 0.055);
            margin-bottom: 12px;
        }

        .awl-card h3 {
            margin: 0 0 8px 0;
            color: #10233F;
            font-size: 18px;
        }

        .awl-card .muted {
            color: var(--awl-muted);
            font-size: 13px;
        }

        .awl-kpi {
            background: white;
            border: 1px solid var(--awl-border);
            border-radius: 12px;
            padding: 12px 14px;
            box-shadow: 0 5px 14px rgba(16, 35, 63, 0.055);
            min-height: 74px;
        }

        .awl-kpi-label {
            color: #667085;
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: .04em;
        }

        .awl-kpi-value {
            color: #10233F;
            font-size: 24px;
            font-weight: 800;
            line-height: 1.1;
            margin-top: 5px;
        }

        .awl-chip {
            display: inline-block;
            padding: 6px 10px;
            border-radius: 999px;
            background: #EAF3FF;
            color: #184E77;
            border: 1px solid #CFE5FF;
            margin: 4px 4px 4px 0;
            font-size: 12px;
            font-weight: 700;
        }

        .awl-chip-green {
            background:#ECFDF3;
            color:#027A48;
            border-color:#ABEFC6;
        }

        .awl-chip-orange {
            background:#FFFAEB;
            color:#B54708;
            border-color:#FEDF89;
        }

        .awl-section-title {
            font-size: 20px;
            color:#10233F;
            font-weight:800;
            margin: 12px 0 10px 0;
        }

        .awl-field-label {
            color:#667085;
            font-size:12px;
            text-transform:uppercase;
            letter-spacing:.04em;
            font-weight:800;
        }

        .awl-field-value {
            color:#101828;
            font-size:15px;
            margin-bottom:12px;
            word-break:break-word;
        }

        div[data-testid="stMetric"] {
            background: white;
            border: 1px solid var(--awl-border);
            border-radius: 14px;
            padding: 12px 14px;
            box-shadow: 0 8px 22px rgba(16,35,63,.06);
        }

        /* Remove Streamlit's default red tab underline/highlight */
        .stTabs [data-baseweb="tab-highlight"],
        div[data-baseweb="tab-highlight"],
        [data-testid="stTabs"] div[data-baseweb="tab-highlight"] {
            display: none !important;
            background-color: transparent !important;
            height: 0 !important;
        }

        .stTabs [data-baseweb="tab-border"],
        div[data-baseweb="tab-border"],
        [data-testid="stTabs"] div[data-baseweb="tab-border"] {
            display: none !important;
            background-color: transparent !important;
            height: 0 !important;
        }

        .stTabs [role="tab"],
        button[role="tab"] {
            border-bottom: 0 !important;
            box-shadow: none !important;
            outline: none !important;
        }

        .stTabs [role="tab"][aria-selected="true"],
        button[role="tab"][aria-selected="true"] {
            background: #10233F !important;
            color: white !important;
            border-bottom: 0 !important;
            box-shadow: none !important;
            outline: none !important;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 10px;
            border-bottom: 1px solid #D8E0EC;
        }

        .stTabs [data-baseweb="tab"] {
            background: white;
            border-radius: 999px;
            border: 1px solid var(--awl-border);
            padding: 8px 16px;
            color: #10233F;
        }

        div.stButton > button,
        div.stDownloadButton > button {
            border-radius: 9px;
            border: 1px solid #184E77;
            background: #184E77;
            color: white;
            font-weight: 700;
            padding: 0.38rem 0.72rem;
            font-size: 12px;
        }

        div.stButton > button:hover,
        div.stDownloadButton > button:hover {
            background: #10233F;
            color: white;
            border-color: #10233F;
        }

        [data-testid="stDataFrame"] {
            border-radius: 14px;
            overflow: hidden;
            box-shadow: 0 8px 22px rgba(16,35,63,.06);
        }

        .awl-detail-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px 18px;
            margin-top: 10px;
        }

        .awl-detail-grid.one {
            grid-template-columns: 1fr;
        }

        .awl-detail-item {
            padding: 5px 0;
            border-bottom: 1px solid #EEF2F7;
            min-height: 36px;
        }

        .awl-card-tight {
            margin-bottom: 10px;
        }

        div[data-testid="stHorizontalBlock"] {
            gap: 0.9rem;
        }
    
        .awl-logo-button {
            font: inherit !important;
            cursor: pointer !important;
        }

    
        /* Final fixes: logo visibility, plain login input, no red tab line */
        .awl-logo-link {
            width: 74px !important;
            height: 74px !important;
            border-radius: 50% !important;
            display: inline-flex !important;
            align-items: center !important;
            justify-content: center !important;
            background: #ffffff !important;
            border: 1px solid rgba(255,255,255,0.65) !important;
            box-shadow: 0 4px 14px rgba(0,0,0,0.22) !important;
            overflow: hidden !important;
            text-decoration: none !important;
            cursor: pointer !important;
            padding: 0 !important;
            margin: 0 !important;
        }
        .awl-logo-link img {
            width: 74px !important;
            height: 74px !important;
            object-fit: cover !important;
            display: block !important;
        }
        [data-testid="stSidebar"] input[type="password"],
        input[type="password"] {
            background: #ffffff !important;
            color: #111827 !important;
            border: 1px solid #B8C5D8 !important;
            border-radius: 8px !important;
            box-shadow: none !important;
            outline: none !important;
        }
        [data-testid="stSidebar"] input[type="password"]:focus,
        input[type="password"]:focus {
            border: 1px solid #184E77 !important;
            box-shadow: none !important;
            outline: none !important;
        }
        div[data-baseweb="tab-highlight"],
        div[data-baseweb="tab-border"],
        [data-testid="stTabs"] div[data-baseweb="tab-highlight"],
        [data-testid="stTabs"] div[data-baseweb="tab-border"] {
            display: none !important;
            background: transparent !important;
            height: 0 !important;
        }
        button[role="tab"] {
            border-bottom: 0 !important;
            box-shadow: none !important;
        }

    </style>
    """, unsafe_allow_html=True)


def render_header(total_carriers=None):
    total_html = "" if total_carriers is None else f"<br><strong>{total_carriers}</strong> carriers loaded"
    logo_html = get_logo_html(74, refresh_link=True)
    st.markdown(f"""
    <div class="awl-hero">
      <div class="awl-hero-grid">
        <div style="display:flex;align-items:center;gap:18px;">
          {logo_html}
          <div>
            <div class="awl-title">Trucking Vendor Management</div>
            <div class="awl-subtitle">Carrier search, vendor profiles, service coverage, documents, and data quality tracking.</div>
          </div>
        </div>
        <div class="awl-company">
          <strong>Ancora Warehousing & Logistics, LLC</strong><br>
          Internal logistics operations dashboard{total_html}
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

def render_kpi_card(label, value):
    st.markdown(f"""
    <div class="awl-kpi">
      <div class="awl-kpi-label">{label}</div>
      <div class="awl-kpi-value">{value}</div>
    </div>
    """, unsafe_allow_html=True)

def render_field(label, value):
    value = "" if value is None else str(value)
    if not value.strip():
        value = "-"
    st.markdown(f"""
    <div class="awl-field-label">{label}</div>
    <div class="awl-field-value">{value}</div>
    """, unsafe_allow_html=True)


def esc(value):
    value = "" if value is None else str(value)
    return html.escape(value.strip() or "-")

def render_info_card(title, fields, columns=2):
    grid_class = "awl-detail-grid one" if columns == 1 else "awl-detail-grid"
    items = []
    for label, value in fields:
        items.append(
            f'<div class="awl-detail-item"><div class="awl-field-label">{html.escape(str(label))}</div>'
            f'<div class="awl-field-value">{esc(value)}</div></div>'
        )
    st.markdown(
        f'<div class="awl-card awl-card-tight"><h3>{html.escape(str(title))}</h3>'
        f'<div class="{grid_class}">{"".join(items)}</div></div>',
        unsafe_allow_html=True
    )

def render_chip_card(title, chips_html, empty_text="None marked."):
    body = chips_html if chips_html else f'<div class="muted">{html.escape(empty_text)}</div>'
    st.markdown(
        f'<div class="awl-card awl-card-tight"><h3>{html.escape(str(title))}</h3><div>{body}</div></div>',
        unsafe_allow_html=True
    )

def truthy(value):
    return str(value).strip().upper() in ["Y", "YES", "TRUE", "1"]

def safe_filename(name):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("_")
    return cleaned or "carrier"

def list_documents(carrier_rowid=None):
    conn = get_connection()
    if carrier_rowid is None:
        docs = pd.read_sql_query(
            "SELECT * FROM carrier_documents ORDER BY uploaded_at DESC",
            conn
        )
    else:
        docs = pd.read_sql_query(
            "SELECT * FROM carrier_documents WHERE carrier_rowid = ? ORDER BY uploaded_at DESC",
            conn,
            params=(carrier_rowid,)
        )
    conn.close()
    return docs

def save_document(carrier_rowid, vendor_info, document_type, uploaded_file, notes):
    carrier_folder = DOCUMENTS_DIR / f"{carrier_rowid}_{safe_filename(vendor_info)}"
    carrier_folder.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    original_name = uploaded_file.name
    stored_name = f"{timestamp}_{safe_filename(original_name)}"
    stored_path = carrier_folder / stored_name

    stored_path.write_bytes(uploaded_file.getbuffer())

    conn = get_connection()
    conn.execute("""
        INSERT INTO carrier_documents
        (carrier_rowid, vendor_info, document_type, original_filename, stored_filename, stored_path, uploaded_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        carrier_rowid,
        vendor_info,
        document_type,
        original_name,
        stored_name,
        str(stored_path),
        datetime.now().isoformat(timespec="seconds"),
        notes
    ))
    conn.execute(
        "INSERT INTO audit_log(action, vendor_info, timestamp, details) VALUES (?, ?, ?, ?)",
        ("DOCUMENT_UPLOAD", vendor_info, datetime.now().isoformat(timespec="seconds"), original_name)
    )
    conn.commit()
    conn.close()


def render_document_viewer(docs, key_prefix):
    if docs.empty:
        st.info("No documents uploaded for this carrier.")
        return

    display_docs = docs.copy()
    display_docs["label"] = (
        display_docs["document_type"].fillna("").astype(str)
        + " - "
        + display_docs["original_filename"].fillna("").astype(str)
        + " - "
        + display_docs["uploaded_at"].fillna("").astype(str)
    )

    selected_label = st.selectbox(
        "Select a document to preview or download",
        display_docs["label"].tolist(),
        key=f"{key_prefix}_document_select"
    )
    selected_doc = display_docs[display_docs["label"] == selected_label].iloc[0]
    file_path = Path(str(selected_doc["stored_path"]))

    st.write(f"**Document Type:** {selected_doc.get('document_type', '')}")
    st.write(f"**File:** {selected_doc.get('original_filename', '')}")
    st.write(f"**Uploaded:** {selected_doc.get('uploaded_at', '')}")
    notes = str(selected_doc.get("notes", "")).strip()
    if notes:
        st.write(f"**Notes:** {notes}")

    if not file_path.exists():
        st.error("The stored file was not found on disk. It may have been moved or deleted.")
        return

    file_bytes = file_path.read_bytes()
    original_filename = str(selected_doc.get("original_filename", file_path.name))
    mime_type, _ = mimetypes.guess_type(original_filename)
    mime_type = mime_type or "application/octet-stream"

    st.download_button(
        "Download / Open Document",
        data=file_bytes,
        file_name=original_filename,
        mime=mime_type,
        key=f"{key_prefix}_download_{selected_doc['id']}"
    )

    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        pdf_base64 = base64.b64encode(file_bytes).decode("utf-8")
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{pdf_base64}" width="100%" height="700" type="application/pdf"></iframe>',
            unsafe_allow_html=True
        )
    elif suffix in [".png", ".jpg", ".jpeg"]:
        st.image(file_bytes, caption=original_filename, use_container_width=True)
    else:
        st.info("Preview is available for PDF and image files. Use Download / Open Document for Word, Excel, or other file types.")

def sort_carriers_df(df):
    if "VENDOR INFO" in df.columns:
        return df.sort_values("VENDOR INFO", key=lambda s: s.astype(str).str.lower()).reset_index(drop=True)
    return df

def get_carrier_options(df):
    carrier_options = df[["id", "VENDOR INFO"]].copy()
    carrier_options["VENDOR INFO"] = carrier_options["VENDOR INFO"].astype(str)
    carrier_options = carrier_options.sort_values("VENDOR INFO", key=lambda s: s.str.lower()).reset_index(drop=True)

    duplicate_names = carrier_options["VENDOR INFO"].duplicated(keep=False)
    carrier_options["label"] = carrier_options["VENDOR INFO"].astype(str)

    if duplicate_names.any():
        for idx in carrier_options[duplicate_names].index:
            row_id = carrier_options.at[idx, "id"]
            source_row = df[df["id"] == row_id].iloc[0]
            scac = str(source_row.get("SCAC", "")).strip()
            mc = str(source_row.get("MC#", "")).strip()
            suffix_parts = []
            if scac:
                suffix_parts.append(f"SCAC {scac}")
            if mc:
                suffix_parts.append(f"MC {mc}")
            suffix = " / ".join(suffix_parts)
            if suffix:
                carrier_options.at[idx, "label"] = f"{carrier_options.at[idx, 'VENDOR INFO']} ({suffix})"

    return carrier_options

def get_record_by_label(df, carrier_options, selected_label):
    selected_id = int(carrier_options.loc[carrier_options["label"] == selected_label, "id"].iloc[0])
    record = df[df["id"] == selected_id].iloc[0]
    return selected_id, record

def show_vendor_detail(record, selected_id):
    vendor_name = record.get("VENDOR INFO", "")
    st.markdown(
        f"<div class='awl-card'><h3>{esc(vendor_name)}</h3>"
        "<div class='muted'>Complete carrier profile and supporting documents</div></div>",
        unsafe_allow_html=True
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi_card("SCAC", record.get("SCAC", "-") or "-")
    with c2:
        render_kpi_card("MC#", record.get("MC#", "-") or "-")
    with c3:
        render_kpi_card("USDOT", record.get("USDOT", "-") or "-")
    with c4:
        render_kpi_card("Base State", record.get("TRK BASE STATE ", "-") or "-")

    left, right = st.columns([1.1, 0.9])
    with left:
        render_info_card(
            "Company Information",
            [
                ("Address 1", record.get("Address 1", "")),
                ("City / Base State", f"{record.get('City', '')} {record.get('TRK BASE STATE ', '')}".strip()),
                ("Base ZIP", record.get("BASE ZIP", "")),
                ("Insurance", format_mmddyyyy(record.get("Insurance", ""))),
                ("Website", record.get("Website", "")),
                ("Address 2", record.get("Address 2", "")),
            ],
            columns=2,
        )

        render_info_card(
            "Contact Information",
            [
                ("Contact / Title", record.get("CONTACT/TITLE", "")),
                ("Phone", record.get("PHONE", "")),
                ("Mobile", record.get("MOBILE ", "")),
                ("Email", record.get("EMAIL ", "")),
            ],
            columns=2,
        )

    with right:
        service_cols = ["BONDED & FTZ", "LOCAL", "DIRECT DRAY", "OTR", "All/Nationwide"]
        chips = []
        for col in service_cols:
            if truthy(record.get(col, "")):
                chips.append(f"<span class='awl-chip awl-chip-green'>{html.escape(col)}</span>")
        other = str(record.get("OTHER", "")).strip()
        if other:
            chips.append(f"<span class='awl-chip awl-chip-orange'>OTHER: {html.escape(other)}</span>")
        render_chip_card("Services", "".join(chips), "No services marked.")

        covered_states = [state for state in STATE_COLUMNS if truthy(record.get(state, ""))]
        state_chips = "".join([f"<span class='awl-chip'>{html.escape(s)}</span>" for s in covered_states])
        render_chip_card("State Coverage", state_chips, "No state coverage marked.")

    st.markdown("<div class='awl-card awl-card-tight'><h3>Documents</h3>", unsafe_allow_html=True)
    docs = list_documents(selected_id)
    render_document_viewer(docs, key_prefix=f"detail_{selected_id}")
    st.markdown("</div>", unsafe_allow_html=True)

    comments = str(record.get("COMMENTS", "")).strip()
    render_info_card("Comments", [("Comments", comments if comments else "No comments entered.")], columns=1)


def is_logged_in():
    token_from_url = st.query_params.get("auth", "")
    if st.session_state.get("logged_in", False):
        return True
    if token_from_url == get_runtime_token():
        st.session_state.logged_in = True
        return True
    return False

def set_logged_in():
    st.session_state.logged_in = True
    st.query_params["auth"] = get_runtime_token()

st.set_page_config(
    page_title="Trucking Vendor Management",
    page_icon="🚛",
    layout="wide",
    initial_sidebar_state="expanded"
)
apply_branding()





with st.sidebar:
    if not is_logged_in():
        side_logo_html = get_logo_html(64, refresh_link=False)
        st.markdown(
            f"""
            <div style="text-align:center;margin-top:0.3rem;margin-bottom:1.1rem;">
                <div style="display:flex;justify-content:center;margin-bottom:0.75rem;">{side_logo_html}</div>
                <div style="font-size:1.25rem;font-weight:850;color:#FFFFFF;line-height:1.15;">
                    Ancora Warehousing & Logistics
                </div>
                <div style="font-size:0.82rem;color:#AFC0D5;margin-top:0.35rem;">
                    Internal Trucking Vendor Management Portal
                </div>
            </div>
            <div style="background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.12);
                        border-radius:12px;padding:0.9rem;margin-bottom:1rem;color:#D8E3F2;">
                <div style="font-weight:800;color:#FFFFFF;margin-bottom:0.35rem;">Secure Internal Login</div>
                <div style="font-size:0.82rem;line-height:1.45;">
                    Access carrier profiles, service coverage, contact information, insurance documents,
                    and data quality tracking for internal logistics operations.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        password = st.text_input("Password", type="password", key="login_password")
        if password == "admin123":
            set_logged_in()
            st.rerun()
        elif password:
            st.error("Incorrect password.")
        else:
            st.info("Enter password to continue.")
        st.stop()

    st.success("Logged in")

    if st.button("Logout", key="logout_button"):
        st.session_state.clear()
        st.query_params.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("### Quick Guide")
    st.write("Search carriers, open vendor profiles, manage documents, and export data.")


init_db_if_missing()
df = read_carriers()
render_header(len(df))

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "Carrier Search",
    "Vendor Detail",
    "Vendor Profile / Edit",
    "Add Carrier",
    "Carrier Documents",
    "Data Quality"
])

with tab1:
    st.markdown("<div class='awl-section-title'>Carrier Search</div>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        query = st.text_input("Global Search", placeholder="Carrier, SCAC, MC#, email, phone, state...")
    with c2:
        base_state = st.selectbox("Base State", [""] + STATE_COLUMNS)
    with c3:
        coverage_state = st.selectbox("State Coverage", [""] + STATE_COLUMNS)
    with c4:
        service = st.selectbox("Service Filter", ["", "LOCAL", "DIRECT DRAY", "OTR", "BONDED & FTZ"])

    c5, c6 = st.columns([1, 3])
    with c5:
        nationwide = st.checkbox("Nationwide only")

    filtered = search_filter(df, query)
    filtered = base_state_filter(filtered, base_state)

    if coverage_state:
        filtered = yes_filter(filtered, coverage_state, True)
    if service:
        filtered = yes_filter(filtered, service, True)
    if nationwide:
        filtered = filtered[
            filtered["All/Nationwide"].astype(str).str.upper().isin(["Y", "YES", "TRUE", "1"])
        ]

    filtered = sort_carriers_df(filtered)

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        render_kpi_card("Matching Carriers", len(filtered))
    with k2:
        render_kpi_card("Local", yes_filter(filtered, "LOCAL", True).shape[0])
    with k3:
        render_kpi_card("Direct Dray", yes_filter(filtered, "DIRECT DRAY", True).shape[0])
    with k4:
        render_kpi_card("OTR", yes_filter(filtered, "OTR", True).shape[0])

    cols = [c for c in DISPLAY_COLUMNS if c in filtered.columns]

    export_df = filtered[cols].copy()
    if "Insurance" in export_df.columns:
        export_df["Insurance"] = export_df["Insurance"].apply(format_mmddyyyy)
    csv_data = export_df.to_csv(index=False).encode("utf-8")
    export_col, spacer_col = st.columns([1.2, 5])
    with export_col:
        st.download_button(
            "Export CSV",
            data=csv_data,
            file_name="carrier_search_results.csv",
            mime="text/csv"
        )

    display_df = filtered[cols].copy()
    if "Insurance" in display_df.columns:
        display_df["Insurance"] = display_df["Insurance"].apply(format_mmddyyyy)

    def highlight_expired(row):
        if "Insurance" in row and insurance_expired(row["Insurance"]):
            return ["color: red; font-weight: bold;" if col == "Insurance" else "" for col in row.index]
        return ["" for _ in row.index]

    st.dataframe(
        display_df.style.apply(highlight_expired, axis=1),
        use_container_width=True,
        hide_index=True
    )

    if not filtered.empty:
        st.markdown("### Open Vendor From Search Results")
        filtered_options = get_carrier_options(filtered)
        selected_label = st.selectbox(
            "Select a vendor to view full details",
            filtered_options["label"].tolist(),
            key="search_detail_vendor"
        )
        selected_id, record = get_record_by_label(df, filtered_options, selected_label)
        with st.expander("Vendor Full Detail", expanded=False):
            show_vendor_detail(record, selected_id)


with tab2:
    st.markdown("<div class='awl-section-title'>Vendor Detail</div>", unsafe_allow_html=True)
    carrier_options = get_carrier_options(df)
    selected_label = st.selectbox("Select Vendor", carrier_options["label"].tolist(), key="detail_vendor")
    selected_id, record = get_record_by_label(df, carrier_options, selected_label)
    show_vendor_detail(record, selected_id)

with tab3:
    st.markdown("<div class='awl-section-title'>Vendor Profile / Edit</div>", unsafe_allow_html=True)
    carrier_options = get_carrier_options(df)
    selected_label = st.selectbox("Select Carrier", carrier_options["label"].tolist(), key="edit_vendor")
    selected_id, record_series = get_record_by_label(df, carrier_options, selected_label)
    record = record_series.to_dict()

    render_edit_form_for_record(f"edit_carrier_{selected_id}", selected_id, record, df)

    st.divider()
    st.subheader("Delete Carrier")
    st.warning("Deleting removes this carrier from the SQLite database. Export or back up your database first if needed.")
    confirm_delete = st.checkbox(f"I understand and want to delete {record.get('VENDOR INFO', '')}", key="confirm_delete")
    if st.button("Delete Selected Carrier", type="primary", disabled=not confirm_delete):
        delete_carrier(selected_id, record.get("VENDOR INFO", ""))
        st.success("Carrier deleted. Refresh the page to update the list.")
        st.stop()

    st.divider()
    st.subheader("Deleted Carrier Recovery")
    deleted_carriers = list_deleted_carriers()
    if deleted_carriers.empty:
        st.info("No deleted carriers available to restore.")
    else:
        deleted_carriers["label"] = (
            deleted_carriers["vendor_info"].fillna("").astype(str)
            + " — deleted "
            + deleted_carriers["deleted_at"].fillna("").astype(str)
        )
        restore_label = st.selectbox(
            "Select a deleted carrier to restore",
            deleted_carriers["label"].tolist(),
            key="restore_deleted_carrier_select"
        )
        restore_id = int(deleted_carriers.loc[deleted_carriers["label"] == restore_label, "id"].iloc[0])
        if st.button("Restore Selected Carrier", key="restore_deleted_carrier"):
            if restore_deleted_carrier(restore_id):
                st.success("Carrier restored. Refresh the page to update the list.")
                st.stop()
            else:
                st.error("Unable to restore carrier.")


with tab4:
    st.markdown("<div class='awl-section-title'>Add Carrier</div>", unsafe_allow_html=True)
    with st.form("add_carrier"):
        values = {}

        for col in [c for c in df.columns if c != "id"]:
            if col in STATE_COLUMNS or col == "All/Nationwide":
                continue

            if col in BOOLEAN_COLUMNS:
                values[col] = "Y" if st.checkbox(col, value=False, key=f"add_{col}") else ""
            elif col in ["COMMENTS", "CONTACT/TITLE", "PHONE", "MOBILE ", "EMAIL ", "OTHER"]:
                values[col] = st.text_area(col, height=90, key=f"add_{col}")
            else:
                required_label = " *" if col == "VENDOR INFO" else ""
                if col == "Insurance":
                    insurance_date = st.date_input(
                        "Insurance Expiration Date",
                        value=datetime.today().date(),
                        key=f"add_{col}"
                    )
                    values[col] = insurance_date.strftime("%m/%d/%Y")
                else:
                    values[col] = st.text_input(f"{col}{required_label}", key=f"add_{col}")

        st.markdown("### Coverage")
        nationwide_selected = st.checkbox(
            "All/Nationwide",
            value=False,
            key="add_All_Nationwide"
        )

        selected_coverage_states = st.multiselect(
            "State Coverage",
            STATE_COLUMNS,
            help="Type to search and select all states this carrier covers."
        )

        values["All/Nationwide"] = "Y" if nationwide_selected else ""
        for state in STATE_COLUMNS:
            values[state] = "Y" if state in selected_coverage_states else ""

        submitted = st.form_submit_button("Add Carrier")
        if submitted:
            vendor_name = str(values.get("VENDOR INFO", "")).strip()

            if not vendor_name:
                st.error("Vendor Info is required. Carrier was not added.")
            elif vendor_exists(df, vendor_name):
                st.error("A carrier with this Vendor Info already exists. Duplicate was not added.")
            else:
                insert_carrier(values)
                st.success("Carrier added. Refresh the page to see it.")

    st.info("Only Vendor Info is required. Other fields can be completed later.")


with tab5:
    st.markdown("<div class='awl-section-title'>Carrier Documents</div>", unsafe_allow_html=True)
    carrier_options = get_carrier_options(df)
    selected_label = st.selectbox("Select Carrier", carrier_options["label"].tolist(), key="doc_vendor")
    selected_id, record = get_record_by_label(df, carrier_options, selected_label)
    vendor_info = record.get("VENDOR INFO", "")

    st.markdown(f"### Upload Document for {vendor_info}")
    with st.form("upload_document"):
        document_type = st.selectbox("Document Type", DOCUMENT_TYPES)
        uploaded_file = st.file_uploader("Upload file", type=["pdf", "png", "jpg", "jpeg", "doc", "docx", "xls", "xlsx"])
        notes = st.text_area("Notes", height=80)
        uploaded = st.form_submit_button("Save Document")
        if uploaded:
            if uploaded_file is None:
                st.error("Please choose a file before saving.")
            else:
                save_document(selected_id, vendor_info, document_type, uploaded_file, notes)
                st.success("Document saved.")

    st.markdown("### Uploaded Documents")
    docs = list_documents(selected_id)
    render_document_viewer(docs, key_prefix=f"documents_{selected_id}")

with tab6:
    st.markdown("<div class='awl-section-title'>Data Quality Dashboard</div>", unsafe_allow_html=True)
    dq = missing_dashboard(df)
    st.dataframe(dq, use_container_width=True, hide_index=True)

    st.markdown("### Issue Detail")
    issue_options = dq[dq["Count"] > 0]["Issue"].tolist()
    if not issue_options:
        st.success("No data quality issues found.")
    else:
        selected_issue = st.selectbox(
            "Select an issue to view affected vendors",
            issue_options,
            key="dq_issue_select"
        )
        affected = vendors_for_quality_issue(df, selected_issue)
        affected_cols = [c for c in ["VENDOR INFO", "SCAC", "MC#", "USDOT", "Website", "CONTACT/TITLE", "PHONE", "EMAIL ", "TRK BASE STATE ", "BASE ZIP"] if c in affected.columns]

        st.markdown(f"**{len(affected)} vendors found for: {selected_issue}**")
        st.dataframe(affected[affected_cols], use_container_width=True, hide_index=True)

        if not affected.empty:
            st.markdown("### Open and Edit Affected Vendor")
            affected_options = get_carrier_options(affected)
            affected_label = st.selectbox(
                "Select a vendor from this issue list to edit",
                affected_options["label"].tolist(),
                key="dq_vendor_edit_select"
            )
            affected_id, affected_record_series = get_record_by_label(df, affected_options, affected_label)
            with st.expander("Edit Selected Vendor", expanded=True):
                render_edit_form_for_record(
                    f"dq_edit_vendor_{affected_id}",
                    affected_id,
                    affected_record_series.to_dict(),
                    df
                )

    st.divider()
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        render_kpi_card("Total Carriers", len(df))
    with c2:
        render_kpi_card("Local", yes_filter(df, "LOCAL", True).shape[0])
    with c3:
        render_kpi_card("Direct Dray", yes_filter(df, "DIRECT DRAY", True).shape[0])
    with c4:
        render_kpi_card("OTR", yes_filter(df, "OTR", True).shape[0])
    with c5:
        render_kpi_card("Nationwide", df[df["All/Nationwide"].astype(str).str.upper().isin(["Y", "YES", "TRUE", "1"])].shape[0])

    full_export_df = df.copy()
    if "Insurance" in full_export_df.columns:
        full_export_df["Insurance"] = full_export_df["Insurance"].apply(format_mmddyyyy)
    full_csv_data = full_export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Export full carrier database to CSV",
        data=full_csv_data,
        file_name="trucking_vendor_database_export.csv",
        mime="text/csv",
    )
