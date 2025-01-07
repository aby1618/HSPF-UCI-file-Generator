from PySide6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget, QFileDialog, QHBoxLayout, QMessageBox,
    QDialog, QFormLayout, QPlainTextEdit  # <-- Add QPlainTextEdit here
)
import sys
from lxml import etree

class SectionWindow(QDialog):
    def __init__(self, section_name, fields):
        super().__init__()
        self.setWindowTitle(section_name)
        self.layout = QFormLayout()

        self.inputs = {}
        for field_name, placeholder in fields.items():
            label = QLabel(field_name)
            input_field = QLineEdit()
            input_field.setPlaceholderText(placeholder)
            self.layout.addRow(label, input_field)
            self.inputs[field_name] = input_field

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        self.layout.addWidget(self.save_button)
        self.setLayout(self.layout)

    def save(self):
        # Gather all inputs and close the window
        self.saved_data = {field: input_field.text() for field, input_field in self.inputs.items()}
        QMessageBox.information(self, "Saved", "Data for the section has been saved.")
        self.accept()

def parse_shapes(root):
    """
    Parse <mxCell vertex="1"> elements, classify them by style:
      - ellipse;...       => Subcatchment
      - shape=hexagon;... => RCHRES
      - shape=waypoint;... => Node
      - triangle;...      => SWM Facility
    If none match, we store as 'Comment/Note'.
    Returns { internal_id: { ... }, ... }
    """
    shapes_by_id = {}
    # No namespace in your file, so we do not use a prefix
    shape_cells = root.xpath(".//mxCell[@vertex='1']")
    for cell in shape_cells:
        internal_id = cell.get("id", "").strip()
        style = cell.get("style", "").lower()
        label = cell.get("value", "").strip()

        if not internal_id:
            continue  # skip shapes with no ID

        # Classification by style
        # We'll do if/elif to avoid conflicting matches
        hydro_type = None
        if "ellipse;" in style:
            # e.g. ellipse;whiteSpace=wrap;html=1;...
            hydro_type = "Subcatchment"
        elif "shape=hexagon" in style:
            # e.g. shape=hexagon;perimeter=hexagonPerimeter2;...
            hydro_type = "RCHRES"
        elif "shape=waypoint" in style and "perimeter=centerperimeter" in style:
            # e.g. shape=waypoint;...;perimeter=centerPerimeter
            hydro_type = "Node"
        elif "triangle;" in style:
            # e.g. triangle;whiteSpace=wrap;...
            hydro_type = "SWM Facility"
        else:
            hydro_type = "Comment/Note"

        shapes_by_id[internal_id] = {
            "id": internal_id,
            "label": label,
            "hydro_type": hydro_type,
            "incoming": [],
            "outgoing": []
        }

    return shapes_by_id

def parse_edges(root):
    """
    Parse <mxCell edge="1"> elements, gather source/target/style.
    Returns a list of (src_id, tgt_id, style).
    """
    edges = []
    edge_cells = root.xpath(".//mxCell[@edge='1']")
    for cell in edge_cells:
        src_id = cell.get("source", "").strip()
        tgt_id = cell.get("target", "").strip()
        style = cell.get("style", "").lower() if cell.get("style") else ""

        if src_id and tgt_id:
            edges.append((src_id, tgt_id, style))

    return edges

def build_graph(shapes_by_id, edges):
    """
    For each (src, tgt, style), if neither is missing,
    add src->tgt to 'outgoing' and tgt->src to 'incoming'.
    Skips dashed lines (if any).
    """
    for (src, tgt, style) in edges:
        # If the line is dashed=1, we skip it
        if "dashed=1" in style:
            continue

        if src in shapes_by_id and tgt in shapes_by_id:
            shapes_by_id[src]["outgoing"].append(tgt)
            shapes_by_id[tgt]["incoming"].append(src)

