from PySide6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget, QFileDialog, QHBoxLayout, QMessageBox,
    QDialog, QFormLayout, QPlainTextEdit  # <-- Add QPlainTextEdit here
)
from PySide6.QtGui import Qt
from PySide6.QtCore import QUrl
import webbrowser
import sys
from lxml import etree

class SectionWindow(QDialog):
    def __init__(self, section_name, fields, pdf_base_url, parent_main):
        super().__init__()
        self.section_name = section_name
        self.fields = fields
        self.pdf_base_url = pdf_base_url  # or fallback if None
        self.parent_main = parent_main
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


class SectionWindow(QDialog):
    def __init__(self, section_name, fields, pdf_base_url, parent_main):
        super().__init__()
        self.section_name = section_name
        self.fields = fields
        self.pdf_base_url = pdf_base_url
        self.parent_main = parent_main  # reference to UCIFileGeneratorApp, if needed

        self.saved_data = {}  # we'll store final results here
        self.section_state = "empty"  # will be "empty", "partial", or "complete"

        self.setWindowTitle(f"{section_name} Section")

        main_layout = QVBoxLayout(self)

        # We'll store references to the QLineEdits so we can check them easily
        self.input_fields = {}

        # Create input rows
        for field_name, field_info in fields.items():
            row_layout = QHBoxLayout()

            label = QLabel(field_name)
            row_layout.addWidget(label)

            input_field = QLineEdit()
            placeholder_text = field_info.get("placeholder", "")
            input_field.setPlaceholderText(placeholder_text)
            self.input_fields[field_name] = input_field
            row_layout.addWidget(input_field)

            # Info/help button
            help_button = QPushButton("?")
            help_button.setFixedWidth(30)
            ht = field_info.get("help_text", "")
            pg = field_info.get("pdf_page", 1)
            help_button.clicked.connect(lambda _, f=field_name, h=ht, p=pg: self.show_help(f, h, p))
            row_layout.addWidget(help_button)

            main_layout.addLayout(row_layout)

            # Connect textChanged signal so we can enable/disable buttons dynamically
            input_field.textChanged.connect(self.on_field_changed)

        # Button layout
        button_layout = QHBoxLayout()

        # Preview button (disabled initially)
        self.preview_button = QPushButton("Preview")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.on_preview_clicked)
        button_layout.addWidget(self.preview_button)

        # Save button (disabled initially)
        self.save_button = QPushButton("Save")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.on_save_clicked)
        button_layout.addWidget(self.save_button)

        main_layout.addLayout(button_layout)

        self.resize(500, 300)

    def on_field_changed(self):
        """Check if we should enable Save/Preview buttons based on required fields."""
        required_filled = True
        any_filled = False

        for field_name, field_info in self.fields.items():
            val = self.input_fields[field_name].text().strip()
            req = field_info.get("required", False)

            if val:
                any_filled = True
            else:
                # It's empty
                if req:
                    required_filled = False

        # Enable Save if there's at least one filled field
        self.save_button.setEnabled(any_filled)

        # Enable Preview if all required fields are filled
        self.preview_button.setEnabled(required_filled and any_filled)

    def on_save_clicked(self):
        """
        Gather all fields into self.saved_data,
        determine section_state (empty, partial, complete),
        then close the dialog (accept).
        """
        self.saved_data.clear()
        filled_count = 0
        required_count = 0
        required_filled_count = 0

        for field_name, field_info in self.fields.items():
            val = self.input_fields[field_name].text().strip()
            self.saved_data[field_name] = val

            if field_info.get("required", False):
                required_count += 1
                if val:
                    required_filled_count += 1

            if val:
                filled_count += 1

        # Determine final state
        if filled_count == 0:
            self.section_state = "empty"
        elif required_filled_count == required_count:
            self.section_state = "complete"
        else:
            self.section_state = "partial"

        # Optional: Show a warning if partial
        if self.section_state == "partial":
            QMessageBox.warning(self, "Incomplete Section",
                "Not all required fields are filled.\n"
                "You can still save, but you won't be able to Preview unless all required fields are filled.")

        self.accept()  # closes the dialog

    def on_preview_clicked(self):
        """
        In step 2, we'll just show a simple message.
        In step 3, we'll show a proper preview dialog with formatted text.
        """
        QMessageBox.information(self, "Preview",
            "This will eventually show the exact text for the UCI section.\n"
            "All required fields are filled, so you can see a preview or proceed.")
        # Later: We will implement a real preview window.

    def show_help(self, field_name, help_text, pdf_page):
        """Displays a help message with a link to the PDF if desired."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Help for {field_name}")
        layout = QVBoxLayout(dialog)

        info_label = QLabel()
        info_label.setTextFormat(Qt.RichText)
        info_label.setOpenExternalLinks(True)

        link_html = ""
        if self.pdf_base_url:
            link_html = f"<br><a href='{self.pdf_base_url}#page={pdf_page}' target='_blank'>Read more</a>"
        info_label.setText(f"{help_text}{link_html}")
        layout.addWidget(info_label)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button)

        dialog.exec()

    def save(self):
        self.saved_data = {
            field: input_field.text()
            for field, input_field in self.inputs.items()
        }
        QMessageBox.information(self, "Saved", "Data for the section has been saved.")
        self.accept()

class UCIFileGeneratorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.pdf_base_url = "https://hydrologicmodels.tamu.edu/wp-content/uploads/sites/103/2018/09/HSPF_User-Manual.pdf"
        self.setWindowTitle("HSPF UCI File Generator")
        self.setGeometry(100, 100, 700, 500)
        self.section_buttons = {}

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

        #change from here
        # ------------------------------------------
        # STEP 1: ADD "Load JSON" BUTTON + STORAGE
        # ------------------------------------------
        self.load_json_button = QPushButton("Load JSON")
        self.load_json_button.clicked.connect(self.load_json_data)
        import_layout.addWidget(self.load_json_button)

        # We'll keep a dictionary to store all section data in memory.
        # Later steps will fill this dict and optionally save it to JSON.
        self.section_data = {}  # { "GLOBAL": {field: value, ...}, "FILES": {...}, etc. }
        #do not change from here

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

    def load_json_data(self):
        """
        Opens a dialog to let the user pick a previously saved JSON,
        then loads it into self.section_data (for later use).
        """
        file_dialog = QFileDialog(self)
        json_file, _ = file_dialog.getOpenFileName(self, "Select JSON File", "", "JSON Files (*.json);;All Files (*)")
        if not json_file:
            return  # user canceled

        try:
            import json
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Store loaded data in memory
            self.section_data = data if isinstance(data, dict) else {}

            QMessageBox.information(self, "JSON Loaded", "Section data successfully loaded from JSON.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load JSON:\n{e}")

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

        self.section_buttons[section_name] = section_button

    def show_help(self, title, message):
        QMessageBox.information(self, title, message)

    # Section callbacks
    def global_section(self):

        fields = {
            "Model Name": {
                "placeholder": "Enter a descriptive name for the watershed/model run",
                "help_text": (
                    "This name or description will appear under the GLOBAL block in the UCI file. "
                    "It identifies your watershed or scenario."
                ),
                "pdf_page": 28
            },
            "Start Date (YYYY/MM/DD)": {
                "placeholder": "Enter the simulation start date",
                "help_text": (
                    "HSPF will begin its simulation on this date. "
                    "Make sure it aligns with your input data availability."
                ),
                "pdf_page": 29
            },
            "End Date (YYYY/MM/DD)": {
                "placeholder": "Enter the simulation end date",
                "help_text": (
                    "HSPF will end its simulation on this date. "
                    "Again, ensure data is available up to this date."
                ),
                "pdf_page": 29
            },
            "Run/Interp/Output Level": {
                "placeholder": "e.g., RUN INTERP OUTPUT LEVEL    3",
                "help_text": (
                    "Specifies how HSPF will run.\n"
                    " - 'RUN' vs 'RESUME' (fresh vs. continue)\n"
                    " - 'INTERP' means timeseries data is interpolated\n"
                    " - 'OUTPUT LEVEL' controls detail in output (0..5)."
                ),
                "pdf_page": 30
            },
            "Resume / Run": {
                "placeholder": "e.g., RESUME     0 RUN     1",
                "help_text": (
                    "'RESUME 0' means do not resume a previous run,\n"
                    "'RUN 1' is the run ID. If continuing an older run,\n"
                    "you might set 'RESUME 1 RUN 2', etc."
                ),
                "pdf_page": 30
            },
            "Unit System": {
                "placeholder": "1 = English, 2 = SI Metric",
                "help_text": (
                    "Defines the measurement units:\n"
                    "1 = English (inch, foot)\n"
                    "2 = Metric (mm, m, etc.)"
                ),
                "pdf_page": 31
            }
        }

        self.open_section_window("GLOBAL", fields)

    def open_section_window(self, section_name, fields, pdf_base_url=None):
        # We add an argument 'section_name' to the SectionWindow so it knows which section it is.
        window = SectionWindow(section_name, fields, self.pdf_base_url, self)
        if window.exec():
            # The dialog was accepted (i.e. user clicked Save)
            # 'window.saved_data' contains all field entries
            # 'window.section_state' is "complete", "partial", or "empty"

            # We'll store it in self.section_data under the correct key
            self.section_data[section_name] = window.saved_data

            # Update the main window's button color
            new_color = None
            if window.section_state == "complete":
                new_color = "green"
            elif window.section_state == "partial":
                new_color = "orange"
            elif window.section_state == "empty":
                new_color = None  # default

            # We have a helper function to recolor the section button
            self.set_section_button_color(section_name, new_color)

    # Add this helper to set the color of the section button:
    def set_section_button_color(self, section_name, color):
        """
        Finds the button in the main layout that matches 'section_name'
        and changes its background color.
        """
        # For simplicity, let's store references to buttons in a dictionary
        # in the add_section_button method. We'll do that next.
        if section_name in self.section_buttons:
            button = self.section_buttons[section_name]
            if color is None:
                # reset to default
                button.setStyleSheet("")
            else:
                button.setStyleSheet(f"background-color: {color};")

    def generate_global_section(global_data):
        """
        Generates the GLOBAL section as a list of lines (strings)
        with exact spacing and formatting, matching your sample.

        global_data keys:
            "model_name", "start_date", "end_date",
            "run_interp_output_level", "resume_run", "unit_system"
        """
        lines = []

        # Optional: add a comment line if you want
        lines.append("*** GLOBAL PARAMETERS ***")

        # 1) "GLOBAL"
        lines.append("GLOBAL")

        # 2) Two-space indent + model_name
        model_name = global_data.get("model_name", "").strip()
        lines.append(f"  {model_name}")

        # 3) Start/End line
        start_date = global_data.get("start_date", "")
        end_date = global_data.get("end_date", "")
        # Adjust spacing as desired:
        lines.append(f"  START       {start_date:<16}END    {end_date}")

        # 4) RUN/INTERP/OUTPUT LEVEL
        run_interp_output_level = global_data.get("run_interp_output_level", "RUN INTERP OUTPUT LEVEL    3")
        lines.append(f"  {run_interp_output_level}")

        # 5) RESUME / RUN + UNIT SYSTEM
        resume_run = global_data.get("resume_run", "RESUME     0 RUN     1")
        # Left-justify it so that "UNIT SYSTEM" lines up at column ~35
        resume_run_str = f"{resume_run:<32}"
        unit_system = global_data.get("unit_system", 2)
        lines.append(f"  {resume_run_str}UNIT SYSTEM     {unit_system}")

        # 6) "END GLOBAL"
        lines.append("END GLOBAL")

        return lines

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
        window = SectionWindow(section_name, fields, self.pdf_base_url, self)
        if window.exec():
            # window.saved_data now contains everything the user typed in
            print(f"Data for {section_name}:", window.saved_data)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = UCIFileGeneratorApp()
    window.show()
    sys.exit(app.exec())
