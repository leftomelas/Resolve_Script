from functools import partial

import dataclasses
import sys
from pathlib import Path
from enum import IntEnum
from PySide6.QtCore import (
    Qt,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QButtonGroup,
)

from rs.core import (
    config,
    util,
    pipe as p,
)
from rs.gui import (
    appearance,
)

from rs_fusion.core import (
    operator as op,
    pose,
)
from rs_fusion.tool.tatie.tatie_ui import Ui_MainWindow

APP_NAME = '立ち絵アシスタント'


class CtrlID(IntEnum):
    CHECKBOX = 0
    COMBOBOX = 1
    SLIDER = 2


@dataclasses.dataclass
class ConfigData(config.Data):
    post_multiply: bool = False
    ctrl_type: int = 1
    ctrl_name: str = 'Select'
    page_name: str = 'User'
    width: int = 1920
    height: int = 1080


def uc_button(node_a, node_b, page, layer_name, width, hide_list=None) -> dict:
    if hide_list is None:
        hide_list = []
    inp = node_a.FindMainInput(1)
    if node_b is None:
        lua = [
            'local node = comp:FindTool("%s")' % node_a.Name,
            'node.%s = nil' % inp.ID,
        ]
    else:
        lua = [
            'local _a = comp:FindTool("%s")' % node_a.Name,
            'local _b = comp:FindTool("%s")' % node_b.Name,
            '_a:ConnectInput("%s", _b)' % inp.ID,
        ]
        if len(hide_list) > 0:
            lua.append('if _a.ParentTool then')
            for x in hide_list:
                lua.append('_a.%s:HideViewControls()' % x)
            lua.append('end')
    return {
        'LINKS_Name': layer_name,
        'LINKID_DataType': 'Number',
        'INPID_InputControl': 'ButtonControl',
        'INP_Integer': False,
        'BTNCS_Execute': '\n'.join(lua),
        'INP_External': False,
        'ICS_ControlPage': page,
        'ICD_Width': width,
    }


