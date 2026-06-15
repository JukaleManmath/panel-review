import environ
from .base import *

env = environ.Env()

DEBUG = False

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['.railway.app'])

# Railway terminates SSL at the proxy layer and forwards plain HTTP internally.
# SECURE_SSL_REDIRECT must be False here — Railway's load balancer enforces HTTPS.
# SECURE_PROXY_SSL_HEADER tells Django to trust the X-Forwarded-Proto header
# so request.is_secure() works correctly behind the proxy.
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Static files — WhiteNoise serves them directly from Django (no S3 needed)
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'
