# server.py — simple backend for ingesting sensor data, storing latest values,
# and sending email notifications to saved contact emails.

from flask import Flask, request, jsonify
from flask_cors import CORS
import os, json, time
from dotenv import load_dotenv
import smtplib
from email.message import EmailMessage
import threading
import traceback

load_dotenv()  # loads .env if present
app = Flask(__name__)
CORS(app)

# === Config (set these as environment variables) ===
SMTP_USER = os.getenv("SMTP_USER")  # your email address (e.g. gmail)
SMTP_PASS = os.getenv("SMTP_PASS")  # app password for gmail or SMTP password
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))

# Threshold for "high stress"
HR_THRESHOLD = int(os.getenv("HR_THRESHOLD", 105))

# Data files
CONTACTS_FILE = "contacts.json"  # stores contact emails per user/device
LATEST_FILE = "latest.json"  # stores latest sensor reading(s)

# === Helper functions: load/save JSON files ===
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
def send_email_async(to_emails, subject, body):
    """Send email in background thread to avoid timeout"""
    def send():
        try:
            if not SMTP_USER or not SMTP_PASS:
                print("SMTP credentials not configured; skipping email send.")
                return False

            msg = EmailMessage()
            msg["From"] = SMTP_USER
            msg["To"] = ", ".join(to_emails)
            msg["Subject"] = subject
            msg.set_content(body)

            # Set timeout for SMTP operations
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
                smtp.ehlo()
                if SMTP_PORT == 587:
                    smtp.starttls()
                smtp.login(SMTP_USER, SMTP_PASS)
                smtp.send_message(msg)
            print(f"✓ Email sent to {to_emails}")
            return True
        except Exception as e:
            print(f"✗ Error sending email: {e}")
            return False
    
    # Start email in background thread
    thread = threading.Thread(target=send)
    thread.daemon = True
    thread.start()
    return True  # Return immediately, don't wait for email

# === API Endpoints ===
@app.route("/", methods=["GET"])
def root():
    return {"status": "ok", "message": "Stress backend running"}

@app.route("/contacts/<device_id>", methods=["POST"])
def save_contacts(device_id):
    """
    Save contacts for a device or user.
    Body JSON: {"emails": ["a@example.com","b@example.com"]}
    """
    try:
        data = request.get_json() or {}
        emails = data.get("emails", [])
        if not isinstance(emails, list):
            return jsonify({"error": "emails must be a list"}), 400

        contacts = load_json(CONTACTS_FILE, {})
        contacts[str(device_id)] = emails
        save_json(CONTACTS_FILE, contacts)
        return jsonify({"ok": True, "saved": emails})
    except Exception as e:
        print(f"Error in save_contacts: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/contacts/<device_id>", methods=["GET"])
def get_contacts(device_id):
    contacts = load_json(CONTACTS_FILE, {})
    return jsonify(contacts.get(str(device_id), []))

@app.route("/ingest", methods=["POST"])
def ingest():
    """Endpoint for Raspberry Pi to POST sensor data"""
    try:
        data = request.get_json() or {}
        print(f"📥 Received data from device: {data.get('device_id', 'unknown')}")
        
        device = str(data.get("device_id", "default"))
        hr = data.get("heart_rate")
        acc = {"x": data.get("acc_x"), "y": data.get("acc_y"), "z": data.get("acc_z")}
        gps = {"lat": data.get("gps_lat"), "lon": data.get("gps_lon")}
        ts = data.get("timestamp", int(time.time()))
        
        # Save latest data (FAST - no email yet)
        latest = load_json(LATEST_FILE, {})
        latest[device] = {
            "heart_rate": hr,
            "acc": acc,
            "gps": gps,
            "timestamp": ts,
            "stress_score": data.get("stress_score"),
            "stress_level": data.get("stress_level"),
            "spo2": data.get("spo2"),
            "temperature": data.get("temperature"),
            "movement": data.get("movement"),
            "emotion": data.get("emotion"),
            "is_emergency": data.get("is_emergency", False)
        }
        save_json(LATEST_FILE, latest)
        
        print(f"💾 Saved data for device: {device}, HR: {hr}")
        
        # Check for high stress (but don't send email in main thread)
        alert_sent = False
        if hr is not None and float(hr) >= HR_THRESHOLD:
            contacts = load_json(CONTACTS_FILE, {}).get(device, [])
            if contacts:
                subject = f"ALERT: High stress detected on device {device}"
                body = f"High heart rate detected: {hr} BPM\n\n"
                if gps.get("lat") and gps.get("lon"):
                    body += f"Location: https://maps.google.com/?q={gps['lat']},{gps['lon']}\n\n"
                body += f"Timestamp: {ts}\n\nSent by Stress Detection System."
                
                # Send email in background (async)
                send_email_async(contacts, subject, body)
                alert_sent = True
                print(f"🚨 High stress alert triggered for {device}")
            else:
                print(f"⚠️ No contacts configured for device {device}")
        
        # Return response IMMEDIATELY (don't wait for email)
        return jsonify({
            "ok": True, 
            "alert_sent": alert_sent,
            "message": "Data received successfully"
        })
        
    except Exception as e:
        print(f"❌ Error in /ingest: {e}")
        print(traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/notify", methods=["POST"])
def notify():
    """
    Manual notify endpoint from frontend.
    """
    try:
        data = request.get_json() or {}
        device = str(data.get("device_id", "default"))
        subject = data.get("subject", "Alert from device")
        message = data.get("message", "")
        emails = data.get("emails")
        
        if emails is None:
            emails = load_json(CONTACTS_FILE, {}).get(device, [])
        
        if not emails:
            return jsonify({"ok": False, "error": "No recipient emails"}), 400
        
        # Send email in background
        send_email_async(emails, subject, message)
        return jsonify({"ok": True, "message": "Email queued for sending"})
        
    except Exception as e:
        print(f"Error in /notify: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/live/<device_id>", methods=["GET"])
def live(device_id):
    latest = load_json(LATEST_FILE, {})
    return jsonify(latest.get(str(device_id), {}))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)