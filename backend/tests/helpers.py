import json
import threading


CHECK_PROMPT = "You decide whether a question about a database can be answered"


def _wants_check(messages) -> bool:
    return bool(messages) and str(messages[0].get("content", "")).startswith(CHECK_PROMPT)


def _is_check_reply(reply) -> bool:
    return isinstance(reply, dict) and "ambiguous" in reply


class FakeLLM:
    """Scripted stand-in for the LLM. Calls are recorded.

    Replies are handed out in order, except that the question check and the SQL generation can be in flight at the
    same time, so a call takes the first queued reply that fits its kind: a check reply (it has an `ambiguous` key)
    goes to the check, anything else to the other calls. For one call at a time this is plain in-order.
    """

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls: list[list[dict]] = []
        self._lock = threading.Lock()

    def complete(self, messages, *, json_output=False, max_tokens=0):
        check = _wants_check(messages)
        with self._lock:
            self.calls.append(messages)
            if not self.replies:
                raise AssertionError("FakeLLM has no reply queued")
            index = next((i for i, r in enumerate(self.replies) if _is_check_reply(r) == check), 0)
            reply = self.replies.pop(index)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)


def sql_reply(sql, explanation="Does a thing.", **extra):
    return {"sql": sql, "explanation": explanation, "tables_used": [], "assumptions": [],
            "clarification": None, **extra}


def upload_csvs(client, files: dict[str, str]) -> dict:
    """Upload several CSVs (table name -> CSV text) into one new data source."""
    datasource_id = None
    datasource = None
    for name, text in files.items():
        preview = client.post(
            "/api/upload/preview", files={"file": (f"{name}.csv", text.encode(), "text/csv")})
        assert preview.status_code == 200, preview.text
        body = {"upload_id": preview.json()["upload_id"], "datasource_name": "Test data"}
        if datasource_id:
            body["datasource_id"] = datasource_id
        r = client.post("/api/upload/import", json=body)
        assert r.status_code == 201, r.text
        datasource = r.json()
        datasource_id = datasource["id"]
    return datasource
