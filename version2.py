from PySide6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget, QFileDialog, QHBoxLayout, QMessageBox,
    QDialog, QFormLayout, QPlainTextEdit, QDateEdit,QGroupBox
)
from PySide6.QtGui import Qt, QIcon,QMouseEvent, QPixmap
from PySide6.QtCore import QDate,QSize
import webbrowser
import sys
from lxml import etree
import json
import pandas as pd

# -------------------------------------------------------
# SectionWindow: Handles one UCI section (GLOBAL, FILES, etc.)
# -------------------------------------------------------
class SectionWindow(QDialog):
    def __init__(
        self,
        section_name,
        fields,
        pdf_base_url,
        parent_main,
        initial_values=None
    ):
        super().__init__(parent_main)
        self.section_name = section_name
        self.fields = fields
        self.pdf_base_url = pdf_base_url
        self.parent_main = parent_main

        if initial_values is None:
            initial_values = {}

        self.saved_data = {}
        self.section_state = "empty"  # can be "empty", "partial", or "complete"

        self.setWindowTitle(f"{section_name} Section")
        main_layout = QVBoxLayout(self)
        self.input_fields = {}

        # Create labeled fields + help buttons
        for field_name, field_info in fields.items():
            row_layout = QHBoxLayout()

            label = QLabel(field_name)
            row_layout.addWidget(label)

            tooltip_text = field_info.get("help_text", "") if isinstance(field_info, dict) else ""
            label.setToolTip(tooltip_text)  # Add tooltip to label

            # Determine if it's a date field
            is_date_field = False
            placeholder_text = ""

            # If fields is a dict, we might have "required", "help_text", "is_date", etc.
            if isinstance(field_info, dict):
                placeholder_text = field_info.get("placeholder", "")
                is_date_field = field_info.get("is_date", False)
            elif isinstance(field_info, str):
                placeholder_text = field_info

            existing_val = initial_values.get(field_name, "")

            if is_date_field:
                # Use QDateEdit for date fields
                date_edit = QDateEdit(self)
                date_edit.setCalendarPopup(True)
                date_edit.setDisplayFormat("yyyy/MM/dd")

                # QDateEdit doesn't implement setPlaceholderText,
                # but we can set it on its internal lineEdit()
                date_edit.lineEdit().setPlaceholderText(placeholder_text)

                # Attempt to parse existing_val (YYYY/MM/DD)
                parts = existing_val.split("/")
                if len(parts) == 3:
                    y, m, d = parts
                    try:
                        date_obj = QDate(int(y), int(m), int(d))
                        if date_obj.isValid():
                            date_edit.setDate(date_obj)
                    except:
                        pass

                input_field = date_edit
                # Connect dateChanged for dynamic enable
                date_edit.dateChanged.connect(lambda _: self.on_field_changed())

            else:
                # Normal QLineEdit
                line_edit = QLineEdit(self)
                line_edit.setText(existing_val)
                line_edit.setPlaceholderText(placeholder_text)
                input_field = line_edit
                input_field.textChanged.connect(self.on_field_changed)

            self.input_fields[field_name] = input_field
            row_layout.addWidget(input_field)

            # Info/help button
            help_button = QPushButton("?")
            help_button.setFixedWidth(30)

            # For dict fields, gather help_text, pdf_page, and "required" if needed
            if isinstance(field_info, dict):
                ht = field_info.get("help_text", "")
                pg = field_info.get("pdf_page", 1)
                req = field_info.get("required", False)
            else:
                ht = ""
                pg = 1
                req = False

            help_button.clicked.connect(
                lambda _, f=field_name, h=ht, p=pg: self.show_help(f, h, p)
            )
            row_layout.addWidget(help_button)

            main_layout.addLayout(row_layout)

        # Bottom row of buttons: Preview, Reset, Save
        button_layout = QHBoxLayout()

        self.preview_button = QPushButton("Preview")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.on_preview_clicked)
        button_layout.addWidget(self.preview_button)

        self.reset_button = QPushButton("Reset")
        self.reset_button.clicked.connect(self.on_reset_clicked)
        button_layout.addWidget(self.reset_button)

        self.save_button = QPushButton("Save")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.on_save_clicked)
        button_layout.addWidget(self.save_button)

        main_layout.addLayout(button_layout)
        self.resize(600, 300)

    def on_field_changed(self):
        """
        Check if there's at least one filled field (-> enable Save),
        and if all required fields are filled (-> enable Preview).
        Highlight invalid or empty required fields.
        """
        required_filled = True
        any_filled = False

        for field_name, field_info in self.fields.items():
            widget = self.input_fields[field_name]
            is_valid = True

            # Extract the current value
            if isinstance(widget, QDateEdit):
                val = widget.date().toString("yyyy/MM/dd").strip()
                # Validate date fields if required
                if field_info.get("required", False) and not widget.date().isValid():
                    is_valid = False
            else:
                val = widget.text().strip()
                # Check if required text fields are filled
                if field_info.get("required", False) and not val:
                    is_valid = False

            # Highlight invalid fields
            if not is_valid:
                widget.setStyleSheet("border: 1px solid orange;")
                placeholder_error = field_info.get("placeholder", "") + " (Required)"
                if isinstance(widget, QDateEdit):
                    widget.lineEdit().setPlaceholderText(placeholder_error)
                else:
                    widget.setPlaceholderText(placeholder_error)
                required_filled = False
            else:
                widget.setStyleSheet("")  # Reset style if valid

            if val:
                any_filled = True

        self.save_button.setEnabled(any_filled)
        self.preview_button.setEnabled(any_filled and required_filled)

    def on_preview_clicked(self):
        """
        Opens a preview dialog with the section content.
        """
        # Gather current user inputs
        data_dict = {}
        for field_name, widget in self.input_fields.items():
            if isinstance(widget, QDateEdit):
                data_dict[field_name] = widget.date().toString("yyyy/MM/dd")
            else:
                data_dict[field_name] = widget.text().strip()

        # Generate the preview text for the section
        if self.section_name == "GLOBAL":
            preview_text = generate_global_section_text(data_dict)
        elif self.section_name == "FILES":
            preview_text = generate_files_section_text(data_dict)
        else:
            preview_text = f"[Preview not implemented for '{self.section_name}']"

        # Open the reusable PreviewDialog
        preview_dialog = PreviewDialog(
            title=f"Preview: {self.section_name}",
            content=preview_text,
            width=900,  # Adjusted dimensions
            height=700,
            parent=self
        )
        preview_dialog.exec()

    def on_reset_clicked(self):
        """
        Clears all fields in this section (makes them empty),
        sets section_state to 'empty', and resets color in main window.
        """
        for field_name, widget in self.input_fields.items():
            if isinstance(widget, QDateEdit):
                widget.lineEdit().clear()
            else:
                widget.clear()

        self.section_state = "empty"
        self.preview_button.setEnabled(False)
        self.save_button.setEnabled(False)

        if hasattr(self.parent_main, "set_section_button_color"):
            self.parent_main.set_section_button_color(self.section_name, None)

    def on_save_clicked(self):
        """
        Copies all field data into self.saved_data,
        determines if the section is empty, partial, or complete,
        warns if partial, then closes.
        """
        self.saved_data.clear()
        filled_count = 0
        required_count = 0
        required_filled_count = 0

        for field_name, field_info in self.fields.items():
            widget = self.input_fields[field_name]
            if isinstance(widget, QDateEdit):
                val = widget.date().toString("yyyy/MM/dd")
            else:
                val = widget.text().strip()

            self.saved_data[field_name] = val

            # Count required fields
            if isinstance(field_info, dict) and field_info.get("required", False):
                required_count += 1
                if val:
                    required_filled_count += 1

            if val:
                filled_count += 1

        if filled_count == 0:
            self.section_state = "empty"
        elif required_filled_count == required_count:
            self.section_state = "complete"
        else:
            self.section_state = "partial"

        if self.section_state == "partial":
            QMessageBox.warning(
                self,
                "Incomplete Section",
                "Not all required fields are filled.\n"
                "You can still save, but the Preview won't be available unless all required fields are filled.",
            )

        self.accept()

    def show_help(self, field_name, help_text, pdf_page):
        """
        Shows a small QDialog with help text and an optional link to the PDF.
        """
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

