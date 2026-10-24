# ValidationError ใช้แจ้งข้อมูลไม่ผ่านกฎ เช่น ชื่อคลาสและ Sec ซ้ำ เพื่อให้ view แสดงในฟอร์ม
from django.core.exceptions import ValidationError
# IntegrityError คือข้อผิดพลาดจากข้อบังคับฐานข้อมูล; transaction ใช้ควบคุมการบันทึกเป็นชุด
from django.db import IntegrityError, transaction
# Lower แปลงเป็นตัวพิมพ์เล็ก และ Trim ตัดช่องว่างหัวท้ายในคำสั่งฐานข้อมูล
from django.db.models.functions import Lower, Trim

from .models import (
    Course,
    CourseAnswer,
    CourseQuestion,
    CourseTA,
    Enrollment,
    Material,
    Topic,
    generate_class_code,
)

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction

# เก็บข้อความแจ้งคลาสซ้ำเป็นค่าคงที่ เพื่อให้การสร้างและแก้ไขใช้ข้อความเดียวกัน
DUPLICATE_CLASS = "You already have a class with this name and section. Use a different name or section."


# ตรวจว่ามีชื่อคลาสและ Sec ซ้ำภายใต้เจ้าของคนเดียวกันหรือไม่
# exclude_pk ใช้ยกเว้นคลาสที่กำลังแก้ไข ค่าเริ่มต้น None หมายถึงไม่มีคลาสเฉพาะที่จะยกเว้น
def duplicate_class(owner, name, section, exclude_pk=None):
    # filter จำกัดเจ้าของ; exclude ตัดคลาสตัวเองออก; annotate ตั้งชื่อผลคำนวณเพื่อใช้กรองต่อ
    return Course.objects.filter(owner=owner).exclude(pk=exclude_pk).annotate(
        # ปรับชื่อและ Sec ในฐานข้อมูลให้เป็นตัวพิมพ์เล็กและไม่มีช่องว่างหัวท้ายก่อนเปรียบเทียบ
        normalized_name=Lower(Trim("name")), normalized_section=Lower(Trim("section")),
    # ปรับข้อมูลที่รับมาแบบเดียวกัน แล้ว exists() คืน True ถ้ามีรายการตรงกัน หรือ False ถ้าไม่มี
    ).filter(normalized_name=name.strip().lower(), normalized_section=section.strip().lower()).exists()


# สร้างคลาสใหม่ โดย * บังคับให้ส่งค่าด้วยชื่อ เช่น owner=user; section มีค่าเริ่มต้นเป็นข้อความว่าง
def create_course(*, owner, name, section=""):
    # ตัดช่องว่างหัวท้ายก่อนตรวจซ้ำและบันทึก โดยยังรักษาเลขศูนย์นำหน้าใน Sec เช่น 001
    name, section = name.strip(), section.strip()
    # ตรวจชื่อและ Sec ซ้ำล่วงหน้าเพื่อแจ้งข้อผิดพลาดที่ผู้ใช้เข้าใจได้
    if duplicate_class(owner, name, section):
        # ยก ValidationError เพื่อให้ view จับแล้วแสดงข้อความในฟอร์ม แทนการสร้างคลาสซ้ำ
        raise ValidationError(DUPLICATE_CLASS)
    # การตรวจล่วงหน้าอย่างเดียวกันคำขอที่มาพร้อมกันไม่ได้ จึงยังต้องใช้ unique constraint ของฐานข้อมูล
    # The unique constraint arbitrates concurrent requests, not an exists() precheck.
    # ลองสร้างได้สูงสุด 10 รอบ เผื่อรหัสสุ่มชนกับคลาสเดิม; _ คือค่ารอบที่ไม่ได้ใช้
    for _ in range(10):
        # สุ่มรหัสใหม่ในแต่ละรอบ ไม่ใช้รหัสที่ชนเดิมซ้ำ
        code = generate_class_code()
        # ลองบันทึก และเตรียมจับกรณีฐานข้อมูลปฏิเสธข้อมูล
        try:
            # atomic ครอบการบันทึกรอบนี้ หากเกิด exception จะย้อนการเปลี่ยนแปลงในขอบเขตนี้
            # จับข้อผิดพลาดนอก with เพื่อให้ rollback ของรอบนี้เสร็จก่อนค้นข้อมูลต่อ
            with transaction.atomic():
                # สร้างแถว Course ด้วยข้อมูลที่เตรียมไว้ แล้วคืน object ทันทีเมื่อสำเร็จ จึงไม่วนรอบต่อ
                return Course.objects.create(owner=owner, name=name, section=section, class_code=code)
        # จับข้อผิดพลาดข้อบังคับฐานข้อมูล เช่น unique ของรหัสคลาส หรือคู่ชื่อ/Sec ภายใต้เจ้าของ
        except IntegrityError:
            # ตรวจซ้ำอีกครั้ง เพราะอาจมีอีกคำขอสร้างชื่อและ Sec เดียวกันระหว่างการตรวจครั้งแรกกับการบันทึก
            if duplicate_class(owner, name, section):
                # ถ้าเป็นคลาสซ้ำ ให้แจ้งข้อผิดพลาดเรื่องคลาสซ้ำ ไม่พยายามแก้ด้วยการสุ่มรหัสใหม่
                raise ValidationError(DUPLICATE_CLASS)
            # ถ้ารหัสนี้ไม่มีในฐานข้อมูลด้วย แปลว่าไม่พบหลักฐานว่ารหัสชน จึงไม่กลืนข้อผิดพลาดอื่น
            if not Course.objects.filter(class_code=code).exists():
                # raise ที่ไม่มีค่าโยน exception เดิมต่อ; ถ้ารหัสชนจริงจะไม่เข้าบรรทัดนี้และวนสุ่มใหม่
                raise
    # มาถึงบรรทัดนี้เมื่อครบ 10 รอบแล้วยังสร้างไม่ได้ จึงแจ้งให้ลองใหม่
    raise ValidationError("Could not allocate a class code. Please try again.")


