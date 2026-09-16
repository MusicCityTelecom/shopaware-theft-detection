"""ShopAware beta.5 application entrypoint.

Loads the validated beta.4 backend, then installs additive per-camera mode
settings before FastAPI starts its inference lifespan.
"""
import backend as core

from shopaware.mode_runtime import install

install(core)
app = core.app
