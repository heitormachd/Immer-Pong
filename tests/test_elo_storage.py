import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ranking import match_elo_history
from storage import Store, StoreError


class EloStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.save_player('Alice')
        players = self.store.save_player('Bob')[0]
        self.a, self.b = [p['id'] for p in players]
        self.path = Path(self.temp.name) / 'matches.csv'

    def legacy(self, fields):
        self.store.register(self.a, self.b, 7, 3)
        self.store.register(self.b, self.a, 7, 2)
        with self.path.open(newline='') as handle:
            rows = list(csv.DictReader(handle))
        with self.path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        return rows

    def test_all_legacy_schemas_backfill_preserve_and_do_not_repeat(self):
        for fields in (Store.LEGACY_MATCH_FIELDS, Store.TOURNAMENT_MATCH_FIELDS, Store.LIVE_MATCH_FIELDS,
                       Store.PREVIOUS_MATCH_FIELDS, Store.BEST_OF_MATCH_FIELDS):
            with self.subTest(fields=fields):
                self.path.unlink(missing_ok=True)
                rows = self.legacy(fields)
                before = self.path.read_bytes()
                backup = self.store.migrate_elo()
                self.assertEqual(backup.read_bytes(), before)
                players, matches, _ = self.store.snapshot()
                self.assertTrue(all(m['first_server'] == m['player1'] for m in matches))
                self.assertEqual((matches[0]['elo1_before'], matches[0]['elo2_before']), (1000, 1000))
                self.assertEqual((matches[1]['elo1_before'], matches[1]['elo2_before']), (984, 1016))
                with self.path.open(newline='') as handle:
                    migrated = list(csv.DictReader(handle))
                for original, updated in zip(rows, migrated):
                    self.assertEqual({k: updated[k] for k in fields}, {k: original[k] for k in fields})
                after = self.path.read_bytes()
                self.assertIsNone(self.store.migrate_elo())
                self.assertEqual(self.path.read_bytes(), after)
                with patch('ranking._replay', side_effect=AssertionError('Must use saved Elo')):
                    self.assertEqual(match_elo_history(players, matches)[matches[1]['id']]['player1']['before'], 984)

    def test_deletion_rewrites_later_ratings(self):
        self.store.register(self.a, self.b, 7, 0)
        self.store.register(self.a, self.b, 7, 0)
        matches = self.store.snapshot()[1]
        self.assertEqual(matches[1]['elo1_before'], 1016)
        self.store.delete_match(matches[0]['id'])
        saved = self.store.snapshot()[1][0]
        self.assertEqual((saved['elo1_before'], saved['elo2_before']), (1000, 1000))

    def test_undo_and_recompletion_refresh_later_ratings(self):
        match = self.store.create_live_match(self.a, self.b, 2)[1][-1]
        self.store.set_first_server(match['id'], self.a, 0)
        self.assertIsNone(match['elo1_before'])
        self.store.score_live_match(match['id'], self.a, 1)
        self.store.score_live_match(match['id'], self.a, 2)
        self.store.register(self.b, self.a, 7, 0)
        self.assertEqual(self.store.snapshot()[1][-1]['elo1_before'], 984)
        self.store.score_live_match(match['id'], None, 3)
        matches = self.store.snapshot()[1]
        self.assertIsNone(matches[0]['elo1_before'])
        self.assertEqual(matches[1]['elo1_before'], 1000)
        self.store.score_live_match(match['id'], self.a, 4)
        matches = self.store.snapshot()[1]
        self.assertEqual(matches[-1]['id'], match['id'])
        self.assertEqual((matches[-1]['elo1_before'], matches[-1]['elo2_before']), (984, 1016))

    def test_failed_migration_preserves_original(self):
        self.legacy(Store.LIVE_MATCH_FIELDS)
        before = self.path.read_bytes()
        with patch('storage.os.replace', side_effect=OSError('Disk unavailable')):
            with self.assertRaises(OSError):
                self.store.migrate_elo()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((Path(self.temp.name) / '.write-lock').exists())
        self.assertIsNotNone(self.store.migrate_elo())

    def test_invalid_saved_rating_is_rejected(self):
        self.store.register(self.a, self.b, 7, 0)
        self.path.write_text(self.path.read_text().replace('1000.0,1000.0', 'nan,1000.0'))
        with self.assertRaisesRegex(StoreError, 'Invalid stored Elo'):
            self.store.snapshot()
