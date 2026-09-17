"""Default Qt widgets for creating, playing and reviewing tournaments."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tournaments import SYSTEMS, tournament_state


RULES = ('Elimination formats require an even number of players; byes fill uneven brackets. '
         'Groups play round-robin. The top half of each group (rounded up) enters the upper bracket; '
         'the rest enters the lower bracket with one playoff loss. Upper losers drop to lower; '
         'lower losers are eliminated. One grand final, with no reset. '
         'Group and round-robin ties: wins, point difference, points scored, then saved draw order.')


class TournamentTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.players, self.matches, self.tournaments = [], [], []
        self.state = None
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(self.show_tournament)
        top.addWidget(self.selector, 1)
        create = QPushButton('Create tournament…')
        create.clicked.connect(self.create_tournament)
        top.addWidget(create)
        layout.addLayout(top)
        self.summary = QLabel('Create a tournament to begin.')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.bracket = QTreeWidget()
        self.bracket.setHeaderLabels(['Stage / round / match', 'Player 1', 'Score', 'Player 2', 'Status'])
        self.bracket.currentItemChanged.connect(self.select_fixture)
        splitter.addWidget(self.bracket)
        self.groups = QTreeWidget()
        self.groups.setHeaderLabels(['Group / rank', 'Player', 'Wins', 'Losses', 'Points for', 'Points against'])
        splitter.addWidget(self.groups)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.selected_label = QLabel('Select a ready match to enter its result.')
        layout.addWidget(self.selected_label)
        entry = QHBoxLayout()
        self.score1, self.score2 = QSpinBox(), QSpinBox()
        for label, spin in (('Player 1 points', self.score1), ('Player 2 points', self.score2)):
            spin.setRange(0, 2_147_483_647)
            entry.addWidget(QLabel(label))
            entry.addWidget(spin)
        self.register_button = QPushButton('Register result')
        self.register_button.clicked.connect(self.register_result)
        entry.addWidget(self.register_button)
        entry.addStretch()
        self.undo_button = QPushButton('Undo latest result…')
        self.undo_button.clicked.connect(self.undo_result)
        entry.addWidget(self.undo_button)
        layout.addLayout(entry)
        self.select_fixture()
        self.undo_button.setEnabled(False)

    def render(self, players, matches, tournaments):
        self.players, self.matches, self.tournaments = players, matches, tournaments
        previous = self.selector.currentData()
        self.selector.blockSignals(True)
        self.selector.clear()
        for tournament in reversed(tournaments):
            state = tournament_state(tournament, matches)
            suffix = 'Completed' if state['complete'] else state['phase']
            self.selector.addItem(f"{tournament['name']} — {suffix}", tournament['id'])
        if tournaments:
            self.selector.setCurrentIndex(max(0, self.selector.findData(previous)))
        self.selector.blockSignals(False)
        self.show_tournament()

    def show_tournament(self, *_):
        self.bracket.clear()
        self.groups.clear()
        self.state = None
        tournament_id = self.selector.currentData()
        tournament = next((t for t in self.tournaments if t['id'] == tournament_id), None)
        if tournament is None:
            self.summary.setText('Create a tournament to begin.')
            self.groups.hide()
            self.undo_button.setEnabled(False)
            self.select_fixture()
            return
        names = {p['id']: p['name'] for p in self.players}
        self.state = tournament_state(tournament, self.matches)
        champion = self.state['champion']
        summary = (f"{SYSTEMS[tournament['system']]} · {len(tournament['players'])} players · "
                   f"{self.state['phase']}")
        if champion:
            summary += f' · Winner: {names[champion]}'
        else:
            summary += ' · Next elimination rounds appear when their current rounds finish.'
        self.summary.setText(summary)
        self.summary.setToolTip(RULES)
        stages, rounds = {}, {}
        for fixture in self.state['fixtures']:
            stage = fixture['stage']
            if stage not in stages:
                stages[stage] = QTreeWidgetItem(self.bracket, [stage])
            key = (stage, fixture['round'])
            if key not in rounds:
                rounds[key] = QTreeWidgetItem(stages[stage], [f"Round {fixture['round']}"])
            result = fixture['result']
            score = f"{result['score1']} – {result['score2']}" if result else '—'
            status = ('Bye' if fixture['bye'] else
                      f"Winner: {names[fixture['winner']]}" if result else 'Ready')
            item = QTreeWidgetItem(rounds[key], [f'Match {rounds[key].childCount() + 1}',
                                   names.get(fixture['player1'], 'Bye'), score,
                                   names.get(fixture['player2'], 'Bye'), status])
            item.setData(0, Qt.ItemDataRole.UserRole, fixture['id'])
        self.bracket.expandAll()
        if not self.state['complete']:
            ready_rounds = {(f['stage'], f['round']) for f in self.state['fixtures']
                            if not f['bye'] and f['result'] is None}
            for key, item in rounds.items():
                item.setExpanded(key in ready_rounds)
            for stage, item in stages.items():
                item.setExpanded(any(key[0] == stage for key in ready_rounds))
        for column in range(4):
            self.bracket.resizeColumnToContents(column)
        self.groups.setVisible(bool(self.state['tables']))
        for table in self.state['tables']:
            root = QTreeWidgetItem(self.groups, [table['label']])
            for rank, row in enumerate(table['rows'], 1):
                QTreeWidgetItem(root, [str(rank), names[row['player']], str(row['wins']),
                                      str(row['losses']), str(row['scored']), str(row['conceded'])])
        self.groups.expandAll()
        for column in range(5):
            self.groups.resizeColumnToContents(column)
        self.undo_button.setEnabled(any(m['tournament_id'] == tournament_id for m in self.matches))
        self.select_fixture()

    def selected_fixture(self):
        item = self.bracket.currentItem()
        key = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return next((f for f in self.state['fixtures'] if f['id'] == key), None) if self.state else None

    def select_fixture(self, *_):
        fixture = self.selected_fixture()
        ready = fixture is not None and not fixture['bye'] and fixture['result'] is None
        self.register_button.setEnabled(ready)
        self.score1.setEnabled(ready)
        self.score2.setEnabled(ready)
        self.score1.setValue(0)
        self.score2.setValue(0)
        if ready:
            names = {p['id']: p['name'] for p in self.players}
            self.selected_label.setText(f"Player 1: {names[fixture['player1']]} · "
                                        f"Player 2: {names[fixture['player2']]}")
        else:
            self.selected_label.setText('Select a ready match to enter its result.')

    def register_result(self):
        fixture = self.selected_fixture()
        if fixture is None or fixture['bye'] or fixture['result'] is not None:
            return
        tournament_id = self.selector.currentData()
        x, y = self.score1.value(), self.score2.value()
        self.window._run(lambda: self.window.store.register_tournament_match(
            tournament_id, fixture['id'], x, y, (fixture['player1'], fixture['player2'])),
            'Tournament result registered')

    def undo_result(self):
        tournament_id = self.selector.currentData()
        result = next((m for m in reversed(self.matches) if m['tournament_id'] == tournament_id), None)
        if result is None:
            return
        names = {p['id']: p['name'] for p in self.players}
        message = (f"Undo {names[result['player1']]} {result['score1']} – {result['score2']} "
                   f"{names[result['player2']]}?\n\nThis removes the match from global history "
                   'and recalculates the tournament and Elo. Earlier corrections require undoing '
                   'later tournament results first.')
        if QMessageBox.question(self, 'Undo tournament result', message,
                                defaultButton=QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.window._run(lambda: self.window.store.delete_match(result['id']), 'Tournament result undone')

    def create_tournament(self):
        dialog = QDialog(self.window)
        dialog.setWindowTitle('Create tournament')
        dialog.resize(540, 600)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        name, system, groups = QLineEdit(), QComboBox(), QSpinBox()
        for key, label in SYSTEMS.items():
            system.addItem(label, key)
        active = sorted((p for p in self.players if p['active']), key=lambda p: p['name'].casefold())
        groups.setRange(1, max(1, len(active) // 2))
        groups.setValue(min(2, groups.maximum()))
        groups.setEnabled(False)
        system.currentIndexChanged.connect(lambda: groups.setEnabled(system.currentData() == 'group_double'))
        form.addRow('Name', name)
        form.addRow('System', system)
        form.addRow('Groups', groups)
        layout.addLayout(form)
        count = QLabel('Players selected: 0')
        layout.addWidget(count)
        members = QListWidget()
        for player in active:
            item = QListWidgetItem(player['name'], members)
            item.setData(Qt.ItemDataRole.UserRole, player['id'])
            item.setCheckState(Qt.CheckState.Unchecked)

        def selected_players():
            return [members.item(i).data(Qt.ItemDataRole.UserRole) for i in range(members.count())
                    if members.item(i).checkState() == Qt.CheckState.Checked]

        members.itemChanged.connect(lambda: count.setText(f'Players selected: {len(selected_players())}'))
        layout.addWidget(members)
        rules = QLabel('The draw is randomized and saved at creation.\n' + RULES)
        rules.setWordWrap(True)
        layout.addWidget(rules)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Create')
        buttons.rejected.connect(dialog.reject)

        def create():
            title, kind, selected = name.text(), system.currentData(), selected_players()
            group_count = groups.value() if kind == 'group_double' else 1
            dialog.setEnabled(False)

            def success():
                self.selector.setCurrentIndex(0)
                dialog.accept()

            self.window._run(lambda: self.window.store.create_tournament(title, kind, selected, group_count),
                             'Tournament created', on_success=success)
            self.window.worker.finished.connect(lambda: dialog.setEnabled(True))

        buttons.accepted.connect(create)
        layout.addWidget(buttons)
        dialog.exec()