# แก้ชื่อและ Sec ของคลาสเดิม โดยต้องระบุ name และ section ด้วยชื่อพารามิเตอร์
def update_course(course, *, name, section):
    # ตัดช่องว่างหัวท้ายข้อมูลใหม่ก่อนตรวจและบันทึก
    name, section = name.strip(), section.strip()
    # ตรวจชื่อ/Sec ซ้ำกับคลาสอื่นของเจ้าของเดิม ส่ง course.pk เพื่อไม่ให้คลาสตัวเองถูกนับว่าซ้ำ
    if duplicate_class(course.owner, name, section, course.pk):
        # ถ้าซ้ำให้หยุดด้วย ValidationError เพื่อให้ view แสดงในฟอร์ม
        raise ValidationError(DUPLICATE_CLASS)
    # เตรียมจับข้อผิดพลาดที่อาจเกิดขึ้นขณะอัปเดตฐานข้อมูล
    try:
        # ครอบการอัปเดตใน transaction เพื่อย้อนการเปลี่ยนแปลงในขอบเขตนี้เมื่อเกิดข้อผิดพลาด
        with transaction.atomic():
            # เลือกคลาสด้วย primary key แล้วอัปเดตเฉพาะชื่อและ Sec ในฐานข้อมูล
            # QuerySet.update() ไม่เรียก save() ของ object และไม่ได้เปลี่ยนค่า object ในหน่วยความจำโดยตรง
            Course.objects.filter(pk=course.pk).update(name=name, section=section)
    # จับกรณีฐานข้อมูลปฏิเสธการอัปเดต เช่น อีกคำขอเพิ่งสร้างข้อมูลที่ทำให้เกิดค่าซ้ำ
    except IntegrityError:
        # ตรวจว่าเป็นการชนชื่อและ Sec กับคลาสอื่นของเจ้าของคนเดียวกันหรือไม่
        if duplicate_class(course.owner, name, section, course.pk):
            # แปลงข้อผิดพลาดคลาสซ้ำเป็นข้อความสำหรับผู้ใช้
            raise ValidationError(DUPLICATE_CLASS)
        # ถ้าไม่ใช่กรณีซ้ำที่จัดการได้ ให้ส่ง exception เดิมต่อเพื่อไม่ซ่อนปัญหาอื่น
        raise
    # โหลดข้อมูลล่าสุดกลับเข้า course object เพื่อให้ view ส่งค่าที่แก้แล้วได้
    # จำเป็นเพราะ QuerySet.update() ด้านบนเปลี่ยนฐานข้อมูล แต่ไม่อัปเดต object เดิม
    course.refresh_from_db()


