import dataclasses
import sys
from pathlib import Path

from PySide6.QtCore import (
    Qt,
    QStringListModel,
    QItemSelectionModel,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
)

from rs.core import (
    config,
    pipe as p,
    lang,
)
from rs.gui import (
    appearance,
)
from rs_resolve.core import get_fps

from rs_resolve.tool.youtube_chapter.youtube_chapter_ui import Ui_MainWindow

APP_NAME = 'Youtubeチャプター'
APP_NAME_EN = 'YoutubeChapter'


@dataclasses.dataclass
class ConfigData(config.Data):
    title: str = '目次'
    delimiter: str = '-'
    is_niconico: bool = False
    color: str = 'Rose'


def select(v, names):
    m: QStringListModel = v.model()
    sm = v.selectionModel()
    sm.clear()
    ss = m.stringList()
    for name in names:
        if name in ss:
            i = m.match(m.index(0, 0), Qt.DisplayRole, name)[0]
            sm.setCurrentIndex(
                i,
                QItemSelectionModel.SelectionFlag.SelectCurrent | QItemSelectionModel.SelectionFlag.Rows
            )


class MainWindow(QMainWindow):
    def __init__(self, parent=None, fusion=None):
        super().__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowCloseButtonHint
            | Qt.WindowStaysOnTopHint
        )
        self.resize(450, 450)
        self.fusion = fusion

        # translate
        self.lang_code: lang.Code = lang.load()
        self.setWindowTitle('%s' % (APP_NAME if self.lang_code == lang.Code.ja else APP_NAME_EN))
        self.translate()

        # list view
        m = QStringListModel()
        m.setStringList(config.MARKER_COLOR_LIST)
        self.ui.markerListView.setModel(m)

        # config
        self.config_file: Path = config.CONFIG_DIR.joinpath('%s.json' % APP_NAME)
        self.load_config()

        # button
        self.ui.copyButton.setStyleSheet(appearance.ex_stylesheet)
        self.ui.makeButton.setStyleSheet(appearance.in_stylesheet)

        # event
        self.ui.makeButton.clicked.connect(self.make)
        self.ui.copyButton.clicked.connect(self.copy)
        self.ui.closeButton.clicked.connect(self.close)

    def translate(self) -> None:
        if self.lang_code == lang.Code.en:
            self.ui.titleLabel.setText('title')
            self.ui.delimiterLabel.setText('delimiter')
            self.ui.niconicoCheckBox.setText('add # for niconico')
            self.ui.makeButton.setText('make')
            self.ui.copyButton.setText('copy')
            self.ui.closeButton.setText('close')

    def make(self):
        v = self.ui.chapterPlainTextEdit
        data = self.get_data()
        delim = (' %s ' % data.delimiter).replace('  ', ' ')
        prefix = '#' if data.is_niconico else ''

        resolve = self.fusion.GetResolve()
        projectManager = resolve.GetProjectManager()

        project = projectManager.GetCurrentProject()
        if project is None:
            if self.lang_code == lang.Code.en:
                v.setPlainText('Project not found.')
            else:
                v.setPlainText('Projectが見付かりません。')
            return

        timeline = project.GetCurrentTimeline()
        if timeline is None:
            if self.lang_code == lang.Code.en:
                v.setPlainText('Timeline not found.')
            else:
                v.setPlainText('Timelineが見付かりません。')
            return

        fps = get_fps(timeline)
        m: dict = timeline.GetMarkers()

        lst = [data.title]
        for key in m:
            if m[key]['color'] == data.color:
                sec = round(key / fps)
                minute, sec = divmod(sec, 60)
                hour, minute = divmod(minute, 60)
                tc = '%02d:%02d' % (minute, sec)
                if hour > 0:
                    tc = '%02d:%s' % (hour, tc)
                if len(lst) == 1 and tc != '00:00':  # タイトルが入っているので  == 1
                    lst.append(prefix + '00:00' + delim)
                lst.append(prefix + tc + delim + m[key]['name'])

        v.setPlainText('\n'.join(lst))

    def copy(self):
        QApplication.clipboard().setText(self.ui.chapterPlainTextEdit.toPlainText())

    def set_data(self, c: ConfigData):
        self.ui.titleLineEdit.setText(c.title)
        self.ui.delimiterLineEdit.setText(c.delimiter)
        self.ui.niconicoCheckBox.setChecked(c.is_niconico)
        select(self.ui.markerListView, [c.color])

    def get_data(self) -> ConfigData:
        c = ConfigData()
        c.title = self.ui.titleLineEdit.text().strip()
        c.delimiter = self.ui.delimiterLineEdit.text().strip()
        c.is_niconico = self.ui.niconicoCheckBox.isChecked()

        # c.color
        v = self.ui.markerListView
        m: QStringListModel = v.model()
        lst = v.selectionModel().selectedIndexes()
        c.color = m.data(lst[0]) if len(lst) > 0 else ''

        return c

    def load_config(self) -> None:
        c = ConfigData()
        if self.lang_code == lang.Code.en:
            c.title = 'Chapter'
        if self.config_file.is_file():
            c.load(self.config_file)
        self.set_data(c)

    def save_config(self) -> None:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        c = self.get_data()
        c.save(self.config_file)

    def closeEvent(self, event):
        self.save_config()
        super().closeEvent(event)


def run(fusion) -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(appearance.palette)
    app.setStyleSheet(appearance.stylesheet)

    window = MainWindow(fusion=fusion)
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    pass
