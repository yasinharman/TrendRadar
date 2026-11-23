# Swagger (OpenAPI) belgesinin eksiksiz olduğunu doğrulayan testler
# Yeni bir endpoint eklenip belgesi unutulursa bu testler kırılır

import pytest

# Her endpoint'in belgelemesi gereken durum kodları
DOCUMENTED_STATUS_CODES = {
    ("get", "/health"): {"200", "503"},
    ("post", "/jobs"): {"202", "422"},
    ("get", "/jobs"): {"200", "422"},
    ("get", "/jobs/{job_id}"): {"200", "404", "422"},
    ("delete", "/jobs/{job_id}"): {"204", "404", "409", "422"},
    ("get", "/jobs/{job_id}/results"): {"200", "404", "422"},
    ("get", "/jobs/{job_id}/export.csv"): {"200", "404", "409", "422"},
    ("get", "/keywords/{keyword}/history"): {"200", "404", "422"},
    ("get", "/insights/breakouts"): {"200", "422"},
    ("get", "/insights/diff"): {"200", "404", "409", "422"},
}

JSON = "application/json"
VALIDATION_ERROR_CODE = "422"


@pytest.fixture
def schema(client) -> dict:
    """Uygulamanın ürettiği OpenAPI belgesi."""
    return client.get("/openapi.json").json()


@pytest.fixture
def operations(schema) -> dict[tuple[str, str], dict]:
    """(metot, yol) → operasyon tanımı."""
    return {
        (method, path): operation
        for path, methods in schema["paths"].items()
        for method, operation in methods.items()
    }


def resolve(schema: dict, node: dict) -> dict:
    """$ref ise gösterdiği bileşen şemasını döner."""
    if "$ref" in node:
        return schema["components"]["schemas"][node["$ref"].rsplit("/", 1)[-1]]
    return node


def has_example(schema: dict, node: dict) -> bool:
    """Şemanın kendisinde veya (sayfa zarfıysa) içindeki kayıt şemasında örnek olup olmadığını söyler."""
    node = resolve(schema, node)
    if "example" in node or "examples" in node:
        return True
    items = node.get("properties", {}).get("items", {}).get("items")
    return items is not None and has_example(schema, items)


def test_every_endpoint_is_listed(operations):
    """Belgedeki endpoint'ler beklenen listeyle birebir aynıdır."""
    assert set(operations) == set(DOCUMENTED_STATUS_CODES)


def test_every_endpoint_has_summary_description_and_tag(schema, operations):
    """Her endpoint'in özeti, açıklaması ve açıklaması tanımlı tek bir etiketi vardır."""
    tag_descriptions = {tag["name"]: tag["description"] for tag in schema["tags"]}

    for key, operation in operations.items():
        assert operation.get("summary"), key
        assert operation.get("description"), key
        assert len(operation["tags"]) == 1, key
        assert tag_descriptions.get(operation["tags"][0]), key


def test_documented_status_codes_match_behaviour(operations):
    """Her endpoint, döndürebildiği başarı ve hata kodlarını belgeler."""
    documented = {key: set(operation["responses"]) for key, operation in operations.items()}

    assert documented == DOCUMENTED_STATUS_CODES


def test_every_parameter_is_described(operations):
    """Tüm yol ve query parametrelerinin açıklaması vardır."""
    for key, operation in operations.items():
        for parameter in operation.get("parameters", []):
            assert parameter.get("description"), (key, parameter["name"])


def test_success_responses_have_examples(schema, operations):
    """Gövdesi olan her başarı yanıtı için Swagger'da örnek gösterilir."""
    for key, operation in operations.items():
        for code, response in operation["responses"].items():
            if not code.startswith("2") or "content" not in response:
                continue
            for media_type, content in response["content"].items():
                documented = "example" in content or has_example(schema, content.get("schema", {}))
                assert documented, (key, code, media_type)


def test_error_responses_have_specific_examples(operations):
    """Her hata yanıtı (404/409/503) o duruma özgü örnek bir mesajla belgelenir."""
    for key, operation in operations.items():
        for code, response in operation["responses"].items():
            if code.startswith("2") or code == VALIDATION_ERROR_CODE:
                continue
            example = response["content"][JSON].get("example")
            assert example and example["detail"], (key, code)
            assert response["description"], (key, code)


def test_validation_errors_use_standard_schema(schema, operations):
    """Tüm 422 yanıtları FastAPI'nin standart doğrulama hatası şemasıyla belgelenir."""
    for key, operation in operations.items():
        response = operation["responses"].get(VALIDATION_ERROR_CODE)
        if response is None:
            continue
        assert response["content"][JSON]["schema"] == {"$ref": "#/components/schemas/HTTPValidationError"}, key


def test_request_body_has_example(schema, operations):
    """POST /jobs gövdesi doldurulmuş bir örnekle belgelenir."""
    body = operations[("post", "/jobs")]["requestBody"]["content"][JSON]["schema"]

    assert resolve(schema, body)["example"] == {
        "keywords": ["Camera Bags", "Backpacks", "Tote Bags"],
        "geo": "TR",
        "timeframe": "today 1-m",
    }


def test_timeframe_is_documented_as_enum(schema):
    """timeframe Swagger'da açılır liste olarak görünür."""
    timeframe = schema["components"]["schemas"]["JobCreate"]["properties"]["timeframe"]
    options = next(option for option in timeframe["anyOf"] if "enum" in option)

    assert options["enum"] == ["now 7-d", "today 1-m", "today 3-m", "today 12-m", "today 5-y"]


def test_created_job_documents_location_header(operations):
    """202 yanıtı Location header'ını belgeler."""
    headers = operations[("post", "/jobs")]["responses"]["202"]["headers"]

    assert headers["Location"]["description"]