def narrative_summary(shapes_by_id):
    """
    Builds a multiline text summary such that:
      - When we handle "ShapeA → NodeX", we immediately look for other feeders into NodeX
        and list them too, before we move on to "NodeX → ???".
      - This yields a continuous 'chain' of lines, so the same node (e.g., NodeX) appears
        consecutively for all incoming lines, then we show NodeX's own outflow.
    """

    visited_lines = set()  # We'll store lines like ("sub_id","node_id") so we don't repeat them
    visited_targets = set()  # We'll store shapes fully processed
    lines = []

    def add_line(source_id, target_id):
        """Helper to add a line 'ShapeType Label → ShapeType Label' to lines[] if not visited."""
        line_key = (source_id, target_id)
        if line_key in visited_lines:
            return  # already listed
        visited_lines.add(line_key)

        source_data = shapes_by_id[source_id]
        target_data = shapes_by_id[target_id]
        src_type = source_data["hydro_type"]
        src_label = source_data["label"] or source_id
        tgt_type = target_data["hydro_type"]
        tgt_label = target_data["label"] or target_id

        lines.append(f"{src_type} {src_label} discharges to {tgt_type} {tgt_label}.")

    def process_target(target_id):
        """
        Once we reach a target shape (e.g., NodeX),
        we gather all *other* shapes that also feed into this target,
        placing them immediately after.
        Then, after listing all feeders, we proceed with target's outflow.
        """
        # If we've already fully processed this shape, skip
        if target_id in visited_targets:
            return

        # 1) For each shape that flows into target_id,
        #    if that line is unvisited, create it and recursively process that feeder first.
        for incoming_id in shapes_by_id[target_id]["incoming"]:
            line_key = (incoming_id, target_id)
            if line_key not in visited_lines:
                # Recursively ensure *its* incoming feeders are processed
                process_target(incoming_id)
                # Now add the line from incoming_id → target_id
                add_line(incoming_id, target_id)

        # 2) Once we've listed all lines that feed into target_id,
        #    we handle target_id's outflow (target_id → next).
        outgoings = shapes_by_id[target_id]["outgoing"]
        if not outgoings:
            data = shapes_by_id[target_id]
            if data["hydro_type"] != "Comment/Note":
                lines.append(f"{data['hydro_type']} {data['label']} does not discharge to any recognized element.")
        else:
            for nxt_id in outgoings:
                add_line(target_id, nxt_id)
                process_target(nxt_id)

        # Mark this shape as fully processed now that we've listed its incoming and outgoing
        visited_targets.add(target_id)

    # -------------
    # MAIN LOGIC
    # -------------
    # We'll start with shapes that have no incoming edges (typical "sources" like subcatchments).
    start_shapes = [sid for sid, data in shapes_by_id.items()
                    if data["hydro_type"] != "Comment/Note" and not data["incoming"]]

    # Process those "source" shapes
    for s_id in start_shapes:
        # Do NOT add s_id to visited_targets here—just call process_target
        process_target(s_id)

    # Now, if there are shapes that never got visited (disconnected or merges),
    # we process them too.
    for sid in shapes_by_id:
        if sid not in visited_targets:
            process_target(sid)

    return "\n".join(lines)

