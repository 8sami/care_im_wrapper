from django.urls import path
from rest_framework.routers import DefaultRouter

from care_im_wrapper.api.viewsets import (
    NotificationEventViewSet,
    NotificationRecipientViewSet,
    NotificationTemplateViewSet,
    NotificationTriggerViewSet,
)
from care_im_wrapper.documents.public_views import public_document
from care_im_wrapper.webhooks.providers.meta import MetaWebhookView

router = DefaultRouter()
router.register("notification-triggers", NotificationTriggerViewSet, basename="notification-triggers")
router.register("notification-templates", NotificationTemplateViewSet, basename="notification-templates")
router.register("notification-events", NotificationEventViewSet, basename="notification-events")
router.register("notification-recipients", NotificationRecipientViewSet, basename="notification-recipients")

urlpatterns = [
    path("webhook/meta/", MetaWebhookView.as_view(), name="im-wrapper-webhook-meta"),
    # add providers webhook paths here
    path("public/documents/<str:token>/", public_document, name="im-wrapper-public-document"),
]

urlpatterns += router.urls
