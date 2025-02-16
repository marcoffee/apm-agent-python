#  BSD 3-Clause License
#
#  Copyright (c) 2019, Elasticsearch BV
#  All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#
#  * Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
#  * Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
#  * Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#  DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
#  FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
#  DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
#  SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
#  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#  OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import pytest  # isort:skip

pytest.importorskip("elasticsearch")  # isort:skip

import json
import os
import urllib.parse

from elasticsearch import VERSION as ES_VERSION
from elasticsearch import Elasticsearch
from elasticsearch.serializer import JSONSerializer

import elasticapm
from elasticapm.conf.constants import TRANSACTION

pytestmark = [pytest.mark.elasticsearch]

if "ES_URL" not in os.environ:
    pytestmark.append(pytest.mark.skip("Skipping elasticsearch test, no ES_URL environment variable"))


document_type = "_doc" if ES_VERSION[0] >= 6 else "doc"


def get_kwargs(document=None, document_kwarg_name="document"):
    if ES_VERSION[0] < 6:
        return {"doc_type": "doc", "body": document} if document else {"doc_type": "doc"}
    if ES_VERSION[0] < 7:
        return {"doc_type": "_doc", "body": document} if document else {"doc_type": "_doc"}
    elif ES_VERSION[0] < 8:
        return {"body": document} if document else {}
    else:
        return {document_kwarg_name: document} if document else {}


class NumberObj:
    def __init__(self, value):
        self.value = value


class SpecialEncoder(JSONSerializer):
    def default(self, obj):
        if isinstance(obj, NumberObj):
            return obj.value
        return JSONSerializer.default(self, obj)

    def force_key_encoding(self, obj):
        if isinstance(obj, dict):

            def yield_key_value(d):
                for key, value in d.items():
                    try:
                        yield self.default(key), self.force_key_encoding(value)
                    except TypeError:
                        yield key, self.force_key_encoding(value)

            return dict(yield_key_value(obj))
        else:
            return obj

    def dumps(self, obj):
        return super(SpecialEncoder, self).dumps(self.force_key_encoding(obj))


@pytest.fixture
def elasticsearch(request):
    """Elasticsearch client fixture."""
    client = Elasticsearch(hosts=os.environ["ES_URL"], serializer=SpecialEncoder())
    client.indices.delete(index="*")
    try:
        yield client
    finally:
        client.indices.delete(index="*")


@pytest.mark.integrationtest
def test_ping(instrument, elasticapm_client, elasticsearch):
    elasticapm_client.begin_transaction("test")
    result = elasticsearch.ping()
    elasticapm_client.end_transaction("test", "OK")
    parsed_url = urllib.parse.urlparse(os.environ["ES_URL"])

    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] == "ES HEAD /"
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["destination"] == {
        "address": parsed_url.hostname,
        "port": parsed_url.port,
        "service": {"name": "", "resource": "elasticsearch", "type": ""},
    }
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_info(instrument, elasticapm_client, elasticsearch):
    elasticapm_client.begin_transaction("test")
    result = elasticsearch.info()
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]

    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] == "ES GET /"
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_create(instrument, elasticapm_client, elasticsearch):
    elasticapm_client.begin_transaction("test")
    elasticsearch.create(index="tweets", id="1", **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticsearch.create(
        index="tweets",
        id="2",
        refresh=True,
        **get_kwargs({"user": "kimchy", "text": "hola"}),
    )
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]

    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 2

    for i, span in enumerate(spans):
        if ES_VERSION[0] >= 5:
            assert span["name"] in (
                "ES PUT /tweets/%s/%d/_create" % (document_type, i + 1),
                "ES PUT /tweets/_create/%d" % (i + 1),
                "ES PUT /tweets/_create/%d?refresh=true" % (i + 1),
            )
        else:
            assert span["name"] == "ES PUT /tweets/%s/%d" % (document_type, i + 1)
        assert span["type"] == "db"
        assert span["subtype"] == "elasticsearch"
        assert span["action"] == "query"
        assert span["context"]["db"]["type"] == "elasticsearch"
        assert "statement" not in span["context"]["db"]
        assert span["context"]["http"]["status_code"] == 201


@pytest.mark.integrationtest
def test_index(instrument, elasticapm_client, elasticsearch):
    elasticapm_client.begin_transaction("test")
    r1 = elasticsearch.index(index="tweets", **get_kwargs({"user": "kimchy", "text": "hola"}))
    r2 = elasticsearch.index(index="tweets", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]

    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 2

    for span in spans:
        assert span["name"] in ("ES POST /tweets/%s" % document_type, "ES POST /tweets/_doc?refresh=true")
        assert span["type"] == "db"
        assert span["subtype"] == "elasticsearch"
        assert span["action"] == "query"
        assert span["context"]["db"]["type"] == "elasticsearch"
        assert "statement" not in span["context"]["db"]
        assert span["context"]["http"]["status_code"] == 201


