from config.settings import *           # noqa: F401,F403
from config import settings as _s
def __getattr__(name):
    return getattr(_s, name)
