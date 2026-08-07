# Installation

`care_im_wrapper` is a CARE plugin, not a standalone package. It is a Django app that CARE
loads through `plug_config.py`, so installing it means registering a `Plug` with the CARE
instance that will run it — installing the distribution on its own does nothing.

Requires Python 3.13, matching CARE core.

## Local development

Use this when you are working on the plugin itself and want your edits to take effect
without reinstalling.

1. Clone the plugin **inside** your care checkout, so the Docker bind mount picks it up:

   ```bash
   cd care
   git clone git@github.com:8sami/care_im_wrapper.git
   ```

2. Register it in `plug_config.py`. `package_name` is `/app/` plus the folder name, because
   `/app` is where the care checkout is mounted in the backend container:

   ```python
   from plugs.plug import Plug

   care_im_wrapper_plug = Plug(
       name="care_im_wrapper",     # the django app name inside the plugin
       package_name="/app/care_im_wrapper",
       version="",                 # empty for a local path
       configs={},
   )

   plugs = [care_im_wrapper_plug]
   ```

3. Install it in editable mode, so a code change does not need a rebuild. In
   `plugs/manager.py`, add `-e` to the install call:

   ```python
   subprocess.check_call(
       [sys.executable, "-m", "pip", "install", "-e", *packages]
   )
   ```

4. Rebuild and start the stack from the care root:

   ```bash
   make re-build
   make up
   ```

:::{important}
The `plug_config.py` and `plugs/manager.py` edits are local-only. Do not include them in a
pull request against care.
:::

## Production

Point `package_name` at the repository and pin `version` to a tag or branch:

```python
from plugs.plug import Plug

care_im_wrapper_plug = Plug(
    name="care_im_wrapper",
    package_name="git+https://github.com/8sami/care_im_wrapper.git",
    version="@master",
    configs={},
)

plugs = [care_im_wrapper_plug]
```

Plugins are resolved when the Docker image is built. Rebuild the image after changing
`version`; a pinned tag is preferable to `@master` for anything you intend to reproduce.

## Configuration

Every setting in {py:mod}`care_im_wrapper.settings` is read from three places, in order:

1. the `configs={}` dict of the `Plug` in `plug_config.py`
2. an environment variable of the same name
3. the plugin's own default

### Required

Four settings have no usable default. If any is missing or empty, the plugin raises
`ImproperlyConfigured` naming the setting, and CARE will not start:

| Setting | Where it comes from |
| --- | --- |
| `WHATSAPP_ACCESS_TOKEN` | Meta app → WhatsApp → API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | the sending number's id, not the number itself |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | a string you choose; Meta echoes it back on verification |
| `WHATSAPP_APP_SECRET` | Meta app secret, used to verify the `X-Hub-Signature-256` on every inbound POST |

### Needed for specific features

| Setting | Default | Needed for |
| --- | --- | --- |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | `""` | pulling the template catalogue from Meta |
| `DOCUMENT_LINK_BASE_URL` | `""` | document links; must be publicly reachable, since the patient's phone opens it |

The remaining settings — channel limits, rate limits, timeouts, trigger slugs — are
documented by their defaults in {py:mod}`care_im_wrapper.settings` and rarely need changing.

## After installing

1. **Register the webhook with Meta.** The callback URL is the plugin's webhook path on your
   deployment:

   ```
   https://<your-care-host>/api/care_im_wrapper/webhook/meta/
   ```

   Use the same string for the verify token as `WHATSAPP_WEBHOOK_VERIFY_TOKEN`. Subscribe
   the app to the `messages` field.

2. **Seed the template variable mappings**, which fills in each approved template's
   placeholder expressions:

   ```bash
   docker compose exec -T backend python manage.py seed_notification_variable_mappings
   ```

3. **Run celery *and* celery beat.** The plugin registers three periodic tasks on startup,
   and none of them run without beat:

   | Task | Interval |
   | --- | --- |
   | dispatch pending notification recipients | `NOTIFICATION_DISPATCH_INTERVAL_SECONDS` (120 s) |
   | sync notification templates | `TEMPLATE_SYNC_INTERVAL_SECONDS` (6 h) |
   | send appointment reminders | `APPOINTMENT_REMINDER_SCAN_INTERVAL_SECONDS` (15 min) |

:::{note}
While a Meta number is unverified, it will only message recipients on the app's allowed
recipient list. A send to any other number fails with Meta error `131030`, which looks like a
plugin fault and is not one.
:::

## Verifying the install

```bash
# the plugin's routes are registered
docker compose exec -T backend python manage.py shell -c "
from django.urls import reverse
print(reverse('im-wrapper-webhook-meta'))"

# the triggers seeded by migrations are present
docker compose exec -T backend python manage.py shell -c "
from care_im_wrapper.models.notification import NotificationTrigger
print(NotificationTrigger.objects.count(), 'triggers')"
```

Ten triggers are seeded by migration. See [](notification-triggers.md) for what each one
fires on, and [](usage.md) for driving the chat side.
