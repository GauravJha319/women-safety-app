from flask import Flask, render_template, request, redirect, session
import sqlite3
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
import pytz
import os
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "monika_secret_key_123"
app.permanent_session_lifetime = timedelta(days=7)

# ----------------------
# Database Connection
# ----------------------
def get_db():
    conn = sqlite3.connect("users.db")
    conn.row_factory = sqlite3.Row
    return conn

# ----------------------
# Create Tables
# ----------------------
def create_table():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            mobile TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            contact1 TEXT NOT NULL,
            contact2 TEXT NOT NULL
        )
    """)

    cursor.execute("PRAGMA table_info(users)")
    existing_columns = [row[1] for row in cursor.fetchall()]
    if "email" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "password" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN password TEXT")
    if "last_latitude" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_latitude TEXT")
    if "last_longitude" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_longitude TEXT")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sos_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            latitude TEXT,
            longitude TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS emergency_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()

# ----------------------
# Home Route
# ----------------------
@app.route("/")
def home():
    return "App is running"

@app.route("/health")
def health():
    return "OK", 200

# ----------------------
# Register
# ----------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        mobile = request.form["mobile"]
        password = request.form["password"]
        contact1 = request.form["contact1"]
        contact2 = request.form["contact2"]

        hashed_password = generate_password_hash(password)

        conn = get_db()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO users (name, email, mobile, password, contact1, contact2) VALUES (?, ?, ?, ?, ?, ?)",
                (name, email, mobile, hashed_password, contact1, contact2)
            )
            conn.commit()
        except sqlite3.IntegrityError as err:
            conn.close()
            error_message = str(err).lower()
            if "email" in error_message:
                return "⚠ Email address already registered!"
            return "⚠ Mobile number already registered!"

        conn.close()
        return redirect("/")

    return render_template("register.html")

# ----------------------
# Login Route (FIXED)
# ----------------------
@app.route("/login", methods=["POST"])
def login():
    identifier = request.form["identifier"]
    password = request.form["password"]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE mobile=? OR email=?", (identifier, identifier))
    user = cursor.fetchone()
    conn.close()

    if not user:
        return "❌ Phone number or email not found!"

    if not check_password_hash(user["password"], password):
        return "❌ Incorrect password!"

    session.permanent = True
    session["user_id"] = user["id"]
    session["name"] = user["name"]
    return redirect("/dashboard")

# ----------------------
# Dashboard Route
# ----------------------
@app.route("/dashboard")
def dashboard():
    if "user_id" in session:
        return render_template("dashboard.html", name=session["name"])
    else:
        return redirect("/")

# ----------------------
# Track User Location
@app.route("/track/<int:user_id>")
def track_user(user_id):
    return render_template("track.html", user_id=user_id)

# ----------------------
# Get User Location API
@app.route("/get_location/<int:user_id>")
def get_location(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT last_latitude, last_longitude FROM users WHERE id=?", (user_id,))
    user = cursor.fetchone()
    conn.close()

    if user and user["last_latitude"] and user["last_longitude"]:
        return {
            "latitude": user["last_latitude"],
            "longitude": user["last_longitude"]
        }
    else:
        return {"error": "Location not available"}, 404

# ----------------------
# Update Live Location
@app.route("/update_location", methods=["POST"])
def update_location():
    if "user_id" not in session:
        return {"error": "Unauthorized"}, 401

    payload = request.get_json(silent=True)
    latitude = None
    longitude = None

    if payload:
        latitude = payload.get("latitude")
        longitude = payload.get("longitude")
    else:
        latitude = request.form.get("latitude")
        longitude = request.form.get("longitude")

    if latitude is None or longitude is None:
        return {"error": "Latitude and longitude are required"}, 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET last_latitude=?, last_longitude=? WHERE id=?",
        (latitude, longitude, session["user_id"])
    )
    conn.commit()
    conn.close()

    return {"status": "ok"}

# ----------------------
# Get Emergency Contacts
# ----------------------
def get_emergency_contacts(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM emergency_contacts WHERE user_id=? ORDER BY id DESC",
        (user_id,)
    )
    contacts = cursor.fetchall()
    conn.close()
    return contacts

# ----------------------
# Email Alert Helper
# ----------------------
def send_sos_emails(user_name, latitude, longitude, contacts, user_id):
    gmail_user = os.getenv("EMAIL")  # update with your Gmail
    gmail_password = os.getenv("PASSWORD")  # set an app password (recommended)

    maps_link = f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"
    tracking_link = f"http://127.0.0.1:5000/track/{user_id}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subject = "SOS! User needs help"

    for contact in contacts:
        email_to = contact["email"]
        if not email_to:
            continue

        body = (
            f"SOS! User {user_name} needs help.\n\n"
            f"Location: {maps_link}\n"
            f"Latitude: {latitude}, Longitude: {longitude}\n"
            f"Timestamp: {timestamp}\n\n"
            f"Live Tracking: {tracking_link}\n\n"
            "Please respond immediately."
        )

        msg = EmailMessage()
        msg["From"] = gmail_user
        msg["To"] = email_to
        msg["Subject"] = subject
        msg.set_content(body)

        try:
            with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
                smtp.starttls()
                smtp.login(gmail_user, gmail_password)
                smtp.send_message(msg)
        except Exception as e:
            print(f"Failed to send SOS email to {email_to}: {e}")

# ----------------------
# Contacts Page
# ----------------------
@app.route("/contacts")
def contacts():
    if "user_id" not in session:
        return redirect("/")

    user_id = session["user_id"]
    contacts = get_emergency_contacts(user_id)
    return render_template("contacts.html", contacts=contacts)

# ----------------------
# Add Contact
# ----------------------
@app.route("/add_contact", methods=["POST"])
def add_contact():
    if "user_id" not in session:
        return redirect("/")

    name = request.form.get("name")
    phone = request.form.get("phone")
    email = request.form.get("email")
    user_id = session["user_id"]

    if not name or not phone:
        return "Name and phone are required", 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO emergency_contacts (user_id, name, phone, email) VALUES (?, ?, ?, ?)",
        (user_id, name, phone, email)
    )
    conn.commit()
    conn.close()

    return redirect("/contacts")

# ----------------------
# Edit Contact
@app.route("/edit_contact/<int:contact_id>", methods=["GET", "POST"])
def edit_contact(contact_id):
    if "user_id" not in session:
        return redirect("/")

    user_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor()

    if request.method == "POST":
        name = request.form.get("name")
        phone = request.form.get("phone")
        email = request.form.get("email")

        if not name or not phone:
            conn.close()
            return "Name and phone are required", 400

        cursor.execute(
            "UPDATE emergency_contacts SET name=?, phone=?, email=? WHERE id=? AND user_id=?",
            (name, phone, email, contact_id, user_id)
        )
        conn.commit()
        conn.close()
        return redirect("/contacts")

    cursor.execute(
        "SELECT * FROM emergency_contacts WHERE id=? AND user_id=?",
        (contact_id, user_id)
    )
    contact = cursor.fetchone()
    conn.close()

    if not contact:
        return "Contact not found", 404

    return render_template("edit_contact.html", contact=contact)

# ----------------------
# Delete Contact
# ----------------------
@app.route("/delete_contact", methods=["POST"])
def delete_contact():
    if "user_id" not in session:
        return redirect("/")

    contact_id = request.form.get("contact_id")
    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM emergency_contacts WHERE id=? AND user_id=?",
        (contact_id, user_id)
    )
    conn.commit()
    conn.close()

    return redirect("/contacts")

# ----------------------
# Save SOS
# ----------------------
@app.route("/save_sos", methods=["POST"])
def save_sos():
    if "user_id" not in session:
        return "Unauthorized", 401

    import pytz
    from datetime import datetime

    # ✅ Get IST time
    ist = pytz.timezone('Asia/Kolkata')
    current_time = datetime.now(ist)
    timestamp = current_time.strftime('%Y-%m-%d %H:%M:%S')

    latitude = request.form["latitude"]
    longitude = request.form["longitude"]
    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor()

    # ✅ Add timestamp in DB
    cursor.execute(
        "INSERT INTO sos_alerts (user_id, latitude, longitude, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, latitude, longitude, timestamp)
    )
    conn.commit()

    contacts = get_emergency_contacts(user_id)

    if contacts:
        for c in contacts:
            message = f"SOS ALERT: User has triggered emergency alert. Location will be shared. Contact: {c['name']} ({c['phone']})"
            print(message)

        # ✅ Pass timestamp in email also (optional but better)
        send_sos_emails(session.get("name", "Unknown User"), latitude, longitude, contacts, user_id)

    else:
        print("SOS ALERT: User has triggered emergency alert. No emergency contacts available.")

    conn.close()
    return "SOS Saved Successfully"

# ----------------------
# History Route
# ----------------------
@app.route("/history")
def history():
    if "user_id" not in session:
        return redirect("/")

    user_id = session["user_id"]

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM sos_alerts WHERE user_id=? ORDER BY timestamp DESC",
        (user_id,)
    )
    alerts = cursor.fetchall()
    conn.close()

    return render_template("history.html", alerts=alerts)

# ----------------------
# Delete Alert
# ----------------------
@app.route("/delete_alert/<int:alert_id>")
def delete_alert(alert_id):
    if "user_id" not in session:
        return redirect("/")

    user_id = session["user_id"]
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM sos_alerts WHERE id=? AND user_id=?",
        (alert_id, user_id)
    )
    conn.commit()
    conn.close()

    return redirect("/history")

# ----------------------
# Logout
# ----------------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ----------------------
# Run App
# ----------------------
if __name__ == "__main__":
    create_table()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)