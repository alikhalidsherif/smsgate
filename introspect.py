# /// script
# dependencies = ["huawei-lte-api"]
# ///
import inspect

from huawei_lte_api import api

import pkgutil, importlib

# List all api submodules and their public methods
for importer, modname, ispkg in pkgutil.walk_packages(
    api.__path__, prefix="huawei_lte_api.api."
):
    try:
        mod = importlib.import_module(modname)
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if name in ("object",):
                continue
            methods = [m for m in dir(obj) if not m.startswith("_")]
            if methods:
                print(f"\n[{name}]")
                for m in methods:
                    print(f"  {m}")
    except Exception as e:
        print(f"  ERROR {modname}: {e}")
