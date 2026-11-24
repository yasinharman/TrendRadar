# Web arayüzü: statik dosyaların sunumu ve kök adresten arayüze yönlendirme
# Arayüz yalnızca mevcut API endpoint'lerini çağırır; burada iş mantığı yoktur

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

# Arayüzün sunulduğu yol ve dosyalarının bulunduğu dizin (app/static)
UI_PATH = "/ui"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Tarayıcı dosyayı saklayabilir ama her kullanımdan önce sunucuya güncel mi diye sorar
REVALIDATE_ALWAYS = "no-cache"

router = APIRouter()


class UIStaticFiles(StaticFiles):
    """Arayüz dosyalarını, tarayıcının eski kopyayı sormadan kullanmasına izin vermeden sunar.

    Bu başlık olmadan tarayıcılar dosyaları bir süre kendi önbelleğinden kullanır; yeni sürüm
    yayınlandığında eski betik yeni sayfayla birlikte çalışıp arayüzü bozabilir. Dosya değişmediyse
    sunucu yalnızca kısa bir "değişmedi" (304) yanıtı döner.
    """

    def file_response(self, *args, **kwargs) -> Response:
        """Dosya yanıtına her seferinde doğrulama isteyen önbellek başlığını ekler."""
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = REVALIDATE_ALWAYS
        return response


# Swagger belgesinde gösterilmez; API'nin parçası değildir
@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Kök adresi web arayüzüne yönlendirir."""
    return RedirectResponse(f"{UI_PATH}/")
