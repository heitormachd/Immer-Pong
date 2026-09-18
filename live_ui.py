"""Point-by-point match entry and a simple saved point-history viewer."""

from datetime import datetime

from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from live_scoring import live_state


def show_point_history(parent, match, players):
    if match.get('target_points') is None:
        QMessageBox.information(parent, 'Point history', 'This match was entered as a final result; no point sequence was recorded.')
        return
    names = {p['id']: p['name'] for p in players}
    state = live_state(match)
    dialog = QDialog(parent)
    dialog.setWindowTitle('Point history')
    dialog.resize(780, 500)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(f"{names[match['player1']]} vs {names[match['player2']]} · Target {match['target_points']}"))
    points = QTableWidget(len(state['history']), 5)
    points.setHorizontalHeaderLabels(['Point', 'Time', 'Scorer', 'Total (P1–P2)', 'Overtime'])
    points.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    points.verticalHeader().hide()
    for index, row in enumerate(state['history']):
        overtime = '—'
        if row['overtime_round']:
            overtime = (f"Round {row['overtime_round']}: 1–1 → reset" if row['reset'] else
                        f"Round {row['overtime_round']}: {row['overtime_score'][0]}–{row['overtime_score'][1]}")
        values = [row['number'], datetime.fromisoformat(row['timestamp']).astimezone().strftime('%Y-%m-%d %H:%M:%S'),
                  names[row['player']], f"{row['score1']}–{row['score2']}", overtime]
        for column, value in enumerate(values):
            points.setItem(index, column, QTableWidgetItem(str(value)))
    points.resizeColumnsToContents()
    points.horizontalHeader().setStretchLastSection(True)
    layout.addWidget(points)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()


class LiveMatchPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.players, self.matches = [], []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        create_row = QHBoxLayout()
        self.player1, self.player2 = QComboBox(), QComboBox()
        for label, combo in (('Player 1', self.player1), ('Player 2', self.player2)):
            create_row.addWidget(QLabel(label))
            create_row.addWidget(combo, 1)
        self.target = QSpinBox()
        self.target.setRange(2, 2_147_483_647)
        self.target.setValue(7)
        create_row.addWidget(QLabel('Target points'))
        create_row.addWidget(self.target)
        self.create_button = QPushButton('Create match')
        self.create_button.clicked.connect(self.create_match)
        create_row.addWidget(self.create_button)
        layout.addLayout(create_row)
        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel('Resume / review'))
        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(self.show_match)
        saved_row.addWidget(self.selector, 1)
        layout.addLayout(saved_row)
        self.score = QLabel('Create a match to begin.')
        self.phase = QLabel('')
        self.score.setWordWrap(True)
        self.phase.setWordWrap(True)
        layout.addWidget(self.score)
        layout.addWidget(self.phase)
        scoring = QHBoxLayout()
        self.point1, self.point2 = QPushButton('Point for player 1'), QPushButton('Point for player 2')
        self.point1.clicked.connect(lambda: self.add_point('player1'))
        self.point2.clicked.connect(lambda: self.add_point('player2'))
        scoring.addWidget(self.point1)
        scoring.addWidget(self.point2)
        layout.addLayout(scoring)
        actions = QHBoxLayout()
        self.undo = QPushButton('Undo last point')
        self.undo.clicked.connect(self.undo_point)
        self.history = QPushButton('Point history…')
        self.history.clicked.connect(self.point_history)
        self.delete = QPushButton('Delete live match…')
        self.delete.clicked.connect(self.delete_match)
        for button in (self.undo, self.history, self.delete):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.show_match()

    def render(self, players, matches):
        self.players = players
        self.matches = [m for m in matches if m.get('target_points') is not None]
        active = sorted((p for p in players if p['active']), key=lambda p: p['name'].casefold())
        for combo in (self.player1, self.player2):
            previous = combo.currentData()
            combo.clear()
            combo.addItem('Select player…', None)
            for player in active:
                combo.addItem(player['name'], player['id'])
            combo.setCurrentIndex(max(0, combo.findData(previous)))
        self.create_button.setEnabled(len(active) >= 2)
        names = {p['id']: p['name'] for p in players}
        previous = self.selector.currentData()
        self.selector.blockSignals(True)
        self.selector.clear()
        for match in reversed(self.matches):
            status = 'Completed' if match['status'] == 'completed' else 'In progress'
            stamp = datetime.fromisoformat(match['timestamp']).astimezone().strftime('%m-%d %H:%M')
            self.selector.addItem(f"{names[match['player1']]} vs {names[match['player2']]} · {stamp} · {status}", match['id'])
        if self.matches:
            self.selector.setCurrentIndex(max(0, self.selector.findData(previous)))
        self.selector.blockSignals(False)
        self.show_match()

    def selected_match(self):
        return next((m for m in self.matches if m['id'] == self.selector.currentData()), None)

    def show_match(self, *_):
        match = self.selected_match()
        active = match is not None and match['status'] == 'in_progress'
        self.point1.setEnabled(active)
        self.point2.setEnabled(active)
        self.undo.setEnabled(match is not None and bool(match['point_log']))
        self.history.setEnabled(match is not None)
        self.delete.setEnabled(match is not None)
        if match is None:
            self.score.setText('Create a match to begin.')
            self.phase.setText('')
            self.point1.setText('Point for player 1')
            self.point2.setText('Point for player 2')
            return
        names = {p['id']: p['name'] for p in self.players}
        a, b = names[match['player1']], names[match['player2']]
        state = live_state(match)
        self.score.setText(f"Total: {a} {state['score1']} – {state['score2']} {b} · Target {match['target_points']}")
        self.point1.setText(f'+1 point: {a}')
        self.point2.setText(f'+1 point: {b}')
        if state['winner']:
            self.phase.setText(f"Winner: {names[state['winner']]} · Result registered in history and leaderboards.")
        elif state['overtime_round']:
            x, y = state['overtime_score']
            self.phase.setText(f"Overtime round {state['overtime_round']}: {a} {x} – {y} {b}. Win 2–0; 1–1 resets to 0–0.")
        else:
            self.phase.setText(f"At {match['target_points'] - 1}–{match['target_points'] - 1}, overtime starts: win a two-point round 2–0.")

    def create_match(self):
        a, b, target = self.player1.currentData(), self.player2.currentData(), self.target.value()
        if a is None or b is None:
            QMessageBox.information(self, 'Select players', 'Select two players first.')
            return

        def created():
            self.selector.setCurrentIndex(0)
            self.player1.setCurrentIndex(0)
            self.player2.setCurrentIndex(0)

        self.window._run(lambda: self.window.store.create_live_match(a, b, target),
                         'Live match created', on_success=created)

    def add_point(self, side):
        match = self.selected_match()
        if match is not None:
            self.window._run(lambda: self.window.store.score_live_match(
                match['id'], match[side], match['revision']), 'Point saved')

    def undo_point(self):
        match = self.selected_match()
        if match is None:
            return
        if match['status'] == 'completed':
            if QMessageBox.question(self, 'Reopen match', 'Undo the winning point? This reopens the match '
                                    'and removes its result from the leaderboards until it finishes again.',
                                    defaultButton=QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self.window._run(lambda: self.window.store.score_live_match(match['id'], None, match['revision']), 'Last point undone')

    def delete_match(self):
        match = self.selected_match()
        if match is None:
            return
        if QMessageBox.question(self, 'Delete live match', 'Delete this match and its entire point history?',
                                defaultButton=QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.window._run(lambda: self.window.store.delete_match(match['id']), 'Live match deleted')

    def point_history(self):
        match = self.selected_match()
        if match is not None:
            show_point_history(self, match, self.players)
