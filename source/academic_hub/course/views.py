# json ใช้แปลง request body ที่เป็น JSON ให้เป็นข้อมูล Python
import json
from datetime import timedelta

from django.db.models import Q
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST
from django.utils import timezone

from django.contrib.auth import get_user_model

from .forms import (CourseForm, CoursePostForm, CsvRosterForm, JoinForm,
                    MaterialEditForm, MaterialUploadForm, RosterForm)
from .models import Course, CoursePost, CourseTA, Enrollment, Material, PostAttachment
from .permissions import can_teach, course_access, visible_courses
from .services import (add_materials, add_students, assign_ta, create_course,
                       get_or_create_topic, remove_ta, update_course)


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
    query = request.GET.get("q", "").strip()
    courses = visible_courses(request.user).select_related("owner")

    # Admin-only search filter
    if request.user.is_superuser and query:
        courses = courses.filter(
            Q(name__icontains=query) |
            Q(section__icontains=query) |
            Q(owner__first_name__icontains=query) |
            Q(owner__last_name__icontains=query)
        ).distinct()

    ta_course_ids = set(
        CourseTA.objects.filter(user=request.user).values_list("course_id", flat=True)
    )

    now = timezone.now()
    upcoming_deadlines = (
        CoursePost.objects.filter(
            course__in=courses,
            deadline__isnull=False,
            deadline__gte=now,
            deadline__lte=now + timedelta(days=7)
        )
        .select_related("course")
        .order_by("deadline")
    )

    if api_request(request):
        return JsonResponse({"courses": [course_data(c, request.user) for c in courses]})

    return render(
        request,
        "course/dashboard.html",
        {
            "courses": courses,
            "query": query,  # Passed to retain search value in input
            "can_create": can_teach(request.user),
            "can_join": not request.user.is_staff and not request.user.is_superuser,
            "ta_course_ids": ta_course_ids,
            "upcoming_deadlines": upcoming_deadlines,
            "create_form": CourseForm(),
            "join_form": JoinForm(),
        },
    )


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


def topic_groups(course):
    """Materials grouped by topic, newest topic first. Files with no topic last."""
    by_topic = {}
    for material in course.materials.order_by("-created_at", "-pk"):
        by_topic.setdefault(material.topic_id, []).append(material)
    groups = [{"title": t.title, "materials": by_topic[t.pk]}
              for t in course.topics.order_by("-pk") if t.pk in by_topic]
    if None in by_topic:
        groups.append({"title": "No topic", "materials": by_topic[None]})
    return groups



def detail_context(request, post_form=None, material_form=None):
    course = request.course
    manage = request.can_manage_content
    if manage and post_form is None:
        post_form = CoursePostForm(course=course)
    if manage and material_form is None:
        material_form = MaterialUploadForm(course=course, auto_id="material_%s")
    groups = topic_groups(course)
    
    now = timezone.now()
    upcoming_deadlines = course.posts.filter(
        deadline__isnull=False, 
        deadline__gte=now,
        deadline__lte=now + timedelta(days=7)
    ).order_by("deadline")

    # Fetch all active DB students not yet assigned as TA for this course
    User = get_user_model()
    ta_user_ids = set(course.ta_links.values_list("user_id", flat=True))
    available_students = User.objects.filter(
        is_active=True, is_staff=False, is_superuser=False
    ).exclude(pk__in=ta_user_ids)

    return {
        "course": course,
        "posts": course.posts.select_related("author").prefetch_related("attachments"),
        "upcoming_deadlines": upcoming_deadlines,
        "post_form": post_form,
        "material_form": material_form,
        "topic_groups": groups,
        "material_count": sum(len(g["materials"]) for g in groups),
        "topics": list(course.topics.order_by("-pk")) if manage else [],
        "can_manage": request.can_manage_roster,
        "can_manage_content": manage,
        "can_delete_any": request.is_owner or request.is_dept_override,

        "enrollments": course.enrollments.select_related("student") if request.can_manage_roster else [],
        "ta_ids": ta_user_ids,
        "available_students": available_students,  # Passed to detail.html for the select dropdown
    }


@course_access()
@require_http_methods(["GET"])
def detail(request, pk):
    if api_request(request):
        data = course_data(request.course, request.user)
        data["can_manage_content"] = request.can_manage_content
        data["can_manage_roster"] = request.can_manage_roster
        return JsonResponse(data)
    return render(request, "course/detail.html", detail_context(request))


