# json ใช้แปลง request body ที่เป็น JSON ให้เป็นข้อมูล Python
import json

# นำเข้า messages, ValidationError, JsonResponse และเครื่องมือค้น/เปลี่ยนหน้า/render รวมถึง decorator จำกัด
# HTTP method
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

# นำเข้าฟอร์มสำหรับตรวจข้อมูล โมเดลสำหรับข้อมูล ตัวตรวจสิทธิ์ และ service สำหรับสร้าง/แก้คลาสและเพิ่มสมาชิก
from .forms import CourseForm, CsvRosterForm, JoinForm, RosterForm
from .models import Course, Enrollment
from .permissions import can_teach, course_access, visible_courses
from .services import add_students, create_course, update_course


# api_request คืน boolean ว่า path เริ่ม /course/api/ หรือไม่ ฟังก์ชันนี้ใช้เลือกรูปแบบ response
# ไม่ใช่ตัวตรวจสิทธิ์
def api_request(request):
    return request.path.startswith("/course/api/")


# payload อ่านข้อมูลคำขอ ถ้า Content-Type เป็น application/json ให้ json.loads แปลง body เป็นข้อมูล Python
def payload(request):
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body)
        # หาก JSON เสียหรือถอดรหัสไม่ได้ ให้ยก ValidationError เพื่อนำไปแสดงเป็นข้อผิดพลาดของฟอร์ม
        except (ValueError, UnicodeDecodeError):
            raise ValidationError("Invalid JSON body.")
        # ตรวจว่า JSON เป็น dictionary และค่าทุกตัวเป็น string เช่น section ต้องส่ง "001" ไม่ใช่เลข 1; any คืน True
        # ถ้ามีค่าใดผิดเงื่อนไข
        if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
            raise ValidationError("Provide an object with string values.")
        # คืน dictionary ที่ผ่านตรวจ หรือถ้าไม่ใช่ JSON ให้คืน request.POST ซึ่งเป็นข้อมูลช่องฟอร์ม
        # ไฟล์อัปโหลดอยู่ใน request.FILES แยกต่างหาก
        return data
    return request.POST


# bind_form รับคลาสของฟอร์มแล้วสร้างฟอร์มที่ผูกกับข้อมูลจาก payload
# ยังไม่ได้ยืนยันว่าข้อมูลฟอร์มผ่านจนกว่าจะ is_valid()
def bind_form(request, form_class):
    try:
        return form_class(payload(request))
    # ถ้า payload ผิด ให้สร้าง bound form จาก dictionary ว่าง เรียก is_valid เพื่อให้เกิดโครงสร้างผลตรวจ แล้ว
    # add_error(None, error) เพิ่มข้อผิดพลาดระดับทั้งฟอร์มและคืนฟอร์มนี้
    except ValidationError as error:
        form = form_class({})
        form.is_valid()
        form.add_error(None, error)
        return form


# course_data แปลง Course เป็น dictionary ที่จะส่งออก ประกอบด้วย id ชื่อ Sec และรหัสเจ้าของ
def course_data(course, user):
    data = {"id": course.pk, "name": course.name, "section": course.section, "owner_id": course.owner_id}
    # แนบ class_code เฉพาะผู้ใช้ที่เป็นทั้งเจ้าของและผู้สอน แล้วคืนข้อมูล
    # จึงไม่ส่งรหัสคลาสให้สมาชิกผ่านตัวแปลงนี้
    if course.owner_id == user.pk and can_teach(user):
        data["class_code"] = course.class_code
    return data


# form_error รวมการตอบเมื่อฟอร์มผิด: API ส่งรายละเอียด error เป็น JSON 400; หน้าเว็บแสดง template พร้อม
# form/title/course และสถานะ 400; * บังคับ course/template ให้ส่งด้วยชื่อ
def form_error(request, form, title, *, course=None, template="course/form.html"):
    if api_request(request):
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    return render(request, template, {"form": form, "title": title, "course": course}, status=400)


# dashboard ใช้ course_access() ตรวจ login/active/guest และรับเฉพาะ GET เมื่อมีคำขอเข้ามา wrapper
# ด้านนอกตรวจสิทธิ์ก่อนตัวจำกัด method
@course_access()
@require_http_methods(["GET"])
def dashboard(request):
    # โหลดเฉพาะคลาสที่มีสิทธิ์ พร้อมข้อมูล owner ด้วย select_related เพื่อลด query เพิ่มทีละคลาส ถ้าเป็น API
    # ส่ง list ที่แปลงด้วย course_data
    courses = visible_courses(request.user).select_related("owner")
    if api_request(request):
        return JsonResponse({"courses": [course_data(c, request.user) for c in courses]})
    # กรณีเว็บ render dashboard พร้อมรายการคลาส flag แสดงปุ่มสร้าง/เข้าร่วม และฟอร์มว่างสำหรับทั้งสอง action
    # flag ช่วยแสดง UI แต่การป้องกันจริงยังอยู่ที่ decorator
    return render(request, "course/dashboard.html", {"courses": courses, "can_create": can_teach(request.user),
                  "can_join": not request.user.is_staff and not request.user.is_superuser,
                  "create_form": CourseForm(), "join_form": JoinForm()})


