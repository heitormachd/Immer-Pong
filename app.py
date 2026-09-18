"""Shared ping-pong scorekeeper. Run with Python or the bundled executable."""

import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QMainWindow, QMessageBox, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ranking import standings, match_elo_history
from storage import Store
from tournament_ui import TournamentTab
from live_ui import LiveMatchPanel, show_point_history


def data_directory():
    base = Path(sys.executable) if getattr(sys, 'frozen', False) else Path(__file__)
    return base.resolve().parent / 'data'


class Operation(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, action, parent):
        super().__init__(parent)
        self.action = action

    def run(self):
        try:
            self.succeeded.emit(self.action())
        except Exception as exc:
            self.failed.emit(str(exc))


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def fill(widget, rows):
    selected = selected_id(widget)
    widget.setRowCount(0)
    for row_id, values in rows:
        index = widget.rowCount()
        widget.insertRow(index)
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setData(Qt.ItemDataRole.UserRole, row_id)
            widget.setItem(index, column, item)
        if row_id == selected:
            widget.selectRow(index)


def selected_id(widget):
    item = widget.item(widget.currentRow(), 0)
    return item.data(Qt.ItemDataRole.UserRole) if item else None


class Window(QMainWindow):
    def __init__(self, store=None):
        super().__init__()
        self.store = store if store is not None else Store(data_directory())
        self.players = []
        self.completed_matches = []
        self.name_drafts = {}
        self.worker = None
        self.setWindowTitle('Ping-pong')
        self.resize(950, 580)
        central = QWidget()
        layout = QVBoxLayout(central)
        top = QHBoxLayout()
        self.status = QLabel('')
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh)
        top.addWidget(self.status, 1)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)
        self._matches_tab()
        self._leaderboards_tab()
        self._players_tab()
        self.tournaments_view = TournamentTab(self)
        self.tabs.addTab(self.tournaments_view, 'Tournament')
        self.tabs.currentChanged.connect(self.refresh)
        self.refresh()

    def _tab(self, title):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.tabs.addTab(widget, title)
        return layout

    def _matches_tab(self):
        layout = self._tab('Matches')
        self.match_mode = QComboBox()
        self.match_mode.addItems(['Final result', 'Live scoring'])
        layout.addWidget(self.match_mode)
        final_entry = QWidget()
        form = QHBoxLayout(final_entry)
        form.setContentsMargins(0, 0, 0, 0)
        self.player1, self.player2 = QComboBox(), QComboBox()
        self.score1, self.score2 = QSpinBox(), QSpinBox()
        for score in (self.score1, self.score2):
            score.setRange(0, 2_147_483_647)
        for label, player, score in (('Player 1', self.player1, self.score1),
                                     ('Player 2', self.player2, self.score2)):
            form.addWidget(QLabel(label))
            form.addWidget(player, 1)
            form.addWidget(QLabel('Points'))
            form.addWidget(score)
        self.register_button = QPushButton('Register')
        self.register_button.clicked.connect(self.register)
        form.addWidget(self.register_button)
        layout.addWidget(final_entry)
        self.live_view = LiveMatchPanel(self)
        layout.addWidget(self.live_view)
        self.live_view.hide()
        self.match_mode.currentIndexChanged.connect(lambda index: final_entry.setVisible(index == 0))
        self.match_mode.currentIndexChanged.connect(lambda index: self.live_view.setVisible(index == 1))
        self.history = table(['Time', 'Player 1', 'P1 ELO', 'Points', 'Player 2',
                              'P2 ELO', 'Tournament'])
        layout.addWidget(self.history)
        actions = QHBoxLayout()
        point_history = QPushButton('Point history…')
        point_history.clicked.connect(self.match_point_history)
        actions.addWidget(point_history)
        actions.addStretch()
        remove = QPushButton('Delete selected match…')
        remove.clicked.connect(self.delete_match)
        actions.addWidget(remove)
        layout.addLayout(actions)

    def _leaderboards_tab(self):
        layout = self._tab('Leaderboards')
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.ranking = table(['Rank', 'Player', 'Elo', 'Matches', 'Wins', 'Losses',
                              'Win %', 'Points for', 'Points against', 'Last 5'])
        layout.addWidget(self.ranking)

    def _players_tab(self):
        layout = self._tab('Players')
        self.members = table(['Player', 'Status'])
        layout.addWidget(self.members)
        buttons = QHBoxLayout()
        for label, callback in [('Add…', self.add_player), ('Rename…', self.rename_player),
                                 ('Remove…', self.retire_player), ('Restore', self.restore_player)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)

    def _run(self, action, message='Updated', reset_scores=False, on_success=None):
        if self.worker is not None:
            return
        self.tabs.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.status.setText('Accessing shared data…')
        self.worker = Operation(action, self)

        def success(snapshot):
            self.render(snapshot)
            if reset_scores:
                self.player1.setCurrentIndex(0)
                self.player2.setCurrentIndex(0)
                self.score1.setValue(0)
                self.score2.setValue(0)
            self.status.setText(message)
            if on_success is not None:
                on_success()

        self.worker.succeeded.connect(success)
        self.worker.failed.connect(self._error)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.tabs.setEnabled(True)
        self.refresh_button.setEnabled(True)

    def _error(self, message):
        self.status.setText('Operation failed. Displayed data may be out of date.')
        QMessageBox.warning(self, 'Could not complete operation', message)

    def refresh(self, *_):
        self._run(self.store.snapshot)

    def render(self, snapshot):
        self.players, matches, tournaments = snapshot
        self.completed_matches = [m for m in matches if m['status'] == 'completed']
        by_id = {p['id']: p for p in self.players}
        tournament_names = {t['id']: t['name'] for t in tournaments}
        active = sorted((p for p in self.players if p['active']),
                        key=lambda p: p['name'].casefold())
        for combo in (self.player1, self.player2):
            previous = combo.currentData()
            combo.clear()
            combo.addItem('Select player…', None)
            for player in active:
                combo.addItem(player['name'], player['id'])
            combo.setCurrentIndex(max(0, combo.findData(previous)))
        self.register_button.setEnabled(len(active) >= 2)
        elo_history = match_elo_history(self.players, matches)
        fill(self.history, [(m['id'], [
            datetime.fromisoformat(m['timestamp']).astimezone().strftime('%Y-%m-%d %H:%M:%S'),
            by_id[m['player1']]['name'], '', f"{m['score1']} – {m['score2']}",
            by_id[m['player2']]['name'], '', tournament_names.get(m['tournament_id'], '—')])
            for m in reversed(self.completed_matches)])
        for row, match in enumerate(reversed(self.completed_matches)):
            for column, player in ((2, 'player1'), (5, 'player2')):
                elo = elo_history[match['id']][player]
                before, change = elo['before'], elo['change']
                color = 'green' if change >= 0 else 'red'
                label = QLabel(f'{before:.2f} <span style="color: {color}">({change:+.2f})</span>')
                label.setContentsMargins(3, 0, 3, 0)
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                item = self.history.item(row, column)
                item.setText(f'{before:.2f} ({change:+.2f})')
                # Keep accessible plain text without painting it beneath the label.
                item.setForeground(QColor('transparent'))
                item.setSizeHint(label.sizeHint())
                self.history.setCellWidget(row, column, label)
        fill(self.members, [(p['id'], [p['name'], 'Active' if p['active'] else 'Retired'])
                            for p in sorted(self.players, key=lambda p: p['name'].casefold())])
        fill(self.ranking, [(s['player']['id'], [
            s['rank'] or '—', s['player']['name'], round(s['elo']), s['matches'],
            s['wins'], s['losses'], f"{100 * s['wins'] / s['matches']:.1f}%" if s['matches'] else '—',
            s['scored'], s['conceded'],
            ' '.join('✅' if won else '❌' for won in s['last5']) or '—'])
            for s in standings(self.players, matches)])
        self.summary.setText(f'{len(self.completed_matches)} matches · {len(active)} active players')
        self.live_view.render(self.players, matches)
        self.tournaments_view.render(self.players, matches, tournaments)

    def match_point_history(self):
        match_id = selected_id(self.history)
        match = next((m for m in self.completed_matches if m['id'] == match_id), None)
        if match is not None:
            show_point_history(self, match, self.players)

    def register(self):
        a, b = self.player1.currentData(), self.player2.currentData()
        x, y = self.score1.value(), self.score2.value()
        if a is None or b is None:
            QMessageBox.information(self, 'Select players', 'Select two players first.')
            return
        self._run(lambda: self.store.register(a, b, x, y), 'Match registered', reset_scores=True)

    def delete_match(self):
        match_id = selected_id(self.history)
        if match_id is None:
            return
        row = self.history.currentRow()
        description = ' · '.join(self.history.item(row, c).text() for c in (0, 1, 3, 4))
        if QMessageBox.question(self, 'Delete match', f'Delete this match?\n{description}\n\n'
                                'Statistics and Elo will be recalculated.',
                                defaultButton=QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self._run(lambda: self.store.delete_match(match_id), 'Match deleted')

    def add_player(self):
        name, accepted = QInputDialog.getText(self, 'Add player', 'Name:',
                                             text=self.name_drafts.get(None, ''))
        if accepted:
            self.name_drafts[None] = name
            self._run(lambda: self.store.save_player(name), 'Player added',
                      on_success=lambda: self.name_drafts.pop(None, None))

    def _selected_player(self):
        player_id = selected_id(self.members)
        return next((p for p in self.players if p['id'] == player_id), None)

    def rename_player(self):
        player = self._selected_player()
        if player:
            name, accepted = QInputDialog.getText(
                self, 'Rename player', 'Name:',
                text=self.name_drafts.get(player['id'], player['name']))
            if accepted:
                self.name_drafts[player['id']] = name
                self._run(lambda: self.store.save_player(name, player['id']), 'Player renamed',
                          on_success=lambda: self.name_drafts.pop(player['id'], None))

    def retire_player(self):
        player = self._selected_player()
        if player and player['active']:
            if QMessageBox.question(self, 'Remove player', f"Retire {player['name']}?\n"
                                    'Their match history will be preserved.',
                                    defaultButton=QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
                self._run(lambda: self.store.set_active(player['id'], False), 'Player retired')

    def restore_player(self):
        player = self._selected_player()
        if player and not player['active']:
            self._run(lambda: self.store.set_active(player['id'], True), 'Player restored')

    def closeEvent(self, event):
        if self.worker is not None:
            self.status.setText('Please wait for the current operation before closing.')
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
