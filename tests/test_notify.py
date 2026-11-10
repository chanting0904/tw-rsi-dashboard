import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import notify


class NotifyTests(unittest.TestCase):
    def test_without_credentials_nothing_is_sent(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(notify.requests, 'post') as post:
            self.assertFalse(notify.alert('test'))
            post.assert_not_called()

    def test_sends_plain_text_to_configured_chat(self):
        with patch.dict(os.environ, {'TG_TOKEN': 't', 'TG_CHAT_ID': 'c'}), \
                patch.object(notify.requests, 'post', return_value=SimpleNamespace(ok=True)) as post:
            self.assertTrue(notify.alert('休市表已更新'))
            self.assertEqual(post.call_args.kwargs['data'], {'chat_id': 'c', 'text': '休市表已更新'})


if __name__ == '__main__':
    unittest.main()
