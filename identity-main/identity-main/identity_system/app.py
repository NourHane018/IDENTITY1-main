from flask import Flask, request, render_template, redirect, jsonify
import sqlite3
from datetime import datetime
import os
import re
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

from dotenv import load_dotenv
import os
import smtplib
from email.message import EmailMessage

# ========================
# Load environment variables
# ========================
load_dotenv()
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

# ========================
# Send confirmation email
# ========================
def send_confirmation(address, uid):
    msg = EmailMessage()
    msg['Subject'] = 'Identity Created'
    msg['From'] = EMAIL_USER
    msg['To'] = address
    msg.set_content(f"""Hello,

Your university identity has been successfully created.

Your ID: {uid}

If you did not request this identity, please contact administration.

University Identity Management System
""")
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        print(f"Email sent to {address}")
    except Exception as e:
        print(f"Email sending failed to {address}: {e}")

print("Current working directory:", os.getcwd())
print("Templates folder exists:", os.path.exists("templates"))

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.debug = True

# ========================
# Database Connection
# ========================
def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

# ========================
# Status Lifecycle Rules
# ========================
VALID_TRANSITIONS = {
    'Pending': ['Active'],  # Pending → Active (initial approval)
    'Active': ['Suspended'],  # Active ↔ Suspended (bidirectional)
    'Suspended': ['Active'],  # Suspended ↔ Active (bidirectional)
    'Inactive': ['Archived'],  # Inactive → Archived ONLY (after 5 years, then final)
    'Archived': []  # Archived cannot transition anywhere (final state)
}

def is_valid_transition(current_status, new_status, status_changed_at=None):
    """Check if transition from current_status to new_status is allowed"""
    if current_status == new_status:
        return True  # Same status is allowed
    if current_status not in VALID_TRANSITIONS:
        return False  # Invalid current status
    if new_status not in VALID_TRANSITIONS[current_status]:
        return False  # Not in allowed transitions
    
    # Special rule: Inactive → Archived only after 5 years
    if current_status == 'Inactive' and new_status == 'Archived':
        if status_changed_at:
            try:
                changed_date = datetime.fromisoformat(status_changed_at)
                age_days = (datetime.now() - changed_date).days
                if age_days < 365 * 5:  # Less than 5 years
                    return False
            except:
                pass
    return True

# ========================
# Initialize Database
# ========================
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # base table
    cur.execute('''CREATE TABLE IF NOT EXISTS People (
                    id TEXT PRIMARY KEY,
                    type TEXT,
                    sub_category TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    dob TEXT,
                    place_of_birth TEXT,
                    nationality TEXT,
                    gender TEXT,
                    email TEXT UNIQUE,
                    phone TEXT,
                    status TEXT,
                    status_changed_at TEXT
                )''')
    
    # Common Data columns (already in base table)
    # No need to add them again
    
    # Student-specific columns
    student_cols = [
        'student_high_school_diploma_type TEXT',
        'student_high_school_diploma_year INTEGER',
        'student_high_school_honors TEXT',
        'student_major TEXT',
        'student_entry_year INTEGER',
        'student_status TEXT',
        'student_faculty_department TEXT',
        'student_group TEXT',
        'student_scholarship_status TEXT'
    ]
    
    # Faculty-specific columns
    faculty_cols = [
        'faculty_rank TEXT',
        'faculty_employment_category TEXT',
        'faculty_appointment_start_date TEXT',
        'faculty_primary_department TEXT',
        'faculty_secondary_departments TEXT',
        'faculty_office_building TEXT',
        'faculty_office_floor TEXT',
        'faculty_office_room TEXT',
        'faculty_phd_institution TEXT',
        'faculty_research_areas TEXT',
        'faculty_habilitation_supervise TEXT',
        'faculty_contract_type TEXT',
        'faculty_contract_start_date TEXT',
        'faculty_contract_end_date TEXT',
        'faculty_teaching_hours INTEGER'
    ]
    
    # Staff-specific columns
    staff_cols = [
        'staff_assigned_department TEXT',
        'staff_job_title TEXT',
        'staff_grade TEXT',
        'staff_entry_date TEXT'
    ]
    
    # External-specific columns
    external_cols = [
        'external_organization TEXT',
        'external_contact_person TEXT'
    ]
    
    all_extra_cols = student_cols + faculty_cols + staff_cols + external_cols
    
    for col in all_extra_cols:
        try:
            cur.execute(f"ALTER TABLE People ADD COLUMN {col}")
        except Exception:
            pass  # column already exists

    # audit table for tracking changes
    cur.execute('''CREATE TABLE IF NOT EXISTS Audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id TEXT,
                    changed_at TEXT,
                    field TEXT,
                    old_value TEXT,
                    new_value TEXT
                )''')
    
    # Add sub_category column if not exists
    try:
        cur.execute("ALTER TABLE People ADD COLUMN sub_category TEXT")
    except Exception:
        pass
    
    conn.commit()
    conn.close()

