# Tek kullanıcılı parola koruması (HTTP Basic Auth)
# Uygulama herkese açık bir adreste yayınlandığında API'yi, Swagger'ı ve web arayüzünü birlikte korur

import base64
import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Parola sorulmayan yollar: konteyner sağlık kontrolü kimlik bilgisi gönderemez
AUTH_EXEMPT_PATHS = frozenset({"/health"})

# Authorization başlığında beklenen şema
BASIC_SCHEME = "basic"

# Tarayıcının kullanıcı adı/parola penceresini açmasını sağlayan başlık
AUTH_CHALLENGE = 'Basic realm="TrendRadar", charset="UTF-8"'

# Yetkisiz isteğe dönen mesaj
UNAUTHORIZED_MESSAGE = "Authentication required"

# Yetkisiz isteğe dönen durum kodu
UNAUTHORIZED_STATUS = 401


def parse_basic_credentials(header: str | None) -> tuple[str, str] | None:
    """`Authorization: Basic <base64>` başlığından (kullanıcı adı, parola) çiftini çıkarır.

    Başlık yoksa, şema Basic değilse veya içerik çözülemiyorsa None döner.
    """
    if not header:
        return None

    # Şemayı ve kodlanmış kısmı ayır
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != BASIC_SCHEME:
        return None
    # Şema doğrulandı

    # base64 → "kullanıcı:parola" metni. Geçersiz base64, ASCII dışı karakter ve bozuk UTF-8
    # hatalarının hepsi ValueError türevidir
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except ValueError:
        return None
    # İçerik çözüldü

    # Parola ":" içerebilir; yalnızca ilk ":" ayırıcıdır
    username, separator, password = decoded.partition(":")
    if not separator:
        return None
    return username, password


def credentials_match(header: str | None, username: str, password: str) -> bool:
    """Başlıktaki kimlik bilgilerinin beklenen kullanıcı adı ve parolayla aynı olduğunu doğrular."""
    credentials = parse_basic_credentials(header)
    if credentials is None:
        return False

    # Karşılaştırma sabit sürede yapılır; yanıt süresinden parola tahmin edilemez.
    # İki karşılaştırma da her zaman çalışır (kısa devre yok)
    username_ok = secrets.compare_digest(credentials[0].encode("utf-8"), username.encode("utf-8"))
    password_ok = secrets.compare_digest(credentials[1].encode("utf-8"), password.encode("utf-8"))
    # Karşılaştırma tamamlandı
    return username_ok and password_ok


class BasicAuthMiddleware:
    """Muaf yollar dışındaki her HTTP isteğinde kullanıcı adı ve parola isteyen ASGI katmanı."""

    def __init__(self, app: ASGIApp, username: str, password: str) -> None:
        """Korunan uygulamayı ve beklenen kimlik bilgilerini saklar."""
        self.app = app
        self.username = username
        self.password = password

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Kimlik bilgisi doğruysa isteği uygulamaya iletir, değilse 401 döner."""
        # HTTP dışı olaylar (açılış/kapanış) ve muaf yollar doğrudan geçer
        if scope["type"] != "http" or scope["path"] in AUTH_EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        # Korunan bir istek

        # Kimlik bilgisi doğruysa isteği ilet
        header = Headers(scope=scope).get("authorization")
        if credentials_match(header, self.username, self.password):
            await self.app(scope, receive, send)
            return
        # Kimlik bilgisi yok veya yanlış

        response = JSONResponse(
            {"detail": UNAUTHORIZED_MESSAGE},
            status_code=UNAUTHORIZED_STATUS,
            headers={"WWW-Authenticate": AUTH_CHALLENGE},
        )
        await response(scope, receive, send)