# decorator ครอบการเพิ่มสมาชิกทั้งชุดใน transaction เดียว
# ถ้ามี exception หลุดออกจากฟังก์ชัน การเพิ่มในชุดนี้จะถูก rollback
@transaction.atomic
# รับคลาสและรายการ User object ที่ forms คัดกรองแล้ว ฟังก์ชันนี้ไม่ได้ตรวจบทบาทผู้เรียกเอง
def add_students(course, students):
    # เริ่มนับจำนวนสมาชิกที่เพิ่มใหม่จากศูนย์
    added = 0
    # วนทำงานทีละบัญชีในรายการที่ผ่านการตรวจ
    for student in students:
        # ค้น Enrollment ของคู่คลาส/นิสิต ถ้ายังไม่มีจึงสร้างใหม่
        # คืน (object, created): _ รับ object ที่ไม่ได้ใช้ ส่วน created เป็น True เฉพาะเมื่อสร้างใหม่
        # unique constraint ของคู่ course/student ในโมเดลช่วยป้องกันสมาชิกซ้ำด้วย
        _, created = Enrollment.objects.get_or_create(course=course, student=student)
        # แปลง True เป็น 1 และ False เป็น 0 จึงนับเฉพาะสมาชิกใหม่ ไม่นับคนที่อยู่ในคลาสแล้ว
        added += int(created)
    # คืนจำนวนที่เพิ่มใหม่ให้ view ใช้ทำรายงานผลการนำเข้า
    return added

def _topic_match(course, title):
    return (Topic.objects.annotate(key=Lower(Trim("title")))
            .filter(course=course, key=title.lower()).first())


def get_or_create_topic(course, title):
    title = " ".join(title.split())[:120]
    found = _topic_match(course, title)
    if found:
        return found
    try:
        with transaction.atomic():
            return Topic.objects.create(course=course, title=title)
    except IntegrityError:  # another request created it first
        return _topic_match(course, title)


def add_materials(course, topic, files, user):
    created = []
    try:
        with transaction.atomic():
            for upload in files:
                created.append(Material.objects.create(
                    course=course, topic=topic, file=upload,
                    title=upload.name[:150], created_by=user))
    except Exception:
        # Rows rolled back; remove the files already written to disk.
        for material in created:
            material.file.storage.delete(material.file.name)
        raise
    return created




def assign_ta(course, student_id):
    """Assign any active student in the database as a TA by Nisit ID or PK."""
    User = get_user_model()
    target_id = str(student_id).strip()

    # Search by nisit_id or primary key
    student = User.objects.filter(
        models.Q(nisit_id=target_id) | models.Q(pk=target_id),
        is_active=True
    ).first()

    if not student:
        raise ValidationError(f"No active student found with ID '{target_id}'. Check the Nisit ID and try again.")
    if student.is_staff or student.is_superuser:
        raise ValidationError("Staff or department users cannot be assigned as TAs.")

    with transaction.atomic():
        # Ensure student is enrolled so permissions and views allow course access
        Enrollment.objects.get_or_create(course=course, student=student)
        _, created = CourseTA.objects.get_or_create(course=course, user=student)

    return created


def remove_ta(course, student_id):
    deleted, _ = CourseTA.objects.filter(course=course, user_id=student_id).delete()
    return bool(deleted)


def qa_author_role_label(user, course):
    if user.is_superuser:
        return "Department"
    if CourseTA.objects.filter(course=course, user=user).exists():
        return "Teaching Assistant"
    if user.is_staff:
        return "Lecturer"
    return "Student"


def qa_notification_hook(created_item):
    """No-op hook for the notifications iteration."""
    return None


def create_course_question(*, course, author, title, body):
    with transaction.atomic():
        question = CourseQuestion.objects.create(
            course=course,
            author=author,
            author_label=author.get_full_name().strip(),
            author_role_label=qa_author_role_label(author, course),
            title=title,
            body=body,
        )
        transaction.on_commit(lambda: qa_notification_hook(question))
    return question


def create_course_answer(*, question, author, body):
    with transaction.atomic():
        answer = CourseAnswer.objects.create(
            question=question,
            author=author,
            author_label=author.get_full_name().strip(),
            author_role_label=qa_author_role_label(author, question.course),
            body=body,
        )
        transaction.on_commit(lambda: qa_notification_hook(answer))
    return answer