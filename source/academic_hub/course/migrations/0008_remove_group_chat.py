from django.db import migrations


def delete_group_chat_uploads(apps, schema_editor):
    attachment_model = apps.get_model("course", "GroupMessageAttachment")
    file_field = attachment_model._meta.get_field("file")
    storage = file_field.storage
    filenames = (
        attachment_model.objects.using(schema_editor.connection.alias)
        .exclude(file="")
        .values_list("file", flat=True)
    )
    for filename in filenames.iterator():
        storage.delete(filename)


class Migration(migrations.Migration):

    dependencies = [
        ("course", "0007_groupmessage_groupmessageattachment_groupwork_and_more"),
    ]

    operations = [
        migrations.RunPython(delete_group_chat_uploads),
        migrations.DeleteModel(name="GroupMessageAttachment"),
        migrations.DeleteModel(name="GroupMessage"),
    ]
