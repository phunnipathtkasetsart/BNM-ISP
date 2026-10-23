# csv ใช้อ่าน CSV; io ทำข้อความให้เป็นแหล่งอ่านแบบไฟล์ในหน่วยความจำ; re ใช้ตรวจและแยกข้อความด้วย regular
# expression
import csv
import io
import re

# forms ให้คลาสและฟิลด์ของฟอร์ม; get_user_model() คืน User model ที่โปรเจกต์ใช้จริง
from django import forms
from django.contrib.auth import get_user_model


# CourseForm สืบทอด forms.Form เพื่อรับและตรวจข้อมูลสร้าง/แก้ไขคลาส ไม่บันทึกฐานข้อมูลเอง section ใช้
# RegexField เพื่อตรวจรูปแบบ
class CourseForm(forms.Form):
    section = forms.CharField(label="Section (Sec)", max_length=20,
                              widget=forms.TextInput(attrs={"placeholder": "e.g. 001"}))
    name = forms.CharField(label="Class name", max_length=120,
                          widget=forms.TextInput(attrs={"placeholder": "e.g. Software Engineering", "autofocus": True}))
    # กำหนดลำดับแสดงชื่อก่อน Sec แม้ประกาศ section ไว้ก่อน
    field_order = ["name", "section"]


# JoinForm รับรหัสคลาสที่ต้องยาว 6 ตัวพอดี ใส่ตัวอย่าง ปิดการเติมค่าอัตโนมัติ และขอโฟกัสช่อง
class JoinForm(forms.Form):
    code = forms.CharField(label="Class code", max_length=6, min_length=6,
                          widget=forms.TextInput(attrs={"placeholder": "ABC234", "autocomplete": "off", "autofocus": True}))

    # clean_code เป็นวิธีตรวจเพิ่มเติมเฉพาะช่อง code ที่ Django เรียกระหว่าง validation; cleaned_data
    # คือค่าที่ผ่านการตรวจพื้นฐานแล้ว upper() แปลงเป็นตัวพิมพ์ใหญ่ เช่น abc234 เป็น ABC234
    def clean_code(self):
        code = self.cleaned_data["code"].upper()
        # fullmatch ต้องตรงรูปแบบทั้งข้อความ: A–Z หรือเลข 0–9 จำนวน 6 ตัว ถ้าไม่ผ่าน raise ValidationError
        # ให้ฟอร์มแสดงข้อผิดพลาด ถ้าผ่านคืนค่าที่ปรับแล้ว ฟอร์มตรวจรูปแบบเท่านั้น ส่วนมีคลาสจริงหรือไม่ตรวจใน view
        if not re.fullmatch(r"[A-Z0-9]{6}", code):
            raise forms.ValidationError("Enter the 6-character code from your lecturer.")
        return code


# ฟังก์ชันกลางแยกนิสิตที่ใช้ได้ รายการที่ปฏิเสธ และจำนวนรหัสซ้ำ ใช้ร่วมกันทั้งกรอกข้อความและนำเข้า CSV;
# rows เป็นคู่ (ลำดับ/บรรทัด, รหัส)
def classify_students(rows):
    """Resolve valid accounts in one query and report every rejected row."""
    # set comprehension เก็บเฉพาะรหัสตัวเลข 10 หลักเป็นชุด candidates ชุด set ไม่เก็บค่าซ้ำ ส่วน _
    # ละทิ้งเลขบรรทัดในขั้นตอนนี้
    candidates = {value for _, value in rows if re.fullmatch(r"[0-9]{10}", value)}
    # ค้นผู้ใช้ครั้งเดียวด้วย pk__in=candidates และจำกัด active=True, staff=False, superuser=False จากนั้นสร้าง
    # dictionary {รหัส: object} ให้ค้นต่อในหน่วยความจำได้เร็ว pk ของ User ในโปรเจกต์นี้คือ nisit_id
    accounts = {u.pk: u for u in get_user_model().objects.filter(
        pk__in=candidates, is_active=True, is_staff=False, is_superuser=False)}
    # เริ่มรายการ students และ rejected เป็น list ว่าง; seen เป็น set เก็บรหัสที่ผ่านไปแล้ว; duplicates
    # เริ่มจากศูนย์
    students, rejected, seen = [], [], set()
    duplicates = 0
    # วนแต่ละรายการ ถ้ารหัสไม่ใช่เลข 10 หลัก ให้เพิ่มเลขรายการ รหัส และเหตุผลลง rejected
    for line, value in rows:
        if not re.fullmatch(r"[0-9]{10}", value):
            rejected.append({"row": line, "student_id": value,
                             "reason": "Invalid 10-digit IDs: enter one 10-digit student ID."})
        # ถ้ารหัสมีรูปแบบถูกแต่ไม่มีในบัญชีที่ผ่านเงื่อนไข ให้รายงานว่าไม่พบบัญชีนิสิตที่ใช้งานได้
        elif value not in accounts:
            rejected.append({"row": line, "student_id": value,
                             "reason": "No active student account for this ID."})
        # ถ้ารหัสบัญชีที่ผ่านเงื่อนไขเคยพบแล้วในการนำเข้าชุดนี้ เพิ่มตัวนับซ้ำ ไม่เพิ่ม object ซ้ำ
        elif value in seen:
            duplicates += 1
        # กรณีถูกต้องและยังไม่ซ้ำ ให้บันทึกลง seen และเพิ่ม User object ลง students
        else:
            seen.add(value)
            students.append(accounts[value])
    # คืนค่า 3 ค่าเป็น tuple ให้ผู้เรียกนำไปแยกเก็บ รหัสผิดซ้ำหลายแถวจะถูกรายงานหลายแถว ไม่ถูกนับเป็น
    # duplicates เพราะตรวจผิดก่อนตรวจซ้ำ
    return students, rejected, duplicates