@pytest.mark.integrationtest
def test_exists(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    result = elasticsearch.exists(id="1", index="tweets", **get_kwargs())
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    assert result
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] == "ES HEAD /tweets/%s/1" % document_type
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.skipif(ES_VERSION[0] < 5, reason="unsupported method")
@pytest.mark.integrationtest
def test_exists_source(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    if ES_VERSION[0] < 7:
        assert elasticsearch.exists_source("tweets", document_type, 1) is True
    else:
        assert bool(elasticsearch.exists_source(index="tweets", id="1", **get_kwargs())) is True
    assert bool(elasticsearch.exists_source(index="tweets", id="1", **get_kwargs())) is True
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]

    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 2

    for span in spans:
        assert span["name"] in ("ES HEAD /tweets/%s/1/_source" % document_type, "ES HEAD /tweets/_source/1")
        assert span["type"] == "db"
        assert span["subtype"] == "elasticsearch"
        assert span["action"] == "query"
        assert span["context"]["db"]["type"] == "elasticsearch"
        assert "statement" not in span["context"]["db"]
        assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_get(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    # this is a fun one. Order pre-6x was (index, id, doc_type), changed to (index, doc_type, id) in 6.x, and reverted
    # to (index, id, doc_type) in 7.x. OK then.
    if ES_VERSION[0] == 6:
        r1 = elasticsearch.get("tweets", document_type, 1)
    else:
        r1 = elasticsearch.get(index="tweets", id="1", **get_kwargs())
    r2 = elasticsearch.get(index="tweets", id="1", **get_kwargs())
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    for r in (r1, r2):
        assert r["found"]
        assert r["_source"] == {"user": "kimchy", "text": "hola"}
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 2

    for span in spans:
        assert span["name"] == "ES GET /tweets/%s/1" % document_type
        assert span["type"] == "db"
        assert span["subtype"] == "elasticsearch"
        assert span["action"] == "query"
        assert span["context"]["db"]["type"] == "elasticsearch"
        assert "statement" not in span["context"]["db"]
        assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_get_source(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", refresh=True, id="1", **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    if ES_VERSION[0] < 7:
        r1 = elasticsearch.get_source("tweets", document_type, 1)
    else:
        r1 = elasticsearch.get_source(index="tweets", id="1", **get_kwargs())
    r2 = elasticsearch.get_source(index="tweets", id="1", **get_kwargs())
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]

    for r in (r1, r2):
        assert r == {"user": "kimchy", "text": "hola"}

    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 2

    for span in spans:
        assert span["name"] in ("ES GET /tweets/%s/1/_source" % document_type, "ES GET /tweets/_source/1")
        assert span["type"] == "db"
        assert span["subtype"] == "elasticsearch"
        assert span["action"] == "query"
        assert span["context"]["db"]["type"] == "elasticsearch"
        assert "statement" not in span["context"]["db"]
        assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_update_document(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    r1 = elasticsearch.update(index="tweets", id=1, body={"doc": {"text": "adios"}}, refresh=True, **get_kwargs())
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    r2 = elasticsearch.get(index="tweets", id="1", **get_kwargs())
    assert r2["_source"] == {"user": "kimchy", "text": "adios"}
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1

    span = spans[0]
    assert span["name"] in ("ES POST /tweets/_update/1", "ES POST /tweets/%s/1/_update" % document_type)
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert "statement" not in span["context"]["db"]
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_search_body(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(
        index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola", "userid": 1})
    )
    elasticapm_client.begin_transaction("test")
    search_query = {"query": {"term": {"user": "kimchy"}}, "sort": ["userid"]}
    result = elasticsearch.search(body=search_query)
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    assert result["hits"]["hits"][0]["_source"] == {"user": "kimchy", "text": "hola", "userid": 1}
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    # Depending on ES_VERSION, could be /_all/_search or /_search, and GET or POST
    assert span["name"] in ("ES GET /_search", "ES GET /_all/_search", "ES POST /_search")
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert json.loads(span["context"]["db"]["statement"]) == json.loads(
        '{"sort": ["userid"], "query": {"term": {"user": "kimchy"}}}'
    ) or json.loads(span["context"]["db"]["statement"]) == json.loads(
        '{"query": {"term": {"user": "kimchy"}}, "sort": ["userid"]}'
    )
    if ES_VERSION[0] >= 6:
        assert span["context"]["db"]["rows_affected"] == 1
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_search_querystring(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    search_query = "user:kimchy"
    result = elasticsearch.search(q=search_query, index="tweets")
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    assert result["hits"]["hits"][0]["_source"] == {"user": "kimchy", "text": "hola"}
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    # Starting in 7.5.1, these turned into POST instead of GET. That detail is
    # unimportant for these tests.
    assert span["name"] in ("ES GET /tweets/_search", "ES POST /tweets/_search")
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert span["context"]["db"]["statement"] == "q=user:kimchy"
    if ES_VERSION[0] >= 6:
        assert span["context"]["db"]["rows_affected"] == 1
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_search_both(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    search_querystring = "text:hola"
    search_query = {"query": {"term": {"user": "kimchy"}}}
    result = elasticsearch.search(body=search_query, q=search_querystring, index="tweets")
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    assert len(result["hits"]["hits"]) == 1
    assert result["hits"]["hits"][0]["_source"] == {"user": "kimchy", "text": "hola"}
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    # Starting in 7.6.0, these turned into POST instead of GET. That detail is
    # unimportant for these tests.
    assert span["name"] in ("ES GET /tweets/_search", "ES POST /tweets/_search")
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert span["context"]["db"]["statement"].startswith('q=text:hola\n\n{"query":')
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_count_body(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    search_query = {"query": {"term": {"user": "kimchy"}}}
    result = elasticsearch.count(body=search_query)
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    assert result["count"] == 1
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    # Depending on ES_VERSION, could be /_all/_count or /_count, and either GET
    # or POST. None of these details actually matter much for this test.
    # Technically no version does `POST /_all/_count` but I added it anyway
    assert span["name"] in ("ES GET /_count", "ES GET /_all/_count", "ES POST /_count", "ES POST /_all/_count")
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert json.loads(span["context"]["db"]["statement"]) == json.loads('{"query": {"term": {"user": "kimchy"}}}')
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_count_querystring(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    search_query = "user:kimchy"
    result = elasticsearch.count(q=search_query, index="tweets")
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    assert result["count"] == 1
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    # Starting in 7.5.1, these turned into POST instead of GET. That detail is
    # unimportant for these tests.
    assert span["name"] in ("ES GET /tweets/_count", "ES POST /tweets/_count")
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert span["context"]["db"]["statement"] == "q=user:kimchy"
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_delete(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    result = elasticsearch.delete(id="1", index="tweets", **get_kwargs())
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)

    span = spans[0]
    assert span["name"] == "ES DELETE /tweets/%s/1" % document_type
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_multiple_indexes(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticsearch.create(index="snaps", id="1", refresh=True, **get_kwargs({"user": "kimchy", "text": "hola"}))
    elasticapm_client.begin_transaction("test")
    result = elasticsearch.search(index=["tweets", "snaps"], q="user:kimchy")
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    # Starting in 7.6.0, these turned into POST instead of GET. That detail is
    # unimportant for these tests.
    assert span["name"] in ("ES GET /tweets,snaps/_search", "ES POST /tweets,snaps/_search")
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.skipif(ES_VERSION[0] >= 7, reason="doc_type unsupported")
@pytest.mark.integrationtest
def test_multiple_indexes_doctypes(instrument, elasticapm_client, elasticsearch):
    elasticsearch.create(index="tweets", doc_type="users", id=1, body={"user": "kimchy", "text": "hola"}, refresh=True)
    elasticsearch.create(index="snaps", doc_type="posts", id=1, body={"user": "kimchy", "text": "hola"}, refresh=True)
    elasticapm_client.begin_transaction("test")
    result = elasticsearch.search(index=["tweets", "snaps"], doc_type=["users", "posts"], q="user:kimchy")
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] == "ES GET /tweets,snaps/users,posts/_search"
    assert span["type"] == "db"
    assert span["subtype"] == "elasticsearch"
    assert span["action"] == "query"
    assert span["context"]["db"]["type"] == "elasticsearch"
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_custom_serializer(instrument, elasticapm_client, elasticsearch):
    if ES_VERSION[0] < 7:
        elasticsearch.index("test-index", document_type, {"2": 1})
    else:
        elasticsearch.index(index="test-index", body={"2": 1})
    elasticapm_client.begin_transaction("test")
    search_query = {"query": {"term": {NumberObj(2): {"value": 1}}}}
    result = elasticsearch.search(index="test-index", body=search_query)
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)
    span = spans[0]
    assert json.loads(span["context"]["db"]["statement"]) == json.loads('{"query":{"term":{"2":{"value":1}}}}')
    assert span["context"]["http"]["status_code"] == 200


@pytest.mark.integrationtest
def test_dropped_span(instrument, elasticapm_client, elasticsearch):
    elasticapm_client.begin_transaction("test")
    with elasticapm.capture_span("test", leaf=True):
        elasticsearch.ping()
    elasticapm_client.end_transaction("test", "OK")

    transaction = elasticapm_client.events[TRANSACTION][0]
    spans = elasticapm_client.spans_for_transaction(transaction)
    assert len(spans) == 1
    span = spans[0]
    assert span["name"] == "test"