# ========================
# Generate Unique ID
# ========================
ID_RANGES = {
    'Undergraduate': {'prefix': 'STU', 'start': 202400001, 'end': 202415000},
    'Continuing Education': {'prefix': 'CED', 'start': 202400001, 'end': 202405000},
    'PhD Candidates': {'prefix': 'PHD', 'start': 202400001, 'end': 202401000},
    'International/Exchange': {'prefix': 'INT', 'start': 202400001, 'end': 202402000},
    'Tenured': {'prefix': 'FAC', 'start': 202400001, 'end': 202401200},
    'Adjunct/Part-time': {'prefix': 'ADJ', 'start': 202400001, 'end': 202400500},
    'Visiting Researchers': {'prefix': 'VIS', 'start': 202400001, 'end': 202400300},
    'Administrative': {'prefix': 'STF', 'start': 202400001, 'end': 202400800},
    'Technical': {'prefix': 'TEC', 'start': 202400001, 'end': 202400400},
    'Temporary': {'prefix': 'TMP', 'start': 202400001, 'end': 202400500},
    'Contractors/Vendors': {'prefix': 'CON', 'start': 202400001, 'end': 202400900},
    'Alumni': {'prefix': 'ALM', 'start': 202400001, 'end': 202420000}
}

def generate_id(sub_category):
    """Generate ID based on sub-category with specific prefix and range"""
    if sub_category not in ID_RANGES:
        # Fallback for unknown categories
        year = datetime.now().year
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM People WHERE sub_category=?", (sub_category,))
        number = cur.fetchone()[0] + 1
        conn.close()
        return f"TMP{year}{number:05d}"
    
    range_info = ID_RANGES[sub_category]
    prefix = range_info['prefix']
    start = range_info['start']
    end = range_info['end']
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM People WHERE sub_category=?", (sub_category,))
    count = cur.fetchone()[0]
    conn.close()
    
    # Get the next sequence number within the range
    next_num = start + count
    
    # Ensure we stay within the range
    if next_num > end:
        # Range exhausted, but we still generate the ID
        pass
    
    # Format: [PREFIX][YEAR][NUMBER]
    # Example: STU202400001
    return f"{prefix}{next_num}"

# ========================
# Home Page
# ========================
@app.route("/")
def index():
    return render_template("index.html")

