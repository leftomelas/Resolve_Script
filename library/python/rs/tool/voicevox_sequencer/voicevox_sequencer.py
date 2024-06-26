import dataclasses
import io
import re
import sys
from copy import deepcopy
from pathlib import Path

import mido
import numpy as np
import scipy as sp
import simpleaudio
import pykakasi

from PySide6.QtCore import (
    Qt,
    QItemSelectionModel,
)
from PySide6.QtGui import QColor, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMenu,
)

from rs.core import (
    config,
    pipe as p,
    voicevox,
    ust,
)
from rs.core.voicevox.data import SpeakerList
from rs.core.voicevox.api import (
    synthesis,
    VOICEVOX_PORT,
    VOICEVOX_NEMO_PORT,
    SHAREVOX_PORT,
)
from rs.core.voicevox.mora_list import openjtalk_text2mora as text2mora
from rs.core.voicevox.mora_list import openjtalk_mora2text as mora2text

from rs.gui import (
    appearance, log,
)

from rs.tool.voicevox_sequencer import seq
from rs.tool.voicevox_sequencer.voicevox_sequencer_ui import Ui_MainWindow
from rs.tool.voicevox_sequencer.lyrics import MainWindow as LyricsWindow

APP_NAME = 'VoicevoxSequencer'


@dataclasses.dataclass
class Engine:
    port: int = VOICEVOX_PORT
    name: str = 'VOICEVOX'
    speakers: SpeakerList = dataclasses.field(default_factory=SpeakerList)

    def get_speakers_file(self):
        return config.CONFIG_DIR.joinpath('%s_speakers.json' % self.name.lower().replace(' ', '_'))

    def load_speakers(self):
        _file = self.get_speakers_file()
        if _file.is_file():
            self.speakers.load(_file)

    def save_speakers(self):
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _file = self.get_speakers_file()
        self.speakers.save(_file)


@dataclasses.dataclass
class ConfigData(config.Data):
    port: int = VOICEVOX_PORT
    speaker_index: int = 0


def msg2notes(msg_lst: list[mido.Message]):
    lst = []
    if len(msg_lst) < 2:
        return lst
    if msg_lst[0].time > 0:
        lst.append(seq.NoteData(note=-1, length=msg_lst[0].time, kana=''))
    pair_lst = zip(msg_lst, msg_lst[1:])
    for msg1, msg2 in pair_lst:
        if msg1.type == 'note_on':
            lst.append(seq.NoteData(note=msg1.note, length=msg2.time))
        elif msg1.type == 'note_off':
            if msg2.time == 0:
                continue
            lst.append(seq.NoteData(note=-1, length=msg2.time, kana=''))
    return lst


@dataclasses.dataclass
class Doc(config.Data):
    port: int = VOICEVOX_PORT
    speaker_id: int = 0
    tempo: int = 120
    note_list: config.DataList = dataclasses.field(default_factory=lambda: config.DataList(seq.NoteData))

    def load_midi(self, f: Path):
        mid = mido.MidiFile(f)
        lst = p.pipe(
            mid.tracks,
            p.map(lambda track: p.pipe(
                track,
                p.filter(lambda msg: msg.type == 'note_on' or msg.type == 'note_off'),
                list,
            )),
            p.map(msg2notes),
            p.filter(lambda s: len(s) > 0),
            list,
        )
        if len(lst) == 0:
            self.note_list.clear()
        else:
            self.note_list.set_list(lst[0])

    def load_ust(self, f: Path):
        dct = ust.read(f)
        self.note_list.clear()

        if 'SETTING' in dct:
            if 'Tempo' in dct['SETTING']:
                try:
                    _tempo = float(dct['SETTING']['Tempo'])
                    self.tempo = int(_tempo)
                except ValueError:
                    pass
        # notes
        kks = pykakasi.kakasi()
        hira_pat = re.compile('[\u3041-\u309F]+')
        for i, _note in enumerate(dct['notes']):
            if 'Tempo' in _note.keys():
                try:
                    _tempo = float(_note['Tempo'])
                    self.tempo = int(_tempo)
                except ValueError:
                    pass

            note = seq.NoteData()

            if 'Length' in _note:
                try:
                    note.length = int(_note['Length'])
                except ValueError:
                    pass
            if 'NoteNum' in _note:
                try:
                    note.note = int(_note['NoteNum'])
                except ValueError:
                    pass

            if 'Lyric' in _note:
                lyric = _note['Lyric']
                if lyric in ['R', 'br']:
                    note.note = -1
                    note.kana = ''
                else:
                    hira_match = hira_pat.search(lyric)
                    if hira_match is not None:
                        note.kana = kks.convert(hira_match.group())[-1]['kana']

                    elif lyric == '-' and i > 0:
                        _pre_kana = self.note_list[i - 1].kana
                        if _pre_kana in text2mora:
                            _vowel = text2mora[_pre_kana][-1]
                            note.kana = mora2text[_vowel]

                    elif lyric in mora2text:
                        note.kana = mora2text[lyric]

            self.note_list.append(note)


