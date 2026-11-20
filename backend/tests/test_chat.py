import sqlite3
from contextlib import closing

from app.config import get_settings
from app.services import conversation_service, registry
from app.services.llm_service import LLMError
from tests.helpers import sql_reply, upload_csvs

CLEAR = {"ambiguous": False, "question": None, "options": [], "reason": ""}
ASK = {"ambiguous": True, "question": "How should 'best-performing' be measured?",
       "options": ["Highest total revenue", "Most units sold"], "reason": "No metric named."}
COUNT = "SELECT COUNT(*) AS n FROM customers"


def chat(client, message, conversation_id=None, datasource_id="sample", include_summary=False):
    # these tests script two LLM replies per message; summaries are covered in test_analysis_chat.py
    body = {"datasource_id": datasource_id, "message": message, "include_summary": include_summary}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/api/chat/query", json=body)


def order_count():
    with closing(sqlite3.connect(registry.sample_db_path())) as conn:
        return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


# ------------------------------------------------------- basic conversation

def test_first_message_creates_a_conversation_and_returns_results(client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply(COUNT, "Counts customers.")]
    r = chat(client, "How many customers are there?")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["result"]["rows"] == [[40]]
    assert body["conversation_id"] and body["turn_id"]
    assert len(fake_llm.calls) == 2                       # ambiguity check, then generation

    listing = client.get("/api/chat/conversations").json()
    assert [c["id"] for c in listing] == [body["conversation_id"]]
    assert listing[0]["title"] == "How many customers are there?" and listing[0]["turn_count"] == 1


def test_stored_turns_keep_sql_and_columns_but_not_result_rows(client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply("SELECT customer_id, customer_name FROM customers LIMIT 3")]
    cid = chat(client, "first three customers").json()["conversation_id"]
    detail = client.get(f"/api/chat/conversations/{cid}").json()
    turn = detail["turns"][0]
    assert turn["status"] == "ok" and turn["columns"] == ["customer_id", "customer_name"]
    assert turn["row_count"] == 3 and turn["sql"].startswith("SELECT customer_id")
    assert "rows" not in turn and "result" not in turn


# ----------------------------------------------------------- scenario D

def test_ambiguous_question_asks_for_clarification_without_generating_sql(client, fake_llm):
    fake_llm.replies += [ASK]
    body = chat(client, "Show me the best-performing product.").json()
    assert body["status"] == "clarification"
    assert body["clarification"].startswith("How should")
    assert body["options"] == ["Highest total revenue", "Most units sold"]
    assert body["sql"] is None and body["result"] is None
    assert len(fake_llm.calls) == 1                       # no SQL was requested


def test_answering_the_clarification_generates_sql_with_the_context(client, fake_llm):
    fake_llm.replies += [ASK]
    cid = chat(client, "Show me the best-performing product.").json()["conversation_id"]

    best = ("SELECT p.product_name, SUM(o.quantity * p.price) AS revenue FROM orders o "
            "JOIN products p ON p.product_id = o.product_id GROUP BY p.product_name ORDER BY revenue DESC LIMIT 1")
    fake_llm.replies += [sql_reply(best, "Top product by revenue.", assumptions=["Revenue = quantity x price."])]
    r = chat(client, "Highest total revenue", cid)
    body = r.json()
    assert body["status"] == "ok" and body["conversation_id"] == cid
    assert body["result"]["columns"] == ["product_name", "revenue"] and body["result"]["row_count"] == 1
    assert len(fake_llm.calls) == 2                       # the answer is not checked for ambiguity again
    prompt = fake_llm.calls[1][1]["content"]
    assert "CONVERSATION SO FAR" in prompt
    assert "Show me the best-performing product." in prompt
    assert "Assistant asked for clarification" in prompt and "Most units sold" in prompt
    assert prompt.rstrip().endswith("Highest total revenue")


def test_ambiguity_check_can_be_turned_off(client, fake_llm, monkeypatch):
    monkeypatch.setenv("AMBIGUITY_CHECK", "false")
    get_settings.cache_clear()
    fake_llm.replies += [sql_reply(COUNT)]
    assert chat(client, "How many customers?").json()["status"] == "ok"
    assert len(fake_llm.calls) == 1


