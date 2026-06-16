"""SessionManager 会话路径安全测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotclaw.memory.store import Session, SessionManager


class TestSessionSecurity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.session_dir = Path(self._tmp.name) / "sessions"
        self.manager = SessionManager(self.session_dir)

    async def asyncTearDown(self):
        self._tmp.cleanup()

    async def test_generated_session_id_can_round_trip(self):
        session = await self.manager.create("安全会话")
        self.assertRegex(session.id, r"^[a-f0-9]{8}$")

        loaded = await self.manager.load(session.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(session.id, loaded.id)

    async def test_invalid_session_id_is_not_loaded_or_deleted(self):
        outside = self.session_dir.parent / "secret.json"
        outside.write_text("secret", encoding="utf-8")

        self.assertIsNone(await self.manager.load("../secret"))
        self.assertFalse(await self.manager.delete("../secret"))
        self.assertTrue(outside.exists())

    async def test_save_rejects_invalid_session_id(self):
        session = Session(
            id="../evil",
            title="bad",
            created_at="2026-06-16T00:00:00",
            updated_at="2026-06-16T00:00:00",
        )

        with self.assertRaises(ValueError):
            await self.manager.save(session)

    async def test_list_all_ignores_invalid_filenames(self):
        valid = await self.manager.create("valid")
        (self.session_dir / "not-a-session.json").write_text("{}", encoding="utf-8")

        sessions = await self.manager.list_all()

        self.assertEqual([valid.id], [s.id for s in sessions])


if __name__ == "__main__":
    unittest.main()