# create อนุญาตผู้สอน รับ GET สำหรับแสดงฟอร์ม และ POST สำหรับส่งข้อมูล
@course_access(lecturer=True)
@require_http_methods(["GET", "POST"])
def create(request):
    # ถ้า POST สร้างฟอร์มผูกข้อมูล ถ้า GET ใช้ฟอร์มว่าง จากนั้นสำหรับ POST เรียก is_valid ซึ่งตรวจฟิลด์และ
    # clean methods ก่อนใช้ cleaned_data
    form = bind_form(request, CourseForm) if request.method == "POST" else CourseForm()
    if request.method == "POST":
        if form.is_valid():
            # เรียก create_course ใน services.py โดยกำหนด owner จากผู้ใช้ที่ login อยู่ และใช้ชื่อ/Sec ที่ผ่านตรวจ
            # ไม่รับ owner จากข้อมูลที่ผู้ใช้ส่งมา
            try:
                course = create_course(owner=request.user, name=form.cleaned_data["name"],
                                       section=form.cleaned_data["section"])
            # ถ้า service แจ้ง ValidationError เช่นชื่อและ Sec ซ้ำ ให้เพิ่ม error ระดับฟอร์ม
            except ValidationError as error:
                form.add_error(None, error)
            # else ของ try ทำงานเมื่อไม่มี exception: API คืนข้อมูลพร้อม 201 Created; เว็บฝากข้อความสำเร็จแล้ว redirect
            # ไป detail ของคลาสใหม่
            else:
                if api_request(request):
                    return JsonResponse(course_data(course, request.user), status=201)
                messages.success(request, "Class created. Share the code with your students.")
                return redirect("course:detail", pk=course.pk)
        # POST ที่ผิดคืน form_error; GET แสดงฟอร์มสร้างคลาส แม้เข้าผ่าน API path ด้วย GET ฟังก์ชันนี้ก็จบด้วยการ
        # render HTML ตามโค้ดนี้
        return form_error(request, form, "Create class")
    return render(request, "course/form.html", {"form": form, "title": "Create class"})


# join จำกัดบทบาทนิสิต รับ GET/POST และตรวจ JoinForm แบบเดียวกับ create
@course_access(student=True)
@require_http_methods(["GET", "POST"])
def join(request):
    form = bind_form(request, JoinForm) if request.method == "POST" else JoinForm()
    if request.method == "POST":
        if form.is_valid():
            # ค้นคลาสจากรหัสที่ผ่านตรวจด้วย filter(...).first() ถ้าไม่พบจะได้ None จึงเพิ่ม error เฉพาะช่อง code
            course = Course.objects.filter(class_code=form.cleaned_data["code"]).first()
            if course is None:
                form.add_error("code", "Class not found. Check the code with your lecturer.")
            # ถ้าพบคลาส ใช้ get_or_create ค้นหรือสร้าง Enrollment โดยคืน (object, created) โค้ดไม่ใช้ object จึงรับด้วย
            # _ ส่วน created บอกว่าเพิ่มใหม่หรือมีอยู่แล้ว
            else:
                _, created = Enrollment.objects.get_or_create(course=course, student=request.user)
                # API คืนข้อมูลคลาสและ joined=created สถานะ 201 เมื่อเพิ่มใหม่หรือ 200 เมื่อเป็นสมาชิกอยู่แล้ว
                # เว็บแสดงข้อความให้ตรงกรณีแล้วไป detail; joined=False ใน response นี้แปลว่าไม่ได้สร้างรายการใหม่
                # ไม่ได้แปลว่าไม่มีสมาชิกภาพ
                if api_request(request):
                    return JsonResponse({"course": course_data(course, request.user), "joined": created}, status=201 if created else 200)
                messages.success(request, "You joined the class." if created else "You are already a member of this class.")
                return redirect("course:detail", pk=course.pk)
        # POST ที่ไม่ผ่านคืน error ส่วน GET แสดงฟอร์มเข้าร่วม
        return form_error(request, form, "Join class")
    return render(request, "course/form.html", {"form": form, "title": "Join class"})


