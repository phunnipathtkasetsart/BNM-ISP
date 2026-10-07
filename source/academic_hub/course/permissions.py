# wraps ช่วยรักษาชื่อและ metadata ของ view เดิมเมื่อห่อด้วย decorator
from functools import wraps

# messages ใช้ฝากข้อความแจ้งผู้ใช้ และ redirect_to_login ส่งผู้ที่ยังไม่เข้าสู่ระบบไปหน้า login
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
# Q ใช้ประกอบเงื่อนไขการค้นข้อมูลแบบ OR/AND
from django.db.models import Q
# Http404 เป็น exception เมื่อหาไม่พบ; JsonResponse ส่ง JSON; get_object_or_404 ค้นหรือโยน 404; redirect
# เปลี่ยนหน้า; render สร้างหน้า HTML จาก template
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

# นำเข้าตัวตรวจ guest ของโปรเจกต์และโมเดล Course/Enrollment โดยจุดหน้า models หมายถึงโมดูลใน package
# เดียวกัน
from accounts.middleware import is_guest_request
from .models import Course, Enrollment


# can_teach คืน True เมื่อเข้าสู่ระบบ บัญชี active และเป็น staff หรือ superuser ระบบ course นี้ใช้ flags
# สองตัวนี้เป็นเกณฑ์บทบาทผู้สอน
def can_teach(user):
    return user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser)


# visible_courses เริ่มจาก query สมาชิกของผู้ใช้ แล้วเลือกเฉพาะ course_id เพื่อใช้เป็น subquery
# ไม่จำเป็นต้องโหลดทั้ง Enrollment object
def visible_courses(user):
    enrolled = Enrollment.objects.filter(student=user).values("course_id")
    # คืนเฉพาะคลาสที่ผู้ใช้เป็นเจ้าของ OR เป็นสมาชิก เครื่องหมาย | เชื่อม Q แบบ OR; pk__in หมายถึงคีย์อยู่ในผล
    # query สมาชิก เป็น QuerySet ที่ยังนำไปกรองต่อได้
    return Course.objects.filter(Q(owner=user) | Q(pk__in=enrolled))


# course_access เป็นฟังก์ชันสร้าง decorator รับตัวเลือก owner/lecturer/student โดยค่าเริ่มต้น False; *
# บังคับส่งตัวเลือกด้วยชื่อ เช่น owner=True
def course_access(*, owner=False, lecturer=False, student=False):
    """Apply the same scope guard to HTML and API routes, including direct URLs."""
    # decorate รับ view ที่ต้องการป้องกัน แล้วสร้าง wrapped ที่จะทำงานก่อน view; request คือคำขอ *args
    # รับอาร์กิวเมนต์ตำแหน่ง และ **kwargs รับอาร์กิวเมนต์ชื่อ เช่น pk จาก URL
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            # แยกคำขอ API ด้วย path ที่เริ่ม /course/api/ เพื่อเลือกรูปแบบตอบกลับ
            api = request.path.startswith("/course/api/")
            # ถ้ายังไม่ login: API คืน JSON พร้อม 401 ส่วนหน้าเว็บส่งไป login โดยแนบ URL ปัจจุบันสำหรับการกลับมาภายหลัง
            if not request.user.is_authenticated:
                if api:
                    return JsonResponse({"error": "Authentication required."}, status=401)
                return redirect_to_login(request.get_full_path())
            # เริ่มเงื่อนไขปฏิเสธถ้าบัญชีไม่ active หรือ session เป็น guest ต้องตรวจ guest เพิ่มเพราะ guest
            # ของโปรเจกต์นี้มีบัญชีและอาจ is_authenticated=True
            denied = not request.user.is_active or is_guest_request(request)
            # เพิ่มเงื่อนไขปฏิเสธเมื่อ action ต้องการ lecturer แต่ผู้ใช้ไม่ผ่าน can_teach; สำหรับ boolean บรรทัด denied
            # |= condition ทำหน้าที่สะสมเงื่อนไขแบบ OR
            denied |= lecturer and not can_teach(request.user)
            # ถ้า action สำหรับ student แต่ผู้ใช้เป็น staff หรือ superuser ให้ปฏิเสธด้วย
            denied |= student and (request.user.is_staff or request.user.is_superuser)
            # เมื่อ denied เป็น True: API คืน JSON 403 ส่วนเว็บ render หน้า no_access พร้อมสถานะ 403 และ return
            # หยุดการทำงานก่อนถึง view
            if denied:
                if api:
                    return JsonResponse({"error": "You do not have access to this action."}, status=403)
                return render(request, "accounts/no_access.html", status=403)
            # ถ้า URL ส่ง pk มา แปลว่าขอทำงานกับคลาสเฉพาะ จึงเริ่มค้นจากเฉพาะคลาสที่ผู้ใช้มองเห็น
            if "pk" in kwargs:
                courses = visible_courses(request.user)
                # ถ้าต้องเป็นเจ้าของ ให้กรอง owner=current user เพิ่ม และถ้าไม่ใช่ผู้สอนให้ใช้ none() ซึ่งเป็น QuerySet
                # ว่าง แม้เป็น superuser ก็ไม่ได้ข้ามกฎเจ้าของในโค้ดนี้
                if owner:
                    courses = courses.filter(owner=request.user)
                    if not can_teach(request.user):
                        courses = courses.none()
                # ค้นคลาสจากขอบเขตที่ผ่านสิทธิ์พร้อม owner ด้วย select_related แล้วใส่ request.course เพื่อให้ view ใช้ต่อ
                # ถ้าไม่พบ get_object_or_404 จะโยน Http404
                try:
                    request.course = get_object_or_404(courses.select_related("owner"), pk=kwargs["pk"])
                # จับกรณีคลาสไม่มีหรือไม่มีสิทธิ์: API คืน 404 ส่วนเว็บแจ้งเตือนแล้วกลับ dashboard
                # จึงรองรับลิงก์เก่าหลังคลาสถูกลบหรือนิสิตถูกเอาออก
                except Http404:
                    if api:
                        return JsonResponse({"error": "Class not found."}, status=404)
                    messages.warning(request, "This class is no longer available to you. It may have been deleted or your access may have changed. Please contact your lecturer if you need help.")
                    return redirect("course:dashboard")
            # เมื่อผ่านทุกเงื่อนไข จึงเรียก view จริงและคืน response ของ view
            return view(request, *args, **kwargs)
        # return wrapped ส่งฟังก์ชันห่อกลับไป ส่วน return decorate ส่งตัว decorator กลับไป เป็นฟังก์ชันซ้อนกัน 3
        # ชั้นเพื่อรองรับ @course_access(owner=True)
        return wrapped
    return decorate
