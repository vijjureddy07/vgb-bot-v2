"""
VGB Delta Bot v2 — Macro News Filter
=======================================
Blocks trading ±15 minutes around high-impact economic events.
"""

import requests
import time as _time
from datetime import datetime, timedelta
import config


class NewsFilter:
    def __init__(self):
        self.events = []
        self.last_fetch = 0

    def _fetch_calendar(self):
        now = _time.time()
        if now - self.last_fetch < config.NEWS_CACHE_SECONDS and self.events:
            return

        try:
            today = datetime.utcnow().strftime('%Y-%m-%d')
            tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%d')
            r = requests.get(config.NEWS_CALENDAR_URL,
                             params={'from': today, 'to': tomorrow}, timeout=10)
            r.raise_for_status()
            data = r.json()

            self.events = []
            if isinstance(data, list):
                for event in data:
                    title = event.get('title', '') or event.get('event', '')
                    event_time = event.get('date', '') or event.get('time', '')

                    is_high_impact = any(
                        kw.lower() in title.lower() for kw in config.NEWS_HIGH_IMPACT
                    )
                    if is_high_impact and event_time:
                        for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S']:
                            try:
                                et = datetime.strptime(event_time[:19], fmt)
                                self.events.append({'title': title, 'time': et})
                                break
                            except:
                                continue

            self.last_fetch = now
            if self.events:
                print(f"[NEWS] {len(self.events)} high-impact events loaded")
        except Exception as e:
            print(f"[NEWS] Calendar fetch error: {e}")

    def is_blocked(self):
        if not config.NEWS_FILTER_ENABLED:
            return False, None
        self._fetch_calendar()
        now = datetime.utcnow()
        window = timedelta(minutes=config.NEWS_BLOCK_MINUTES)
        for event in self.events:
            if event['time'] - window <= now <= event['time'] + window:
                return True, event['title']
        return False, None


news_filter = NewsFilter()