def render_members(request, *, form=None, csv_form=None, import_report=None, status=200):
    course = request.course
    return render(request, "course/members.html", {
        "course": course,
        # อ่าน Enrollment ของคลาสพร้อม student ด้วย select_related และกำหนด flag ว่าจัดการสมาชิกได้หรือไม่
        "members": course.enrollments.select_related("student"),
        "ta_ids": set(course.ta_links.values_list("user_id", flat=True)),
        "can_manage": request.can_manage_roster,
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
    tas = set(request.course.ta_links.values_list("user_id", flat=True))
    return JsonResponse({"lecturer": {"id": request.course.owner_id, "name": request.course.owner.get_full_name()},
                         "students": [{"id": e.student_id, "name": e.student.get_full_name(),
                                       "is_ta": e.student_id in tas} for e in roster]})


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


# --- Task 4.3: announcements and attachments ---------------------------------

@course_access(manage_content=True)
@require_POST
def create_post(request, pk):
    form = CoursePostForm(request.POST, request.FILES, course=request.course)
    if not form.is_valid():
        if api_request(request):
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        return render(request, "course/detail.html", detail_context(request, post_form=form), status=400)

    # Validate topic outside atomic transaction to avoid orphaned files on 404
    existing_topic = None
    if form.cleaned_data.get("attachment") and form.cleaned_data.get("add_to_materials"):
        choice = form.cleaned_data["topic"]
        if choice != MaterialUploadForm.NEW_TOPIC:
            existing_topic = get_object_or_404(request.course.topics, pk=choice)

    with transaction.atomic():
        post = form.save(commit=False)
        post.course = request.course
        post.author = request.user
        post.save()

        upload = form.cleaned_data.get("attachment")
        if upload:
            attachment = PostAttachment.objects.create(post=post, file=upload)
            if form.cleaned_data.get("add_to_materials"):
                topic = existing_topic or get_or_create_topic(request.course, form.cleaned_data["new_topic"])
                Material.objects.create(
                    course=request.course, topic=topic, created_by=request.user,
                    title=attachment.original_name[:150], file=attachment.file.name,
                    original_name=attachment.original_name, size=attachment.size
                )

    if api_request(request):
        return JsonResponse({"id": post.pk, "title": post.title}, status=201)
    messages.success(request, "Announcement posted.")
    return redirect("course:detail", pk=pk)

@course_access(manage_content=True)
@require_POST
def delete_post(request, pk, post_id):
    post = get_object_or_404(CoursePost, pk=post_id, course=request.course)
    if not (request.is_owner or request.is_dept_override) and post.author_id != request.user.pk:
        if api_request(request):
            return JsonResponse({"error": "You can only delete your own announcements."}, status=403)
        messages.error(request, "You can only delete your own announcements.")
        return redirect("course:detail", pk=pk)
    post.delete()
    if api_request(request):
        return JsonResponse({"deleted": True})
    messages.success(request, "Announcement deleted.")
    return redirect("course:detail", pk=pk)


def file_download(attachment):
    """
    Helper function to safely stream an attachment file.
    """
    try:
        # Obtain original filename safely
        filename = getattr(attachment, 'original_name', None) or getattr(attachment, 'name', None) or 'attachment'
        
        return FileResponse(
            attachment.file.open('rb'),
            as_attachment=True,
            filename=filename
        )
    except (FileNotFoundError, ValueError, AttributeError):
        raise Http404("Attachment file unavailable")



@course_access()
@require_http_methods(["GET"])
def download_attachment(request, pk, attachment_id):
    attachment = get_object_or_404(PostAttachment, pk=attachment_id, post__course=request.course)
    return file_download(attachment)


# --- Task 4.4: materials ------------------------------------------------------

@course_access(manage_content=True)
@require_POST
def upload_materials(request, pk):
    course = request.course
    form = MaterialUploadForm(request.POST, request.FILES, course=course, auto_id="material_%s")
    if not form.is_valid():
        if api_request(request):
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        return render(request, "course/detail.html",
                      detail_context(request, material_form=form), status=400)

    choice = form.cleaned_data["topic"]
    if choice == MaterialUploadForm.NEW_TOPIC:
        topic = get_or_create_topic(course, form.cleaned_data["new_topic"])
    else:
        topic = get_object_or_404(course.topics, pk=choice)
    created = add_materials(course, topic, form.cleaned_data["files"], request.user)

    if api_request(request):
        return JsonResponse({"added": len(created), "topic": topic.title}, status=201)
    messages.success(request, f"{len(created)} file(s) added to {topic.title}.")
    return redirect("course:detail", pk=pk)


@course_access(manage_content=True)
@require_POST
def edit_material(request, pk, material_id):
    material = get_object_or_404(Material, pk=material_id, course=request.course)
    form = MaterialEditForm(request.POST, course=request.course)
    if not form.is_valid():
        if api_request(request):
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
        messages.error(request, "Could not update the material. Check the name and topic.")
        return redirect("course:detail", pk=pk)
    topic_id = form.cleaned_data["topic"]
    material.title = form.cleaned_data["title"].strip()
    material.topic = get_object_or_404(request.course.topics, pk=topic_id) if topic_id else None
    material.save(update_fields=["title", "topic", "updated_at"])
    if api_request(request):
        return JsonResponse({"id": material.pk, "title": material.title})
    messages.success(request, "Material updated.")
    return redirect("course:detail", pk=pk)


@course_access(manage_content=True)
@require_POST
def delete_material(request, pk, material_id):
    material = get_object_or_404(Material, pk=material_id, course=request.course)
    material.delete()  # signals.py removes the file after commit
    if api_request(request):
        return JsonResponse({"deleted": True})
    messages.success(request, "Material deleted.")
    return redirect("course:detail", pk=pk)


@course_access()
@require_http_methods(["GET"])
def download_material(request, pk, material_id):
    material = get_object_or_404(Material, pk=material_id, course=request.course)
    return file_download(material)



@course_access(manage_content=True)
@require_http_methods(["GET", "POST"])
def edit_post(request, pk, post_id):
    post = get_object_or_404(CoursePost, pk=post_id, course=request.course)

    if not (request.is_owner or request.is_dept_override) and post.author_id != request.user.pk:
        if api_request(request):
            return JsonResponse({"error": "You can only edit your own announcements."}, status=403)
        messages.error(request, "You can only edit your own announcements.")
        return redirect("course:detail", pk=pk)

    if request.method == "POST":
        form = CoursePostForm(request.POST, request.FILES, instance=post, course=request.course)
        if form.is_valid():
            with transaction.atomic():
                post = form.save()

                remove_att = form.cleaned_data.get("remove_attachment") or request.POST.get("remove_attachment") == "true"
                new_upload = form.cleaned_data.get("attachment")

                # If removing attachment or uploading a replacement, delete old attachment(s)
                if remove_att or new_upload:
                    for att in post.attachments.all():
                        att.delete()

                # If a new file was uploaded, attach it
                if new_upload:
                    attachment = PostAttachment.objects.create(post=post, file=new_upload)
                    if form.cleaned_data.get("add_to_materials"):
                        choice = form.cleaned_data.get("topic")
                        if choice == MaterialUploadForm.NEW_TOPIC:
                            topic = get_or_create_topic(request.course, form.cleaned_data.get("new_topic"))
                        elif choice:
                            topic = get_object_or_404(request.course.topics, pk=choice)
                        else:
                            topic = None

                        if topic:
                            Material.objects.create(
                                course=request.course, topic=topic, created_by=request.user,
                                title=attachment.original_name[:150], file=attachment.file.name,
                                original_name=attachment.original_name, size=attachment.size
                            )

            if api_request(request):
                return JsonResponse({"id": post.pk, "title": post.title})
            messages.success(request, "Announcement updated successfully.")
            return redirect("course:detail", pk=pk)
        else:
            if api_request(request):
                return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
            messages.error(request, "Failed to update announcement. Please check your input.")
            return render(request, "course/detail.html", detail_context(request, post_form=form), status=400)

    # Fallback GET handler
    form = CoursePostForm(instance=post, course=request.course)
    if api_request(request):
        return JsonResponse({"id": post.pk, "title": post.title, "body": getattr(post, 'body', '')})

    return render(request, "course/form.html", {
        "form": form,
        "title": "Edit Announcement",
        "course": request.course,
        "post": post,
    })

# --- Task 4.5: teaching assistants (owner only) -------------------------------

@course_access(owner=True)
@require_POST
def ta_assign(request, pk, student_id=None):
    # Retrieve Nisit ID from input form POST body or URL parameter
    target_id = request.POST.get("student_id", "").strip() or student_id
    if not target_id:
        messages.error(request, "Please enter a Nisit ID.")
        return redirect("course:detail", pk=pk)

    try:
        created = assign_ta(request.course, target_id)
    except ValidationError as error:
        if api_request(request):
            return JsonResponse({"error": error.messages[0]}, status=400)
        messages.error(request, error.messages[0])
        return redirect("course:detail", pk=pk)

    if api_request(request):
        return JsonResponse({"assigned": True, "created": created}, status=201 if created else 200)

    messages.success(request, "Student is now a TA." if created else "Student is already a TA.")
    return redirect("course:detail", pk=pk)


@course_access(owner=True)
@require_POST
def ta_remove(request, pk, student_id):
    removed = remove_ta(request.course, student_id)
    if api_request(request):
        return JsonResponse({"removed": removed})
    messages.success(request, "TA role removed." if removed else "That student was not a TA.")
    return redirect("course:detail", pk=pk)