def test_unusable_ambiguity_reply_does_not_block_the_question(client, fake_llm):
    fake_llm.replies += ["I am not JSON", sql_reply(COUNT)]
    assert chat(client, "How many customers?").json()["status"] == "ok"


def test_ambiguous_join_paths_ask_which_relationship_without_calling_the_llm(client, fake_llm):
    ds = upload_csvs(client, {
        "orders": "order_id,billing_id,shipping_id\n1,10,20\n2,10,10\n3,20,20\n",
        "addresses": "address_id,city\n10,Oslo\n20,Rome\n",
    })
    for column in ("billing_id", "shipping_id"):
        r = client.post(f"/api/datasources/{ds['id']}/relationships", json={
            "from_table": "orders", "from_column": column, "to_table": "addresses", "to_column": "address_id"})
        assert r.status_code == 201
    body = chat(client, "orders by address city", datasource_id=ds["id"]).json()
    assert body["status"] == "clarification" and len(body["options"]) == 2
    assert any("billing_id" in o for o in body["options"]) and any("shipping_id" in o for o in body["options"])
    assert fake_llm.calls == []                           # decided from the schema alone


# ----------------------------------------------------------- scenario E

def test_follow_up_uses_the_previous_question_and_sql_but_is_generated_and_run_afresh(client, fake_llm):
    first = ("SELECT r.region_name, COUNT(*) AS customers FROM customers c "
             "JOIN regions r ON r.region_id = c.region_id GROUP BY r.region_name ORDER BY customers DESC")
    fake_llm.replies += [CLEAR, sql_reply(first)]
    cid = chat(client, "Show customers by region").json()["conversation_id"]

    second = first + " LIMIT 2"
    fake_llm.replies += [CLEAR, sql_reply(second, "Top two regions.")]
    body = chat(client, "Now show only the top two", cid).json()
    assert body["status"] == "ok" and body["result"]["row_count"] == 2
    assert body["sql"].endswith("LIMIT 2")
    check_prompt, generation_prompt = fake_llm.calls[2][1]["content"], fake_llm.calls[3][1]["content"]
    for prompt in (check_prompt, generation_prompt):
        assert "Show customers by region" in prompt and "GROUP BY r.region_name" in prompt
    assert generation_prompt.rstrip().endswith("Now show only the top two")
    assert "Result columns: region_name, customers (4 rows)" in generation_prompt
    assert len(client.get(f"/api/chat/conversations/{cid}").json()["turns"]) == 2


def test_follow_up_sql_is_validated_like_any_other(client, fake_llm):
    before = order_count()
    fake_llm.replies += [CLEAR, sql_reply(COUNT)]
    cid = chat(client, "How many customers?").json()["conversation_id"]
    fake_llm.replies += [CLEAR, sql_reply("DELETE FROM orders"), sql_reply("SELECT COUNT(*) AS n FROM orders")]
    body = chat(client, "and how many orders?", cid).json()
    assert body["status"] == "ok" and len(body["attempts"]) == 1 and "Only SELECT" in body["attempts"][0]["error"]
    assert body["result"]["rows"] == [[before]] and order_count() == before


def test_a_failed_turn_gives_later_questions_no_context(client, fake_llm):
    fake_llm.replies += [CLEAR] + [sql_reply("SELECT nope FROM orders")] * 3
    first = chat(client, "something impossible").json()
    assert first["status"] == "failed"
    cid = first["conversation_id"]
    fake_llm.replies += [CLEAR, sql_reply(COUNT)]
    assert chat(client, "How many customers?", cid).json()["status"] == "ok"
    assert "CONVERSATION SO FAR" not in fake_llm.calls[-1][1]["content"]
    turns = client.get(f"/api/chat/conversations/{cid}").json()["turns"]
    assert [t["status"] for t in turns] == ["failed", "ok"]          # still recorded for the user


