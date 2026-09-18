import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ranking import standings, match_elo_changes, match_elo_history
from storage import Store, StoreError


def register_many(directory, a, b, count):
    store = Store(directory)
    for _ in range(count):
        deadline = time.monotonic() + 20
        while True:
            try:
                store.register(a, b, 11, 5)
                break
            except StoreError as exc:
                if 'busy' not in str(exc) or time.monotonic() > deadline:
                    raise
                time.sleep(0.01)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        # Set this to a NAS directory to exercise the actual mount.
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('PING_PONG_TEST_ROOT'))
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        players, _, _ = self.store.save_player('Alice')
        self.a = players[0]['id']
        players, _, _ = self.store.save_player('Bob')
        self.b = players[1]['id']

    def test_elo_replay_and_statistics(self):
        players, matches, _ = self.store.register(self.a, self.b, 11, 8)
        rows = standings(players, matches)
        self.assertEqual([r['elo'] for r in rows], [1016, 984])
        self.assertEqual(match_elo_changes(players, matches)[matches[0]['id']], 16)
        self.assertEqual((rows[0]['wins'], rows[0]['scored'], rows[0]['conceded']), (1, 11, 8))
        first = matches[0]['id']
        players, matches, _ = self.store.register(self.a, self.b, 0, 11)
        changes = match_elo_changes(players, matches)
        history = match_elo_history(players, matches)
        self.assertEqual(history[matches[-1]['id']]['player1']['before'], 1016)
        self.assertEqual(history[matches[-1]['id']]['player2']['before'], 984)
        self.assertLess(history[matches[-1]['id']]['player1']['change'], 0)
        self.assertGreater(history[matches[-1]['id']]['player2']['change'], 0)
        self.assertEqual(changes[first], 16)
        self.assertAlmostEqual(changes[matches[-1]['id']], 32 / (1 + 10 ** (-32 / 400)))
        self.assertAlmostEqual(sum(r['elo'] for r in standings(players, matches)), 2000)
        players, matches, _ = self.store.delete_match(first)
        rows = standings(players, matches)
        self.assertEqual(rows[0]['player']['id'], self.b)
        self.assertEqual(rows[0]['elo'], 1016)
        self.assertEqual(rows[1]['losses'], 1)
        self.assertEqual(match_elo_changes(players, matches), {matches[0]['id']: 16})

    def test_player_identity_and_retirement(self):
        self.store.register(self.a, self.b, 11, 2)
        self.store.save_player(' Alice, "A" ', self.a)
        players, matches, _ = self.store.set_active(self.a, False)
        self.assertEqual(matches[0]['player1'], self.a)
        self.assertEqual(len(standings(players, matches)), 1)
        with self.assertRaises(StoreError):
            self.store.register(self.a, self.b, 11, 1)
        self.store.set_active(self.a, True)
        players, matches, _ = self.store.snapshot()
        self.assertEqual(players[0]['name'], 'Alice, "A"')
        self.assertEqual(standings(players, matches)[0]['elo'], 1016)

    def test_invalid_inputs_do_not_write(self):
        for name in ('', ' ', ' ALICE ', 'bob'):
            with self.assertRaises(StoreError):
                self.store.save_player(name)
        for a, b, x, y in [(self.a, self.a, 11, 1), (self.a, self.b, -1, 11),
                            (self.a, self.b, 3, 3), (self.a, self.b, 1.5, 11),
                            (self.a, 'missing', 11, 1)]:
            with self.assertRaises(StoreError):
                self.store.register(a, b, x, y)
        self.assertEqual(self.store.snapshot()[1], [])

    def test_failed_replace_preserves_file_and_releases_lock(self):
        before = (Path(self.temp.name) / 'players.csv').read_bytes()
        with patch('storage.os.replace', side_effect=OSError('Disk unavailable')):
            with self.assertRaises(OSError):
                self.store.save_player('Changed', self.a)
        self.assertEqual((Path(self.temp.name) / 'players.csv').read_bytes(), before)
        self.assertFalse((Path(self.temp.name) / '.write-lock').exists())
        self.assertEqual(list(Path(self.temp.name).glob('.save-*')), [])

    def test_malformed_data_is_not_overwritten(self):
        path = Path(self.temp.name) / 'matches.csv'
        path.write_text('bad,columns\n1,2\n')
        with self.assertRaisesRegex(StoreError, 'malformed'):
            self.store.register(self.a, self.b, 11, 3)
        self.assertEqual(path.read_text(), 'bad,columns\n1,2\n')

    def test_busy_lock_is_never_removed(self):
        lock = Path(self.temp.name) / '.write-lock'
        lock.mkdir()
        with self.assertRaisesRegex(StoreError, 'busy'):
            self.store.save_player('Carol')
        self.assertTrue(lock.is_dir())
        lock.rmdir()

    def test_concurrent_processes_do_not_lose_matches(self):
        ctx = multiprocessing.get_context('spawn')
        processes = [ctx.Process(target=register_many,
                                 args=(self.temp.name, self.a, self.b, 10)) for _ in range(3)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(30)
            if process.is_alive():
                process.terminate()
                process.join()
            self.assertEqual(process.exitcode, 0)
        matches = self.store.snapshot()[1]
        self.assertEqual(len(matches), 30)
        self.assertEqual(len({m['id'] for m in matches}), 30)

    def test_unplayed_and_alphabetical_ties(self):
        players, matches, _ = self.store.save_player('Aaron')
        rows = standings(players, matches)
        self.assertEqual([r['player']['name'] for r in rows], ['Aaron', 'Alice', 'Bob'])
        self.assertTrue(all(r['rank'] is None for r in rows))

    def test_last_five_results_put_newest_match_on_the_right(self):
        scores = [(11, 5), (4, 11), (11, 5), (3, 11), (11, 5), (11, 5)]
        for score1, score2 in scores:
            self.store.register(self.a, self.b, score1, score2)

        rows = {row['player']['id']: row for row in standings(*self.store.snapshot()[:2])}
        self.assertEqual(rows[self.a]['last5'], [False, True, False, True, True])
        self.assertEqual(rows[self.b]['last5'], [True, False, True, False, False])


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def wait_for_idle(self, window):
        deadline = time.monotonic() + 10
        while window.worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertIsNone(window.worker)

    def test_tabs_register_refresh_and_failed_input_preservation(self):
        from app import Window
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            players, _, _ = store.save_player('Alice')
            a = players[0]['id']
            players, _, _ = store.save_player('Bob')
            b = players[1]['id']
            window = Window(store)
            self.wait_for_idle(window)
            self.assertEqual(window.tabs.count(), 4)
            window.player1.setCurrentIndex(window.player1.findData(a))
            window.player2.setCurrentIndex(window.player2.findData(b))
            window.score1.setValue(11)
            window.score2.setValue(6)
            window.register()
            self.wait_for_idle(window)
            self.assertEqual(window.history.rowCount(), 1)
            self.assertEqual(window.history.item(0, 2).text(), '1000.00 (+16.00)')
            self.assertEqual(window.history.item(0, 5).text(), '1000.00 (-16.00)')
            self.assertIn('color: green', window.history.cellWidget(0, 2).text())
            self.assertIn('color: red', window.history.cellWidget(0, 5).text())
            self.assertEqual(window.score1.value(), 0)
            self.assertEqual(window.score2.value(), 0)
            self.assertIsNone(window.player1.currentData())
            self.assertIsNone(window.player2.currentData())
            store.save_player('Carol')
            window.tabs.setCurrentIndex(2)
            self.wait_for_idle(window)
            self.assertEqual(window.members.rowCount(), 3)
            window.tabs.setCurrentIndex(1)
            self.wait_for_idle(window)
            self.assertEqual(window.ranking.item(0, 2).text(), '1016')
            window.tabs.setCurrentIndex(0)
            self.wait_for_idle(window)
            window.player1.setCurrentIndex(window.player1.findData(a))
            window.player2.setCurrentIndex(window.player2.findData(b))
            window.score1.setValue(7)
            window.score2.setValue(7)
            with patch('app.QMessageBox.warning') as warning:
                window.register()
                self.wait_for_idle(window)
                warning.assert_called_once()
            self.assertEqual((window.score1.value(), window.score2.value()), (7, 7))
            self.assertEqual(window.player1.currentData(), a)
            with patch('app.QInputDialog.getText', return_value=('Carol', True)), \
                    patch('app.QMessageBox.warning'):
                window.add_player()
                self.wait_for_idle(window)
            self.assertEqual(window.name_drafts[None], 'Carol')
            with patch('app.QInputDialog.getText', return_value=('Dana', True)) as dialog:
                window.add_player()
                self.assertEqual(dialog.call_args.kwargs['text'], 'Carol')
                self.wait_for_idle(window)
            self.assertNotIn(None, window.name_drafts)
            self.assertEqual(window.members.rowCount(), 4)
            window.close()


if __name__ == '__main__':
    unittest.main()