# detail รับ GET และผ่านสิทธิ์ก่อน request.course มาจาก decorator ที่ค้นและตรวจคลาสให้แล้ว
# จึงไม่ต้องค้นซ้ำใน view
@course_access()
@require_http_methods(["GET"])
def detail(request, pk):
    course = request.course
    # API ส่งข้อมูลคลาส เว็บ render รายละเอียดพร้อม can_manage=True เฉพาะเจ้าของที่เป็นผู้สอน
    if api_request(request):
        return JsonResponse(course_data(course, request.user))
    return render(request, "course/detail.html", {"course": course,
                  "can_manage": course.owner_id == request.user.pk and can_teach(request.user)})


# render_members เป็น helper สร้างหน้าสมาชิก รับฟอร์ม/รายงาน/status แบบ optional แล้วใช้คลาสจาก
# request.course
def render_members(request, *, form=None, csv_form=None, import_report=None, status=200):
    course = request.course
    return render(request, "course/members.html", {
        "course": course,
        # อ่าน Enrollment ของคลาสพร้อม student ด้วย select_related และกำหนด flag ว่าจัดการสมาชิกได้หรือไม่
        "members": course.enrollments.select_related("student"),
        "can_manage": course.owner_id == request.user.pk and can_teach(request.user),
        # ใช้ฟอร์มที่ส่งมาเพื่อรักษาค่าที่กรอกและ error หากไม่มีค่อยสร้างฟอร์มว่าง แยกฟอร์มกรอกรหัสกับฟอร์ม CSV
        "roster_form": form if form is not None else RosterForm(),
        "csv_form": csv_form if csv_form is not None else CsvRosterForm(),
        # ส่งรายงานนำเข้าให้ template พร้อม HTTP status ที่ผู้เรียกระบุ ค่าเริ่มต้น 200
        "import_report": import_report,
    }, status=status)


# members_page เป็น endpoint เว็บสำหรับหน้าสมาชิก รับ GET ผ่านสิทธิ์ของคลาสแล้วเรียก helper
# จึงให้สมาชิกคลาสดูรายชื่อได้ แม้จะจัดการรายชื่อไม่ได้
@course_access()
@require_http_methods(["GET"])
def members_page(request, pk):
    return render_members(request)


# edit จำกัดเจ้าของที่เป็นผู้สอน ใช้ request.course; POST ผูกข้อมูลใหม่ GET ใส่ initial เป็นชื่อและ Sec
# เดิมให้ฟอร์ม
@course_access(owner=True)
@require_http_methods(["GET", "POST"])
def edit(request, pk):
    course = request.course
    form = bind_form(request, CourseForm) if request.method == "POST" else CourseForm(initial={"name": course.name, "section": course.section})
    # เมื่อ POST และฟอร์มผ่าน ให้ update_course แก้ชื่อและ Sec ใน service
    if request.method == "POST":
        if form.is_valid():
            try:
                update_course(course, name=form.cleaned_data["name"], section=form.cleaned_data["section"])
            # ถ้า service ปฏิเสธให้เพิ่ม error ลงฟอร์ม ส่วน else ทำงานเมื่อแก้ไขสำเร็จ
            except ValidationError as error:
                form.add_error(None, error)
            else:
                # API คืนข้อมูลที่แก้แล้ว เว็บแจ้งสำเร็จและเปลี่ยนไปหน้ารายละเอียด
                if api_request(request):
                    return JsonResponse(course_data(course, request.user))
                messages.success(request, "Class updated.")
                return redirect("course:detail", pk=pk)
        # เมื่อ POST ผิดใช้ template course/edit.html พร้อม error; GET แสดงหน้าแก้ไขตามปกติ
        return form_error(request, form, "Edit class", course=course, template="course/edit.html")
    return render(request, "course/edit.html", {"form": form, "title": "Edit class", "course": course})


# delete ต้องเป็นเจ้าของผู้สอนและรับ POST เท่านั้น จากนั้นลบ Course ที่ผ่านตรวจสิทธิ์แล้ว Enrollment
# ของคลาสจะถูกลบตาม CASCADE แต่บัญชีนิสิตยังอยู่
@course_access(owner=True)
@require_POST
def delete(request, pk):
    request.course.delete()
    # API คืน deleted=True เว็บแสดงข้อความลบสำเร็จและกลับ dashboard
    if api_request(request):
        return JsonResponse({"deleted": True})
    messages.success(request, "Class deleted.")
    return redirect("course:dashboard")


# members เป็น endpoint ส่งรายชื่อ JSON รับ GET และต้องเข้าถึงคลาสได้ อ่านสมาชิกพร้อม student เพื่อลด query
# ต่อคน
@course_access()
@require_http_methods(["GET"])
def members(request, pk):
    roster = request.course.enrollments.select_related("student")
    # คืน dictionary ที่มีข้อมูลผู้สอนและ list นิสิต แต่ละคนประกอบด้วย id และชื่อเต็มจาก get_full_name()
    # ฟังก์ชันนี้คืน JSON เสมอ
    return JsonResponse({"lecturer": {"id": request.course.owner_id, "name": request.course.owner.get_full_name()},
                         "students": [{"id": e.student_id, "name": e.student.get_full_name()} for e in roster]})


