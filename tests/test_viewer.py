"""Viewer surface (calendar rail, day groups, speaker chips) and backwards paging."""
import json
import unittest

from support import bus, base_env, get, post


class Viewer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._ctx = bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000")
        cls.base, _tmp, _svc = cls._ctx.__enter__()
        cls.env = base_env(AGENT_CHAT_URL=cls.base, AGENT_CHAT_SECRET="s")

    @classmethod
    def tearDownClass(cls):
        cls._ctx.__exit__(None, None, None)

    def test_html_has_calendar_rail_and_day_groups(self):
        st, html = get(self.base + "/")
        self.assertEqual(st, 200)
        self.assertIn('id="cal"', html)
        self.assertIn('class="dayhead', html)          # one collapsible header per day
        self.assertIn('data-act="toggle"', html)
        self.assertIn("localStorage", html)            # collapsed days survive a reload
        self.assertIn("chat_collapsed", html)

    def test_html_has_speaker_filter(self):
        st, html = get(self.base + "/")
        self.assertIn('id="chips"', html)
        self.assertIn('data-act="solo"', html)
        self.assertIn("solo=", html)                   # speaker-only filter state

    def test_html_uses_clock_times_not_per_message_dates(self):
        st, html = get(self.base + "/")
        # per-message element renders HH:MM with the full timestamp only in a tooltip
        self.assertIn("hour:'2-digit',minute:'2-digit',hour12:false", html)
        self.assertIn('title="${esc(fullts(m.ts))}"', html)
        self.assertIn("dayKey(m.ts)", html)            # date lives on the day separator

    def test_html_is_newest_first(self):
        st, html = get(self.base + "/")
        self.assertIn("sort((a,b)=>b.id-a.id)", html)          # descending by id
        self.assertIn('data-act="newest"', html)               # rail jump to the top
        self.assertNotIn("toBottom()", html)
        self.assertIn("function atTop()", html)

    def test_html_compose_is_anchored_on_top(self):
        st, html = get(self.base + "/")
        self.assertLess(html.index('class="compose"'), html.index('id="feed"'),
                        "compose must come before the feed in document order")
        self.assertIn(".compose{position:sticky;top:0", html)
        self.assertIn("--composeH", html)                      # day headers clear it

    def test_html_loads_the_tail_of_the_room(self):
        st, html = get(self.base + "/")
        self.assertIn("/api/tip", html)                # bootstrap from the newest end
        self.assertIn("msgs[0].id>1", html.replace(" ", ""))  # load-earlier control
        self.assertIn("before=", html)

class Paging(unittest.TestCase):
    """Fresh bus per test: ids must be predictable."""

    def setUp(self):
        self._ctx = bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000")
        self.base, _tmp, _svc = self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_before_paging_walks_backwards(self):
        ids = []
        for i in range(6):
            st, body = post(self.base + "/send", {"from": "pager", "topic": "unit",
                                                  "text": f"page msg {i}"})
            ids.append(json.loads(body)["id"])
        st, body = get(self.base + f"/api/messages?before={ids[3]}&limit=2")
        rows = json.loads(body)
        self.assertEqual([r["id"] for r in rows], ids[1:3], "must be the 2 just before, ascending")
        st, body = get(self.base + "/api/messages?before=1&limit=5")
        self.assertEqual(json.loads(body), [], "nothing exists before the first message")
        st, body = get(self.base + f"/api/messages?before={ids[3]}&limit=9")
        got = [r["id"] for r in json.loads(body)]
        self.assertEqual(got[-3:], ids[:3], "the tail of the page is the 3 messages before")
        self.assertTrue(all(i < ids[3] for i in got))

    def test_before_paging_respects_topic_and_telemetry(self):
        post(self.base + "/send", {"from": "pager", "topic": "attention", "text": "telemetry row"})
        st, body = post(self.base + "/send", {"from": "pager", "topic": "unit", "text": "after tel"})
        last = json.loads(body)["id"]
        st, body = get(self.base + f"/api/messages?before={last + 1}&limit=50&topic=unit")
        texts = [r["text"] for r in json.loads(body)]
        self.assertNotIn("telemetry row", texts)
        self.assertTrue(all(r["topic"] == "unit" for r in json.loads(body)))


if __name__ == "__main__":
    unittest.main()


class Roster(unittest.TestCase):
    """Registered agents must be visible even when they have not posted."""

    def setUp(self):
        self._ctx = bus(AGENT_CHAT_RATE_PER_MIN="600", AGENT_CHAT_RATE_BURST="1000")
        self.base, _tmp, _svc = self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_agents_carry_activity(self):
        st, body = post(self.base + "/agents/register",
                        {"name": "quiet-agent",
                         "webhook_url": "http://127.0.0.1:1/wake/quiet-agent"})
        self.assertEqual(st, 200, body)
        rows = json.loads(get(self.base + "/agents")[1])
        row = [r for r in rows if r["name"] == "quiet-agent"][0]
        self.assertEqual(row["messages"], 0)
        self.assertIsNone(row["last_ts"])

        post(self.base + "/send", {"from": "quiet-agent", "topic": "unit", "text": "hello"})
        rows = json.loads(get(self.base + "/agents")[1])
        row = [r for r in rows if r["name"] == "quiet-agent"][0]
        self.assertEqual(row["messages"], 1)
        self.assertIsNotNone(row["last_ts"])

    def test_viewer_renders_quiet_chips(self):
        st, html = get(self.base + "/")
        self.assertIn("chip quiet", html)
        self.assertIn("chat_solo", html)