# RosterForm รับรหัสหลายคนผ่าน Textarea ยาวไม่เกิน 12,000 ตัวอักษร แสดงคำแนะนำว่าแต่ละรหัส 10
# หลักและไม่เกิน 500 รายการ rows=5 กำหนดความสูงโดยประมาณของช่อง ไม่ใช่จำนวนคนสูงสุด
class RosterForm(forms.Form):
    student_ids = forms.CharField(label="Student IDs", max_length=12000,
                                 help_text="10-digit IDs separated by commas, spaces or new lines. Up to 500 entries. Valid students are added; rejected IDs are reported.",
                                 widget=forms.Textarea(attrs={"rows": 5, "placeholder": "6610540001\n6610540002"}))

    # clean_student_ids ตัดช่องว่างหัวท้าย แล้ว re.split แยกด้วยช่องว่าง/แท็บ/ขึ้นบรรทัดใหม่ (\s), comma หรือ
    # semicolon เครื่องหมาย + ให้กลุ่มตัวคั่นต่อเนื่องเป็นตัวคั่นเดียว
    def clean_student_ids(self):
        ids = re.split(r"[\s,;]+", self.cleaned_data["student_ids"].strip())
        # ถ้าเกิน 500 รายการให้ฟอร์มไม่ผ่าน รวมรายการซ้ำในจำนวนนี้เพราะยังไม่ได้คัดซ้ำ
        if len(ids) > 500:
            raise forms.ValidationError("Add at most 500 student entries at a time.")
        # enumerate(ids, 1) ใส่ลำดับเริ่มที่ 1 ไม่ใช่เลขบรรทัดต้นฉบับของข้อความ แล้ว classify_students แยกผล
        # เก็บเป็น attribute ของ form ได้แก่ students, rejected และ duplicate_count
        self.students, self.rejected, self.duplicate_count = classify_students(list(enumerate(ids, 1)))
        # คืนรายการ User object ที่ใช้ได้ ดังนั้น cleaned_data[student_ids] หลังตรวจสำเร็จเป็น list ของผู้ใช้
        # ไม่ใช่ข้อความดิบ โดยบรรทัดที่ผิดรายตัวอยู่ใน rejected และไม่ทำให้ทั้งฟอร์มล้มทันที
        return self.students


