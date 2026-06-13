# Trucking Vendor Management App

## Stack

- Python
- Streamlit
- SQLite
- CSV import

## Run locally

1. Install Python 3.10+
2. Open terminal in this folder
3. Install requirements:

```bash
pip install -r requirements.txt
```

4. Run the app:

```bash
streamlit run app.py
```

5. Open the local URL shown in the terminal.

## Login

Default local password:

```text
admin123
```

To change it, create a `.streamlit/secrets.toml` file:

```toml
APP_PASSWORD = "your-password-here"
```

## Notes

- The SQLite database is created from the uploaded CSV.
- Do not run the live SQLite database from OneDrive with multiple simultaneous editors.
- Store backups/exports in OneDrive, not the live database.