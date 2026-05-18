from celery import current_app, shared_task
from celery.schedules import crontab
from django.utils.timezone import now


@shared_task
def periodic_task_example():
    print("This is an example task.")


@current_app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    # sender.add_periodic_task(
    #     crontab(hour="0", minute="0"),
    #     periodic_task_example.s(),
    #     name="reset_ai_usage_limits",
    # )
    return
