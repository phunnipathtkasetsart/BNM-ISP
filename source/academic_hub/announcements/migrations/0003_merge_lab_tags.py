"""Fold the per-course lab tags into one #Lab tag.

The rename was done by hand in a shell, so it never reached anyone else's
database. This does it for everyone on `migrate`.
"""

from django.db import migrations

OLD_SLUGS = ["cs101", "lab-ske"]


def merge_lab_tags(apps, schema_editor):
    Tag = apps.get_model("announcements", "Tag")

    lab = Tag.objects.filter(slug="lab").first()
    if lab is None:
        # Promote cs101 in place so its existing links survive.
        lab = Tag.objects.filter(slug="cs101").first()
        if lab is None:
            # Nothing to merge. A database without either tag gets #Lab from
            # seed_public_content, so creating an empty one here would only
            # leave an orphan chip behind.
            return
        lab.slug, lab.label, lab.kind = "lab", "Lab", "lab"
        lab.save()

    for old in Tag.objects.filter(slug__in=OLD_SLUGS).exclude(pk=lab.pk):
        for announcement in old.announcements.all():
            announcement.tags.add(lab)
            announcement.tags.remove(old)
        for faq in old.faqs.all():
            faq.tags.add(lab)
            faq.tags.remove(old)
        old.delete()


class Migration(migrations.Migration):

    dependencies = [("announcements", "0002_alter_tag_kind")]

    # No reverse: the original split cannot be recovered once merged, and
    # failing loudly would block a rollback that is otherwise harmless.
    operations = [migrations.RunPython(merge_lab_tags, migrations.RunPython.noop)]
