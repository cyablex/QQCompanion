# Local compatibility patch

2026-10-20: `core/qzone/client.py` normalizes a response's `data` field before
checking its `ret` member. Qzone like acknowledgements may contain null or an
array. This prevents an AttributeError after the server has already processed
the like. The original upstream attribution and license remain in this folder;
UPSTREAM.json records the original source hashes before this local patch.
