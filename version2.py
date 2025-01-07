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
    shape_cells = root.xpath(".//mxCell[@vertex='1']")
    for cell in shape_cells:
        internal_id = cell.get("id", "").strip()
        style = cell.get("style", "").lower()
        label = cell.get("value", "").strip()

        if not internal_id:
            continue

        recognized = False
        # Classification by style
        # We'll do if/elif to avoid conflicting matches
        if "ellipse;" in style:
            hydro_type = "Subcatchment"
            recognized = True
        elif "shape=hexagon" in style:
            hydro_type = "RCHRES"
            recognized = True
        elif "shape=waypoint" in style and "perimeter=centerperimeter" in style:
            hydro_type = "Node"
            recognized = True
        elif "triangle;" in style:
            hydro_type = "SWM Facility"
            recognized = True
        else:
            hydro_type = "Comment/Note"

        # Optional: produce a debug print or store a "warning" if not recognized
        if not recognized:
            print(
                f"WARNING: Shape with ID {internal_id} and style='{style}' was not recognized; treating as Comment/Note.")

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
    For each (src, tgt, style), add:
        src -> tgt to 'outgoing'
        tgt -> src to 'incoming'
    Distinguish dashed=1 (Groundwater) vs. solid (Surface).
    """
    for (src, tgt, style) in edges:
        if src in shapes_by_id and tgt in shapes_by_id:
            flow_type = "Groundwater" if "dashed=1" in style else "Surface"

            shapes_by_id[src]["outgoing"].append({
                "target": tgt,
                "flow_type": flow_type
            })
            shapes_by_id[tgt]["incoming"].append({
                "source": src,
                "flow_type": flow_type
            })


def compute_branch_length(shapes_by_id, start_id, memo=None):
    """
    Returns the maximum depth from 'start_id' down to any leaf.
    This helps us prioritize outflows from longest to shortest branch.
    """
    if memo is None:
        memo = {}
    if start_id in memo:
        return memo[start_id]

    outgoings = shapes_by_id[start_id]["outgoing"]
    if not outgoings:
        memo[start_id] = 1
        return 1

    max_depth = 0
    for out_dict in outgoings:
        tgt_id = out_dict["target"]
        depth_tgt = compute_branch_length(shapes_by_id, tgt_id, memo)
        if depth_tgt > max_depth:
            max_depth = depth_tgt
    memo[start_id] = 1 + max_depth
    return memo[start_id]

def narrative_summary(shapes_by_id):
    """
    Builds a multiline text summary such that:
      - When we handle "ShapeA → NodeX", we immediately look for other feeders into NodeX
        and list them too, before we move on to "NodeX → ???".
      - This yields a continuous 'chain' of lines, so the same node (e.g., NodeX) appears
        consecutively for all incoming lines, then we show NodeX's own outflow.
    """

    visited_lines = set()
    visited_targets = set()
    lines = []

    # Precompute branch lengths for each shape
    memo_lengths = {}
    for sid in shapes_by_id:
        compute_branch_length(shapes_by_id, sid, memo_lengths)

    def add_line(source_id, target_id, flow_type):
        """Add a line 'Source discharges (Surface/Groundwater) to Target' if not visited."""
        line_key = (source_id, target_id, flow_type)
        if line_key in visited_lines:
            return
        visited_lines.add(line_key)

        source_data = shapes_by_id[source_id]
        target_data = shapes_by_id[target_id]
        src_type = source_data["hydro_type"]
        src_label = source_data["label"] or source_id
        tgt_type = target_data["hydro_type"]
        tgt_label = target_data["label"] or target_id

        flow_text = "(Surface)" if flow_type == "Surface" else "(Groundwater)"
        lines.append(f"{src_type} {src_label} discharges {flow_text} to {tgt_type} {tgt_label}.")

    def process_target(target_id):
        # If we've already fully processed this shape, skip
        if target_id in visited_targets:
            return

        # (1) Process all incoming feeders first (they might not have been processed yet).
        for inc_dict in shapes_by_id[target_id]["incoming"]:
            incoming_id = inc_dict["source"]
            flow_type = inc_dict["flow_type"]

            line_key = (incoming_id, target_id, flow_type)
            if line_key not in visited_lines:
                process_target(incoming_id)
                add_line(incoming_id, target_id, flow_type)

        # (2) Sort outgoings by descending branch length
        outgoings = shapes_by_id[target_id]["outgoing"]
        if not outgoings:
            # No outflow
            data = shapes_by_id[target_id]
            if data["hydro_type"] != "Comment/Note":
                # Print "does not discharge..." only once
                lines.append(f"{data['hydro_type']} {data['label']} does not discharge to any recognized element.")
        else:
            # Sort by descending branch length
            outgoings_sorted = sorted(
                outgoings,
                key=lambda od: memo_lengths[od["target"]],
                reverse=True
            )
            for out_dict in outgoings_sorted:
                nxt_id = out_dict["target"]
                flow_type = out_dict["flow_type"]
                add_line(target_id, nxt_id, flow_type)
                process_target(nxt_id)

        visited_targets.add(target_id)

    # MAIN LOGIC
    start_shapes = [
        sid for sid, data in shapes_by_id.items()
        if data["hydro_type"] != "Comment/Note" and not data["incoming"]
    ]
    for s_id in start_shapes:
        process_target(s_id)

    # If any shapes remain unvisited (possibly orphaned or merges), process them
    for sid in shapes_by_id:
        if sid not in visited_targets:
            process_target(sid)

    # --------------------------------------------------
    # HIGHLIGHT ORPHANS HERE, before returning the lines
    # --------------------------------------------------
    orphans = []
    for sid, data in shapes_by_id.items():
        if not data["incoming"] and not data["outgoing"] and data["hydro_type"] != "Comment/Note":
            orphans.append(sid)

    if orphans:
        lines.append("The following shapes are not connected to any flow path:")
        for orphan_id in orphans:
            lbl = shapes_by_id[orphan_id]["label"] or orphan_id
            lines.append(f"  - {shapes_by_id[orphan_id]['hydro_type']} {lbl}")
        lines.append("")  # optional blank line

    return "\n".join(lines)

class ModelSummaryDialog(QDialog):
    def __init__(self, summary_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Model Summary")
        self.summary_text = summary_text

        layout = QVBoxLayout()

        self.text_area = QPlainTextEdit()
        self.text_area.setReadOnly(False)  # allow user to copy
        self.text_area.setPlainText(summary_text)
        layout.addWidget(self.text_area)

        button_layout = QHBoxLayout()

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_summary)
        button_layout.addWidget(save_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)
        self.resize(800, 600)

    def save_summary(self):
        file_dialog = QFileDialog(self)
        file_path, _ = file_dialog.getSaveFileName(
            self, "Save Summary", "", "Text Files (*.txt);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(self.summary_text)
                QMessageBox.information(self, "Success", f"Summary saved to {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error saving file:\n{e}")

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
