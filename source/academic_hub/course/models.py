# นำเข้า secrets เพื่อสุ่มรหัสด้วยตัวสุ่มที่เหมาะกับข้อมูลที่ไม่ควรเดาได้ง่าย
import secrets

# นำเข้า settings เพื่ออ้างอิง User model ที่โปรเจกต์กำหนดไว้
from django.conf import settings
# นำเข้า RegexValidator สำหรับตรวจรูปแบบข้อความ
from django.core.validators import RegexValidator
# นำเข้าเครื่องมือสร้างโมเดลและฟิลด์ของ Django
from django.db import models
# Lower แปลงข้อความเป็นตัวพิมพ์เล็ก และ Trim ตัดช่องว่างหัวท้ายในคำสั่งฐานข้อมูล
from django.db.models.functions import Lower, Trim


# ประกาศฟังก์ชันสร้างรหัสคลาส: secrets.choice เลือกหนึ่งตัวอักษรจากชุดที่กำหนด ทำซ้ำ 6 ครั้งด้วย range(6)
# แล้ว join รวมเป็นข้อความเดียว เครื่องหมาย _ คือค่ารอบที่ไม่ได้ใช้ ชุดนี้ไม่ใส่ O, 0, I, 1
# เพื่อลดความสับสน รหัสที่สุ่มได้ยังมีโอกาสซ้ำ จึงมี unique และการลองใหม่ใน services.py
def generate_class_code():
    # O/0 and I/1 are omitted so codes are easier to read aloud.
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))


# ประกาศโมเดล Course ที่สืบทอดจาก models.Model ใช้แทนข้อมูลชั้นเรียน และกำหนดโครงสร้างตารางเมื่อสร้างและใช้
# migration
class Course(models.Model):
    # ชื่อคลาสเป็นข้อความยาวได้ไม่เกิน 120 ตัวอักษร
    name = models.CharField(max_length=120)
    # Sec เก็บเป็นข้อความสูงสุด 20 ตัว เพื่อรักษาศูนย์นำหน้า เช่น 001; blank=True
    # อนุญาตค่าว่างในการตรวจระดับโมเดล และ default เป็นข้อความว่าง แต่ CourseForm ปัจจุบันบังคับกรอกตัวเลข
    section = models.CharField(max_length=20, blank=True, default="")
    # owner เป็น ForeignKey ไปยัง User: ผู้ใช้หนึ่งคนเป็นเจ้าของหลายคลาสได้ CASCADE หมายถึงเมื่อลบเจ้าของ
    # คลาสที่อ้างถึงเจ้าของถูกลบตาม related_name ทำให้เรียกย้อนกลับได้ด้วย user.owned_courses.all()
    # การลบคลาสไม่ทำให้เจ้าของถูกลบ
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="owned_courses")
    # class_code ยาวไม่เกิน 6 และห้ามซ้ำในฐานข้อมูล default=generate_class_code
    # ส่งตัวฟังก์ชันให้เรียกเมื่อจำเป็น ไม่ใช่เรียกทันที; editable=False
    # ตัดออกจากฟอร์มที่สร้างอัตโนมัติจากโมเดล แต่ไม่ได้ห้ามแก้ด้วยโค้ด; RegexValidator
    # กำหนดรูปแบบตัวพิมพ์ใหญ่หรือตัวเลข 6 ตัว การ save() ปกติไม่ได้เรียก full_clean() อัตโนมัติ
    class_code = models.CharField(max_length=6, unique=True, default=generate_class_code,
                                  editable=False, validators=[RegexValidator(r"^[A-Z0-9]{6}$")])
    # บันทึกเวลาสร้างอัตโนมัติครั้งแรกด้วย auto_now_add=True ไม่ใช่เวลาแก้ไขล่าสุด
    created_at = models.DateTimeField(auto_now_add=True)

    # Meta เป็นการตั้งค่าโมเดล: ordering เรียงเวลาสร้างใหม่ก่อน เครื่องหมายลบคือเรียงจากมากไปน้อย
    # ถ้าเวลาเท่ากันใช้ pk มากก่อน pk คือ primary key
    class Meta:
        ordering = ["-created_at", "-pk"]
        # กำหนดข้อบังคับฐานข้อมูลว่าคู่ชื่อคลาสและ Sec ภายใต้เจ้าของคนเดียวกันต้องไม่ซ้ำ models.F อ้างถึงคอลัมน์
        # owner; Lower(Trim(...)) ทำให้ไม่แยกตัวพิมพ์ใหญ่เล็กและช่องว่างหัวท้าย เช่น SE กับ se
        # ถือว่าซ้ำเมื่อเจ้าของและ Sec เหมือนกัน ชื่อ constraint ใช้ระบุกฎนี้
        constraints = [models.UniqueConstraint(
            models.F("owner"), Lower(Trim("name")), Lower(Trim("section")),
            name="unique_owner_class_section",
        )]

    # __str__ กำหนดข้อความแทน object เมื่อนำไปแสดงเป็น string โดยคืนชื่อคลาส
    def __str__(self):
        return self.name


# Enrollment เป็นโมเดลกลางสำหรับความสัมพันธ์นิสิตกับคลาส หนึ่งแถวหมายถึงนิสิตหนึ่งคนเป็นสมาชิกหนึ่งคลาส
class Enrollment(models.Model):
    # เชื่อมสมาชิกไปยัง Course ถ้าลบคลาสให้ลบ Enrollment ตาม และอ่านสมาชิกย้อนกลับได้ด้วย
    # course.enrollments.all()
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    # เชื่อมสมาชิกไปยัง User ถ้าลบบัญชีให้ลบ Enrollment ของบัญชีนั้นตาม user.course_enrollments.all()
    # ใช้ดูรายการเข้าร่วมของผู้ใช้
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="course_enrollments")
    # บันทึกเวลาเข้าร่วมเมื่อสร้าง Enrollment
    joined_at = models.DateTimeField(auto_now_add=True)

    # กำหนดว่าคู่ course กับ student ห้ามซ้ำ ป้องกันนิสิตคนเดิมเข้าคลาสเดิมซ้ำแม้มีคำขอพร้อมกัน
    class Meta:
        constraints = [models.UniqueConstraint(fields=["course", "student"],
                                               name="unique_course_student")]
        # เรียงสมาชิกตามชื่อ นามสกุล แล้วรหัสผู้ใช้ __ ใช้เดินผ่านความสัมพันธ์ student ไปยัง first_name/last_name
        # ส่วน student_id คือค่าคีย์ของ ForeignKey ไม่ใช่ object ผู้ใช้ ในโปรเจกต์นี้ User ใช้ nisit_id เป็น
        # primary key
        ordering = ["student__first_name", "student__last_name", "student_id"]
