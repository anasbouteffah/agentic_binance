import json

import httpx
import pytest
import respx

from w2e.square import BASE_URL_V1, BASE_URL_V2, SquareClient, SquareError


def ok(data):
    return httpx.Response(200, json={"code": "000000", "message": None, "data": data})


@respx.mock
def test_text_post_matches_official_request_shape():
    route = respx.post(f"{BASE_URL_V1}/content/add").mock(return_value=ok({"id": 123, "shareLink": "https://l/123"}))
    res = SquareClient("k" * 20).publish("hello $BTC")
    req = route.calls.last.request
    assert req.headers["X-Square-OpenAPI-Key"] == "k" * 20
    assert req.headers["clienttype"] == "binanceSkill"
    assert json.loads(req.content) == {"contentType": 1, "bodyTextOnly": "hello $BTC"}
    assert res.post_id == "123" and res.link == "https://l/123"


@respx.mock
def test_article_with_cover_uploads_and_polls(tmp_path):
    img = tmp_path / "c.png"
    img.write_bytes(b"\x89PNG")
    respx.post(f"{BASE_URL_V2}/image/presignedUrl").mock(return_value=ok({"presignedUrl": "https://s3/u", "fileTicket": "t"}))
    put = respx.put("https://s3/u").mock(return_value=httpx.Response(200))
    respx.post(f"{BASE_URL_V2}/image/imageStatus").mock(side_effect=[ok({"status": 0}), ok({"status": 1, "imageUrl": "https://img/1"})])
    add = respx.post(f"{BASE_URL_V1}/content/add").mock(return_value=ok({"id": 9, "shareLink": None}))
    SquareClient("key", poll_interval=0).publish("body", title="T", image_path=str(img))
    assert put.calls.last.request.headers["Content-Type"] == "image/png"
    assert json.loads(add.calls.last.request.content) == {"contentType": 2, "bodyTextOnly": "body", "title": "T",
                                                          "cover": "https://img/1"}


@respx.mock
def test_short_post_image_goes_to_image_list(tmp_path):
    img = tmp_path / "c.png"
    img.write_bytes(b"x")
    respx.post(f"{BASE_URL_V2}/image/presignedUrl").mock(return_value=ok({"presignedUrl": "https://s3/u", "fileTicket": "t"}))
    respx.put("https://s3/u").mock(return_value=httpx.Response(200))
    respx.post(f"{BASE_URL_V2}/image/imageStatus").mock(return_value=ok({"status": 1, "imageUrl": "https://img/1"}))
    add = respx.post(f"{BASE_URL_V1}/content/add").mock(return_value=ok({"id": 1}))
    SquareClient("key", poll_interval=0).publish("body", image_path=str(img))
    assert json.loads(add.calls.last.request.content)["imageList"] == ["https://img/1"]


@respx.mock
def test_error_codes_and_504():
    route = respx.post(f"{BASE_URL_V1}/content/add")
    route.mock(return_value=httpx.Response(200, json={"code": "220009", "message": "limit"}))
    with pytest.raises(SquareError) as e:
        SquareClient("key").publish("x")
    assert e.value.permanent and "100/day" in str(e.value)
    route.mock(return_value=httpx.Response(504, text="timeout"))
    assert SquareClient("key").publish("x").post_id is None