def test_only_the_most_recent_turns_are_used_as_context(client, fake_llm):
    cid = None
    for i in range(8):
        fake_llm.replies += [CLEAR, sql_reply(f"SELECT {i} AS n")]
        cid = chat(client, f"question number {i}", cid).json()["conversation_id"]
    history = conversation_service.recent_history(cid)
    assert [t.seq for t in history] == [3, 4, 5, 6, 7, 8]
    # the 8th message was sent when 7 turns existed, so its prompt holds turns 2-7, not turn 1
    prompt = fake_llm.calls[-1][1]["content"]
    assert 'asked: "question number 1"' in prompt and 'asked: "question number 0"' not in prompt


# ------------------------------------------------------- errors and safety

def test_conversation_errors(client, other_client, fake_llm):
    assert chat(client, "hi", conversation_id="nope").status_code == 404
    assert chat(client, "hi", datasource_id="nope").status_code == 404
    assert client.post("/api/chat/query", json={"datasource_id": "sample", "message": ""}).status_code == 422
    assert client.post("/api/chat/query", json={"datasource_id": "sample", "message": "x" * 1001}).status_code == 422

    fake_llm.replies += [CLEAR, sql_reply(COUNT)]
    cid = chat(client, "How many customers?").json()["conversation_id"]
    # another workspace cannot read, continue or delete it
    assert other_client.get(f"/api/chat/conversations/{cid}").status_code == 404
    assert other_client.delete(f"/api/chat/conversations/{cid}").status_code == 404
    assert chat(other_client, "hi", conversation_id=cid).status_code == 404
    assert other_client.get("/api/chat/conversations").json() == []
    # a conversation is tied to its data source
    ds = upload_csvs(client, {"t": "a\n1\n"})
    assert chat(client, "hi", conversation_id=cid, datasource_id=ds["id"]).status_code == 400


def test_llm_outage_leaves_no_conversation_behind(client, fake_llm):
    fake_llm.replies += [LLMError("The LLM request timed out.", 504)]
    assert chat(client, "How many customers?").status_code == 504
    assert client.get("/api/chat/conversations").json() == []


def test_deleting_a_conversation_and_its_data_source(client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply(COUNT)]
    cid = chat(client, "How many customers?").json()["conversation_id"]
    assert client.delete(f"/api/chat/conversations/{cid}").status_code == 204
    assert client.get(f"/api/chat/conversations/{cid}").status_code == 404

    ds = upload_csvs(client, {"t": "a\n1\n2\n"})
    fake_llm.replies += [CLEAR, sql_reply("SELECT COUNT(*) AS n FROM t")]
    cid = chat(client, "how many rows", datasource_id=ds["id"]).json()["conversation_id"]
    assert [c["id"] for c in client.get(f"/api/chat/conversations?datasource_id={ds['id']}").json()] == [cid]
    client.delete(f"/api/datasources/{ds['id']}")
    assert client.get(f"/api/chat/conversations/{cid}").status_code == 404
    assert conversation_service.list_turns(cid) == []


def test_conversations_are_listed_most_recently_active_first(client, fake_llm):
    ids = []
    for i in range(5):                                    # all created within the same second
        fake_llm.replies += [CLEAR, sql_reply(f"SELECT {i} AS n")]
        ids.append(chat(client, f"conversation {i}").json()["conversation_id"])
    assert [c["id"] for c in client.get("/api/chat/conversations").json()] == ids[::-1]

    # a new message moves an older conversation back to the top
    fake_llm.replies += [CLEAR, sql_reply("SELECT 99 AS n")]
    chat(client, "one more", ids[1])
    assert client.get("/api/chat/conversations").json()[0]["id"] == ids[1]


def test_conversations_can_be_filtered_by_data_source(client, fake_llm):
    fake_llm.replies += [CLEAR, sql_reply(COUNT)]
    chat(client, "How many customers?")
    ds = upload_csvs(client, {"t": "a\n1\n"})
    assert client.get(f"/api/chat/conversations?datasource_id={ds['id']}").json() == []
    assert len(client.get("/api/chat/conversations?datasource_id=sample").json()) == 1