# ========================
# Validate User Data
# ========================
def validate_user_data(data):
    """Validate user data before creating identity"""
    errors = []
    
    # Check for empty fields
    required_fields = ['first_name', 'last_name', 'email', 'dob', 'type', 'sub_category']
    for field in required_fields:
        if not data.get(field) or str(data.get(field)).strip() == '':
            errors.append(f"{field.replace('_', ' ')} cannot be empty")

    # duplicate check: same name + dob + same sub_category
    if data.get('first_name') and data.get('last_name') and data.get('dob') and data.get('sub_category'):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM People WHERE lower(first_name)=? AND lower(last_name)=? AND dob=? AND sub_category=?",
            (data['first_name'].strip().lower(), data['last_name'].strip().lower(), data['dob'], data['sub_category'])
        )
        if cur.fetchone()[0] > 0:
            errors.append("An identity with the same name, date of birth, and sub-category already exists")
        conn.close()
    
    # Check first name (at least 2 characters)
    first_name = str(data.get('first_name', '')).strip()
    if first_name and len(first_name) < 2:
        errors.append("First name must be at least 2 characters")
    # also check last name exists (same requirement)
    last_name = str(data.get('last_name', '')).strip()
    if last_name and len(last_name) < 2:
        errors.append("Last name must be at least 2 characters")

    # sub_category-specific required fields
    sub_cat = data.get('sub_category', '').strip()
    
    # Student sub-categories
    if sub_cat in ['Undergraduate', 'Continuing Education', 'PhD Candidates', 'International/Exchange']:
        if not data.get('student_major') or not str(data.get('student_major')).strip():
            errors.append("Major/Program is required for students")
        if not data.get('student_entry_year') or not str(data.get('student_entry_year')).strip():
            errors.append("Entry year is required for students")
        # student_status is now defaulted to Pending; user no longer supplies it
        if not data.get('student_faculty_department') or not str(data.get('student_faculty_department')).strip():
            errors.append("Faculty & Department is required for students")
    
    # Faculty sub-categories
    if sub_cat in ['Tenured', 'Adjunct/Part-time', 'Visiting Researchers']:
        if not data.get('faculty_rank') or not str(data.get('faculty_rank')).strip():
            errors.append("Rank is required for faculty")
        if not data.get('faculty_primary_department') or not str(data.get('faculty_primary_department')).strip():
            errors.append("Primary Department is required for faculty")
        if not data.get('faculty_appointment_start_date') or not str(data.get('faculty_appointment_start_date')).strip():
            errors.append("Appointment Start Date is required for faculty")
    
    # Staff sub-categories
    if sub_cat in ['Administrative', 'Technical', 'Temporary']:
        if not data.get('staff_assigned_department') or not str(data.get('staff_assigned_department')).strip():
            errors.append("Assigned Department/Service is required for staff")
        if not data.get('staff_job_title') or not str(data.get('staff_job_title')).strip():
            errors.append("Job Title is required for staff")
        if not data.get('staff_entry_date') or not str(data.get('staff_entry_date')).strip():
            errors.append("Date of Entry to University is required for staff")
    
    # External sub-categories
    if sub_cat in ['Contractors/Vendors', 'Alumni']:
        if not data.get('external_organization') or not str(data.get('external_organization')).strip():
            errors.append("Organization is required for external members")
    
    # Check email validity
    email = str(data.get('email', '')).strip()
    email_regex = r'^[^\s@]+@[^\s@]+\.[^\s@]+$'
    if email and not re.match(email_regex, email):
        errors.append("Invalid email format")
    
    # Check if email is not duplicate
    if email:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM People WHERE email=? COLLATE NOCASE", (email.lower(),))
        count = cur.fetchone()[0]
        conn.close()
        if count > 0:
            errors.append("Email already exists")
    
    # Check phone number (numbers only)
    phone = str(data.get('phone', '')).strip()
    if phone and not phone.isdigit():
        errors.append("Phone must contain only numbers")
    
    # Check birth date
    dob = data.get('dob')
    if dob:
        try:
            dob_date = datetime.strptime(dob, '%Y-%m-%d')
            today = datetime.now()
            
            # Check if date is not in future
            if dob_date > today:
                errors.append("Birth date cannot be in the future")
            
            # Check age (>=16 for all)
            age = (today - dob_date).days / 365.25
            if age < 16:
                errors.append("You must be at least 16 years old")
        except ValueError:
            errors.append("Invalid date format")
    
    return errors