# CsvRosterForm ประกาศ FileField รับไฟล์ พร้อมคำแนะนำและ accept สำหรับกรองรายการไฟล์ในหน้าต่างเลือกไฟล์
# accept เป็นเพียงตัวช่วยฝั่งเบราว์เซอร์
class CsvRosterForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV file",
        help_text="UTF-8 CSV, up to 1 MB / 500 student rows. Header: student_id or nisit_id. One 10-digit ID per row.",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
    )

    # clean_csv_file เริ่มตรวจไฟล์ โดยดึง uploaded file จาก cleaned_data
    def clean_csv_file(self):
        upload = self.cleaned_data["csv_file"]
        # ตรวจนามสกุลแบบไม่สนตัวพิมพ์ใหญ่เล็ก ต้องลงท้าย .csv นี่เป็นการตรวจชื่อไฟล์ ไม่ใช่การยืนยันโครงสร้าง CSV
        # ทั้งหมด
        if not upload.name.lower().endswith(".csv"):
            raise forms.ValidationError("Please upload a .csv file.")
        # ตั้งขีดจำกัด 1,048,576 ไบต์ แล้วตรวจขนาดไฟล์ที่รายงานไว้
        limit = 1024 * 1024
        if upload.size > limit:
            raise forms.ValidationError("CSV must be no larger than 1 MB.")
        # อ่านจริงไม่เกินขีดจำกัดบวก 1 ไบต์ เพื่อรู้ว่าเกินขนาดหรือไม่และไม่อ่านไฟล์ใหญ่ทั้งหมดเข้าหน่วยความจำ
        raw = upload.read(limit + 1)
        if len(raw) > limit:
            raise forms.ValidationError("CSV must be no larger than 1 MB.")
        # ลองแปลงไบต์เป็นข้อความ UTF-8 ด้วย utf-8-sig ซึ่งรองรับ BOM ด้วย หากถอดรหัสไม่ได้ให้แจ้งบันทึกเป็น CSV
        # UTF-8
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise forms.ValidationError("Save the file as CSV UTF-8 and upload it again.")
        # เตรียม list รหัส และ flag ว่ายังไม่อ่านหัวตาราง
        ids = []
        header_seen = False
        # สร้าง csv.reader จาก StringIO(text) ให้ parser อ่านข้อความเหมือนไฟล์ newline ว่างให้จัดการบรรทัดตามรูปแบบ
        # CSV และ strict=True ให้แจ้งข้อผิดพลาดของรูปแบบที่ parser ตรวจพบ
        try:
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            # วนแต่ละแถว ถ้าแถวไม่มีข้อมูล หรือทุกช่องเป็นช่องว่าง ให้ continue ข้ามไปแถวต่อไป
            for row in reader:
                if not row or all(not cell.strip() for cell in row):
                    continue
                # แถวแรกที่ไม่ว่างต้องมีคอลัมน์เดียว และชื่อหลังตัดช่องว่าง/แปลงตัวพิมพ์เล็กต้องเป็น student_id หรือ
                # nisit_id ไม่ตรงให้ฟอร์มไม่ผ่าน
                if not header_seen:
                    if len(row) != 1 or row[0].strip().lower() not in {"student_id", "nisit_id"}:
                        raise forms.ValidationError("Use one column with the header student_id or nisit_id. Download the template below.")
                    # บันทึกว่าอ่าน header แล้ว และข้าม header ไม่ให้นำไปเป็นรหัสนิสิต
                    header_seen = True
                    continue
                # เก็บเลขบรรทัดที่ parser อ่านถึงพร้อมรหัส ถ้ามีคอลัมน์เดียวให้ตัดช่องว่าง ถ้ามีหลายคอลัมน์ให้รวมด้วย comma
                # เพื่อให้ classify_students ตรวจไม่ผ่านและรายงานแถวผิด ไม่แอบรับเฉพาะคอลัมน์แรก
                ids.append((reader.line_num, row[0].strip() if len(row) == 1 else ",".join(row)))
                # จำกัดข้อมูลนิสิตไม่เกิน 500 แถว ไม่รวมหัวตารางและแถวว่างที่ข้ามไปแล้ว
                if len(ids) > 500:
                    raise forms.ValidationError("CSV may contain at most 500 student rows.")
        # จับ csv.Error จาก parser และแสดงข้อผิดพลาดรูปแบบ CSV
        except csv.Error:
            raise forms.ValidationError("Invalid CSV format. Use the template and upload it again.")
        # ถ้าไม่มีรายการข้อมูลเลย เช่น มีเฉพาะ header ให้แจ้งว่าไม่มีรหัสนิสิต
        if not ids:
            raise forms.ValidationError("CSV contains no student IDs.")
        # ใช้ classify_students ตัวเดียวกับการกรอกเอง แล้วคืน uploaded file เป็นค่าของฟิลด์
        # รายการนิสิตที่ผ่านและรายงานผิดเก็บอยู่บน form เพื่อให้ view ใช้งาน
        self.students, self.rejected, self.duplicate_count = classify_students(ids)
        return upload
