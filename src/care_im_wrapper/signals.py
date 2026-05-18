from django.db.models.signals import post_save
from django.dispatch import receiver

from care.emr.models.patient import Patient


@receiver(post_save, sender=Patient)
def hook_patient_created(sender, instance, created, **kwargs):
    if created:
        # do something when a Patient is created
        pass
