def get_header(name: str, use_group: bool) -> str:
    return """
{
	Tools = ordered() {
		['%s'] = %s {
""" % (name, 'GroupOperator' if use_group else 'MacroOperator')


def get_input(m_lst, lst) -> str:
    lines = ['			Inputs = ordered() {']
    cnt = 1
    for d in m_lst:
        lines.append('				MainInput%d = InstanceInput {' % cnt)
        lines.append('					Source = "%s",' % d['id'])
        lines.append('					SourceOp = "%s",' % d['node'])
        lines.append('				},')
        cnt += 1
    cnt = 1
    for d in lst:
        lines.append('				Input%d = InstanceInput {' % cnt)
        lines.append('					Source = "%s",' % d['id'])
        lines.append('					SourceOp = "%s",' % d['node'])
        if d['name'] is not None:
            lines.append('					Name = "%s",' % d['name'])
        if d['value'] is not None:
            lines.append('					Default = %s,' % d['value'])
        if d['control_group'] is not None:
            lines.append('					ControlGroup = %s,' % d['control_group'])
        if d['option01'] != '':
            lines.append('					%s,' % d['option01'])
        if d['option02'] != '':
            lines.append('					%s,' % d['option02'])
        if d['option03'] != '':
            lines.append('					%s,' % d['option03'])
        lines.append('				},')
        cnt += 1
    lines.append('			},')
    return '\n'.join(lines)


def get_output(lst) -> str:
    cnt = 1
    lines = ['			Outputs = ordered() {']
    for d in lst:
        lines.append('				MainOutput%d = InstanceOutput {' % cnt)
        lines.append('					Source = "%s",' % d['id'])
        lines.append('					SourceOp = "%s",' % d['node'])
        lines.append('				},')
        cnt += 1
    lines.append('			},')
    return '\n'.join(lines)


def get_footer():
    return """
			ViewInfo = GroupInfo {
				Flags = {
					AllowPan = false,
					ForceAll = true,
					GridSnap = true,
					AutoSnap = true
				},
				Direction = "Horizontal",
				PipeStyle = "Direct",
				Scale = 1,
				Offset = { 0, 0 }
			},
			CtrlWZoom = false,
			Tools = ordered() {
			}
		}
	}
}
"""


def get_save_script(path, name, text):
    lua = """
local function SaveMacro(path, name, text)
    local tools = comp:CopySettings()['Tools']
    local st = bmd.readstring(text)
    st['Tools'][name]['Tools'] = tools
    bmd.writefile(path, st)
end
"""
    return '\n'.join([
        lua,
        'SaveMacro(',
        '[[%s]],' % path,
        '[[%s]],' % name,
        '[[%s]])' % text,
    ])
