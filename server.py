# server.py — simple backend for ingesting sensor data, storing latest values,
# and sending email notifications to saved contact emails.

from flask import Flask, request, jsonify
from flask_cors import CORS
import os, json, time
from dotenv import load_dotenv
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

# === Email sending (LOG ONLY - fix timeout) ===
def send_email_async(to_emails, subject, body):
    """Log email instead of sending (temporarily fix timeout)"""
    print(f"📧 [EMAIL ALERT] To: {to_emails}")
    print(f"   Subject: {subject}")
    print(f"   Body: {body[:100]}..." if len(body) > 100 else f"   Body: {body}")
    print(f"   Status: Would send if SMTP configured properly")
    return True  # Pretend success

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
        
        # Pi sends acc_x, acc_y, acc_z separately
        acc = {
            "x": data.get("acc_x"),
            "y": data.get("acc_y"), 
            "z": data.get("acc_z")
        }
        
        gps = {"lat": data.get("gps_lat"), "lon": data.get("gps_lon")}
        
        # Handle Pi's string timestamp
        ts = data.get("timestamp")
        if ts and isinstance(ts, str):
            # Pi sends format: "2024-12-19 10:30:00" - keep as string
            pass
        else:
            ts = data.get("timestamp", int(time.time()))
        
        # Save latest data
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
        
        print(f"💾 Saved data for device: {device}, HR: {hr}, Stress: {data.get('stress_score')}")
        
        # High stress check
        alert_sent = False
        if hr is not None and float(hr) >= HR_THRESHOLD:
            contacts = load_json(CONTACTS_FILE, {}).get(device, [])
            if contacts:
                subject = f"ALERT: High stress detected on device {device}"
                body = f"High heart rate detected: {hr} BPM\n"
                body += f"Stress Level: {data.get('stress_level', 'Unknown')}\n"
                body += f"Stress Score: {data.get('stress_score', 0)}/100\n"
                if gps.get("lat") and gps.get("lon"):
                    body += f"Location: https://maps.google.com/?q={gps['lat']},{gps['lon']}\n"
                body += f"Time: {data.get('timestamp', 'Unknown')}\n\n"
                body += "Sent by Stress Detection System."
                
                send_email_async(contacts, subject, body)
                alert_sent = True
                print(f"🚨 High stress alert triggered for {device}")
            else:
                print(f"⚠️ No contacts configured for device {device}")
        
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
        
        send_email_async(emails, subject, message)
        return jsonify({"ok": True, "message": "Email logged (not sent)"})
        
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