class ModelSummaryDialog(QDialog):
    """
    A dialog with a multiline text area for displaying/copying the imported model summary.
    """

    def __init__(self, summary_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Model Summary")
        layout = QVBoxLayout()

        self.text_area = QPlainTextEdit()
        self.text_area.setReadOnly(False)  # allow user to copy
        self.text_area.setPlainText(summary_text)
        layout.addWidget(self.text_area)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self.setLayout(layout)
        self.resize(800, 600)

class UCIFileGeneratorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HSPF UCI File Generator")
        self.setGeometry(100, 100, 700, 500)

        # We'll store shapes by internal ID after importing the draw.io file
        # Key: internal_id (string), Value: dictionary with "label", "hydro_type", "incoming", "outgoing"
        self.shapes_by_id = {}

        # Create and set the central widget/layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)

        # --- Section Buttons for each UCI section ---
        # This is an example set; adapt to your needs
        self.add_section_button(main_layout, "GLOBAL",
                                "Defines global simulation parameters.",
                                self.global_section)
        self.add_section_button(main_layout, "FILES",
                                "Specifies file names for input and output.",
                                self.files_section)
        self.add_section_button(main_layout, "OPN SEQUENCE",
                                "Defines the operation sequence for the simulation.",
                                self.opn_sequence_section)
        self.add_section_button(main_layout, "PERLND",
                                "Specifies parameters for pervious land areas.",
                                self.perlnd_section)
        self.add_section_button(main_layout, "IMPLND",
                                "Specifies parameters for impervious land areas.",
                                self.implnd_section)
        self.add_section_button(main_layout, "RCHRES",
                                "Defines routing for reaches and reservoirs.",
                                self.rchres_section)
        self.add_section_button(main_layout, "FTABLES",
                                "Specifies tables for flow routing.",
                                self.ftables_section)
        self.add_section_button(main_layout, "EXT SOURCES",
                                "Defines external sources of input.",
                                self.ext_sources_section)
        self.add_section_button(main_layout, "EXT TARGETS",
                                "Specifies targets for external inputs.",
                                self.ext_targets_section)
        self.add_section_button(main_layout, "NETWORK",
                                "Defines flow relationships between elements.",
                                self.network_section)

        # --- Import/Show Model Buttons ---
        import_layout = QHBoxLayout()

        # Button to import a draw.io XML file
        self.import_button = QPushButton("Import Draw.io File")
        self.import_button.clicked.connect(self.import_drawio_file)
        import_layout.addWidget(self.import_button)

        # Button to show the imported model summary
        self.show_model_button = QPushButton("Show Imported Model")
        self.show_model_button.clicked.connect(self.show_imported_model)
        import_layout.addWidget(self.show_model_button)

        main_layout.addLayout(import_layout)

    def import_drawio_file(self):
        file_dialog = QFileDialog(self)
        xml_file, _ = file_dialog.getOpenFileName(self, "Select Draw.io XML", "", "XML Files (*.xml)")
        if xml_file:
            try:
                tree = etree.parse(xml_file)
                root = tree.getroot()

                # 1) Parse shapes
                self.shapes_by_id = parse_shapes(root)

                # 2) Parse edges
                edges = parse_edges(root)

                # 3) Build connectivity
                build_graph(self.shapes_by_id, edges)

                QMessageBox.information(self, "Import Complete", "File parsed successfully.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to parse XML:\n{e}")

    def show_imported_model(self):
        """
        Display a summary of the recognized shapes in a new window,
        using the new 'narrative_summary' approach.
        """
        if not self.shapes_by_id:
            QMessageBox.warning(self, "No Data", "No model data has been imported yet.")
            return

        from functools import partial  # if needed, or not if we do not use it
        summary = narrative_summary(self.shapes_by_id)
        if not summary.strip():
            summary = "No recognized connections."

        dialog = ModelSummaryDialog(summary, self)
        dialog.exec()

    # Existing callback stubs for sections
    def add_section_button(self, layout, section_name, help_text, callback):
        section_layout = QHBoxLayout()
        section_button = QPushButton(section_name)
        section_button.clicked.connect(callback)
        help_button = QPushButton("?")
        help_button.setMaximumWidth(30)
        help_button.clicked.connect(lambda: self.show_help(section_name, help_text))
        section_layout.addWidget(section_button)
        section_layout.addWidget(help_button)
        layout.addLayout(section_layout)

    def show_help(self, title, message):
        QMessageBox.information(self, title, message)

    # Section callbacks
    def global_section(self):
        fields = {
            "Start Date (YYYY/MM/DD)": "Enter the start date for the simulation",
            "End Date (YYYY/MM/DD)": "Enter the end date for the simulation"
        }
        self.open_section_window("GLOBAL", fields)

    def files_section(self):
        fields = {
            "WDM1 File Name": "Enter the first WDM file name (e.g., CONMET.WDM)",
            "WDM2 File Name": "Enter the second WDM file name (e.g., CONOUT.WDM)"
        }
        self.open_section_window("FILES", fields)

    def opn_sequence_section(self):
        fields = {
            "Operation Sequence": "Define the operation sequence (e.g., INDELT 00:15)"
        }
        self.open_section_window("OPN SEQUENCE", fields)

    def perlnd_section(self):
        fields = {
            "Pervious Land Parameters": "Specify parameters for pervious land areas"
        }
        self.open_section_window("PERLND", fields)

    def implnd_section(self):
        fields = {
            "Impervious Land Parameters": "Specify parameters for impervious land areas"
        }
        self.open_section_window("IMPLND", fields)

    def rchres_section(self):
        fields = {
            "Routing Parameters": "Specify parameters for reaches and reservoirs"
        }
        self.open_section_window("RCHRES", fields)

    def ftables_section(self):
        fields = {
            "FTable Parameters": "Specify flow tables for routing"
        }
        self.open_section_window("FTABLES", fields)

    def ext_sources_section(self):
        fields = {
            "External Source Parameters": "Define external input sources"
        }
        self.open_section_window("EXT SOURCES", fields)

    def ext_targets_section(self):
        fields = {
            "External Target Parameters": "Specify targets for external inputs"
        }
        self.open_section_window("EXT TARGETS", fields)

    def network_section(self):
        fields = {
            "Flow Network Parameters": "Define flow relationships between elements"
        }
        self.open_section_window("NETWORK", fields)

    def open_section_window(self, section_name, fields):
        window = SectionWindow(section_name, fields)
        if window.exec():
            print(f"Data for {section_name}:", window.saved_data)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = UCIFileGeneratorApp()
    window.show()
    sys.exit(app.exec())
