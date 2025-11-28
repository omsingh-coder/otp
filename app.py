# app.py
import os
import time
from flask import Flask, request, jsonify, render_template_string, abort
from twilio.rest import Client
from dotenv import load_dotenv
from ipaddress import ip_address

# Optional: load .env in local dev only
load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_NUMBER")
FIXED_OTP = os.getenv("FIXED_OTP", "123456")  # default only for local dev

if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER):
    # allow app to still start locally for frontend testing, but warn
    print("Warning: Twilio credentials missing. Set TWILIO_SID, TWILIO_TOKEN, TWILIO_NUMBER in env.")

# Initialize Twilio client lazily (so app can still load without creds in dev)
_twilio_client = None
def get_twilio_client():
    global _twilio_client
    if _twilio_client is None:
        _twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return _twilio_client

app = Flask(__name__)

# ---- Simple in-memory rate limiter (per IP) ----
# Note: This uses process memory; for multiple dynos or long-term use, replace with Redis.
RATE_LIMIT_WINDOW = 60        # seconds
MAX_PER_WINDOW = 3            # max OTP sends per window per IP

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

# ---- Helper: normalize phone number a bit (very basic) ----
def normalize_phone(number: str):
    # remove spaces, hyphens
    n = ''.join(ch for ch in (number or "") if ch.isdigit() or ch == '+')
    # Basic: if starts with 0 and length reasonable, user probably local; but prefer E.164 from client
    return n

# ---- Routes ----

INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Send OTP — Secure</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#0f1724; --card:#0b1220; --accent:#7c3aed; --muted:#94a3b8; --glass: rgba(255,255,255,0.03);
  }
  *{box-sizing:border-box;font-family:'Poppins',system-ui,-apple-system,Segoe UI,Roboto,"Helvetica Neue",Arial;}
  body{margin:0;background:linear-gradient(180deg,var(--bg),#07102b);color:#e6eef8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;}
  .card{width:100%;max-width:720px;background:linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));border-radius:16px;padding:28px;box-shadow:0 10px 30px rgba(2,6,23,0.6);backdrop-filter: blur(6px);}
  header{display:flex;align-items:center;gap:16px;margin-bottom:18px;}
  .logo{width:56px;height:56px;border-radius:12px;background:linear-gradient(135deg,var(--accent),#06b6d4);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;color:white;box-shadow:0 6px 18px rgba(124,58,237,0.18);}
  h1{font-size:20px;margin:0}
  p.lead{margin:4px 0 14px;color:var(--muted);font-size:13px}
  form{display:grid;gap:12px;grid-template-columns:1fr auto}
  input[type="tel"]{padding:12px 14px;border-radius:10px;border:1px solid rgba(255,255,255,0.04);background:var(--glass);color:inherit;font-size:15px;outline:none}
  button.send{padding:12px 18px;border-radius:10px;border:0;background:linear-gradient(90deg,var(--accent),#06b6d4);color:white;font-weight:600;cursor:pointer;min-width:120px}
  .meta{margin-top:12px;color:var(--muted);font-size:13px}

.status{margin-top:14px;padding:12px;border-radius:10px;background:rgba(255,255,255,0.02);font-size:14px}
  .footer{display:flex;justify-content:space-between;align-items:center;margin-top:18px;color:var(--muted);font-size:13px}
  @media (max-width:520px){form{grid-template-columns:1fr} .footer{flex-direction:column;gap:8px;align-items:flex-start}}
</style>
</head>
<body>
  <div class="card" role="main">
    <header>
      <div class="logo">OTP</div>
      <div>
        <h1>Send OTP (fixed)</h1>
        <p class="lead">Ye page ek predefined OTP backend se lekar Twilio se SMS bhejta hai. Phone number E.164 format mein bhejein (example: +911234567890).</p>
      </div>
    </header>

    <form id="otpForm" onsubmit="return sendOtp(event)">
      <input id="phone" type="tel" placeholder="+911234567890" required autocomplete="tel" />
      <button class="send" id="sendBtn" type="submit">Send OTP</button>
      <div class="meta">No signup required. Limited to a few sends per minute.</div>
    </form>

    <div id="status" class="status" hidden></div>

    <div class="footer">
      <div>Hosted securely • Railway</div>
      <div style="opacity:.85">Make sure FIXED_OTP is set in env vars</div>
    </div>
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
      status.innerHTML = <strong>Error:</strong> ${data.error || data.message || 'Something went wrong'};
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

@app.route("/", methods=["GET"])
def index():
    return render_template_string(INDEX_HTML)

@app.route("/send-otp", methods=["POST"])
def send_otp():
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    # If forwarded header contains multiple, take first:
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()

    # Basic rate-limit per IP
    if too_many_requests(client_ip):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429

    data = request.get_json(silent=True) or {}
    phone = normalize_phone(data.get("phone", ""))
    if not phone:
        return jsonify({"error": "Phone number required."}), 400

    # Some basic validation: length heuristic
    digits = ''.join(ch for ch in phone if ch.isdigit())
    if len(digits) < 8 or len(digits) > 15:
        return jsonify({"error": "Invalid phone number format."}), 400

    # Record request (rate limiter)
    record_request(client_ip)

    # Compose message using fixed OTP (from env)
    otp = FIXED_OTP or "123456"
    message_body = f"Your verification code is: {otp}"

    # If Twilio not configured, return a fake success for dev
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER):
        # In local dev, we return success but do not send SMS
        return jsonify({"message": f"(DEV) Would send to {phone}: {message_body}"}), 200

    try:
        twilio_client = get_twilio_client()

sent = twilio_client.messages.create(body=message_body, from_=TWILIO_FROM_NUMBER, to=phone)
        # Do NOT return account SID/token anywhere
        return jsonify({"message": f"OTP sent to {phone}. SID: {sent.sid}"}), 200
    except Exception as e:
        # Twilio errors could leak info; send friendly message but log server-side
        print("Twilio send error:", str(e))
        return jsonify({"error": "Failed to send SMS. Check server logs and Twilio configuration."}), 500

if __name == "__main__":
    # For local dev
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
