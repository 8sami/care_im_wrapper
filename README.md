# care_im_wrapper

BE pl Care IM Wrapper

## Local Development

To develop the plug in local environment along with care, follow the steps below:

1. Go to the care root directory and clone the plugin repository:

```bash
cd care
git clone git@github.com:ohcnetwork/care_im_wrapper.git
```

2. Add the plugin config in plug_config.py

```python
...

care_im_wrapper_plugin = Plug(
    name=care_im_wrapper, # name of the django app in the plugin
    package_name="/app/care_im_wrapper", # this has to be /app/ + plugin folder name
    version="", # keep it empty for local development
    configs={}, # plugin configurations if any
)
plugs = [care_im_wrapper_plugin]

...
```

3. Tweak the code in plugs/manager.py, install the plugin in editable mode

```python
...

subprocess.check_call(
    [sys.executable, "-m", "pip", "install", "-e", *packages] # add -e flag to install in editable mode
)

...
```

4. Rebuild the docker image and run the server

```bash
make re-build
make up
```

> [!IMPORTANT]
> Do not push these changes in a PR. These changes are only for local development.

## Production Setup

To install care care_im_wrapper, you can add the plugin config in [care/plug_config.py](https://github.com/ohcnetwork/care/blob/develop/plug_config.py) as follows:

```python
...

care_im_wrapper_plug = Plug(
    name=care_im_wrapper,
    package_name="git+https://github.com/ohcnetwork/care_im_wrapper.git",
    version="@master",
    configs={},
)
plugs = [care_im_wrapper_plug]
...
```

or for development:

```python
...

care_im_wrapper_plug = Plug(
    name="care_im_wrapper",
    package_name="./care_im_wrapper",
    version="",  # Empty for local development
    configs={},
)
plugs = [care_im_wrapper_plug]
...
```

[Extended Docs on Plug Installation](https://care-be-docs.ohc.network/pluggable-apps/configuration.html)



This plugin was created with [Cookiecutter](https://github.com/audreyr/cookiecutter) using the [ohcnetwork/care-plugin-cookiecutter](https://github.com/ohcnetwork/care-plugin-cookiecutter).
