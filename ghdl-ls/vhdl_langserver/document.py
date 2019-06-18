import ctypes
import logging
import os
import libghdl.thin.name_table as name_table
import libghdl.thin.files_map as files_map
import libghdl.thin.files_map_editor as files_map_editor
import libghdl.thin.libraries as libraries
import libghdl.thin.vhdl.nodes as nodes
import libghdl.thin.vhdl.sem_lib as sem_lib
import libghdl.thin.vhdl.sem as sem
import libghdl.thin.vhdl.formatters as formatters

from . import symbols, references

log = logging.getLogger(__name__)

class Document(object):

    def __init__(self, uri, sfe=None, version=None):
        self.uri = uri
        self.version = version
        self._fe = sfe
        self._tree = nodes.Null_Iir

    @staticmethod
    def load(source, dirname, filename):
        # Write text to file buffer.
        src_utf8 = source.encode('utf-8')
        src_len = len(src_utf8)
        buf_len = src_len + 4096
        fileid = name_table.Get_Identifier(filename.encode('utf-8'))
        if os.path.isabs(filename):
            dirid = name_table.Null_Identifier
        else:
            dirid = name_table.Get_Identifier(dirname.encode('utf-8'))
        sfe = files_map.Reserve_Source_File(dirid, fileid, buf_len)
        files_map_editor.Fill_Text(sfe, ctypes.c_char_p(src_utf8), src_len)
        return sfe

    def reload(self, source):
        """Reload the source of a document.  """
        src_utf8 = source.encode('utf-8')
        files_map_editor.Fill_Text(self._fe,
            ctypes.c_char_p(src_utf8), len(src_utf8))

    def __str__(self):
        return str(self.uri)

    def apply_change(self, change):
        """Apply a change to the document."""
        text = change['text']
        change_range = change.get('range')

        text_utf8 = text.encode('utf-8')

        if not change_range:
            # The whole file has changed
            raise AssertionError
            #if len(text_utf8) < thin.Files_Map.Get_Buffer_Length(self._fe):
            #    xxxx_replace
            #else:
            #    xxxx_free
            #    xxxx_allocate
            #return

        start_line = change_range['start']['line']
        start_col = change_range['start']['character']
        end_line = change_range['end']['line']
        end_col = change_range['end']['character']

        files_map_editor.Replace_Text(
            self._fe,
            start_line + 1, start_col,
            end_line + 1, end_col,
            ctypes.c_char_p(text_utf8), len(text_utf8))

    def check_document(self, text):
        log.debug("Checking document: %s", self.uri)

        text_utf8 = text.encode('utf-8')

        files_map_editor.Check_Buffer_Content(
            self._fe, ctypes.c_char_p(text_utf8), len(text_utf8))

    @staticmethod
    def parse_document(sfe):
        return sem_lib.Load_File(sfe)

    @staticmethod
    def add_to_library(tree):
        # Detach the chain of units.
        unit = nodes.Get_First_Design_Unit(tree)
        nodes.Set_First_Design_Unit(tree, nodes.Null_Iir)
        # FIXME: free the design file ?
        tree = nodes.Null_Iir
        # Analyze unit after unit.
        while unit != nodes.Null_Iir:
            # Pop the first unit.
            next_unit = nodes.Get_Chain(unit)
            nodes.Set_Chain(unit, nodes.Null_Iir)
            lib_unit = nodes.Get_Library_Unit(unit)
            if (lib_unit != nodes.Null_Iir
                and nodes.Get_Identifier(unit) != name_table.Null_Identifier):
                # Put the unit (only if it has a library unit) in the library.
                libraries.Add_Design_Unit_Into_Library(unit, False)
                tree = nodes.Get_Design_File(unit)
            unit = next_unit
        return tree

    def compute_diags(self):
        log.debug("parse doc %d %s", self._fe, self.uri)
        tree = Document.parse_document(self._fe)
        if self._tree != nodes.Null_Iir:
            # FIXME: free + dependencies ?
            log.debug("purge %d", self._tree)
            libraries.Purge_Design_File(self._tree)
        self._tree = Document.add_to_library(tree)
        if self._tree == nodes.Null_Iir:
            # No units, nothing to add.
            return
        nodes.Set_Design_File_Source(self._tree, self._fe)
        log.debug("add_to_library(%u) -> %u", tree, self._tree)
        # Semantic analysis.
        unit = nodes.Get_First_Design_Unit(self._tree)
        while unit != nodes.Null_Iir:
            sem.Semantic(unit)
            nodes.Set_Date_State(unit, nodes.Date_State.Analyze)
            unit = nodes.Get_Chain(unit)

    def flatten_symbols(self, syms, parent):
        res = []
        for s in syms:
            s['location'] = {'uri': self.uri, 'range': s['range']}
            del s['range']
            s.pop('detail', None)
            if parent is not None:
                s['containerName'] = parent
            res.append(s)
            children = s.pop('children', None)
            if children is not None:
                res.extend(self.flatten_symbols(children, s))
        return res

    def document_symbols(self):
        log.debug("document_symbols")
        if self._tree == nodes.Null_Iir:
            return []
        syms = symbols.get_symbols_chain(self._fe, nodes.Get_First_Design_Unit(self._tree))
        return self.flatten_symbols(syms, None)

    def position_to_location(self, position):
        pos = files_map.File_Line_To_Position(self._fe, position['line'] + 1)
        return files_map.File_Pos_To_Location(self._fe, pos) + position['character']

    def goto_definition(self, position):
        loc = self.position_to_location(position)
        return references.goto_definition(self._tree, loc)

    def format_range(self, rng):
        first_line = rng['start']['line'] + 1
        last_line = rng['end']['line'] + (1 if rng['end']['character'] != 0 else 0)
        if last_line < first_line:
            return None
        if self._tree == nodes.Null_Iir:
            return None
        hand = formatters.Allocate_Handle()
        formatters.Indent_String(self._tree, hand, first_line, last_line)
        buffer = formatters.Get_C_String(hand)
        buf_len = formatters.Get_Length(hand)
        newtext = buffer[:buf_len].decode('utf-8')
        res = [ {'range': {
                     'start': { 'line': first_line - 1, 'character': 0},
                     'end': { 'line': last_line, 'character': 0}},
                 'newText': newtext}]
        formatters.Free_Handle(hand)
        return res