class MainWindow(QMainWindow):

    def __init__(self, parent=None, fusion=None):
        super().__init__(parent)
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowCloseButtonHint
            | Qt.WindowStaysOnTopHint
        )
        self.resize(450, 250)

        self.fusion = fusion
        # button group
        self.button_group = QButtonGroup()
        self.button_group.addButton(self.ui.chkRadioButton, CtrlID.CHECKBOX)
        self.button_group.addButton(self.ui.cmbRadioButton, CtrlID.COMBOBOX)
        self.button_group.addButton(self.ui.sldRadioButton, CtrlID.SLIDER)

        # config
        self.config_file: Path = config.CONFIG_DIR.joinpath('%s.json' % APP_NAME)
        self.load_config()

        # tab
        self.ui.tabWidget.setCurrentIndex(0)

        # button
        self.ui.openDirButton.setStyleSheet(appearance.other_stylesheet)
        self.ui.openSampleButton.setStyleSheet(appearance.other_stylesheet)
        self.ui.loaderButton.setStyleSheet(appearance.in_stylesheet)
        self.ui.margeButton.setStyleSheet(appearance.in_stylesheet)
        self.ui.dissolveButton.setStyleSheet(appearance.in_stylesheet)
        self.ui.switchButton.setStyleSheet(appearance.in_stylesheet)
        self.ui.addButtonButton.setStyleSheet(appearance.in_stylesheet)
        self.ui.addPoseToolsButton.setStyleSheet(appearance.in_stylesheet)

        # event
        self.ui.openSiteButton.clicked.connect(partial(
            util.open_url,
            'https://www.steakunderwater.com/VFXPedia/96.0.243.189/indexae7c.html',
        ))
        self.ui.openDirButton.clicked.connect(partial(
            self.open_dir,
            config.RESOLVE_USER_PATH.joinpath('Templates', 'Edit', 'Generators'),
        ))  # FusionでもResolveへ入れる。

        self.ui.openSampleButton.clicked.connect(partial(
            self.open_dir,
            config.ROOT_PATH.joinpath('Templates', 'Edit', 'Generators'),
        ))
        user_path = config.get_user_path(self.fusion.GetResolve() is not None)
        self.ui.openFuseDirButton.clicked.connect(partial(
            self.open_dir,
            user_path.joinpath('Fuses'),
        ))
        self.ui.loaderButton.clicked.connect(self.make_loader)
        self.ui.addPoseToolsButton.clicked.connect(self.add_pose_tools)
        self.ui.margeButton.clicked.connect(self.marge)
        self.ui.dissolveButton.clicked.connect(self.dissolve)
        self.ui.switchButton.clicked.connect(self.switch_fuse)
        self.ui.addButtonButton.clicked.connect(self.add_button)
        self.ui.closeButton.clicked.connect(self.close)

    def open_dir(self, d):
        if not d.is_dir():
            r = QMessageBox.warning(
                self,
                f'Warning:  {str(d)}', f'ディレクトリが見つかりません。\n{str(d)}\n作成しますか？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if r == QMessageBox.StandardButton.Yes:
                d.mkdir(parents=True, exist_ok=True)
            else:
                return
        util.open_directory(d)

    def check_page(self):
        resolve = self.fusion.GetResolve()
        if resolve is not None and resolve.GetCurrentPage() != 'fusion':
            QMessageBox.warning(self, 'Warning', 'Fusion Pageで実行してください。')
            return False
        return True

    def get_comp(self):
        if not self.check_page():
            return None
        comp = self.fusion.CurrentComp
        if comp is None:
            QMessageBox.warning(self, 'Warning', 'コンポジションが見付かりません。')
            return None
        return comp

    def make_loader(self):
        comp = self.get_comp()
        if comp is None:
            return

        # data
        data = self.get_data()
        op.loader(comp, data.post_multiply)
        print('Done!')

    def get_tools(self, comp):
        tools = list(comp.GetToolList(True).values())
        if len(tools) == 0:
            QMessageBox.warning(self, 'Warning', 'ノードを選択してください。')
            return None
        return tools

    def switch_fuse(self):
        comp = self.get_comp()
        if comp is None:
            return

        # tools
        tools = self.get_tools(comp)
        if tools is None:
            return
        if len(tools) > 64:
            QMessageBox.warning(self, 'Warning', '選択数を64個以下にしてください。')
            return
        flow = comp.CurrentFrame.FlowView
        tools.sort(key=lambda x: list(flow.GetPosTable(x).values())[0])

        # data
        data = self.get_data()

        # undo
        comp.Lock()
        comp.StartUndo('RS SwitchFuse')

        # Fuse.Switch
        _x, _y = flow.GetPosTable(tools[len(tools) - 1]).values()
        sw = comp.AddTool('Fuse.Switch', _x, _y + 4)

        # user_controls
        user_controls = {}
        ctrl_name = 'SW_ComboBox'
        user_controls[ctrl_name] = {
            'LINKID_DataType': 'Number',
            'INPID_InputControl': 'ComboControl',
            'LINKS_Name': data.ctrl_name,
            'INP_Integer': True,
            'INP_Default': 0,
            'ICS_ControlPage': data.page_name,
        }
        for i, layer in enumerate(tools):
            layer_name: str = layer.Name
            if layer.ID == 'Loader':
                layer_name = Path(layer.Clip[1]).stem.strip()
            sw.ConnectInput('Input%d' % (i + 1), layer)
            dct = user_controls[ctrl_name]
            dct[i + 1] = {'CCS_AddString': '%s' % layer_name}
        # xf
        uc = {'__flags': 2097152}  # 順番を保持するフラグ
        for k, v in list(user_controls.items()):
            uc[k] = v
        sw.UserControls = uc
        sw.Which.SetExpression('%s + 1' % ctrl_name)
        sw.Refresh()

        # end
        comp.EndUndo(True)
        comp.Unlock()
        print('Done!')

    def marge(self):
        comp = self.get_comp()
        if comp is None:
            return

        # tools
        tools = self.get_tools(comp)
        if tools is None:
            return

        flow = comp.CurrentFrame.FlowView
        tools.sort(key=lambda x: list(flow.GetPosTable(x).values())[0])

        # data
        data = self.get_data()

        # undo
        comp.Lock()
        comp.StartUndo('RS Marge')

        # XF
        _x, _y = flow.GetPosTable(tools[len(tools) - 1]).values()
        xf = comp.AddTool('Transform', round(_x) + 1, round(_y) + 4)

        # BG
        _x, _y = flow.GetPosTable(tools[0]).values()
        bg = comp.AddTool('Background', round(_x) - 1, round(_y) + 4)
        bg.UseFrameFormatSettings = 0
        bg.Width = data.width
        bg.Height = data.height
        bg.TopLeftAlpha = 0
        bg.Depth = 1

        pre_node = bg

        # user_controls
        user_controls = {}
        ctrl_name = 'RS_ComboBox'
        if data.ctrl_type == CtrlID.COMBOBOX:
            user_controls[ctrl_name] = {
                'LINKID_DataType': 'Number',
                'INPID_InputControl': 'ComboControl',
                'LINKS_Name': data.ctrl_name,
                'INP_Integer': True,
                'INP_Default': 0,
                'ICS_ControlPage': data.page_name,
            }
        elif data.ctrl_type == CtrlID.SLIDER:
            ctrl_name = 'RS_Slider'
            user_controls[ctrl_name] = {
                'LINKID_DataType': 'Number',
                'INPID_InputControl': 'SliderControl',
                'LINKS_Name': data.ctrl_name,
                'INP_Integer': True,
                'INP_Default': 0,
                'INP_MinScale': 0,
                'INP_MaxScale': len(tools) - 1,
                'ICS_ControlPage': data.page_name,
            }

        for i, layer in enumerate(tools):
            layer_name: str = layer.Name
            if layer.ID == 'Loader':
                layer_name = Path(layer.Clip[1]).stem.strip()
            _x, _y = flow.GetPosTable(layer).values()
            mg = comp.AddTool('Merge', round(_x), round(_y) + 4)
            mg.ConnectInput('Foreground', layer)
            mg.ConnectInput('Background', pre_node)
            if data.ctrl_type == CtrlID.COMBOBOX:
                dct = user_controls[ctrl_name]
                dct[i + 1] = {'CCS_AddString': '%s' % layer_name}
                mg.Blend.SetExpression('iif(%s.%s == %d, 1, 0)' % (xf.Name, ctrl_name, i))
            elif data.ctrl_type == CtrlID.SLIDER:
                mg.Blend.SetExpression('iif(%s.%s == %d, 1, 0)' % (xf.Name, ctrl_name, i))
            else:
                uc_name = 'N' + str(i).zfill(3) + '_' + layer.Name
                user_controls[uc_name] = {
                    'LINKID_DataType': 'Number',
                    'INPID_InputControl': 'CheckboxControl',
                    'LINKS_Name': layer_name,
                    'INP_Integer': True,
                    'CBC_TriState': False,
                    'INP_Default': 1,
                    'ICS_ControlPage': data.page_name,
                    # 'ICS_ControlPage': 'Controls',
                }
                mg.Blend.SetExpression('%s.%s' % (xf.Name, uc_name))
            pre_node = mg
        # xf
        xf.ConnectInput('Input', pre_node)
        uc = {'__flags': 2097152}  # 順番を保持するフラグ
        for k, v in list(user_controls.items()):
            uc[k] = v
        xf.UserControls = uc
        xf.Refresh()

        # end
        comp.EndUndo(True)
        comp.Unlock()
        print('Done!')

    def dissolve(self):
        comp = self.get_comp()
        if comp is None:
            return

        # tools
        tools = self.get_tools(comp)
        if tools is None:
            return

        flow = comp.CurrentFrame.FlowView
        tools.sort(key=lambda x: list(flow.GetPosTable(x).values())[0])

        # data
        data = self.get_data()

        # undo
        comp.Lock()
        comp.StartUndo('RS Dissolve')

        # XF
        _x, _y = flow.GetPosTable(tools[len(tools) - 1]).values()
        xf = comp.AddTool('Transform', _x + 1, _y + 4)

        pre_node = None

        # user_controls
        user_controls = {data.ctrl_name: {
            'LINKID_DataType': 'Number',
            'INPID_InputControl': 'SliderControl',
            'LINKS_Name': data.ctrl_name,
            'INP_Integer': False,
            'INP_Default': 0,
            'ICS_ControlPage': data.page_name,
        }}

        for i, layer in enumerate(tools):
            if pre_node is None:
                pre_node = layer
                continue
            num = len(tools) - i

            _x, _y = flow.GetPosTable(layer).values()
            dx = comp.AddTool('Dissolve', _x, _y + 4)
            dx.ConnectInput('Foreground', pre_node)
            dx.ConnectInput('Background', layer)

            uc_name = 'Threshold_' + str(num).zfill(3)
            user_controls[uc_name] = {
                'LINKID_DataType': 'Number',
                'INPID_InputControl': 'SliderControl',
                'LINKS_Name': uc_name,
                'INP_Integer': False,
                'INP_Default': (1 / len(tools)) * num,
                'ICS_ControlPage': data.page_name,
            }
            dx.Mix.SetExpression('iif(%s.%s >= %s.%s, 1, 0)' % (
                xf.Name,
                data.ctrl_name,
                xf.Name,
                uc_name,
            ))
            pre_node = dx

        # xf
        xf.ConnectInput('Input', pre_node)
        uc = {'__flags': 2097152}  # 順番を保持するフラグ
        for k, v in list(user_controls.items()):
            uc[k] = v
        xf.UserControls = uc
        xf.Refresh()

        # end
        comp.EndUndo(True)
        comp.Unlock()
        print('Done!')

    def add_button(self):
        comp = self.get_comp()
        if comp is None:
            return

        # tools
        tools = self.get_tools(comp)
        if tools is None:
            return

        flow = comp.CurrentFrame.FlowView
        tools.sort(key=lambda x: list(flow.GetPosTable(x).values())[0])

        # data
        data = self.get_data()

        # undo
        comp.Lock()
        comp.StartUndo('RS Button')

        # XF
        _x, _y = flow.GetPosTable(tools[len(tools) - 1]).values()
        xf = comp.AddTool('Transform', _x, _y + 4)
        xf.SetAttrs({'TOOLS_Name': data.page_name + 'Selector'})
        xf.ConnectInput('Input', tools[0])

        # user_controls
        user_controls = {}
        for i, layer in enumerate(tools):
            layer_name: str = layer.Name
            if layer.ID == 'Loader':
                layer_name = Path(layer.Clip[1]).stem.strip()
            uc_name = 'N' + str(i).zfill(3) + '_' + layer.Name
            user_controls[uc_name] = uc_button(xf, layer, data.page_name, layer_name, 1.0, [
                'Center',
                'Pivot',
                'Angle',
                'Size',
                'Aspect',
                'XSize',
                'YSize',
            ])

        # xf
        uc = {'__flags': 2097152}  # 順番を保持するフラグ
        for k, v in list(user_controls.items()):
            uc[k] = v
        xf.UserControls = uc
        xf.Refresh()

        # end
        comp.EndUndo(True)
        comp.Unlock()
        print('Done!')

    def add_pose_tools(self):
        comp = self.get_comp()
        if comp is None:
            return

        # tool
        tools = self.get_tools(comp)
        if tools is None:
            return
        tool = tools[0]

        # user_controls
        user_controls = pose.get_uc('Controls')
        # undo
        comp.Lock()
        comp.StartUndo('RS Button')

        # set user_controls
        tool.UserControls = user_controls
        comp.Copy(tool)
        tool.Delete()
        comp.Paste()

        # end
        comp.EndUndo(True)
        comp.Unlock()
        QMessageBox.information(self, "Info", 'Done!')
        print('Done!')

    def set_data(self, c: ConfigData):
        self.ui.multiplyCheckBox.setChecked(c.post_multiply)
        self.button_group.button(c.ctrl_type).setChecked(True)
        self.ui.ctrlNameLineEdit.setText(c.ctrl_name)
        self.ui.pageNameLineEdit.setText(c.page_name)
        self.ui.widthSpinBox.setValue(c.width)
        self.ui.heightSpinBox.setValue(c.height)

    def get_data(self) -> ConfigData:
        c = ConfigData()
        c.post_multiply = self.ui.multiplyCheckBox.isChecked()
        c.ctrl_type = self.button_group.checkedId()
        c.ctrl_name = self.ui.ctrlNameLineEdit.text().strip()
        if c.ctrl_name == '':
            c.ctrl_name = 'Select'
        c.page_name = self.ui.pageNameLineEdit.text().strip()
        if c.page_name == '':
            c.page_name = 'User'
        c.width = self.ui.widthSpinBox.value()
        c.height = self.ui.heightSpinBox.value()
        return c

    def load_config(self) -> None:
        c = ConfigData()
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
