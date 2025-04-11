from flask import Flask, render_template, render_template_string
import imaplib
import email
from email.header import decode_header
import smtplib
from email.mime.text import MIMEText
import time
import threading
import os
import google.generativeai as genai
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime

load_dotenv()

app = Flask(__name__)

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONGODB_URI = os.getenv("DATABASE_URL")

DEPARTMENTS = {
    "Technical": os.getenv("TECH_EMAIL"),
    "Billing": os.getenv("BILLING_EMAIL"),
    "Complaint": os.getenv("COMPLAINT_EMAIL"),
    "General Inquiry": os.getenv("GENERAL_EMAIL"),
}

# MongoDB connection
client = MongoClient(MONGODB_URI)
db = client.emails_db
emails_collection = db.emails

# Initialize model
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

def classify_email(body):
    prompt = f"""
    Classify the issue below into one of these categories:
    - Technical
    - Billing
    - Complaint
    - General Inquiry

    Message: "{body}"
    Only return the category.
    """
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print("Classification Error:", e)
        return "Unclassified"

def forward_email(subject, body, to_email):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_USER
        msg["To"] = to_email

        print(f"Attempting to forward email to {to_email}...")
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 587) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
            print(f"Email successfully forwarded to {to_email}")
            return True
    except Exception as e:
        print(f"Email forwarding error: {str(e)}")
        return False

def store_email(category, sender, subject, body, forwarded_to=None):
    try:
        email_doc = {
            "category": category,
            "sender": sender,
            "subject": subject,
            "body": body,
            "forwarded_to": forwarded_to,
            "timestamp": datetime.now()
        }
        emails_collection.insert_one(email_doc)
        return True
    except Exception as e:
        print("Database Error:", e)
        return False

def get_emails_by_category():
    try:
        email_data = {
            "Technical": [],
            "Billing": [],
            "Complaint": [],
            "General Inquiry": [],
            "Unclassified": []
        }
        
        emails = emails_collection.find().sort("timestamp", -1)
        
        for email_doc in emails:
            category = email_doc['category']
            email_data.setdefault(category, []).append({
                "from": email_doc['sender'],
                "subject": email_doc['subject'],
                "body": email_doc['body'],
                "forwarded_to": email_doc.get('forwarded_to', 'Not recorded'),
                "timestamp": email_doc['timestamp']
            })
            
        return email_data
    except Exception as e:
        print("Database Read Error:", e)
        return {"Technical": [], "Billing": [], "Complaint": [], "General Inquiry": [], "Unclassified": []}

def fetch_and_process_emails():
    while True:
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(EMAIL_USER, EMAIL_PASS)
            mail.select("inbox")

            _, messages = mail.search(None, "UNSEEN")
            email_ids = messages[0].split()
            
            print(f"Found {len(email_ids)} unread email(s)")
            
            processed_count = 0

            for eid in email_ids:
                _, msg_data = mail.fetch(eid, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])

                subject, encoding = decode_header(msg["Subject"])[0]
                if isinstance(subject, bytes):
                    subject = subject.decode(encoding or "utf-8")
                sender = msg.get("From")

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode(errors="ignore")
                            break
                else:
                    body = msg.get_payload(decode=True).decode(errors="ignore")

                category = classify_email(body)
                to_email = DEPARTMENTS.get(category, os.getenv("GENERAL_EMAIL"))  # Fallback to general email if category not found
                
                if not to_email:
                    print(f"Warning: No forwarding email found for category '{category}'. Check your environment variables.")
                    to_email = EMAIL_USER  # Fallback to sending to self
                
                forward_result = forward_email(subject, body, to_email)
                
                if forward_result:
                    print(f"Successfully forwarded '{subject}' to {to_email} (Category: {category})")
                else:
                    print(f"Failed to forward '{subject}' to {to_email} (Category: {category})")

                store_email(category, sender, subject, body, to_email)

                mail.store(eid, '+FLAGS', '\\Seen')
                
                processed_count += 1
                print(f"Forwarded '{subject}' from {sender} as {category} to {to_email}")
                
            print(f"Successfully processed {processed_count}/{len(email_ids)} unread email(s)")
            
            mail.logout()
        except Exception as e:
            print("Polling Error:", e)

        time.sleep(30)

threading.Thread(target=fetch_and_process_emails, daemon=True).start()

@app.route('/')
def dashboard():
    email_log = get_emails_by_category()
    return render_template('dashboard.html', email_log=email_log)

if __name__ == '__main__':
    app.run(debug=True)
