# Web arayüzü: statik dosyaların yeri ve kök adresten arayüze yönlendirme
# Arayüz yalnızca mevcut API endpoint'lerini çağırır; burada iş mantığı yoktur

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

# Arayüzün sunulduğu yol ve dosyalarının bulunduğu dizin (app/static)
UI_PATH = "/ui"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

router = APIRouter()


# Swagger belgesinde gösterilmez; API'nin parçası değildir
@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Kök adresi web arayüzüne yönlendirir."""
    return RedirectResponse(f"{UI_PATH}/")