class PreviewDialog(QDialog):
    def __init__(self, title, content, width=800, height=600, parent=None):
        """
        Reusable dialog to display preview text with Copy and Save buttons.
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(width, height)

        # Main layout
        layout = QVBoxLayout(self)

        # Text area
        self.text_area = QPlainTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setPlainText(content)
        layout.addWidget(self.text_area)

        # Buttons layout
        button_layout = QHBoxLayout()

        # Copy button
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self.copy_to_clipboard)
        button_layout.addWidget(copy_button)

        # Save button
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_to_file)
        button_layout.addWidget(save_button)

        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)

    def copy_to_clipboard(self):
        """
        Copies the content of the text area to the clipboard.
        """
        text = self.text_area.toPlainText()
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "Copied", "Text has been copied to clipboard.")

    def save_to_file(self):
        """
        Opens a file dialog to save the content as a .txt file.
        """
        file_dialog = QFileDialog(self)
        save_path, _ = file_dialog.getSaveFileName(
            self, "Save File", "", "Text Files (*.txt);;All Files (*)"
        )
        if save_path:
            try:
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(self.text_area.toPlainText())
                QMessageBox.information(self, "Success", f"File saved to {save_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save file:\n{e}")

# -------------------------------------------------------
# Functions for Parsing the Diagram and Summaries
# -------------------------------------------------------
def parse_shapes(root):
    shapes_by_id = {}
    shape_cells = root.xpath(".//mxCell[@vertex='1']")
    for cell in shape_cells:
        internal_id = cell.get("id", "").strip()
        style = cell.get("style", "").lower()
        label = cell.get("value", "").strip()

        if not internal_id:
            continue

        recognized = False
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

        if not recognized:
            print(f"WARNING: Shape ID {internal_id} style '{style}' not recognized; using Comment/Note.")

        shapes_by_id[internal_id] = {
            "id": internal_id,
            "label": label,
            "hydro_type": hydro_type,
            "incoming": [],
            "outgoing": []
        }
    return shapes_by_id

def parse_edges(root):
    edges = []
    edge_cells = root.xpath(".//mxCell[@edge='1']")
    for cell in edge_cells:
        src_id = cell.get("source", "").strip()
        tgt_id = cell.get("target", "").strip()
        style = cell.get("style", "").lower() if cell.get("style") else ""

        if src_id and tgt_id:
            edges.append((src_id, tgt_id, style))
    return edges

def normalize_target_types(shapes_by_id):
    """
    Normalize target types to ensure Nodes and SWM Facilities
    are classified as RCHRES if applicable.
    """
    for shape_id, data in shapes_by_id.items():
        label = data.get("label", "")
        hydro_type = data.get("hydro_type", "")

        # Normalize Node and SWM Facility to RCHRES if the label is numeric
        if hydro_type in ["Node", "SWM Facility"] and label.isdigit():
            print(f"Normalizing {label}: {hydro_type} -> RCHRES")  # Debug
            shapes_by_id[shape_id]["hydro_type"] = "RCHRES"

def build_graph(shapes_by_id, edges):
    for (src, tgt, style) in edges:
        if src in shapes_by_id and tgt in shapes_by_id:
            flow_type = "Groundwater" if "dashed=1" in style else "Surface"
            shapes_by_id[src]["outgoing"].append({"target": tgt, "flow_type": flow_type})
            shapes_by_id[tgt]["incoming"].append({"source": src, "flow_type": flow_type})

def compute_branch_length(shapes_by_id, start_id, memo=None):
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
    visited_lines = set()
    visited_targets = set()
    lines = []

    memo_lengths = {}
    for sid in shapes_by_id:
        compute_branch_length(shapes_by_id, sid, memo_lengths)

    def add_line(source_id, target_id, flow_type):
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

        flow_txt = "(Surface)" if flow_type == "Surface" else "(Groundwater)"
        lines.append(f"{src_type} {src_label} discharges {flow_txt} to {tgt_type} {tgt_label}.")

    def process_target(tid):
        if tid in visited_targets:
            return
        for inc_dict in shapes_by_id[tid]["incoming"]:
            inc_id = inc_dict["source"]
            fl_type = inc_dict["flow_type"]
            if (inc_id, tid, fl_type) not in visited_lines:
                process_target(inc_id)
                add_line(inc_id, tid, fl_type)

        outgoings = shapes_by_id[tid]["outgoing"]
        if not outgoings:
            data = shapes_by_id[tid]
            if data["hydro_type"] != "Comment/Note":
                lines.append(f"{data['hydro_type']} {data['label']} does not discharge to any recognized element.")
        else:
            out_sorted = sorted(
                outgoings, key=lambda od: memo_lengths[od["target"]], reverse=True
            )
            for outd in out_sorted:
                nxt_id = outd["target"]
                fl_type = outd["flow_type"]
                add_line(tid, nxt_id, fl_type)
                process_target(nxt_id)

        visited_targets.add(tid)

    # Start with shapes that have no incoming edges
    start_shapes = [
        sid for sid, data in shapes_by_id.items()
        if data["hydro_type"] != "Comment/Note" and not data["incoming"]
    ]
    for s_id in start_shapes:
        process_target(s_id)

    for sid in shapes_by_id:
        if sid not in visited_targets:
            process_target(sid)

    # highlight orphans
    orphans = []
    for sid, data in shapes_by_id.items():
        if not data["incoming"] and not data["outgoing"] and data["hydro_type"] != "Comment/Note":
            orphans.append(sid)
    if orphans:
        lines.append("The following shapes are not connected to any flow path:")
        for o_id in orphans:
            lbl = shapes_by_id[o_id]["label"] or o_id
            lines.append(f"  - {shapes_by_id[o_id]['hydro_type']} {lbl}")
        lines.append("")

    return "\n".join(lines)

class ModelSummaryDialog(QDialog):
    def __init__(self, summary_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Model Summary")
        self.summary_text = summary_text

        layout = QVBoxLayout()

        self.text_area = QPlainTextEdit()
        self.text_area.setReadOnly(True)
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

# -------------------------------------------------------
# UCIFileGeneratorApp: Main Window
# -------------------------------------------------------
class UCIFileGeneratorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.loaded_excel_file = None  # Store the loaded Excel file path
        self.drainage_area_mapping = None  # Store the drainage area mapping

        self.pdf_base_url = (
            "https://hydrologicmodels.tamu.edu/wp-content/uploads/sites/103/2018/09/HSPF_User-Manual.pdf"
        )

        self.setWindowFlags(Qt.FramelessWindowHint)  # Remove native title bar

        self.setWindowTitle("HSP-F UCI File Generator")
        self.setGeometry(100, 100, 900, 600)  # Increased size for better spacing

        # Set the application icon
        self.setWindowIcon(QIcon("Icon.png"))  # Replace 'icon.ico' with your icon file path

        # Apply stylesheet for modern UI
        style_sheet = """
        QMainWindow {
            background-color: #1e1e1e;  /* Dark gray background */
            color: #e1e6e8;  /* Light text for readability */
        }
        
        QPushButton {
            background-color: #2b2b2b;  /* black background */
            color: #e1e6e8;  /* White text */
            border-radius: 15px;  /* Rounded corners */
            padding: 6px 12px;  /* Padding */
            font-size: 14px;  /* Font size */
        }
        QPushButton:hover {
            background-color: #333333;  /* dark blue on hover */
        }
        QGroupBox {
            font-size: 16px;  /* Group box title font size */
            font-weight: bold;  /* Bold group box title */
            color: #e1e6e8;  /* White text */
            border: 2px solid gray;  /* Gray border */
            border-radius: 10px;  /* Rounded group box */
            margin-top: 10px;  /* Margin above the group box */
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;  /* Title position */
            padding: 0 5px;  /* Padding around title */
        }
        """
        self.setStyleSheet(style_sheet)

        self.section_buttons = {}
        self.shapes_by_id = {}
        self.section_data = {}

        # Main layout for the window
        main_layout = QVBoxLayout()
        main_widget = QWidget()
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

        # Custom Title Bar
        main_layout.addWidget(self.create_custom_title_bar())

        # Content layout for Inputs, Outputs, and Sections
        content_widget = QWidget()
        content_layout = QVBoxLayout()
        content_layout.addWidget(self.create_input_group())
        content_layout.addWidget(self.create_output_group())
        content_layout.addWidget(self.create_sections_group())
        content_widget.setLayout(content_layout)
        main_layout.addWidget(content_widget)

    def create_custom_title_bar(self):
        title_bar = QWidget()
        title_bar.setStyleSheet("background-color: dark grey; color: white;")
        title_bar.setFixedHeight(40)

        layout = QHBoxLayout()
        layout.setContentsMargins(10, 0, 10, 0)

        # Add logo to the title bar
        logo_label = QLabel()
        logo_pixmap = QPixmap("Icon.png")  # Replace with your logo image path
        logo_label.setPixmap(logo_pixmap.scaled(30, 30, Qt.KeepAspectRatio))  # Adjust size
        layout.addWidget(logo_label)

        # Window title
        title_label = QLabel("HSP-F UCI File Generator")
        title_label.setStyleSheet("color: white; font-size: 14px;")
        layout.addWidget(title_label)

        # Spacer
        layout.addStretch(1)

        # Minimize button
        minimize_button = QPushButton("-")
        minimize_button.setFixedSize(QSize(30, 30))
        minimize_button.setStyleSheet("background-color: grey; color: white; border: none;")
        minimize_button.clicked.connect(self.showMinimized)
        layout.addWidget(minimize_button)

        # Close button
        close_button = QPushButton("×")
        close_button.setFixedSize(QSize(30, 30))
        close_button.setStyleSheet("background-color: red; color: white; border: none;")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        title_bar.setLayout(layout)
        return title_bar

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
            event.accept()

    # -----------------------------------------
    # Create Input Group Box
    # -----------------------------------------
    def create_input_group(self):
        """
        Creates the Inputs group box containing the Import, Load JSON, and Load Excel buttons.
        """
        input_group = QGroupBox("Inputs")
        input_layout = QVBoxLayout()

        # Add spacing between buttons
        input_layout.setSpacing(10)
        input_layout.setContentsMargins(10, 10, 10, 10)

        # Import Draw.io File button
        drawio_layout = QHBoxLayout()
        self.import_button = QPushButton("Import Draw.io File")
        self.import_button.clicked.connect(self.import_drawio_file)
        drawio_layout.addWidget(self.import_button)

        # Tick mark for Import Draw.io File
        self.drawio_tick_button = QPushButton("✔")
        self.drawio_tick_button.setFixedSize(35, 35)
        self.drawio_tick_button.setStyleSheet(
            "background-color: grey; color: white; font-weight: bold; border-radius: 15px;"
        )
        self.drawio_tick_button.setToolTip("No file selected")  # Default tooltip
        self.drawio_tick_button.clicked.connect(self.toggle_drawio_tooltip)  # Fixed method
        drawio_layout.addWidget(self.drawio_tick_button)
        input_layout.addLayout(drawio_layout)

        # Load JSON button
        json_layout = QHBoxLayout()
        self.load_json_button = QPushButton("Load JSON")
        self.load_json_button.clicked.connect(self.load_json_data)
        json_layout.addWidget(self.load_json_button)

        # Tick mark for Load JSON
        self.json_tick_button = QPushButton("✔")
        self.json_tick_button.setFixedSize(35, 35)
        self.json_tick_button.setStyleSheet(
            "background-color: grey; color: white; font-weight: bold; border-radius: 15px;"
        )
        self.json_tick_button.setToolTip("No file selected")  # Default tooltip
        self.json_tick_button.clicked.connect(self.toggle_json_tooltip)  # Fixed method
        json_layout.addWidget(self.json_tick_button)
        input_layout.addLayout(json_layout)

        # Load Excel File button
        excel_layout = QHBoxLayout()
        self.load_excel_button = QPushButton("Load Excel File")
        self.load_excel_button.clicked.connect(self.load_drainage_areas)
        self.load_excel_button.setToolTip("No file selected")  # Default tooltip
        excel_layout.addWidget(self.load_excel_button)

        # Tick mark for Load Excel File
        self.excel_tick_button = QPushButton("✔")
        self.excel_tick_button.setFixedSize(35, 35)
        self.excel_tick_button.setStyleSheet(
            "background-color: grey; color: white; font-weight: bold; border-radius: 15px;"
        )
        self.excel_tick_button.setToolTip("No file selected")  # Default tooltip
        self.excel_tick_button.clicked.connect(self.toggle_excel_tooltip)
        excel_layout.addWidget(self.excel_tick_button)
        input_layout.addLayout(excel_layout)

        input_group.setLayout(input_layout)
        return input_group

    # -----------------------------------------
    # Create Output Group Box
    # -----------------------------------------
    def create_output_group(self):
        output_group = QGroupBox("Outputs")
        output_layout = QVBoxLayout()

        # Show Imported Model
        self.show_model_button = QPushButton("Show Imported Model")
        self.show_model_button.clicked.connect(self.show_imported_model)
        output_layout.addWidget(self.show_model_button)

        # Save JSON
        self.save_json_button = QPushButton("Save JSON")
        self.save_json_button.clicked.connect(self.save_json_data)
        output_layout.addWidget(self.save_json_button)

        output_group.setLayout(output_layout)
        return output_group

    # -----------------------------------------
    # Create Sections Group Box
    # -----------------------------------------
    def create_sections_group(self):
        sections_group = QGroupBox("UCI Sections")
        sections_layout = QVBoxLayout()

        # Add buttons for each section with revised help text and manual links
        self.add_section_button(sections_layout, "GLOBAL",
                                "Specifies the global simulation parameters, including model name, time step, simulation start/end dates, and unit system.",
                                self.global_section, pdf_page=28)
        self.add_section_button(sections_layout, "FILES",
                                "Defines the input and output file configurations, including WDM files, error logs, and message logs used by the simulation.",
                                self.files_section, pdf_page=52)
        self.add_section_button(sections_layout, "OPN SEQUENCE",
                                "Specifies the sequence of operations for hydrologic and hydraulic processes, such as subcatchments, reaches, and reservoirs.",
                                self.opn_sequence_section, pdf_page=53)
        self.add_section_button(sections_layout, "PERLND",
                                "Defines parameters for pervious land areas, including surface runoff, infiltration, and interflow processes.",
                                self.perlnd_section, pdf_page=66)
        self.add_section_button(sections_layout, "IMPLND",
                                "Specifies parameters for impervious land areas, focusing on surface runoff processes and water balance calculations.",
                                self.implnd_section, pdf_page=68)
        self.add_section_button(sections_layout, "RCHRES",
                                "Manages routing and storage for reaches and reservoirs, including flow routing, sediment transport, and water quality simulations.",
                                self.rchres_section, pdf_page=70)
        self.add_section_button(sections_layout, "FTABLES",
                                "Defines flow tables (FTABLEs) for routing water through channels and reservoirs, specifying stage-discharge relationships.",
                                self.ftables_section, pdf_page=72)
        self.add_section_button(sections_layout, "EXT SOURCES",
                                "Specifies external sources of input, including precipitation, point source flows, and other inflows to the hydrologic system.",
                                self.ext_sources_section, pdf_page=74)
        self.add_section_button(sections_layout, "EXT TARGETS",
                                "Defines output targets for external inputs, such as monitoring locations or downstream flow points.",
                                self.ext_targets_section, pdf_page=76)
        self.add_section_button(sections_layout, "NETWORK",
                                "Specifies flow relationships between elements like pervious land, impervious land, reaches, and reservoirs.",
                                self.network_section, pdf_page=78)

        sections_group.setLayout(sections_layout)
        return sections_group

    # ------------------------------------------------
    # Add a section button (with a help "?") to the UI
    # ------------------------------------------------
    def add_section_button(self, layout, section_name, help_text, callback, pdf_page=None):
        """
        Adds a section button and help button to the layout.
        """
        section_layout = QHBoxLayout()
        section_button = QPushButton(section_name)
        section_button.clicked.connect(callback)
        help_button = QPushButton("?")
        help_button.setMaximumWidth(30)

        # Pass detailed help text and PDF page to the help dialog
        help_button.clicked.connect(
            lambda: self.show_help(section_name, help_text, pdf_page)
        )
        section_layout.addWidget(section_button)
        section_layout.addWidget(help_button)
        layout.addLayout(section_layout)

        self.section_buttons[section_name] = section_button

    def show_help(self, title, message, pdf_page=None):
        """
        Displays a help dialog with detailed text and a link to the manual page.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Help for {title}")
        layout = QVBoxLayout(dialog)

        info_label = QLabel()
        info_label.setTextFormat(Qt.RichText)
        info_label.setOpenExternalLinks(True)

        # Add manual link if pdf_page is provided
        link_html = ""
        if pdf_page:
            link_html = f"<br><a href='{self.pdf_base_url}#page={pdf_page}' target='_blank'>Read more</a>"
        info_label.setText(f"{message}{link_html}")
        layout.addWidget(info_label)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.close)
        layout.addWidget(close_button)

        dialog.setLayout(layout)
        dialog.exec()

    # -----------------------------------------
    # JSON Load/Save
    # -----------------------------------------
    def load_json_data(self):
        """
        Load a JSON file and update the tick mark button.
        """
        file_dialog = QFileDialog(self)
        json_file, _ = file_dialog.getOpenFileName(
            self, "Select JSON File", "", "JSON Files (*.json);;All Files (*)"
        )
        if not json_file:
            QMessageBox.warning(self, "No File", "No JSON file selected.")
            return

        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.section_data = data if isinstance(data, dict) else {}

            # Update tickmark button style and tooltip
            self.json_tick_button.setStyleSheet(
                "background-color: green; color: white; font-weight: bold; border-radius: 15px;"
            )
            self.update_file_tooltip(self.json_tick_button, json_file)

            QMessageBox.information(self, "Success", "JSON file has been loaded successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load JSON file:\n{e}")

    def save_json_data(self):
        file_dialog = QFileDialog(self)
        save_path, _ = file_dialog.getSaveFileName(
            self, "Save JSON File", "", "JSON Files (*.json);;All Files (*)"
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(self.section_data, f, indent=2)
            QMessageBox.information(self, "Success", f"Data saved to {save_path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save JSON:\n{e}")

    # -----------------------------------------
    # Import & Show Model
    # -----------------------------------------
    def import_drawio_file(self):
        """
        Import a Draw.io file and update the tick mark button.
        """
        file_dialog = QFileDialog(self)
        drawio_file, _ = file_dialog.getOpenFileName(
            self, "Select Draw.io XML", "", "XML Files (*.xml);;All Files (*)"
        )
        if not drawio_file:
            QMessageBox.warning(self, "No File", "No Draw.io file selected.")
            return

        try:
            # Process the file (existing logic)
            tree = etree.parse(drawio_file)
            root = tree.getroot()
            self.shapes_by_id = parse_shapes(root)
            edges = parse_edges(root)
            build_graph(self.shapes_by_id, edges)

            # Update tickmark button style and tooltip
            self.drawio_tick_button.setStyleSheet(
                "background-color: green; color: white; font-weight: bold; border-radius: 15px;"
            )
            self.update_file_tooltip(self.drawio_tick_button, drawio_file)

            QMessageBox.information(self, "Success", "Draw.io file has been loaded successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load Draw.io file:\n{e}")

    def show_imported_model(self):
        if not self.shapes_by_id:
            QMessageBox.warning(self, "No Data", "No model data has been imported yet.")
            return
        summary = narrative_summary(self.shapes_by_id)
        if not summary.strip():
            summary = "No recognized connections."
        dialog = ModelSummaryDialog(summary, self)
        dialog.exec()

    # -----------------------------------------
    # Open a Section Window
    # -----------------------------------------
    def open_section_window(self, section_name, fields):
        # Check if there's existing data for this section
        existing_data = self.section_data.get(section_name, {})

        # Create a new SectionWindow
        window = SectionWindow(section_name, fields, self.pdf_base_url, self, existing_data)
        if window.exec():
            # If user clicked Save, store updated data
            self.section_data[section_name] = window.saved_data

            # Update color based on final state
            new_color = None
            if window.section_state == "complete":
                new_color = "limegreen"
            elif window.section_state == "partial":
                new_color = "darkorange"
            elif window.section_state == "empty":
                new_color = None

            self.set_section_button_color(section_name, new_color)

    def set_section_button_color(self, section_name, color):
        if section_name in self.section_buttons:
            button = self.section_buttons[section_name]
            if color is None:
                button.setStyleSheet("")
            else:
                button.setStyleSheet(f"background-color: {color}; color: black;")

    # -----------------------------------------
    # Section Callbacks
    # -----------------------------------------
    def global_section(self):
        fields = {
            "Model Name": {
                "placeholder": "Enter a descriptive name for the watershed/model run",
                "help_text": "This appears under GLOBAL in the UCI.",
                "pdf_page": 28,
                "required": True
            },
            "Start Date (YYYY/MM/DD)": {
                "placeholder": "YYYY/MM/DD",
                "help_text": "Simulation start date.",
                "pdf_page": 29,
                "required": True,
                "is_date": True
            },
            "End Date (YYYY/MM/DD)": {
                "placeholder": "YYYY/MM/DD",
                "help_text": "Simulation end date.",
                "pdf_page": 29,
                "required": True,
                "is_date": True
            },
            "Run/Interp/Output Level": {
                "placeholder": "RUN INTERP OUTPUT LEVEL    3",
                "help_text": "Specifies how HSPF will run.",
                "pdf_page": 30,
                "required": True
            },
            "Resume": {
                "placeholder": "e.g., 0",
                "help_text": "'RESUME 0' means do not resume a previous run.",
                "pdf_page": 30,
                "required": True
            },
            "Run": {
                "placeholder": "e.g., 1",
                "help_text": "Sets a run number, e.g. 'RUN 1'.",
                "pdf_page": 30,
                "required": True
            },
            "Unit System": {
                "placeholder": "1=English, 2=Metric",
                "help_text": "Defines unit system: 1=English, 2=Metric.",
                "pdf_page": 31,
                "required": True
            }
        }
        self.open_section_window("GLOBAL", fields)

    def files_section(self):
        """
        Opens a section window for FILES, allowing the user to specify input and output file names.
        """
        fields = {
            "WDM1 (Input File Name)": {
                "placeholder": "e.g., CONMET.WDM",
                "help_text": "The primary input file for data.",
                "pdf_page": 52,
                "required": True,
            },
            "WDM2 (Output File Name)": {
                "placeholder": "e.g., CONOUT.WDM",
                "help_text": "The primary output file for data.",
                "pdf_page": 52,
                "required": True,
            },
            "INFO (Output File Name)": {
                "placeholder": "e.g., 01_HSPINF.DA",
                "help_text": "The file for general information output.",
                "pdf_page": 53,
                "required": True,
            },
            "ERROR (Output File Name)": {
                "placeholder": "e.g., 01_HSPERR.DA",
                "help_text": "The file for error message logs.",
                "pdf_page": 53,
                "required": True,
            },
            "WARN (Output File Name)": {
                "placeholder": "e.g., 01_HSPWRN.DA",
                "help_text": "The file for warning message logs.",
                "pdf_page": 53,
                "required": True,
            },
            "MESSU (Output File Name)": {
                "placeholder": "e.g., 01_HSPMES.DA",
                "help_text": "The file for user message logs.",
                "pdf_page": 53,
                "required": True,
            },
            "Optional Output File": {
                "placeholder": "e.g., 01_EXTL1.OUT",
                "help_text": "Any additional output file (optional).",
                "pdf_page": 53,
                "required": False,
            },
        }
        self.open_section_window("FILES", fields)

    def opn_sequence_section(self):
        """
        Generate and preview the Operation Sequence Block.
        """
        if not hasattr(self, "network_block") or not self.network_block:
            QMessageBox.warning(self, "No Network Block", "Generate the NETWORK block first.")
            return

        try:
            # Generate the Operation Sequence Block
            operation_sequence = generate_operation_sequence_block(self.network_block)

            if not operation_sequence:
                QMessageBox.warning(self, "Error", "Failed to generate Operation Sequence block.")
                return

            # Show the Operation Sequence Block in the preview dialog
            preview_dialog = PreviewDialog(
                title="Preview: Operation Sequence",
                content="\n".join(operation_sequence),
                width=600,
                height=400,
                parent=self
            )
            preview_dialog.exec()
        except Exception as e:
            print(f"Error in opn_sequence_section: {e}")
            QMessageBox.critical(self, "Error", f"Failed to generate Operation Sequence block:\n{e}")

    def perlnd_section(self):
        fields = {"Pervious Land Parameters": "Specify parameters for pervious land areas"}
        self.open_section_window("PERLND", fields)

    def implnd_section(self):
        fields = {"Impervious Land Parameters": "Specify parameters for impervious land areas"}
        self.open_section_window("IMPLND", fields)

    def rchres_section(self):
        fields = {"Routing Parameters": "Specify parameters for reaches and reservoirs"}
        self.open_section_window("RCHRES", fields)

    def ftables_section(self):
        fields = {"FTable Parameters": "Specify flow tables for routing"}
        self.open_section_window("FTABLES", fields)

    def ext_sources_section(self):
        fields = {"External Source Parameters": "Define external input sources"}
        self.open_section_window("EXT SOURCES", fields)

    def ext_targets_section(self):
        fields = {"External Target Parameters": "Specify targets for external inputs"}
        self.open_section_window("EXT TARGETS", fields)

    def network_section(self):
        """
        Handle the NETWORK section logic, ensuring the network block is generated correctly.
        """
        if not self.shapes_by_id:
            QMessageBox.warning(self, "No Data", "No model data has been imported yet.")
            return

        if not self.drainage_area_mapping:
            QMessageBox.warning(self, "Excel File Required", "Please load an Excel file to proceed.")
            self.load_drainage_areas()  # Allow the user to load the file
            if not self.drainage_area_mapping:
                return

        try:
            # Generate the NETWORK block
            network_block = generate_corrected_network_block(self.shapes_by_id, self.drainage_area_mapping)

            if not network_block:
                QMessageBox.warning(self, "Error", "Failed to generate network block.")
                return

            # Store the generated NETWORK block for later use
            self.network_block = network_block

            # Show the NETWORK block in the preview dialog
            preview_dialog = PreviewDialog(
                title="Preview: NETWORK",
                content="\n".join(network_block),
                width=900,
                height=700,
                parent=self
            )
            preview_dialog.exec()
        except Exception as e:
            print(f"Error in network_section: {e}")
            QMessageBox.critical(self, "Error", f"Failed to generate NETWORK section:\n{e}")

    def load_drainage_areas(self):
        """
        Load drainage areas from the Excel file into a mapping and display confirmation.
        """
        file_dialog = QFileDialog(self)
        excel_file, _ = file_dialog.getOpenFileName(
            self, "Select Excel File", "", "Excel Files (*.xlsx);;All Files (*)"
        )
        if not excel_file:
            QMessageBox.warning(self, "No File", "No Excel file selected.")
            return {}

        try:
            df = pd.read_excel(excel_file)
            drainage_area_mapping = {}
            for _, row in df.iterrows():
                subcatchment = int(row["SUBCATCHMENT"])
                perlnd_area = row["PERLND"]
                implnd_area = row["IMPLND"]

                drainage_area_mapping[f"PERLND {subcatchment}.0"] = perlnd_area
                drainage_area_mapping[f"IMPLND {subcatchment}.0"] = implnd_area

            # Store the state
            self.loaded_excel_file = excel_file
            self.drainage_area_mapping = drainage_area_mapping

            # Update tickmark button style and tooltip
            self.excel_tick_button.setStyleSheet(
                "background-color: green; color: white; font-weight: bold; border-radius: 15px;"
            )
            self.update_file_tooltip(self.excel_tick_button, excel_file)

            # Confirmation message
            QMessageBox.information(self, "Success", "Excel file has been loaded successfully.")
            return drainage_area_mapping
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load Excel file:\n{e}")
            return {}

    def update_file_tooltip(self, button, file_path):
        """
        Update the tooltip for the tick mark button to show the file name or full path.
        """
        file_name = file_path.split("/")[-1]  # Extract the file name
        button.setToolTip(f"File: {file_name}")  # Default to file name
        button.full_path = file_path  # Store the full path in the button

        # Attach toggle functionality
        def toggle_tooltip():
            current_tooltip = button.toolTip()
            if "Full Path" in current_tooltip:
                button.setToolTip(f"File: {file_name}")  # Show file name
            else:
                button.setToolTip(f"Full Path: {file_path}")  # Show full path

        button.clicked.disconnect()  # Ensure no duplicate connections
        button.clicked.connect(toggle_tooltip)

    def toggle_full_path(self, event):
        """
        Toggles the tooltip and label text between file name and full path.
        """
        if self.is_showing_full_path:
            # Switch to file name
            self.excel_tick_button.setToolTip(f"File: {self.selected_file_name}")
            self.toggle_label.setText("Click for Full Path")
            self.is_showing_full_path = False
        else:
            # Switch to full path
            self.excel_tick_button.setToolTip(f"Full Path: {self.selected_full_path}")
            self.toggle_label.setText("Click for File Name")
            self.is_showing_full_path = True

    # -----------------------------------------
    # Add Tooltip Toggle Functionality
    # -----------------------------------------
    def toggle_excel_tooltip(self):
        """
        Toggles the tooltip between the file name and full file path for the tick mark button.
        """
        if self.is_showing_full_path:
            # Switch to file name
            self.excel_tick_button.setToolTip(f"File: {self.selected_file_name}")
            self.is_showing_full_path = False
        else:
            # Switch to full path
            self.excel_tick_button.setToolTip(f"Full Path: {self.selected_full_path}")
            self.is_showing_full_path = True

    def toggle_drawio_tooltip(self):
        """
        Toggles the tooltip between the file name and full file path for the Draw.io tick mark button.
        """
        if hasattr(self.drawio_tick_button, "full_path"):
            current_tooltip = self.drawio_tick_button.toolTip()
            if "Full Path" in current_tooltip:
                # Switch to file name
                file_name = self.drawio_tick_button.full_path.split("/")[-1]
                self.drawio_tick_button.setToolTip(f"File: {file_name}")
            else:
                # Switch to full path
                self.drawio_tick_button.setToolTip(f"Full Path: {self.drawio_tick_button.full_path}")

    def toggle_json_tooltip(self):
        """
        Toggles the tooltip between the file name and full file path for the JSON tick mark button.
        """
        if hasattr(self.json_tick_button, "full_path"):
            current_tooltip = self.json_tick_button.toolTip()
            if "Full Path" in current_tooltip:
                # Switch to file name
                file_name = self.json_tick_button.full_path.split("/")[-1]
                self.json_tick_button.setToolTip(f"File: {file_name}")
            else:
                # Switch to full path
                self.json_tick_button.setToolTip(f"Full Path: {self.json_tick_button.full_path}")

# -------------------------------------------------------
# Generate text for GLOBAL (you could add others similarly)
# -------------------------------------------------------
def generate_global_section_text(data_dict):
    """
    data_dict might look like:
    {
      "Model Name": "SIXTEEN MILE CREEK WATERSHED...",
      "Start Date (YYYY/MM/DD)": "1962/01/01",
      "End Date (YYYY/MM/DD)": "2017/12/31",
      "Run/Interp/Output Level": "RUN INTERP OUTPUT LEVEL    3",
      "Resume": "0",
      "Run": "1",
      "Unit System": "2"
    }
    """
    lines = []
    lines.append("GLOBAL")
    lines.append(f"  {data_dict.get('Model Name', '').strip()}")

    start_d = data_dict.get("Start Date (YYYY/MM/DD)", "")
    end_d   = data_dict.get("End Date (YYYY/MM/DD)", "")
    lines.append(f"  START       {start_d:<16}  END    {end_d}")

    run_interp = data_dict.get("Run/Interp/Output Level", "RUN INTERP OUTPUT LEVEL    3")
    lines.append(f"  RUN INTERP OUTPUT LEVEL    {run_interp}")

    resume_val = data_dict.get("Resume", "0")
    run_val    = data_dict.get("Run", "1")
    combined   = f"RESUME     {resume_val} RUN     {run_val}"
    combined   = f"{combined:<32}"  # left-justify
    unit_sys   = data_dict.get("Unit System", "2")
    lines.append(f"  {combined}         UNIT SYSTEM     {unit_sys}")

    lines.append("END GLOBAL")
    return "\n".join(lines)

def generate_files_section_text(data_dict):
    """
    Generate the text for the FILES section from the user-provided data.
    """
    lines = []
    lines.append("FILES")
    lines.append("<ftyp>  <un#>   <-------file name ------------------------------------->****")

    # Add required file entries
    file_entries = [
        ("WDM1", 23, data_dict.get("WDM1 (Input File Name)", "").strip()),
        ("WDM2", 21, data_dict.get("WDM2 (Output File Name)", "").strip()),
        ("INFO", 24, data_dict.get("INFO (Output File Name)", "").strip()),
        ("ERROR", 25, data_dict.get("ERROR (Output File Name)", "").strip()),
        ("WARN", 26, data_dict.get("WARN (Output File Name)", "").strip()),
        ("MESSU", 27, data_dict.get("MESSU (Output File Name)", "").strip()),
    ]

    for ftyp, un, fname in file_entries:
        if fname:
            lines.append(f"{ftyp:<10}{un:<6}{fname}")

    # Add optional file entry if provided
    optional_file = data_dict.get("Optional Output File", "").strip()
    if optional_file:
        lines.append(f"{'':<10}{50:<6}{optional_file}")

    lines.append("END FILES")
    return "\n".join(lines)

def generate_corrected_network_block(shapes_by_id, drainage_area_mapping):
    """
    Generate the NETWORK block with corrected drainage areas and relationships,
    grouped and ordered as required.
    """
    network_lines = []
    rchres_groups = {}

    # Step 1: Process Subcatchments (PERLND, IMPLND) and group by their target RCHRES
    for shape_id, shape_data in sorted(shapes_by_id.items(), key=lambda x: x[1]["label"]):
        label = shape_data["label"]
        hydro_type = shape_data["hydro_type"]
        outgoing = shape_data["outgoing"]

        if hydro_type == "Subcatchment":
            perlnd_key = f"PERLND {label}.0"
            implnd_key = f"IMPLND {label}.0"

            # Validate outgoing connections
            if not outgoing or outgoing[0]["target"] not in shapes_by_id:
                print(f"Warning: Invalid or missing target for Subcatchment {label}")
                continue

            target_id = outgoing[0]["target"]
            target_label = shapes_by_id[target_id]["label"]

            if target_label not in rchres_groups:
                rchres_groups[target_label] = []

            # Add PERLND connection
            if perlnd_key in drainage_area_mapping:
                drainage_area = round(drainage_area_mapping[perlnd_key] / 100000, 7)
                rchres_groups[target_label].append(
                    f"PERLND {label:<3} PWATER PERO      {drainage_area:<9.7f}      RCHRES {target_label:<3}     INFLOW"
                )
                print(f"Added PERLND Connection: {label} -> {target_label}")

            # Add IMPLND connection
            if implnd_key in drainage_area_mapping:
                drainage_area = round(drainage_area_mapping[implnd_key] / 100000, 7)
                rchres_groups[target_label].append(
                    f"IMPLND {label:<3} IWATER SURO      {drainage_area:<9.7f}      RCHRES {target_label:<3}     INFLOW"
                )
                print(f"Added IMPLND Connection: {label} -> {target_label}")

    # Step 2: Process RCHRES relationships
    for shape_id, shape_data in sorted(shapes_by_id.items(), key=lambda x: x[1]["label"]):
        if shape_data["hydro_type"] in ["RCHRES", "Node", "SWM Facility"]:
            label = shape_data["label"]

            for connection in shape_data["outgoing"]:
                target_id = connection["target"]
                if target_id not in shapes_by_id:
                    print(f"Warning: Invalid or missing target ID {target_id} for RCHRES {label}")
                    continue

                target_label = shapes_by_id[target_id]["label"]
                if label not in rchres_groups:
                    rchres_groups[label] = []
                rchres_groups[label].append(
                    f"RCHRES {label:<3} HYDR   ROVOL                    RCHRES {target_label:<3}     INFLOW"
                )
                print(f"Added RCHRES Connection: {label} -> {target_label}")

    # Step 3: Order and Format the Output
    processed_rchres = set()

    def process_rchres(label):
        if label in processed_rchres:
            print(f"Skipping already processed RCHRES: {label}")
            return

        # Debug: Starting processing
        print(f"Processing RCHRES Group: {label}")

        # Add the group for this RCHRES
        if label in rchres_groups and rchres_groups[label]:
            network_lines.extend(rchres_groups[label])
            network_lines.append("")  # Add a blank line between groups

        processed_rchres.add(label)

        # Process downstream connections
        for line in rchres_groups.get(label, []):
            if "RCHRES" in line:
                target_label = line.split()[-2]
                if target_label in rchres_groups:
                    process_rchres(target_label)
                else:
                    print(f"Warning: Target label {target_label} not found in RCHRES groups.")

    # Start with all RCHRES groups
    for label in rchres_groups:
        process_rchres(label)

    # Final clean-up: Remove trailing blank line
    if network_lines and network_lines[-1] == "":
        network_lines.pop()

    # Debug final output
    if not network_lines:
        print("Error: No valid network lines generated.")
        return None

    print(f"Generated Network Block: \n{network_lines}")
    return network_lines

def generate_operation_sequence_block(network_block):
    """
    Generate the Operation Sequence Block from the given NETWORK block.
    Ensures all RCHRES targets are included, even if only referenced downstream,
    while preserving grouping and sequence order.
    """
    operation_sequence = []
    processed_operations = set()  # To track operations already added
    referenced_rchres_targets = set()  # To track all RCHRES targets explicitly referenced

    current_group = []  # Temporary storage for operations within the current group

    def flush_group():
        """Flush the current group to the operation sequence and clear it."""
        if current_group:
            operation_sequence.extend(current_group)
            operation_sequence.append("")  # Add a blank line to separate groups
            current_group.clear()

    for line in network_block:
        if not line.strip():  # Blank line indicates group separation
            flush_group()
            continue

        parts = line.split()
        if len(parts) < 2:  # Skip invalid lines
            continue

        # Extract the operation and its ID
        operation = f"{parts[0]} {parts[1]}"
        if operation not in processed_operations:
            processed_operations.add(operation)
            current_group.append(operation)

        # Track RCHRES targets explicitly referenced in the line
        if parts[0] == "RCHRES" and len(parts) >= 8:
            target_rchres = f"RCHRES {parts[-2]}"
            referenced_rchres_targets.add(target_rchres)

    # Flush the last group
    flush_group()

    # Add any unprocessed RCHRES targets to the sequence
    unprocessed_targets = referenced_rchres_targets - processed_operations
    if unprocessed_targets:
        operation_sequence.append("")  # Separate dangling targets into a new group
        for target in sorted(unprocessed_targets):
            operation_sequence.append(target)
            print(f"Added missing RCHRES target to operation sequence: {target}")

    # Remove trailing blank lines
    while operation_sequence and operation_sequence[-1] == "":
        operation_sequence.pop()

    return operation_sequence

# -------------------------------------------------------
# Main Entry Point
# -------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = UCIFileGeneratorApp()
    window.show()
    sys.exit(app.exec())