# import_members จำกัดเจ้าของและ POST แล้วสร้าง RosterForm จากข้อมูลที่กรอกหรือ JSON
@course_access(owner=True)
@require_POST
def import_members(request, pk):
    form = bind_form(request, RosterForm)
    # ถ้าฟอร์มผิด เช่นเกินจำนวน ให้เว็บแสดงหน้าสมาชิกพร้อมฟอร์มเดิมและ status 400 ส่วน API ใช้ form_error
    if not form.is_valid():
        if not api_request(request):
            return render_members(request, form=form, status=400)
        return form_error(request, form, "Add students", course=request.course)
    # เมื่อฟอร์มผ่าน เรียก finish_import ด้วย csv=False เพื่อเพิ่มเฉพาะบัญชีที่ใช้ได้และแสดงรายงานผิดรายตัว
    return finish_import(request, form, csv=False)



# remove_member จำกัดเจ้าของและ POST แล้วค้น Enrollment ที่ต้องตรงทั้งคลาสปัจจุบันและ student_id จาก URL
# จึงไม่ไปลบสมาชิกของคลาสอื่น ถ้าไม่พบโยน 404
@course_access(owner=True)
@require_POST
def remove_member(request, pk, student_id):
    enrollment = get_object_or_404(Enrollment, course=request.course, student_id=student_id)
    # ลบเฉพาะความเป็นสมาชิก ไม่ลบบัญชีผู้ใช้ จากนั้น API คืน removed=True หรือเว็บแจ้งผลแล้วกลับหน้าสมาชิก
    enrollment.delete()
    if api_request(request):
        return JsonResponse({"removed": True})
    messages.success(request, "Student removed from the class.")
    return redirect("course:members_page", pk=pk)


# import_csv จำกัดเจ้าของและ POST สร้าง CsvRosterForm ด้วยทั้ง request.POST และ request.FILES
# เพราะเนื้อหาไฟล์ไม่อยู่ใน POST dictionary
@course_access(owner=True)
@require_POST
def import_csv(request, pk):
    form = CsvRosterForm(request.POST, request.FILES)
    # หากไฟล์ผิดเงื่อนไขระดับฟอร์ม API ส่ง errors 400 ส่วนเว็บแสดงหน้าเดิมพร้อมข้อผิดพลาด CSV
    if not form.is_valid():
        if api_request(request):
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        return render_members(request, csv_form=form, status=400)
    # ไฟล์ผ่านตรวจให้เรียก finish_import ด้วย csv=True เพื่อรายงานผลกลับฟอร์ม CSV ที่ถูกตัว
    return finish_import(request, form, csv=True)


# finish_import เป็น helper ใช้ร่วมกัน เรียก service add_students ด้วยเฉพาะ form.students ที่คัดแล้ว
# และรับจำนวนที่เพิ่มใหม่
def finish_import(request, form, *, csv):
    added = add_students(request.course, form.students)
    # สร้างรายงาน: added คือเพิ่มใหม่; already_enrolled คือจำนวนบัญชีที่ผ่านแบบไม่ซ้ำในชุดนี้ลบจำนวนเพิ่มใหม่;
    # duplicates_skipped คือรายการซ้ำในอินพุต; rejected คือรายการผิดพร้อมเหตุผล
    result = {"added": added, "already_enrolled": len(form.students) - added,
              "duplicates_skipped": form.duplicate_count, "rejected": form.rejected}
    # ถ้ามีบัญชีที่ผ่านอย่างน้อยหนึ่งคนให้ 200 แม้ทั้งหมดเป็นสมาชิกเดิม ถ้าไม่มีบัญชีที่ผ่านให้ 400; API
    # ส่งรายงานนี้ทันที
    status = 200 if form.students else 400
    if api_request(request):
        return JsonResponse(result, status=status)
    # ถ้ามีรายการผิด เว็บแสดงหน้าสมาชิกพร้อมรายงาน ** แตก dictionary เป็น keyword arguments และเลือกส่ง
    # csv_form หรือ form ตามประเภทนำเข้า รายการที่ถูกได้ถูกเพิ่มไปแล้ว
    if form.rejected:
        return render_members(request, import_report=result, status=status,
                              **({"csv_form": form} if csv else {"form": form}))
    # ถ้าไม่มีรายการผิด แสดงข้อความจำนวนเพิ่มใหม่/สมาชิกเดิม/รายการซ้ำโดยใช้ f-string แล้ว redirect
    # กลับหน้าสมาชิก
    messages.success(request, f"Added {added} student(s); {result['already_enrolled']} already enrolled; "
                     f"{result['duplicates_skipped']} duplicate row(s) skipped.")
    return redirect("course:members_page", pk=request.course.pk)
