"""Course views, one module per feature. Re-exported here for ``urls.py``."""
from .courses import (
    archive_course_view,
    create,
    dashboard,
    delete,
    detail,
    edit,
    join,
    restore_course_view,
)
from .materials import (
    archive_material_view,
    delete_material,
    download_material,
    edit_material,
    restore_material_view,
    upload_materials,
)
from .posts import (
    archive_post_view,
    create_post,
    delete_post,
    download_attachment,
    edit_post,
    restore_post_view,
)
from .qa import (
    qa_answer_archive,
    qa_answer_create,
    qa_answer_edit,
    qa_answer_restore,
    qa_answer_verify,
    qa_question_archive,
    qa_question_detail,
    qa_question_edit,
    qa_question_list,
    qa_question_pin,
    qa_question_restore,
)
from .roster import (
    import_csv,
    import_members,
    members,
    members_page,
    remove_member,
    ta_assign,
    ta_remove,
)

# group_views.py still imports payload from here; it moves to responses in the next step.
from ..responses import payload
