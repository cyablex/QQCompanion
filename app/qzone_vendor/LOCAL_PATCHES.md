# Local compatibility patch

2026-10-20: `core/qzone/client.py` normalizes a response's `data` field before
checking its `ret` member. Qzone like acknowledgements may contain null or an
array. This prevents an AttributeError after the server has already processed
the like. The original upstream attribution and license remain in this folder;
UPSTREAM.json records the original source hashes before this local patch.

2026-11-03: `core/model.py` preserves the recipient of a leading QQ
`@{uin:...,nick:...}` mention as `Comment.reply_uin`. The original content is
retained. This distinguishes replies to the companion from other conversations
inside the same comment thread.

2026-11-03: `core/qzone/api.py` accepts separate root-comment and reply-recipient
identities for nested replies. `commentId`/`commentUin` identify the root, while
the content mentions the nested author. This follows the upstream plugin's
two-level reply handling rather than using the nested author as the root author.