def paragraph2audio_query(paragraph: seq.Paragraph, tempo: int, sampling_rate: int) -> voicevox.data.AudioQuery:
    moras = []
    accent_phrases = []
    for note in paragraph.note_list:
        mora = voicevox.data.Mora()
        length = note.get_sec(tempo)
        max_sec = note.max_time
        mora.set_note(
            note.kana,
            note.note,
            min(length, max_sec),
        )
        moras.append(mora)

        if note.get_sec(tempo) > max_sec:
            # 最大時間を超えた場合は、休符追加し次のアクセント句に切り替え
            accent_phrase = voicevox.data.AccentPhrase()
            pause_mora = voicevox.data.Mora()
            pause_mora.set_rest(length - max_sec)
            accent_phrase.pause_mora = pause_mora
            accent_phrase.moras.set_list(moras)
            accent_phrases.append(accent_phrase)
            moras = []
    if len(moras) > 0:
        accent_phrase = voicevox.data.AccentPhrase()
        accent_phrase.moras.set_list(moras)
        accent_phrases.append(accent_phrase)

    # audio_query
    audio_query = voicevox.data.AudioQuery()
    audio_query.accent_phrases.set_list(accent_phrases)
    audio_query.outputSamplingRate = sampling_rate
    return audio_query


def write_label(f: Path, phoneme_list: list[dict]):
    if len(phoneme_list) == 0:
        return
    n = 10000000
    lst = [{
        's': 0,
        'e': int(phoneme_list[0]['length'] * n),
        'sign': phoneme_list[0]['sign'],
    }]
    for phoneme in phoneme_list[1:]:
        lst.append({
            's': lst[-1]['e'],
            'e': lst[-1]['e'] + int(phoneme['length'] * n),
            'sign': phoneme['sign'],
        })
    with f.open('w', encoding='utf-8') as fp:
        for phoneme in lst:
            fp.write('%s %s %s\n' % (
                str(phoneme['s']),
                str(phoneme['e']),
                phoneme['sign'],
            ))


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.file = None
        self.set_title()
        self.setWindowFlags(
            Qt.Window
        )
        self.resize(600, 800)

        # Engine
        self.engine_list = [
            Engine(port=VOICEVOX_PORT, name='VOICEVOX'),
            Engine(port=VOICEVOX_NEMO_PORT, name='VOICEVOX Nemo'),
            Engine(port=SHAREVOX_PORT, name='SHAREVOX'),
        ]
        for engine in self.engine_list:
            engine.load_speakers()
        self.ui.engineComboBox.clear()
        self.ui.engineComboBox.addItems([e.name for e in self.engine_list])
        self.ui.engineComboBox.setCurrentIndex(0)
        self.ui.engineComboBox.currentIndexChanged.connect(self.set_speaker_list)

        # speaker
        self.set_speaker_list()

        self.ui.tempoSpinBox.setValue(120)
        self.sampling_rate = 24000

        self.play_obj = None

        # config
        self.config_file: Path = config.CONFIG_DIR.joinpath('%s.json' % APP_NAME)
        self.load_config()

        # style sheet
        self.ui.saveButton.setStyleSheet(appearance.ex_stylesheet)
        self.ui.playButton.setStyleSheet(appearance.other_stylesheet)
        self.ui.playPhraseButton.setStyleSheet(appearance.other_stylesheet)
        self.ui.stopButton.setStyleSheet(appearance.other_stylesheet)
        self.ui.getSpeakerButton.setStyleSheet(appearance.other_stylesheet)

        # table
        v = self.ui.tableView
        self.undo_stack: QUndoStack = v.undo_stack

        v.setContextMenuPolicy(Qt.CustomContextMenu)
        v.customContextMenuRequested.connect(self.contextMenu)

        # window
        self.lyrics_window = LyricsWindow(self)

        #
        self.new_doc()

        # event
        self.undo_stack.cleanChanged.connect(self.set_title)

        self.ui.getSpeakerButton.clicked.connect(self.get_speakers)

        self.ui.playButton.clicked.connect(self.play)
        self.ui.playPhraseButton.clicked.connect(self.play_phrase)
        self.ui.stopButton.clicked.connect(self.stop)
        self.ui.saveButton.clicked.connect(self.wave_save)
        self.ui.closeButton.clicked.connect(self.close)
        #
        self.ui.actionNew.triggered.connect(self.new_doc)
        self.ui.actionOpen.triggered.connect(self.open_doc)
        self.ui.actionSave.triggered.connect(self.save_doc)
        self.ui.actionSave_As.triggered.connect(self.save_as_doc)
        self.ui.actionExit.triggered.connect(self.close)

        self.ui.actionUndo.triggered.connect(self.ui.tableView.undo)
        self.ui.actionRedo.triggered.connect(self.ui.tableView.redo)

        self.ui.actionEdit.triggered.connect(self.edit)
        self.ui.actionAdd.triggered.connect(self.add)
        self.ui.actionDuplicate.triggered.connect(self.duplicate)
        self.ui.actionSplit.triggered.connect(self.split)
        self.ui.actionIncrement.triggered.connect(self.ui.tableView.increment)
        self.ui.actionIncrementPlus.triggered.connect(self.ui.tableView.increment_plus)
        self.ui.actionDecrement.triggered.connect(self.ui.tableView.decrement)
        self.ui.actionDecrementPlus.triggered.connect(self.ui.tableView.decrement_plus)
        self.ui.actionClear.triggered.connect(self.ui.tableView.clear)
        self.ui.actionCopy.triggered.connect(self.ui.tableView.copy)
        self.ui.actionPaste.triggered.connect(self.ui.tableView.paste)
        self.ui.actionDelete.triggered.connect(self.ui.tableView.delete)
        self.ui.actionUp.triggered.connect(self.ui.tableView.up)
        self.ui.actionDown.triggered.connect(self.ui.tableView.down)

        self.ui.actionLyrics.triggered.connect(self.lyrics_window.show)

    def set_title(self):
        if self.file is None:
            self.setWindowTitle('%s' % APP_NAME)
        else:
            star = '*' if self.ui.tableView.model().undo_stack.isClean() is False else ''
            self.setWindowTitle('%s - %s%s' % (APP_NAME, self.file, star))

    def make_wav_data(self, is_phrase=False):
        v = self.ui.tableView
        doc = self.get_data()

        # phrase or all
        paragraph_list = [v.get_current_paragraph()] if is_phrase else v.get_paragraph_list()

        # make data
        data_list = []
        phoneme_list = []
        self.log_clear()
        for paragraph in paragraph_list:
            if len(paragraph.note_list) > 0:
                # use voicevox
                # make query
                query = paragraph2audio_query(
                    paragraph, doc.tempo, self.sampling_rate
                )
                # phoneme_list
                phoneme_list.extend(query.get_phoneme_list())

                # synthesis
                self.add_log('Synthesis...  %s' % query.get_text())
                try:
                    audio = synthesis(doc.speaker_id, query.as_dict(), 5, port=doc.port)
                    fs, data = sp.io.wavfile.read(io.BytesIO(audio))
                    data_list.append(data)
                except Exception as e:
                    self.add_error(f'Error: {e}')
                    self.add_log('Failed.')
                    return
            # rest
            if paragraph.rest_length > 0:
                phoneme_list.append({
                    'sign': 'pau',
                    'length': paragraph.get_rest_sec(doc.tempo),
                })
                # 想定している尺
                all_length = p.pipe(
                    phoneme_list,
                    p.map(lambda x: x['length']),
                    sum,
                )
                # wav dataの長さ(voicevoxから長めに返ってくる)
                data_num = p.pipe(
                    data_list,
                    p.map(lambda x: x.shape[-1]),
                    sum,
                )
                # 足りない分を0埋め
                rest_num = int(all_length * self.sampling_rate) - data_num
                if rest_num > 0:
                    data = np.zeros(rest_num, dtype=np.int16)
                    data_list.append(data)

        # 連結
        return np.block(data_list), phoneme_list

    def play(self):
        data, _ = self.make_wav_data()
        # Play
        self.add_log('Play')
        self.stop()
        self.play_obj = simpleaudio.play_buffer(data, 1, 2, self.sampling_rate)

    def play_phrase(self):
        v = self.ui.tableView
        if v.get_current_paragraph() is None:
            self.add_log('No selected paragraph.')
            return
        data, _ = self.make_wav_data(is_phrase=True)
        # Play
        self.add_log('Play')
        self.stop()
        self.play_obj = simpleaudio.play_buffer(data, 1, 2, self.sampling_rate)

    def stop(self):
        if self.play_obj is not None:
            self.play_obj.stop()
            self.play_obj = None

    def wave_save(self):
        dir_path = ''
        if self.file is not None:
            dir_path = Path(self.file).parent
        path, _ = QFileDialog.getSaveFileName(
            self,
            'Save File',
            str(dir_path),
            'WAV File (*.wav);;All File (*.*)'
        )
        if path != '':
            file_path = Path(path)
            label_path = file_path.with_suffix('.lab')
            self.log_clear()
            self.add_log('Start ...')
            data, phoneme_list = self.make_wav_data()
            sp.io.wavfile.write(file_path, self.sampling_rate, data)
            self.add_log('Save: %s' % str(file_path))
            write_label(label_path, phoneme_list)
            self.add_log('Save: %s' % str(label_path))
            self.add_log('Done.')

    def edit(self):
        v = self.ui.tableView
        v.edit(v.currentIndex())

    def contextMenu(self, pos):
        v = self.ui.tableView
        menu = QMenu(v)
        menu.addAction(self.ui.actionUndo)
        menu.addAction(self.ui.actionRedo)
        menu.addSeparator()
        menu.addAction(self.ui.actionEdit)
        menu.addSeparator()
        menu.addAction(self.ui.actionIncrement)
        menu.addAction(self.ui.actionIncrementPlus)
        menu.addAction(self.ui.actionDecrement)
        menu.addAction(self.ui.actionDecrementPlus)
        menu.addSeparator()
        menu.addAction(self.ui.actionCopy)
        menu.addAction(self.ui.actionPaste)
        menu.addSeparator()
        menu.addAction(self.ui.actionAdd)
        menu.addAction(self.ui.actionDuplicate)
        menu.addAction(self.ui.actionSplit)
        menu.addSeparator()
        menu.addAction(self.ui.actionDelete)
        menu.addSeparator()
        menu.addAction(self.ui.actionUp)
        menu.addAction(self.ui.actionDown)
        menu.exec(v.mapToGlobal(pos))

    def add(self):
        v = self.ui.tableView
        m: seq.Model = v.model()
        sm = v.selectionModel()
        row = v.currentIndex().row()
        d = seq.NoteData()
        if row < 0:
            m.add_row_data(d)
        else:
            current_index = v.currentIndex()
            m.insert_row_data(row + 1, d)
            sm.setCurrentIndex(
                current_index.siblingAtRow(row + 1),
                QItemSelectionModel.SelectionFlag.ClearAndSelect
            )

    def duplicate(self):
        v = self.ui.tableView
        m: seq.Model = v.model()
        sm = v.selectionModel()
        row = v.currentIndex().row()
        d = deepcopy(m.get_row_data(row))
        if row < 0:
            m.add_row_data(d)
        else:
            current_index = v.currentIndex()
            m.insert_row_data(row + 1, d)
            sm.setCurrentIndex(
                current_index.siblingAtRow(row + 1),
                QItemSelectionModel.SelectionFlag.ClearAndSelect
            )

    def split(self):
        v = self.ui.tableView
        m: seq.Model = v.model()
        sm = v.selectionModel()
        row = v.currentIndex().row()
        d = deepcopy(m.get_row_data(row))
        length = d.length // 2
        current_index = v.currentIndex()

        m.undo_stack.beginMacro('Split')
        m.setData(current_index.siblingAtColumn(1), d.length - length, Qt.EditRole)
        d.length = length
        if row < 0:
            m.add_row_data(d)
        else:
            current_index = v.currentIndex()
            m.insert_row_data(row + 1, d)
            sm.setCurrentIndex(
                current_index.siblingAtRow(row + 1),
                QItemSelectionModel.SelectionFlag.ClearAndSelect
            )
        m.undo_stack.endMacro()

    def add_log(self, text: str, color: QColor = log.TEXT_COLOR) -> None:
        self.ui.logTextEdit.log(text, color)

    def add_error(self, text: str) -> None:
        self.ui.logTextEdit.log(text, log.ERROR_COLOR)

    def log_clear(self) -> None:
        self.ui.logTextEdit.clear()

    def set_speaker_list(self) -> None:
        self.ui.speakerComboBox.clear()
        speaker_list = self.engine_list[self.ui.engineComboBox.currentIndex()].speakers
        self.ui.speakerComboBox.addItems(speaker_list.get_display_name_list())

    def get_speakers(self):
        self.log_clear()
        self.add_log('Get speakers...')
        engine = self.engine_list[self.ui.engineComboBox.currentIndex()]
        try:
            engine.speakers.set_from_voicevox(port=engine.port)
        except Exception as e:
            self.add_error(f'Error: {e}')
            self.add_log('Failed.')
            return
        self.set_speaker_list()
        engine.save_speakers()
        self.add_log('Done.')

    def port2engine(self, port: int) -> Engine:
        for engine in self.engine_list:
            if engine.port == port:
                return engine
        return self.engine_list[0]

    def set_data(self, doc: Doc):
        engine = self.port2engine(doc.port)
        # port
        self.ui.engineComboBox.setCurrentText(engine.name)
        # id
        speaker_list = engine.speakers
        display_name = speaker_list.get_display_name(doc.speaker_id)
        if display_name is not None:
            self.ui.speakerComboBox.setCurrentText(display_name)
        pass
        # tempo
        self.ui.tempoSpinBox.setValue(doc.tempo)
        # note
        v = self.ui.tableView
        m: seq.Model = v.model()
        m.set_data(doc.note_list)

    def get_data(self) -> Doc:
        doc = Doc()
        engine = self.engine_list[self.ui.engineComboBox.currentIndex()]
        # port
        doc.port = engine.port
        # id
        speaker_id = engine.speakers.get_id_from_display_name(
            self.ui.speakerComboBox.currentText()
        )
        if speaker_id is not None:
            doc.speaker_id = speaker_id
        # tempo
        doc.tempo = self.ui.tempoSpinBox.value()
        # note
        v = self.ui.tableView
        m: seq.Model = v.model()
        doc.note_list.set_list(m.to_list())
        return doc

    def new_doc(self):
        self.file = None
        v = self.ui.tableView
        m: seq.Model = v.model()
        m.clear()
        self.add()
        self.undo_stack.clear()
        self.set_title()

    def open_doc(self):
        dir_path = ''
        if self.file is not None:
            dir_path = Path(self.file).parent
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Open File',
            str(dir_path),
            'JSON MIDI UST File (*.json *.mid *.midi *.smf *.ust);;All File (*.*)'
        )
        if path != '':
            file_path = Path(path)
            if file_path.is_file():
                doc = self.get_data()
                if file_path.suffix in ('.mid', '.midi', '.smf'):
                    doc.load_midi(file_path)
                    self.file = None
                elif file_path.suffix == '.ust':
                    doc.load_ust(file_path)
                    self.file = None
                else:
                    doc.port = VOICEVOX_PORT
                    doc.load(file_path)
                    self.file = str(file_path)
                self.set_data(doc)
                self.log_clear()
                self.add_log('Open: %s' % str(file_path))
                self.undo_stack.clear()
                self.set_title()

    def save_doc(self):
        if self.file is None:
            self.save_as_doc()
            return
        file_path = Path(self.file)
        doc = self.get_data()
        doc.save(file_path)
        self.log_clear()
        self.add_log('Save: %s' % str(file_path))
        self.undo_stack.setClean()
        self.set_title()

    def save_as_doc(self):
        dir_path = ''
        if self.file is not None:
            dir_path = Path(self.file).parent
        path, _ = QFileDialog.getSaveFileName(
            self,
            'Save File',
            str(dir_path),
            'JSON File (*.json);;All File (*.*)'
        )
        if path != '':
            file_path = Path(path)
            doc = self.get_data()
            doc.save(file_path)
            self.log_clear()
            self.add_log('Save: %s' % str(file_path))
            self.file = str(file_path)
            self.undo_stack.setClean()
            self.set_title()

    def set_config(self, c: ConfigData):
        engine = self.port2engine(c.port)
        # port
        self.ui.engineComboBox.setCurrentText(engine.name)
        # id
        display_name = engine.speakers.get_display_name(c.speaker_index)
        if display_name is not None:
            self.ui.speakerComboBox.setCurrentText(display_name)
        pass

    def get_config(self) -> ConfigData:
        c = ConfigData()
        engine = self.engine_list[self.ui.engineComboBox.currentIndex()]
        # port
        c.port = engine.port
        # id
        speaker_id = engine.speakers.get_id_from_display_name(
            self.ui.speakerComboBox.currentText()
        )
        if speaker_id is not None:
            c.speaker_index = speaker_id
        return c

    def load_config(self) -> None:
        c = ConfigData()
        if self.config_file.is_file():
            c.load(self.config_file)
        self.set_config(c)

    def save_config(self) -> None:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        c = self.get_config()
        c.save(self.config_file)
        pass

    def closeEvent(self, event):
        self.save_config()
        self.stop()
        super().closeEvent(event)


def run() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(appearance.palette)
    app.setStyleSheet(appearance.stylesheet)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    run()