# ========================
# Create Identity
# ========================
@app.route("/create", methods=["GET","POST"])
def create():
    if request.method == "POST":
        user_type = request.form.get("type")
        sub_category = request.form.get("sub_category")
        first_name = request.form.get("first_name")
        last_name = request.form.get("last_name")
        dob = request.form.get("dob")
        place_of_birth = request.form.get("place_of_birth")
        nationality = request.form.get("nationality")
        gender = request.form.get("gender")
        email = request.form.get("email")
        phone = request.form.get("phone")
        status = "Pending"
        
        # Student fields
        student_diploma_type = request.form.get("student_high_school_diploma_type")
        student_diploma_year = request.form.get("student_high_school_diploma_year")
        student_diploma_honors = request.form.get("student_high_school_honors")
        student_major = request.form.get("student_major")
        student_entry_year = request.form.get("student_entry_year")
        # student_status removed from form; default to Pending for student types
        student_status = "Pending" if user_type == "Student" else None
        student_faculty_department = request.form.get("student_faculty_department")
        student_group = request.form.get("student_group")
        student_scholarship = request.form.get("student_scholarship_status")
        
        # Faculty fields
        faculty_rank = request.form.get("faculty_rank")
        faculty_employment = request.form.get("faculty_employment_category")
        faculty_appt_start = request.form.get("faculty_appointment_start_date")
        faculty_primary_dept = request.form.get("faculty_primary_department")
        faculty_secondary_depts = request.form.get("faculty_secondary_departments")
        faculty_office_building = request.form.get("faculty_office_building")
        faculty_office_floor = request.form.get("faculty_office_floor")
        faculty_office_room = request.form.get("faculty_office_room")
        faculty_phd_inst = request.form.get("faculty_phd_institution")
        faculty_research = request.form.get("faculty_research_areas")
        faculty_habilitation = request.form.get("faculty_habilitation_supervise")
        faculty_contract_type = request.form.get("faculty_contract_type")
        faculty_contract_start = request.form.get("faculty_contract_start_date")
        faculty_contract_end = request.form.get("faculty_contract_end_date")
        faculty_teaching_hours = request.form.get("faculty_teaching_hours")
        
        # Staff fields
        staff_dept = request.form.get("staff_assigned_department")
        staff_job_title = request.form.get("staff_job_title")
        staff_grade = request.form.get("staff_grade")
        staff_entry = request.form.get("staff_entry_date")
        
        # External fields
        external_org = request.form.get("external_organization")
        external_contact = request.form.get("external_contact_person")
        
        # Validate data
        validation_data = {
            'type': user_type,
            'sub_category': sub_category,
            'first_name': first_name,
            'last_name': last_name,
            'dob': dob,
            'email': email,
            'phone': phone,
            'student_major': student_major,
            'student_entry_year': student_entry_year,
            # student_status intentionally omitted from validation_data
            'student_faculty_department': student_faculty_department,
            'faculty_rank': faculty_rank,
            'faculty_primary_department': faculty_primary_dept,
            'faculty_appointment_start_date': faculty_appt_start,
            'staff_assigned_department': staff_dept,
            'staff_job_title': staff_job_title,
            'staff_entry_date': staff_entry,
            'external_organization': external_org
        }
        
        errors = validate_user_data(validation_data)
        if errors:
            return render_template("create.html", errors=errors)

        uid = generate_id(sub_category)

        try:
            conn = get_db_connection()
            cur = conn.cursor()
            now = datetime.now().isoformat()
            cur.execute("""INSERT INTO People (id,type,sub_category,first_name,last_name,dob,place_of_birth,
                            nationality,gender,email,phone,status,status_changed_at,
                            student_high_school_diploma_type,student_high_school_diploma_year,student_high_school_honors,
                            student_major,student_entry_year,student_status,student_faculty_department,student_group,student_scholarship_status,
                            faculty_rank,faculty_employment_category,faculty_appointment_start_date,
                            faculty_primary_department,faculty_secondary_departments,
                            faculty_office_building,faculty_office_floor,faculty_office_room,
                            faculty_phd_institution,faculty_research_areas,faculty_habilitation_supervise,
                            faculty_contract_type,faculty_contract_start_date,faculty_contract_end_date,faculty_teaching_hours,
                            staff_assigned_department,staff_job_title,staff_grade,staff_entry_date,
                            external_organization,external_contact_person)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (uid,user_type,sub_category,first_name.strip(),last_name.strip(),dob,place_of_birth,nationality,gender,email.strip().lower(),phone,status,now,
                         student_diploma_type,student_diploma_year,student_diploma_honors,
                         student_major,student_entry_year,student_status,student_faculty_department,student_group,student_scholarship,
                         faculty_rank,faculty_employment,faculty_appt_start,
                         faculty_primary_dept,faculty_secondary_depts,
                         faculty_office_building,faculty_office_floor,faculty_office_room,
                         faculty_phd_inst,faculty_research,faculty_habilitation,
                         faculty_contract_type,faculty_contract_start,faculty_contract_end,faculty_teaching_hours,
                         staff_dept,staff_job_title,staff_grade,staff_entry,
                         external_org,external_contact))
            conn.commit()
            conn.close()
            # send confirmation email (print if failure)
            if email:
                send_confirmation(email.strip().lower(), uid)
            return render_template("success.html", 
                                 uid=uid,
                                 identity_type=user_type,
                                 sub_category=sub_category,
                                 first_name=first_name.strip(),
                                 last_name=last_name.strip(),
                                 email=email.strip().lower(),
                                 status=status)
        except Exception as e:
            return render_template("error.html", error=str(e))

    return render_template("create.html")

# ========================
# View All Identities
# ========================
@app.route("/view_all")
def view_all():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM People")
    people = cur.fetchall()
    conn.close()
    return render_template("view_all.html", people=people)

# ========================
# View Single Identity
# ========================
@app.route("/view/<uid>")
def view(uid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM People WHERE id=?", (uid,))
    person = cur.fetchone()
    conn.close()
    if not person:
        return "Identity not found"
    # fetch audit history
    conn2 = get_db_connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT * FROM Audit WHERE person_id=? ORDER BY changed_at DESC", (uid,))
    audits = cur2.fetchall()
    conn2.close()
    return render_template("view.html", person=person, audits=audits)

# ========================
# Delete identity
# ========================
@app.route("/delete/<uid>", methods=["POST"])
def delete(uid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM People WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return redirect("/view_all")

# ========================
# Edit Identity
# ========================
@app.route("/edit/<uid>", methods=["GET","POST"])
def edit(uid):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM People WHERE id=?", (uid,))
    person = cur.fetchone()
    if not person:
        conn.close()
        return "Identity not found"

    if request.method == "POST":
        # Check if trying to edit Archived status (not allowed)
        if person['status'] == 'Archived':
            conn.close()
            return render_template("edit.html", person=person, error="Cannot edit archived identities")
        
        # collect editable fields based on sub_category
        sub_cat = person['sub_category']
        
        # Common fields
        fields = ['first_name', 'last_name', 'status']
        
        # Add sub-category specific fields
        if sub_cat in ['Undergraduate', 'Continuing Education', 'PhD Candidates', 'International/Exchange']:
            fields.extend(['student_high_school_diploma_type', 'student_high_school_diploma_year', 'student_high_school_honors',
                          'student_major', 'student_entry_year', 'student_faculty_department',
                          'student_group', 'student_scholarship_status'])
        elif sub_cat in ['Tenured', 'Adjunct/Part-time', 'Visiting Researchers']:
            fields.extend(['faculty_rank', 'faculty_employment_category', 'faculty_appointment_start_date',
                          'faculty_primary_department', 'faculty_secondary_departments',
                          'faculty_office_building', 'faculty_office_floor', 'faculty_office_room',
                          'faculty_phd_institution', 'faculty_research_areas', 'faculty_habilitation_supervise',
                          'faculty_contract_type', 'faculty_contract_start_date', 'faculty_contract_end_date',
                          'faculty_teaching_hours'])
        elif sub_cat in ['Administrative', 'Technical', 'Temporary']:
            fields.extend(['staff_assigned_department', 'staff_job_title', 'staff_grade', 'staff_entry_date'])
        elif sub_cat in ['Contractors/Vendors', 'Alumni']:
            fields.extend(['external_organization', 'external_contact_person'])
        
        changes = []
        new_status = request.form.get('status')
        old_status = person['status']
        
        # Validate status transition
        if new_status and new_status != old_status:
            if not is_valid_transition(old_status, new_status, person['status_changed_at']):
                conn.close()
                from_to = f"{old_status} → {new_status}"
                if old_status == 'Inactive' and new_status == 'Archived':
                    years_ago = (datetime.now() - datetime.fromisoformat(person['status_changed_at'])).days / 365
                    return render_template("edit.html", person=person, 
                                         error=f"Cannot transition {from_to}: Inactive status requires 5 years before archiving (current: {years_ago:.1f} years)")
                else:
                    return render_template("edit.html", person=person, 
                                         error=f"Invalid status transition: {from_to} is not allowed")
        
        for f in fields:
            new = request.form.get(f)
            old = person[f] if person[f] is not None else ''
            if str(new) != str(old):
                changes.append((f, old, new))
                if f == 'status':
                    # Also update status_changed_at when status changes
                    cur.execute(f"UPDATE People SET {f}=?, status_changed_at=? WHERE id=?", (new, datetime.now().isoformat(), uid))
                else:
                    cur.execute(f"UPDATE People SET {f}=? WHERE id=?", (new, uid))
        if changes:
            now = datetime.now().isoformat()
            for f,old,new in changes:
                cur.execute("INSERT INTO Audit (person_id,changed_at,field,old_value,new_value) VALUES (?,?,?,?,?)",
                            (uid, now, f, old, new))
        conn.commit()
        conn.close()
        return redirect(f"/view/{uid}")

    conn.close()
    return render_template("edit.html", person=person)

# ========================
# Search Identity
# ========================
@app.route("/search", methods=["GET","POST"])
def search():
    results = []
    if request.method == "POST":
        query = request.form.get("query","").strip()
        type_filter = request.form.get("type_filter","")
        status_filter = request.form.get("status_filter","")
        year_filter = request.form.get("year_filter","").strip()
        department_filter = request.form.get("department_filter","").strip()
        
        conn = get_db_connection()
        cur = conn.cursor()
        sql = "SELECT * FROM People WHERE 1=1"
        params = []
        
        # Search by name or email
        if query:
            sql += " AND (first_name LIKE ? OR last_name LIKE ? OR email LIKE ?)"
            params.extend([f"%{query}%"]*3)
        
        # Filter by type
        if type_filter:
            sql += " AND type=?"
            params.append(type_filter)
        
        # Filter by status
        if status_filter:
            sql += " AND status=?"
            params.append(status_filter)
        
        # Filter by year
        if year_filter:
            sql += " AND (entry_year=? OR diploma_year=?)"
            params.extend([year_filter]*2)
        
        # Filter by department
        if department_filter:
            sql += " AND (primary_department LIKE ? OR staff_department LIKE ?)"
            params.extend([f"%{department_filter}%"]*2)
        
        sql += " ORDER BY first_name, last_name"
        cur.execute(sql, tuple(params))
        results = cur.fetchall()
        conn.close()
    
    return render_template("search.html", results=results)

# ========================
# Run Application
# ========================
if __name__ == "__main__":
    init_db()
    print("Starting Flask server...")
    app.run(debug=True, host="127.0.0.5", port=5000)
   
   

