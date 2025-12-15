# server.py — simple backend for ingesting sensor data, storing latest values,
# and sending email notifications to saved contact emails.
# Save at: D:\StressDetectionProject\backend\server.py

from flask import Flask, request, jsonify
from flask_cors import CORS
import os, json, time
from dotenv import load_dotenv
import smtplib
from email.message import EmailMessage

load_dotenv()  # loads .env if present

app = Flask(__name__)
CORS(app)

# === Config (set these as environment variables, see instructions below) ===
SMTP_USER = os.getenv("SMTP_USER")     # your email address (e.g. gmail)
SMTP_PASS = os.getenv("SMTP_PASS")     # app password for gmail or SMTP password
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))

# Threshold for "high stress" — you can tune this
HR_THRESHOLD = int(os.getenv("HR_THRESHOLD", 105))

# Data files
CONTACTS_FILE = "contacts.json"   # stores contact emails per user/device
LATEST_FILE = "latest.json"       # stores latest sensor reading(s)

# Helper: load/save JSON files
def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# Ensure files exist
if not os.path.exists(CONTACTS_FILE):
    save_json(CONTACTS_FILE, {})
if not os.path.exists(LATEST_FILE):
    save_json(LATEST_FILE, {})

# === Email sending (supports Gmail SMTP or other SMTP) ===
def send_email(to_emails, subject, body):
    """
    to_emails: list of email addresses
    """
    if not SMTP_USER or not SMTP_PASS:
        print("SMTP credentials not configured; skipping email send.")
        return False

    try:
        msg = EmailMessage()
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(to_emails)
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            if SMTP_PORT == 587:
                smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        print(f"Sent email to {to_emails}")
        return True
    except Exception as e:
        print("Error sending email:", e)
        return False

# === API Endpoints ===

@app.route("/", methods=["GET"])
def root():
    return {"status":"ok", "message":"Stress backend running"}

@app.route("/contacts/<device_id>", methods=["POST"])
def save_contacts(device_id):
    """
    Save contacts for a device or user.
    Body JSON: {"emails": ["a@example.com","b@example.com"]}
    """
    data = request.get_json() or {}
    emails = data.get("emails", [])
    if not isinstance(emails, list):
        return jsonify({"error":"emails must be a list"}), 400

    contacts = load_json(CONTACTS_FILE, {})
    contacts[str(device_id)] = emails
    save_json(CONTACTS_FILE, contacts)
    return jsonify({"ok":True, "saved":emails})

@app.route("/contacts/<device_id>", methods=["GET"])
def get_contacts(device_id):
    contacts = load_json(CONTACTS_FILE, {})
    return jsonify(contacts.get(str(device_id), []))

@app.route("/ingest", methods=["POST"])
def ingest():
    """
    Endpoint for Raspberry Pi to POST sensor data.
    Expected JSON:
    {
      "device_id": "device1",
      "heart_rate": 92,
      "acc_x": 0.01,
      "acc_y": -0.02,
      "acc_z": 0.15,
      "gps_lat": 35.3629,     # optional
      "gps_lon": 74.6911,     # optional
      "timestamp": 1690000000 # optional epoch
    }
    """
    data = request.get_json() or {}
    device = str(data.get("device_id", "default"))
    hr = data.get("heart_rate")
    acc = {
        "x": data.get("acc_x"),
        "y": data.get("acc_y"),
        "z": data.get("acc_z")
    }
    gps = {"lat": data.get("gps_lat"), "lon": data.get("gps_lon")}
    ts = data.get("timestamp", int(time.time()))

    # Save latest
    latest = load_json(LATEST_FILE, {})
    latest[device] = {
        "heart_rate": hr,
        "acc": acc,
        "gps": gps,
        "timestamp": ts
    }
    save_json(LATEST_FILE, latest)

    # Check for high stress based on heart rate threshold
    try:
        if hr is not None and float(hr) >= HR_THRESHOLD:
            # Send email alert to saved contacts
            contacts = load_json(CONTACTS_FILE, {}).get(device, [])
            if contacts:
                subject = f"ALERT: High stress detected on device {device}"
                body = f"High heart rate detected: {hr} BPM\n\n"
                if gps.get("lat") and gps.get("lon"):
                    body += f"Location: https://maps.google.com/?q={gps['lat']},{gps['lon']}\n\n"
                body += f"Timestamp: {ts}\n\nSent by Stress Detection System."
                send_email(contacts, subject, body)
            else:
                print(f"No contacts configured for device {device}.")
            return jsonify({"ok":True, "alert_sent": True})
    except Exception as e:
        print("Error checking HR threshold:", e)

    return jsonify({"ok":True, "alert_sent": False})

@app.route("/notify", methods=["POST"])
def notify():
    """
    Manual notify endpoint from frontend.
    Body: {"device_id":"device1","subject":"...","message":"...", "emails":["a@.."]}
    If emails field present, they will be used; otherwise uses saved contacts.
    """
    data = request.get_json() or {}
    device = str(data.get("device_id", "default"))
    subject = data.get("subject", "Alert from device")
    message = data.get("message", "")
    emails = data.get("emails")

    if emails is None:
        emails = load_json(CONTACTS_FILE, {}).get(device, [])

    if not emails:
        return jsonify({"ok":False, "error":"No recipient emails"}), 400

    ok = send_email(emails, subject, message)
    return jsonify({"ok": ok})

@app.route("/live/<device_id>", methods=["GET"])
def live(device_id):
    latest = load_json(LATEST_FILE, {})
    return jsonify(latest.get(str(device_id), {}))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
