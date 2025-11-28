# app.py
import os
import time
from flask import Flask, request, jsonify, render_template_string
from twilio.rest import Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ---- Twilio credentials ----
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_NUMBER")
FIXED_OTP = os.getenv("FIXED_OTP", "123456")  # Default OTP if not set

if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER):
    print("Warning: Twilio credentials missing. OTP sending won't work!")

# Initialize Twilio client lazily
_twilio_client = None
def get_twilio_client():
    global _twilio_client
    if _twilio_client is None:
        _twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _twilio_client

# ---- Flask app ----
app = Flask(__name__)

# ---- Simple rate limiter per IP ----
RATE_LIMIT_WINDOW = 60  # seconds
MAX_PER_WINDOW = 3
_recent_requests = {}  # ip -> [timestamps]

def clean_old(ip):
    now = time.time()
    _recent_requests.setdefault(ip, [])
    _recent_requests[ip] = [t for t in _recent_requests[ip] if now - t < RATE_LIMIT_WINDOW]

def too_many_requests(ip):
    clean_old(ip)
    return len(_recent_requests[ip]) >= MAX_PER_WINDOW

def record_request(ip):
    clean_old(ip)
    _recent_requests[ip].append(time.time())

# ---- Phone normalization ----
def normalize_phone(number: str):
    return ''.join(ch for ch in (number or "") if ch.isdigit() or ch == '+')

# ---- HTML ----
INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Send OTP — Secure</title>
<style>
body{font-family:sans-serif;background:#0f1724;color:#e6eef8;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}
.card{background:#0b1220;padding:28px;border-radius:16px;width:100%;max-width:500px;box-shadow:0 10px 30px rgba(2,6,23,0.6);}
input,button{padding:12px;margin:6px 0;width:100%;border-radius:10px;border:none;font-size:16px;}
input{background:rgba(255,255,255,0.03);color:white;}
button{background:#7c3aed;color:white;font-weight:600;cursor:pointer;}
.status{margin-top:12px;padding:12px;border-radius:10px;background:rgba(255,255,255,0.02);}
</style>
</head>
<body>
<div class="card">
  <h2>Send OTP</h2>
  <form id="otpForm" onsubmit="return sendOtp(event)">
    <input id="phone" type="tel" placeholder="+911234567890" required />
    <button type="submit" id="sendBtn">Send OTP</button>
  </form>
  <div id="status" class="status" hidden></div>
</div>
<script>
async function sendOtp(e){
  e.preventDefault();
  const phone = document.getElementById('phone').value.trim();
  const status = document.getElementById('status');
  const btn = document.getElementById('sendBtn');
  status.hidden = true;
  btn.disabled = true;
  btn.textContent = 'Sending...';
  try {
    const res = await fetch('/send-otp', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({phone})
    });
    const data = await res.json();
    if(res.ok){
      status.style.borderLeft = '4px solid #10b981';
      status.innerHTML = <strong>Success:</strong> ${data.message};
    } else {
      status.style.borderLeft = '4px solid #ef4444';
      status.innerHTML = <strong>Error:</strong> ${data.error || data.message};
    }
  } catch(err){
    status.style.borderLeft = '4px solid #ef4444';
    status.innerHTML = <strong>Error:</strong> ${err.message};
  } finally {
    status.hidden = false;
    btn.disabled = false;
    btn.textContent = 'Send OTP';
  }
  return false;
}
</script>
</body>
</html>
"""

# ---- Routes ----
@app.route("/", methods=["GET"])
def index():
    return render_template_string(INDEX_HTML)

@app.route("/send-otp", methods=["POST"])
def send_otp():
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()

if too_many_requests(client_ip):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429

    data = request.get_json(silent=True) or {}
    phone = normalize_phone(data.get("phone", ""))
    if not phone:
        return jsonify({"error": "Phone number required."}), 400

    digits = ''.join(ch for ch in phone if ch.isdigit())
    if len(digits) < 8 or len(digits) > 15:
        return jsonify({"error": "Invalid phone number format."}), 400

    record_request(client_ip)

    otp = FIXED_OTP
    message_body = f"Your verification code is: {otp}"

    # Dev mode: If Twilio credentials missing
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER):
        return jsonify({"message": f"(DEV) Would send to {phone}: {message_body}"}), 200

    # Send OTP via Twilio
    try:
        twilio_client = get_twilio_client()
        sent = twilio_client.messages.create(
            body=message_body,
            from_=TWILIO_FROM_NUMBER,
            to=phone
        )
        return jsonify({"message": f"OTP sent to {phone}. SID: {sent.sid}"}), 200
    except Exception as e:
        print("Twilio send error:", str(e))
        return jsonify({"error": "Failed to send SMS. Check server logs and Twilio configuration."}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
