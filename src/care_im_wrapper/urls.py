from django.urls import path

from care_im_wrapper.webhooks.providers.meta import MetaWebhookView

urlpatterns = [
    path("webhook/meta/", MetaWebhookView.as_view(), name="im-wrapper-webhook-meta"),
    # TODO: Week 2+ path("webhook/telegram/", TelegramWebhookView.as_view(), ...)
]
